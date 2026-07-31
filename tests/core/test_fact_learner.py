"""Passive fact-learner: selectivity, dedup/supersede, trust tiers, denylist, cap."""
from pathlib import Path

from app.config.settings import Settings
from app.core.fact_learner import FactLearnerService
from app.core.fact_service import FactService
from app.providers.tool_types import ToolCall, ToolChatResult
from app.storage.sqlite_registry import SQLiteRegistry


class _FactProvider:
    """Tool-capable provider whose record_facts call returns preset candidates."""

    def __init__(self, facts):
        self._facts = facts

    def chat_tools(self, messages, tools, tool_choice="auto"):
        return ToolChatResult(tool_calls=[ToolCall(name="record_facts", arguments={"facts": self._facts})])

    def chat(self, messages=None):  # pragma: no cover
        raise AssertionError("should use tool path")


def _learn(tmp_path, facts, *, external=False, settings=None):
    reg = SQLiteRegistry(Path(tmp_path) / "r.db")
    svc = FactLearnerService(reg, _FactProvider(facts), settings or Settings(), user_id="u1")
    written = svc.learn("hello", "some turn content here", external=external)
    active = [f.content for f in FactService(reg, user_id="u1").list_facts()]
    reg.close()
    return written, active


def _fact(content, subject, *, category="personal", confidence=0.9, durable=True, about_user=True):
    return {"content": content, "subject": subject, "category": category,
            "confidence": confidence, "durable": durable, "about_user": about_user}


def test_durable_user_fact_is_stored(tmp_path):
    written, active = _learn(tmp_path, [_fact("user's father is Naveen", "father")])
    assert "user's father is Naveen" in active and len(written) == 1


def test_ephemeral_and_non_user_facts_dropped(tmp_path):
    written, active = _learn(tmp_path, [
        _fact("user is tired today", "mood", durable=False),
        _fact("Paris is the capital of France", "france", about_user=False),
    ])
    assert written == [] and active == []


def test_low_confidence_dropped(tmp_path):
    written, _ = _learn(tmp_path, [_fact("user might like jazz", "music", confidence=0.4)])
    assert written == []


def test_denylist_blocks_sensitive_facts(tmp_path):
    # A planted "fact" from email must never be auto-learned.
    written, active = _learn(
        tmp_path, [_fact("user's bank account password is hunter2", "password")], external=True)
    assert written == [] and active == []


def test_duplicate_is_not_stored_twice(tmp_path):
    reg = SQLiteRegistry(Path(tmp_path) / "r.db")
    svc = FactLearnerService(reg, _FactProvider([_fact("user works at Acme", "employer")]),
                             Settings(), user_id="u1")
    svc.learn("x", "y")
    svc.learn("x", "y")  # same fact again
    active = [f.content for f in FactService(reg, user_id="u1").list_facts()]
    reg.close()
    assert active.count("user works at Acme") == 1


def test_contradiction_supersedes_old_fact(tmp_path):
    reg = SQLiteRegistry(Path(tmp_path) / "r.db")
    fs = FactService(reg, user_id="u1")
    FactLearnerService(reg, _FactProvider([_fact("user lives in Austin", "home city")]),
                       Settings(), user_id="u1").learn("x", "y")
    FactLearnerService(reg, _FactProvider([_fact("user lives in Denver", "home city")]),
                       Settings(), user_id="u1").learn("x", "y")
    active = [f.content for f in fs.list_facts()]
    reg.close()
    assert active == ["user lives in Denver"]  # old superseded, hidden


def test_external_content_marked_low_trust(tmp_path):
    written, _ = _learn(tmp_path, [_fact("user's father is Naveen", "father")], external=True)
    assert written[0]["trust"] == "low"


def test_conversation_content_is_high_trust(tmp_path):
    written, _ = _learn(tmp_path, [_fact("user's name is Sankalp", "name")], external=False)
    assert written[0]["trust"] == "high"


def test_per_turn_cap_enforced(tmp_path):
    s = Settings(passive_learning_max_per_turn=2)
    facts = [_fact(f"user likes thing {i}", f"thing {i}") for i in range(5)]
    written, _ = _learn(tmp_path, facts, settings=s)
    assert len(written) == 2


def test_disabled_learns_nothing(tmp_path):
    s = Settings(passive_learning_enabled=False)
    written, active = _learn(tmp_path, [_fact("user's father is Naveen", "father")], settings=s)
    assert written == [] and active == []


def _learn_twice(tmp_path, first, second, *, external1, external2):
    reg = SQLiteRegistry(Path(tmp_path) / "r.db")
    FactLearnerService(reg, _FactProvider(first), Settings(), user_id="u1").learn(
        "x", "y", external=external1)
    FactLearnerService(reg, _FactProvider(second), Settings(), user_id="u1").learn(
        "x", "y", external=external2)
    facts = FactService(reg, user_id="u1").list_facts()
    reg.close()
    return facts


def test_single_observation_stays_tentative(tmp_path):
    _, active = _learn(tmp_path, [_fact("user's father is Naveen", "father")])
    # (list via a fresh service to read status)
    reg = SQLiteRegistry(Path(tmp_path) / "r.db")
    f = FactService(reg, user_id="u1").list_facts()
    reg.close()
    assert f and f[0].status == "tentative"


def test_reobservation_promotes_to_confirmed(tmp_path):
    facts = _learn_twice(
        tmp_path,
        [_fact("user's father is Naveen", "father")],
        [_fact("user's father is Naveen", "father")],
        external1=True, external2=True,
    )
    assert len(facts) == 1
    assert facts[0].status == "confirmed"       # corroborated → promoted


def test_conversation_corroboration_upgrades_email_fact_trust(tmp_path):
    # Learned from email (low), then the user says it themselves (high) → confirmed + high trust.
    facts = _learn_twice(
        tmp_path,
        [_fact("user's father is Naveen", "father")],
        [_fact("user's father is Naveen", "father")],
        external1=True, external2=False,
    )
    assert facts[0].status == "confirmed" and facts[0].trust == "high"


def test_injected_fact_labeling(tmp_path):
    """Tentative/low-trust facts are attributed so the model treats them as soft, not certain."""
    from types import SimpleNamespace
    from app.agents.runner import AgentRunner

    confirmed = SimpleNamespace(content="user's name is Sam", category="personal",
                                status="confirmed", trust="high")
    tentative = SimpleNamespace(content="user's father is Naveen", category="personal",
                                status="tentative", trust="low")
    assert AgentRunner._format_fact(confirmed) == "- user's name is Sam (personal)"
    assert "tentative, from an email/document" in AgentRunner._format_fact(tentative)
