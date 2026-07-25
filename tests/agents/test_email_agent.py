"""Tests for EmailAgent instruction-aware synthesis + fallback."""
from app.agents.email_agent import EmailAgent
from app.services.email_service import EmailMessage, TriagedEmail


def _email(sender, subject, snippet="..."):
    return EmailMessage(message_id="m", sender=sender, subject=subject, date="today", snippet=snippet)


class _FakeEmailService:
    def __init__(self, emails, triaged):
        self._emails = emails
        self._triaged = triaged

    def fetch_recent(self, token_json, max_results=20):
        return self._emails, None

    def triage(self, emails, provider):
        return self._triaged


class _Provider:
    def __init__(self, reply=None, raise_exc=False):
        self.reply = reply
        self.raise_exc = raise_exc
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.raise_exc:
            raise RuntimeError("llm down")
        return self.reply


class _Registry:
    def get_email_token(self, user_id, account_type="personal"):
        return {"token": "t", "refresh_token": "r"}

    def upsert_email_token(self, *a, **k):
        pass


def _agent(provider, emails=None, triaged=None):
    emails = emails or [_email("naveen@x.com", "Fwd: resume", "Call him at 555")]
    triaged = triaged or [TriagedEmail(email=emails[0], category="action", reason="Wants a call")]
    return EmailAgent(_FakeEmailService(emails, triaged), provider, _Registry())


def test_instruction_aware_answer_uses_llm():
    provider = _Provider(reply="Naveen forwarded your resume and wants a call at 555.")
    result = _agent(provider).execute(
        task="fetch and triage inbox",
        original_question="summarize the action items in my inbox",
        history=[], user_id="u1",
    )
    assert result.success is True
    assert result.output == "Naveen forwarded your resume and wants a call at 555."
    # The user's actual request is passed into the synthesis prompt.
    assert "summarize the action items" in provider.prompts[-1]
    # And the email content is provided as context.
    assert "naveen@x.com" in provider.prompts[-1]


def test_falls_back_to_canned_list_when_llm_fails():
    provider = _Provider(raise_exc=True)
    result = _agent(provider).execute(
        task="fetch and triage inbox",
        original_question="summarize my inbox",
        history=[], user_id="u1",
    )
    assert result.success is True
    assert "ACTION NEEDED" in result.output          # deterministic fallback
    assert "naveen@x.com" in result.output


def test_not_connected_returns_helpful_message():
    class _NoToken(_Registry):
        def get_email_token(self, user_id, account_type="personal"):
            return None

    agent = EmailAgent(_FakeEmailService([], []), _Provider(), _NoToken())
    result = agent.execute(task="t", original_question="check email", history=[], user_id="u1")
    assert result.success is False
    assert result.error == "not_connected"
