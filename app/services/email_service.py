"""Gmail email fetching and AI triage service."""
import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.providers.factory import ChatProvider

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


class EmailNotConnectedError(Exception):
    """Raised when no OAuth token exists for the user."""


class EmailService:
    """
    Gmail service backed by per-user tokens stored in Supabase.

    client_secrets: the parsed contents of the Google OAuth credentials.json
                    (type: web) — loaded from GOOGLE_CLIENT_SECRETS_JSON env var.
    """

    def __init__(self, client_secrets: Dict[str, Any], account_type: AccountType = "personal") -> None:
        self._client_secrets = client_secrets
        self._account_type = account_type

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def build_service(self, token_json: Dict) -> Tuple[Any, Optional[Dict]]:
        """
        Build a Gmail API client from a stored token dict.

        Returns (service, refreshed_token_or_None).
        refreshed_token_or_None is set when the token was refreshed and
        the caller should persist it back to the DB.
        """
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_info(token_json, GMAIL_READONLY_SCOPE)

        refreshed: Optional[Dict] = None
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                refreshed = json.loads(creds.to_json())
            else:
                raise EmailNotConnectedError("Gmail token is invalid. Please reconnect your account.")

        return build("gmail", "v1", credentials=creds), refreshed

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Generate a Google OAuth consent URL for the web flow."""
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            self._client_secrets,
            scopes=GMAIL_READONLY_SCOPE,
            redirect_uri=redirect_uri,
        )
        # Disable PKCE — we use a client secret (server-side flow), so PKCE
        # is not needed and causes "Missing code verifier" on exchange.
        flow.code_verifier = None
        flow.oauth2session.code_challenge_method = None
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
        )
        return auth_url

    def exchange_code(self, code: str, redirect_uri: str, state: str) -> Dict:
        """Exchange an OAuth authorization code for a token dict."""
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            self._client_secrets,
            scopes=GMAIL_READONLY_SCOPE,
            redirect_uri=redirect_uri,
            state=state,
        )
        flow.code_verifier = None
        flow.oauth2session.code_challenge_method = None
        flow.fetch_token(code=code)
        return json.loads(flow.credentials.to_json())

    # ------------------------------------------------------------------
    # Fetch & triage
    # ------------------------------------------------------------------

    def fetch_recent(self, token_json: Dict, max_results: int = 20) -> Tuple[List[EmailMessage], Optional[Dict]]:
        """
        Fetch recent emails using a stored token.

        Returns (emails, refreshed_token_or_None).
        """
        service, refreshed = self.build_service(token_json)

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

        return emails, refreshed

    def triage(self, emails: List[EmailMessage], chat_provider: "ChatProvider") -> List[TriagedEmail]:
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
