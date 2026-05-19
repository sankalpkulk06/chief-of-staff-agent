from datetime import datetime

from app.api.todos import _parse_due_at


def test_parse_due_at_accepts_browser_iso_utc_suffix():
    due_at = _parse_due_at("2026-05-19T17:00:00.000Z")
    expected = datetime.fromisoformat("2026-05-19T17:00:00.000+00:00")

    assert due_at == expected
    assert due_at.tzinfo is not None
