"""SecurityAgent — input validation, PII flagging, and output scrubbing."""
import html as _html_module
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Optional

from app.agents.base import SecurityResult
from app.agents.prompts import load
from app.agents.security_policy import SecurityPolicy

if TYPE_CHECKING:
    # Type-only import — pulling this at runtime would drag the whole storage
    # package (and chromadb) into every SecurityAgent import.
    from app.storage.sqlite_registry import SQLiteRegistry

log = logging.getLogger(__name__)

# Sentinel for backwards-compatible legacy __init__ kwargs
_UNSET = object()

# ---------------------------------------------------------------------------
# PII detection patterns
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(\+1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d{4}[\s\-]?){3}\d{4}\b")

# ---------------------------------------------------------------------------
# Secret leak scrubbing patterns — applied to LLM output
# Authorization header first (greedy line match) so Bearer sub-pattern doesn't fire twice.
# ---------------------------------------------------------------------------
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Authorization:\s*.+", re.IGNORECASE), "Authorization: [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9\-]{20,}"), "[REDACTED]"),
    (re.compile(r"gsk_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"AIza[A-Za-z0-9_\-]{35}"), "[REDACTED]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}"), "Bearer [REDACTED]"),
    # ALL_CAPS_KEY=longvalue (env-var style)
    (
        re.compile(
            r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]{2,}\s*=\s*[^\s\"']{8,}",
            re.MULTILINE,
        ),
        "[REDACTED]",
    ),
]

# ---------------------------------------------------------------------------
# HTML / script injection sanitization patterns
# Targets executable/dangerous constructs only — benign HTML (<b>, <a href="...">) untouched.
# Entity decoding (html.unescape) must run BEFORE these patterns to catch &#60;script&#62; etc.
# ---------------------------------------------------------------------------
_HTML_SANITIZE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"<script\b[^>]*>.*?</script>",  re.IGNORECASE | re.DOTALL), ""),
    (re.compile(r"<iframe\b[^>]*>.*?</iframe>",   re.IGNORECASE | re.DOTALL), ""),
    (re.compile(r"<object\b[^>]*>.*?</object>",   re.IGNORECASE | re.DOTALL), ""),
    (re.compile(r"<embed\b[^>]*/?>",              re.IGNORECASE),             ""),
    (re.compile(r"<form\b[^>]*>.*?</form>",       re.IGNORECASE | re.DOTALL), ""),
    (re.compile(r'(href|src|action)\s*=\s*(?:"javascript\s*:[^"]*"|\'javascript\s*:[^\']*\'|javascript\s*:[^\s>]*)', re.IGNORECASE), ""),
    (re.compile(r'\bon\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', re.IGNORECASE), ""),
]

# LLM classifier system prompt for subtle injection detection
_CLASSIFIER_SYSTEM = load("security_classifier")


