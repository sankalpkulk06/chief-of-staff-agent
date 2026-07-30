"""Passive fact-learning: mine durable user-facts from a finished turn and store them safely.

Runs AFTER a turn (off the response path). Pipeline: extract -> filter -> dedup/supersede -> write.
Learned facts are always ``status='tentative'`` and reversible; facts touched by external content
(email/documents) are ``trust='low'`` and attributed on use. A category denylist blocks sensitive
data (credentials/payment/medical) regardless of source, closing the obvious injection abuse.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

from app.agents.fact_tools import LEARN_SYSTEM, RECORD_FACTS
from app.config.settings import Settings
from app.core.fact_service import FactService, content_key
from app.providers.tool_types import supports_tools

logger = logging.getLogger(__name__)

# Never auto-learn these regardless of source — an email/doc must not be able to plant them.
_DENYLIST = re.compile(
    r"\b(password|passcode|pin|otp|2fa|api[\s_-]?key|secret|token|credential|"
    r"ssn|social security|credit[\s_-]?card|card number|cvv|cvc|bank account|routing number|"
    r"diagnos|medical|prescription|blood type|medication)\b",
    re.IGNORECASE,
)


class FactLearnerService:
    """Extracts and stores durable user-facts from a turn's content."""

    def __init__(self, registry: Any, provider: Any, settings: Settings, user_id: str = ""):
        self._facts = FactService(registry, user_id=user_id)
        self._provider = provider
        self._settings = settings
        self._user_id = user_id

    def learn(self, user_message: str, assistant_context: str, *, external: bool = False) -> List[dict]:
        """Learn from one turn. ``external`` = the turn's content came from email/documents
        (untrusted) → learned facts are marked low-trust. Returns the facts written (for logging)."""
        if not self._settings.passive_learning_enabled:
            return []
        text = f"USER SAID:\n{user_message}\n\nWHAT THE TURN REVEALED:\n{assistant_context}".strip()
        if len(text) < 12:
            return []

        candidates = self._extract(text)
        min_conf = self._settings.passive_learning_min_confidence
        written: List[dict] = []
        for c in candidates:
            if len(written) >= self._settings.passive_learning_max_per_turn:
                logger.info("fact-learner: per-turn cap reached, dropping %d extra candidate(s)",
                            len(candidates) - len(written))
                break
            fact = self._consider(c, min_conf, external)
            if fact is not None:
                written.append(fact)
        # One summary line per turn (INFO) so decisions are watchable in `docker compose logs -f sage`.
        if candidates:
            stored = ", ".join(f"{w['content']!r} ({w['trust']})" for w in written) or "nothing"
            logger.info("fact-learner: %d candidate(s) → kept %d, dropped %d | stored: %s",
                        len(candidates), len(written), len(candidates) - len(written), stored)
        return written

    # ------------------------------------------------------------------

    def _consider(self, c: dict, min_conf: float, external: bool) -> Optional[dict]:
        content = str(c.get("content") or "").strip()
        if not content:
            return None
        # Selectivity: durable + about-the-user + confident. This is what stops "store everything".
        if not (c.get("durable") and c.get("about_user")):
            return None
        try:
            confidence = float(c.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_conf:
            return None
        # Safety: never auto-learn sensitive data, whatever the source.
        subject = str(c.get("subject") or "").strip()
        if _DENYLIST.search(content) or _DENYLIST.search(subject):
            logger.info("fact-learner: denylisted candidate blocked")
            return None

        category = c.get("category") if c.get("category") in {"personal", "work"} else "personal"
        trust = "low" if external else "high"
        key = content_key(subject or content)

        # Dedup / supersede against active facts sharing the same subject key.
        existing = self._facts.find_by_key(key)
        norm = content_key(content)
        if any(content_key(e["content"]) == norm for e in existing):
            return None  # already known — don't pile up duplicates

        new_fact = self._facts.remember(
            content=content, category=category, source=("email/doc" if external else "conversation"),
            confidence_score=confidence, trust=trust, status="tentative", key=key,
        )
        for e in existing:  # same subject, different value → the older fact is superseded
            self._facts.supersede(e["fact_id"], new_fact.fact_id)
        logger.info("fact-learner: stored (trust=%s) %r", trust, content)
        return {"fact_id": new_fact.fact_id, "content": content, "trust": trust}

    def _extract(self, text: str) -> List[dict]:
        messages = [{"role": "system", "content": LEARN_SYSTEM}, {"role": "user", "content": text}]
        if self._settings.tool_calling_enabled and supports_tools(self._provider):
            try:
                res = self._provider.chat_tools(messages, [RECORD_FACTS], tool_choice="required")
                out: List[dict] = []
                for call in res.tool_calls:
                    if call.name == "record_facts":
                        out.extend(f for f in (call.arguments or {}).get("facts", []) if isinstance(f, dict))
                return out
            except Exception:
                logger.debug("fact-learner: tool extraction failed, using prompt fallback", exc_info=True)
        return self._extract_legacy(messages)

    def _extract_legacy(self, messages: List[dict]) -> List[dict]:
        prompt = list(messages)
        prompt[0] = {"role": "system", "content": LEARN_SYSTEM +
                     "\n\nRespond with ONLY JSON: {\"facts\": [{\"content\":..., \"subject\":..., "
                     "\"category\":\"personal|work\", \"confidence\":0-1, \"durable\":bool, \"about_user\":bool}]}"}
        try:
            raw = self._provider.chat(messages=prompt)
            match = re.search(r"\{.*\}", raw or "", re.DOTALL)
            if not match:
                return []
            data = json.loads(match.group())
            return [f for f in data.get("facts", []) if isinstance(f, dict)]
        except Exception:
            logger.debug("fact-learner: legacy extraction failed", exc_info=True)
            return []
