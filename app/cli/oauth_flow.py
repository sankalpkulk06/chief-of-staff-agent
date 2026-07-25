"""Shared localhost loopback OAuth flow for CLI Google connects (Gmail, Calendar).

Runs the Google consent handshake entirely from the CLI: opens the consent URL in a
browser, captures the redirect on a one-shot localhost HTTP server, exchanges the code,
and persists the per-user token — no web server required.

The redirect URI (``http://localhost:8765/callback``) must be registered as an Authorized
redirect URI on the Google "web" OAuth client whose JSON lives in
``GOOGLE_CLIENT_SECRETS_JSON``. Web clients allow ``http://localhost`` redirects, so no
"desktop" client type is needed.
"""
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from rich.console import Console

OAUTH_LOOPBACK_PORT = 8765

_SUCCESS_HTML = (
    b"<html><body style='font-family:sans-serif;text-align:center;padding-top:4rem'>"
    b"<h2>&#10003; Connected</h2><p>You can close this tab and return to the terminal.</p>"
    b"</body></html>"
)
_ERROR_HTML = (
    b"<html><body style='font-family:sans-serif;text-align:center;padding-top:4rem'>"
    b"<h2>&#10007; Connection failed</h2><p>Return to the terminal for details.</p>"
    b"</body></html>"
)


class _CallbackServer(HTTPServer):
    """One-shot server that captures the OAuth redirect on /callback."""

    code: Optional[str] = None
    error: Optional[str] = None
    state_ok: bool = False
    expected_state: str = ""


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            # Ignore favicon / stray requests without ending the wait loop.
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        server: _CallbackServer = self.server  # type: ignore[assignment]
        server.error = (params.get("error") or [None])[0]
        state = (params.get("state") or [""])[0]
        server.state_ok = bool(state) and secrets.compare_digest(state, server.expected_state)
        server.code = (params.get("code") or [None])[0]

        ok = server.code and server.state_ok and not server.error
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML if ok else _ERROR_HTML)

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        pass


def run_loopback_oauth(
    *,
    service: Any,
    account_type: str,
    registry: Any,
    user_id: str,
    console: Console,
    label: str,
    port: int = OAUTH_LOOPBACK_PORT,
    timeout: int = 180,
) -> bool:
    """Drive the loopback consent flow and persist the token. Returns True on success.

    ``service`` is any object exposing ``get_oauth_url(redirect_uri, state)`` and
    ``exchange_code(code, redirect_uri, state)`` (both EmailService and CalendarService do).
    """
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(24)
    auth_url = service.get_oauth_url(redirect_uri, state)

    try:
        server = _CallbackServer(("localhost", port), _CallbackHandler)
    except OSError as exc:
        console.print(
            f"[red]Could not start local server on port {port}:[/red] {exc}\n"
            f"[dim]Something may be using it. Retry with a different --port "
            f"(and register that redirect URI in Google Cloud Console).[/dim]"
        )
        return False

    server.expected_state = state
    server.timeout = timeout

    console.print(f"\n[bold]Connecting {label}…[/bold]")
    console.print("Opening your browser to grant access. If it doesn't open, visit:\n")
    console.print(f"[cyan]{auth_url}[/cyan]\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass  # URL already printed as fallback

    try:
        # Loop so stray requests (e.g. /favicon.ico) don't end the wait prematurely.
        while server.code is None and server.error is None:
            server.handle_request()  # blocks up to server.timeout
            if server.code is None and server.error is None:
                console.print("[red]Timed out waiting for authorization.[/red]")
                return False
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return False
    finally:
        server.server_close()

    if server.error:
        console.print(f"[red]Authorization denied:[/red] {server.error}")
        return False
    if not server.state_ok:
        console.print("[red]State mismatch — possible CSRF. Aborted.[/red]")
        return False

    try:
        token_json = service.exchange_code(server.code, redirect_uri, state)
    except Exception as exc:
        console.print(
            f"[red]Failed to exchange the authorization code:[/red] {exc}\n"
            f"[dim]If this is a redirect_uri mismatch, ensure "
            f"'{redirect_uri}' is an Authorized redirect URI on your Google OAuth "
            f"client (Google Cloud Console → Credentials).[/dim]"
        )
        return False

    registry.upsert_email_token(user_id, token_json, account_type)
    return True
