"""EmailAgent — fetches and triages the user's Gmail inbox."""
import logging
from typing import Any, List, Optional

from app.agents.base import AgentResult
from app.providers.factory import ChatProvider
from app.services.email_service import EmailNotConnectedError, EmailService

log = logging.getLogger(__name__)


class EmailAgent:
    """Fetches and triages Gmail. Reads/writes the OAuth token from the registry."""

    def __init__(
        self,
        email_service: EmailService,
        chat_provider: ChatProvider,
        registry: Any,
        assistant_name: str = "Sage",
    ):
        self._email = email_service
        self._provider = chat_provider
        self._registry = registry
        self._assistant_name = assistant_name

    def execute(
        self,
        task: str,
        original_question: str,
        history: List[dict[str, Any]],
        previous_results: Optional[List[AgentResult]] = None,
        user_id: Optional[str] = None,
        response_style: Optional[str] = None,
    ) -> AgentResult:
        if not user_id:
            return AgentResult(
                agent="email_agent", task=task,
                output="I need to know who you are to fetch your email.",
                success=False, error="no_user_id",
            )

        token_json = self._registry.get_email_token(user_id, "personal")
        if not token_json:
            return AgentResult(
                agent="email_agent", task=task,
                output=(
                    "Your Gmail account isn't connected yet. "
                    "Go to **Settings → Connect Gmail** to link your account."
                ),
                success=False, error="not_connected",
            )

        try:
            emails, refreshed_token = self._email.fetch_recent(token_json)
        except EmailNotConnectedError as exc:
            return AgentResult(
                agent="email_agent", task=task,
                output=(
                    "Your Gmail token has expired or been revoked. "
                    "Go to **Settings → Connect Gmail** to reconnect."
                ),
                success=False, error=str(exc),
            )
        except Exception as exc:
            log.error("EmailAgent: fetch failed: %s", exc, exc_info=True)
            return AgentResult(
                agent="email_agent", task=task,
                output=f"Failed to fetch emails: {exc}",
                success=False, error=str(exc),
            )

        # Persist refreshed token if Google rotated it
        if refreshed_token:
            try:
                self._registry.upsert_email_token(user_id, refreshed_token)
            except Exception as exc:
                log.warning("EmailAgent: failed to persist refreshed token: %s", exc)

        if not emails:
            return AgentResult(
                agent="email_agent", task=task,
                output="Your inbox is empty — nothing to report.",
                success=True,
            )

        try:
            triaged = self._email.triage(emails, self._provider)
        except Exception as exc:
            return AgentResult(
                agent="email_agent", task=task,
                output=f"Fetched {len(emails)} emails but triage failed: {exc}",
                success=False, error=str(exc),
            )

        action_items = [t for t in triaged if t.category == "action"]
        fyi_items    = [t for t in triaged if t.category == "fyi"]

        lines = [f"Checked {len(emails)} emails. Here's what needs your attention:\n"]
        if action_items:
            lines.append(f"**ACTION NEEDED ({len(action_items)})**")
            for i, item in enumerate(action_items, 1):
                lines.append(f"{i}. **{item.email.sender}** — {item.email.subject}\n   → {item.reason}")
            lines.append("")
        else:
            lines.append("No action needed.\n")

        if fyi_items:
            lines.append(f"**FYI ({len(fyi_items)})**")
            for i, item in enumerate(fyi_items, 1):
                lines.append(f"{i}. **{item.email.sender}** — {item.email.subject}\n   → {item.reason}")

        return AgentResult(
            agent="email_agent",
            task=task,
            output="\n".join(lines),
            success=True,
        )
