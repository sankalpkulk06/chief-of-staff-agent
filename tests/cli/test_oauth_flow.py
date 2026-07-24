"""Unit tests for the CLI loopback OAuth helper (no real Google round-trip)."""
import socket
import threading
import time
import urllib.request

from rich.console import Console

from app.cli import oauth_flow
from app.cli.oauth_flow import run_loopback_oauth


def _free_port() -> int:
    s = socket.socket()
    s.bind(("localhost", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _StubService:
    """Mimics EmailService/CalendarService's OAuth trio."""

    def __init__(self):
        self.captured = {}

    def get_oauth_url(self, redirect_uri, state):
        self.captured["state"] = state
        self.captured["redirect_uri"] = redirect_uri
        return "http://example.test/consent"

    def exchange_code(self, code, redirect_uri, state):
        self.captured["exchanged"] = (code, redirect_uri, state)
        return {"token": "AT", "refresh_token": "RT"}


class _StubRegistry:
    def __init__(self):
        self.tokens = {}

    def upsert_email_token(self, user_id, token_json, account_type):
        self.tokens[(user_id, account_type)] = token_json


def _drive_callback(monkeypatch, port, query_builder):
    """Patch webbrowser.open so that, once the server is listening, we fire the redirect."""
    def fake_open(url):
        def hit():
            time.sleep(0.05)
            try:
                urllib.request.urlopen(
                    f"http://localhost:{port}/callback?{query_builder()}", timeout=5
                ).read()
            except Exception:
                pass
        threading.Thread(target=hit, daemon=True).start()
    monkeypatch.setattr(oauth_flow.webbrowser, "open", fake_open)


def test_loopback_oauth_happy_path(monkeypatch):
    port = _free_port()
    service = _StubService()
    registry = _StubRegistry()

    _drive_callback(monkeypatch, port, lambda: f"code=CODE123&state={service.captured['state']}")

    ok = run_loopback_oauth(
        service=service, account_type="personal", registry=registry,
        user_id="u1", console=Console(), label="Gmail (personal)", port=port, timeout=5,
    )

    assert ok is True
    assert service.captured["exchanged"][0] == "CODE123"                 # exchange got the code
    assert registry.tokens[("u1", "personal")] == {"token": "AT", "refresh_token": "RT"}


def test_loopback_oauth_user_denied(monkeypatch):
    port = _free_port()
    service = _StubService()
    registry = _StubRegistry()

    _drive_callback(monkeypatch, port, lambda: "error=access_denied")

    ok = run_loopback_oauth(
        service=service, account_type="google_calendar", registry=registry,
        user_id="u1", console=Console(), label="Google Calendar", port=port, timeout=5,
    )

    assert ok is False
    assert "exchanged" not in service.captured        # never exchanged
    assert registry.tokens == {}                       # nothing stored


def test_loopback_oauth_state_mismatch(monkeypatch):
    port = _free_port()
    service = _StubService()
    registry = _StubRegistry()

    _drive_callback(monkeypatch, port, lambda: "code=CODE123&state=WRONG")

    ok = run_loopback_oauth(
        service=service, account_type="personal", registry=registry,
        user_id="u1", console=Console(), label="Gmail (personal)", port=port, timeout=5,
    )

    assert ok is False
    assert registry.tokens == {}
