"""EmailAgent — fetches and triages the user's Gmail inbox."""
import logging
from typing import Any, List, Optional

from app.agents.base import AgentResult
from app.providers.factory import ChatProvider

log = logging.getLogger(__name__)


class EmailAgent:
    """Fetches and triages Gmail. Instantiated only when email service is available."""

    def __init__(self, email_service: Any, chat_provider: ChatProvider, assistant_name: str = "Sage"):
        self._email = email_service
        self._provider = chat_provider
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
        try:
            emails = self._email.fetch_recent()
        except FileNotFoundError:
            return AgentResult(
                agent="email_agent",
                task=task,
                output=(
                    "Gmail credentials not found. Run `sage email-personal` from the CLI "
                    "first to authorise access."
                ),
                success=False,
                error="credentials_not_found",
            )
        except Exception as exc:
            return AgentResult(
                agent="email_agent",
                task=task,
                output=f"Failed to fetch emails: {exc}",
                success=False,
                error=str(exc),
            )

        if not emails:
            return AgentResult(
                agent="email_agent",
                task=task,
                output="Your inbox is empty — nothing to report.",
                success=True,
            )

        try:
            triaged = self._email.triage(emails, self._provider)
        except Exception as exc:
            return AgentResult(
                agent="email_agent",
                task=task,
                output=f"Fetched {len(emails)} emails but triage failed: {exc}",
                success=False,
                error=str(exc),
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