class SecurityAgent:
    """
    Pipeline guard that wraps AgentRunner at two hook points.

    check_input()  — called BEFORE orchestrator.plan():
        0. Rate limit (hard block — fastest, runs first)
        1. Length limit (hard block)
        2. HTML sanitization (strip dangerous tags, set working_text)
        3. Regex prompt injection scan (hard block on working_text)
        4. LLM fallback injection check (hard block on working_text)
        5. PII detection (soft flag on working_text)

    check_output() — called AFTER orchestrator.synthesize():
        6. Secret leak scrubbing (redact patterns in-place)
        7. Max output length enforcement (truncate with [truncated])

    Construction:
        Preferred: SecurityAgent(registry=..., chat_provider=..., policy=SecurityPolicy.from_settings(settings))
        Legacy:    SecurityAgent(registry=..., chat_provider=..., max_input_length=2000, enabled=True)
    """

    def __init__(
        self,
        registry: Optional["SQLiteRegistry"] = None,
        chat_provider: Optional[Any] = None,
        max_input_length: Any = _UNSET,   # legacy param — ignored when policy is given
        enabled: Any = _UNSET,            # legacy param — ignored when policy is given
        policy: Optional[SecurityPolicy] = None,
    ):
        if policy is not None:
            self._policy = policy
        else:
            # Build minimal policy from legacy kwargs, preserving old defaults
            self._policy = SecurityPolicy(
                enabled=(enabled if enabled is not _UNSET else True),
                max_input_length=(max_input_length if max_input_length is not _UNSET else 2000),
            )

        self._registry = registry
        self._chat_provider = chat_provider

        # Compile injection patterns once — case-insensitive for most, case-sensitive for DAN etc.
        self._injection_patterns: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in self._policy.injection_patterns
        ]
        self._injection_patterns += [
            re.compile(p) for p in self._policy.case_sensitive_injection_patterns
        ]

        # Per-user sliding window: {user_id: [monotonic_timestamp, ...]}
        # Not thread-safe across concurrent requests — acceptable for single-worker deployment.
        self._rate_limit_window: dict[str, list[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_input(self, text: str, user_id: str = "") -> SecurityResult:
        """Scan input text before it reaches the orchestrator.

        Returns SecurityResult. If blocked=True the caller must return a
        rejection immediately without calling any downstream agents.
        If sanitized_input is not None, the caller should use that as the
        question going forward (HTML was stripped).
        """
        if not self._policy.enabled:
            return SecurityResult(blocked=False)

        # 0. Rate limit — fastest check, no string ops
        if self._check_rate_limit(user_id):
            self._log_event(
                user_id=user_id,
                event_type="rate_limit_exceeded",
                severity="block",
                snippet=text[:100],
            )
            return SecurityResult(blocked=True, reason="rate_limit_exceeded")

        # 1. Length limit — on raw text before sanitization
        if len(text) > self._policy.max_input_length:
            self._log_event(
                user_id=user_id,
                event_type="length_exceeded",
                severity="block",
                snippet=text[:100],
            )
            return SecurityResult(blocked=True, reason="length_exceeded")

        # 2. HTML sanitization — strip dangerous tags, run subsequent checks on clean text
        working_text = text
        sanitized: Optional[str] = None
        if self._policy.html_sanitization_enabled:
            result, dangerous = self._sanitize_html(text)
            if result is not None:
                sanitized = result
                working_text = result
            if dangerous:
                self._log_event(
                    user_id=user_id,
                    event_type="html_injection",
                    severity="sanitize",
                    snippet=text[:100],
                )

        # 3. Regex injection scan — on working_text (post-sanitization)
        for pattern in self._injection_patterns:
            if pattern.search(working_text):
                self._log_event(
                    user_id=user_id,
                    event_type="prompt_injection",
                    severity="block",
                    snippet=working_text[:100],
                )
                return SecurityResult(blocked=True, reason="prompt_injection")

        # 4. LLM fallback — catches subtle / obfuscated injections that bypass regex
        if self._chat_provider is not None:
            if self._llm_injection_check(working_text):
                self._log_event(
                    user_id=user_id,
                    event_type="prompt_injection",
                    severity="block",
                    snippet=working_text[:100],
                )
                return SecurityResult(blocked=True, reason="prompt_injection")

        # 5. PII detection — soft flag only, never block
        flags: list[str] = []
        if self._has_pii(working_text):
            flags.append("pii_detected")
            self._log_event(
                user_id=user_id,
                event_type="pii_detected",
                severity="flag",
                snippet=working_text[:100],
            )

        return SecurityResult(blocked=False, flags=flags, sanitized_input=sanitized)

    def check_output(self, text: str, user_id: str = "") -> str:
        """Scrub secrets from LLM output and enforce max length.

        Returns the (possibly redacted / truncated) text.
        """
        if not self._policy.enabled:
            return text

        # Secret scrubbing
        redacted = text
        found_leak = False
        for pattern, replacement in _SECRET_PATTERNS:
            new_text = pattern.sub(replacement, redacted)
            if new_text != redacted:
                found_leak = True
                redacted = new_text

        if found_leak:
            self._log_event(
                user_id=user_id,
                event_type="secret_leak",
                severity="redact",
                snippet=text[:100],
            )

        # Max output length enforcement
        if len(redacted) > self._policy.max_output_length:
            redacted = redacted[:self._policy.max_output_length] + " [truncated]"
            self._log_event(
                user_id=user_id,
                event_type="output_truncated",
                severity="info",
                snippet=text[:100],
            )

        return redacted

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_rate_limit(self, user_id: str) -> bool:
        """Sliding window rate limiter. Returns True if the request should be blocked."""
        if not self._policy.rate_limit_enabled:
            return False
        # Unauthenticated users share a global bucket
        key = "__global__" if not user_id else user_id
        now = time.monotonic()
        window = self._rate_limit_window[key]
        cutoff = now - 60.0
        # Evict timestamps older than 60 seconds
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= self._policy.rate_limit_per_minute:
            return True
        window.append(now)
        return False

    def _sanitize_html(self, text: str) -> tuple[Optional[str], bool]:
        """Strip dangerous HTML constructs.

        Returns (cleaned_text_or_None, dangerous_content_removed).

        Entity decoding runs first to catch obfuscated payloads (&#60;script&#62;),
        and the decoded text is returned even when no dangerous pattern matched —
        the downstream injection scan must see decoded text or entity-encoded
        attacks slip past the regexes.

        The second element distinguishes "we actually removed an attack" from
        "we merely decoded entities or collapsed whitespace". Only the former is
        worth a security_events row; conflating them floods the audit table with
        html_injection events for benign input like a trailing space.
        """
        decoded = _html_module.unescape(text)
        cleaned = decoded
        dangerous = False
        for pattern, replacement in _HTML_SANITIZE_PATTERNS:
            substituted = pattern.sub(replacement, cleaned)
            if substituted != cleaned:
                dangerous = True
                cleaned = substituted
        # Collapse whitespace gaps left by removed tags
        cleaned = re.sub(r" {2,}", " ", cleaned).strip()
        return (cleaned if cleaned != text else None), dangerous

    def _has_pii(self, text: str) -> bool:
        return bool(
            _EMAIL_RE.search(text)
            or _PHONE_RE.search(text)
            or _SSN_RE.search(text)
            or _CARD_RE.search(text)
        )

    def _llm_injection_check(self, text: str) -> bool:
        """LLM-based classifier for subtle injection attempts.

        Fails open — if the classifier call throws for any reason,
        the input is allowed through (never block on uncertainty).
        """
        try:
            messages = [
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": text[:500]},
            ]
            response = self._chat_provider.chat(messages=messages)
            cleaned = re.sub(r"```(?:json)?", "", response).strip()
            match = re.search(r"\{[^{}]*\}", cleaned)
            if match:
                result = json.loads(match.group())
                inject = bool(result.get("inject", False))
                confidence = str(result.get("confidence", "low")).lower()
                # Only block on high confidence — medium has too many false positives
                # on benign queries that contain words like "check", "summarize", etc.
                return inject and confidence == "high"
        except Exception as exc:
            log.warning("SecurityAgent: LLM injection check failed: %s", exc)
        return False

    def _log_event(
        self,
        user_id: str,
        event_type: str,
        severity: str,
        snippet: Optional[str],
    ) -> None:
        """Write one row to security_events. Swallows exceptions so a DB
        failure never disrupts the request path."""
        if not self._registry:
            return
        event_id = str(uuid.uuid4())
        safe_snippet = (snippet or "")[:100]
        try:
            self._registry.log_security_event(event_id, user_id, event_type, severity, safe_snippet)
        except Exception as exc:
            log.warning("SecurityAgent: failed to log event: %s", exc)
