"""Gmail email fetching and AI triage service."""
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, List, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.providers.ollama_chat import OllamaChatProvider

AccountType = Literal["personal", "work"]

GMAIL_READONLY_SCOPE = ["https://www.googleapis.com/auth/gmail.readonly"]


class EmailMessage(BaseModel):
    message_id: str
    sender: str
    subject: str
    date: str
    snippet: str


class TriagedEmail(BaseModel):
    email: EmailMessage
    category: Literal["action", "fyi", "ignore"]
    reason: str


class EmailService:
    def __init__(self, credentials_dir: Path, account_type: AccountType) -> None:
        self._credentials_dir = credentials_dir
        self._account_type = account_type
        self._token_path = credentials_dir / f"{account_type}_token.json"
        self._client_secrets_path = credentials_dir / "credentials.json"

    def _authenticate(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        if not self._client_secrets_path.exists():
            raise FileNotFoundError(
                f"Gmail credentials not found at {self._client_secrets_path}.\n"
                "Download OAuth 2.0 credentials (Desktop app) from Google Cloud Console\n"
                "and save as data/credentials/credentials.json"
            )

        creds = None
        if self._token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_path), GMAIL_READONLY_SCOPE)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self._client_secrets_path), GMAIL_READONLY_SCOPE
                )
                creds = flow.run_local_server(port=0)
            self._token_path.write_text(creds.to_json())

        return build("gmail", "v1", credentials=creds)

    def fetch_recent(self, max_results: int = 20) -> List[EmailMessage]:
        service = self._authenticate()
        response = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX", "CATEGORY_PERSONAL"], maxResults=max_results)
            .execute()
        )

        raw_messages = response.get("messages", [])
        emails: List[EmailMessage] = []

        for msg_ref in raw_messages:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender = _clean_sender(headers.get("From", ""))
            subject = headers.get("Subject", "(no subject)")
            date = headers.get("Date", "")
            snippet = msg.get("snippet", "")

            emails.append(EmailMessage(
                message_id=msg_ref["id"],
                sender=sender,
                subject=subject,
                date=date,
                snippet=snippet,
            ))

        return emails

    def triage(self, emails: List[EmailMessage], chat_provider: "OllamaChatProvider") -> List[TriagedEmail]:
        if not emails:
            return []

        email_lines = "\n".join(
            f"{i+1}. From: {e.sender} | Subject: {e.subject} | Snippet: {e.snippet[:120]}"
            for i, e in enumerate(emails)
        )

        prompt = f"""You are an email triage assistant. Classify each email below as one of:
- ACTION: requires a reply, decision, or task from the user
- FYI: informational, good to know but no action needed
- IGNORE: promotional, automated notification, or irrelevant

For each email output exactly one line in this format:
<number>|<ACTION|FYI|IGNORE>|<one sentence reason>

Emails:
{email_lines}

Classifications:"""

        try:
            raw = chat_provider.generate(prompt)
        except Exception as e:
            return [TriagedEmail(email=em, category="fyi", reason=f"Triage unavailable: {e}") for em in emails]

        return _parse_triage_response(raw, emails)


def _clean_sender(raw: str) -> str:
    match = re.search(r"<([^>]+)>", raw)
    if match:
        return match.group(1)
    return raw.strip()


def _parse_triage_response(raw: str, emails: List[EmailMessage]) -> List[TriagedEmail]:
    results: List[TriagedEmail] = []
    lines = [line.strip() for line in raw.strip().splitlines() if "|" in line]

    parsed: dict[int, tuple[str, str]] = {}
    for line in lines:
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        try:
            idx = int(re.sub(r"\D", "", parts[0]))
            label = parts[1].strip().upper()
            reason = parts[2].strip()
            parsed[idx] = (label, reason)
        except ValueError:
            continue

    for i, email in enumerate(emails):
        label, reason = parsed.get(i + 1, ("FYI", "Could not parse classification"))
        if label == "ACTION":
            category = "action"
        elif label == "IGNORE":
            category = "ignore"
        else:
            category = "fyi"
        results.append(TriagedEmail(email=email, category=category, reason=reason))

    return results
