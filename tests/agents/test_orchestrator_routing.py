"""Deterministic email-routing guard in the orchestrator."""
from app.agents.orchestrator import OrchestratorAgent


class _Provider:
    """Fake ChatProvider returning a canned plan JSON, or raising."""
    def __init__(self, response=None, raise_exc=False):
        self.response = response
        self.raise_exc = raise_exc

    def chat(self, messages):
        if self.raise_exc:
            raise RuntimeError("planning failed")
        return self.response


_CONVERSATIONAL_ONLY = '{"steps": [{"agent": "conversational", "task": "reply"}]}'
_EMAIL_Q = "pull my emails and tell me what i missed today"


def _agents(plan):
    return [s.agent for s in plan.steps]


def test_email_request_injects_email_agent_when_llm_drops_it():
    plan = OrchestratorAgent(_Provider(_CONVERSATIONAL_ONLY)).plan(_EMAIL_Q, [])
    assert "email_agent" in _agents(plan)
    assert plan.steps[-1].agent == "conversational"        # synthesis stays last
    # conversational depends on the injected email step
    email_id = next(s.id for s in plan.steps if s.agent == "email_agent")
    assert email_id in plan.steps[-1].depends_on


def test_guard_applies_even_when_planning_fails():
    plan = OrchestratorAgent(_Provider(raise_exc=True)).plan(_EMAIL_Q, [])
    assert "email_agent" in _agents(plan)


def test_non_email_request_untouched():
    plan = OrchestratorAgent(_Provider(_CONVERSATIONAL_ONLY)).plan("what's the weather in Paris", [])
    assert "email_agent" not in _agents(plan)


def test_no_duplicate_when_llm_already_routed_email():
    resp = '{"steps": [{"agent": "email_agent", "task": "check inbox"}, {"agent": "conversational", "task": "present"}]}'
    plan = OrchestratorAgent(_Provider(resp)).plan(_EMAIL_Q, [])
    assert _agents(plan).count("email_agent") == 1


def test_matcher_is_tight_no_false_positive_on_bare_mail():
    # "mail" alone (e.g. mailing list) should not trigger the email guard.
    plan = OrchestratorAgent(_Provider(_CONVERSATIONAL_ONLY)).plan("add mailing the package to my todos", [])
    assert "email_agent" not in _agents(plan)
