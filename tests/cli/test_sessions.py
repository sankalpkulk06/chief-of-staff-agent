"""Tests for CLI session management helpers + registry orphan cleanup."""
import tempfile
from pathlib import Path

from app.cli.commands_sessions import _resolve, _user_sessions
from app.storage.sqlite_registry import SQLiteRegistry


def _reg():
    return SQLiteRegistry(Path(tempfile.mkdtemp()) / "t.db")


def _sessions(ids):
    return [{"session_id": i, "title": "", "updated_at": ""} for i in ids]


def test_resolve_full_id_and_unique_prefix():
    s = _sessions(["abc12345-aaaa", "def67890-bbbb"])
    assert _resolve("abc12345-aaaa", s)["session_id"] == "abc12345-aaaa"   # full id
    assert _resolve("abc1", s)["session_id"] == "abc12345-aaaa"            # unique prefix
    assert _resolve("zzz", s) is None                                       # no match


def test_resolve_ambiguous_prefix_returns_none():
    s = _sessions(["abc111", "abc222"])
    assert _resolve("abc", s) is None            # ambiguous → refuse


def test_delete_session_clears_named_alias():
    reg = _reg()
    u = reg.create_user("alice", "pw12345")["user_id"]
    sid = reg.get_or_create_named_session(f"cli:{u}:default", user_id=u)
    reg.append_turn(session_id=sid, turn_id="t0", role="user", content="hi", turn_index=0)

    reg.delete_session(sid)

    # Session, its turns, AND the named alias are gone (no dangling alias).
    assert reg.list_sessions(limit=10, user_id=u) == []
    assert reg.get_session_turns(sid) == []
    new_sid = reg.get_or_create_named_session(f"cli:{u}:default", user_id=u)
    assert new_sid != sid                        # alias was cleared → fresh id minted


def test_ownership_other_users_session_not_resolvable():
    reg = _reg()
    a = reg.create_user("alice", "pw12345")["user_id"]
    b = reg.create_user("bob", "pw12345")["user_id"]
    b_sid = reg.get_or_create_named_session(f"cli:{b}:default", user_id=b)

    # Alice's candidate set excludes Bob's session → she can't resolve/mutate it.
    assert _resolve(b_sid, _user_sessions(reg, a)) is None
