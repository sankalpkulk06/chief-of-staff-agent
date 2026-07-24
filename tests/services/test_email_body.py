"""Tests for Gmail body extraction (plain preferred, HTML fallback)."""
import base64

from app.services.email_service import _extract_body


def _enc(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def test_prefers_plain_text_part():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _enc("Call me at 555 about the resume.")}},
            {"mimeType": "text/html", "body": {"data": _enc("<p>ignored</p>")}},
        ],
    }
    assert _extract_body(payload) == "Call me at 555 about the resume."


def test_falls_back_to_stripped_html():
    payload = {
        "mimeType": "text/html",
        "body": {"data": _enc("<html><body><h1>Statement</h1><p>Balance <b>$500</b>.</p></body></html>")},
    }
    out = _extract_body(payload)
    assert "<" not in out and ">" not in out
    assert "Statement" in out and "$500" in out


def test_empty_payload_returns_empty_string():
    assert _extract_body({"mimeType": "text/plain", "body": {}}) == ""
