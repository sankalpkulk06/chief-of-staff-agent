"""CLI commands to connect Google accounts (Gmail, Calendar) via loopback OAuth."""
import typer
from rich.console import Console

from app.cli.oauth_flow import run_loopback_oauth
from app.cli.session import resolve_cli_user
from app.config import get_settings
from app.config.settings import get_google_client_secrets
from app.services.calendar_service import CalendarService
from app.services.email_service import EmailService
from app.storage.factory import create_registry

console = Console()

# Mirrors the constant in app/api/calendar.py / calendar_plan_executor.py / planner_service.py.
CALENDAR_ACCOUNT_TYPE = "google_calendar"


def _bootstrap(require_secrets: bool = True):
    """Return (settings, registry, user_id, client_secrets), exiting with a message on error.

    ``client_secrets`` is None when ``require_secrets`` is False and none are configured —
    used by status/disconnect, which only touch the registry.
    """
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    user = resolve_cli_user(settings, registry, console)
    client_secrets = get_google_client_secrets(settings)
    if require_secrets and not client_secrets:
        console.print("[red]Google OAuth is not configured (set GOOGLE_CLIENT_SECRETS_JSON).[/red]")
        registry.close()
        raise typer.Exit(code=1)
    return settings, registry, user["user_id"], client_secrets


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

def email_connect_command(work: bool, port: int) -> None:
    settings, registry, user_id, client_secrets = _bootstrap()
    account_type = "work" if work else "personal"
    service = EmailService(client_secrets=client_secrets, account_type=account_type)
    try:
        ok = run_loopback_oauth(
            service=service,
            account_type=account_type,
            registry=registry,
            user_id=user_id,
            console=console,
            label=f"Gmail ({account_type})",
            port=port,
        )
    finally:
        registry.close()
    if ok:
        cmd = "email-work" if work else "email-personal"
        console.print(f"[green]✓ Gmail ({account_type}) connected.[/green] Try `sage {cmd}`.")
    else:
        raise typer.Exit(code=1)


def email_status_command(work: bool) -> None:
    settings, registry, user_id, _ = _bootstrap(require_secrets=False)
    account_type = "work" if work else "personal"
    try:
        connected = registry.has_email_token(user_id, account_type)
    finally:
        registry.close()
    state = "[green]connected[/green]" if connected else "[dim]not connected[/dim]"
    console.print(f"Gmail ({account_type}): {state}")


def email_disconnect_command(work: bool) -> None:
    settings, registry, user_id, _ = _bootstrap(require_secrets=False)
    account_type = "work" if work else "personal"
    try:
        registry.delete_email_token(user_id, account_type)
    finally:
        registry.close()
    console.print(f"[green]✓ Gmail ({account_type}) disconnected.[/green]")


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------

def calendar_connect_command(port: int) -> None:
    settings, registry, user_id, client_secrets = _bootstrap()
    service = CalendarService(client_secrets=client_secrets)
    try:
        ok = run_loopback_oauth(
            service=service,
            account_type=CALENDAR_ACCOUNT_TYPE,
            registry=registry,
            user_id=user_id,
            console=console,
            label="Google Calendar",
            port=port,
        )
    finally:
        registry.close()
    if ok:
        console.print("[green]✓ Google Calendar connected.[/green] Try planning your day in `sage chat`.")
    else:
        raise typer.Exit(code=1)


def calendar_status_command() -> None:
    settings, registry, user_id, _ = _bootstrap(require_secrets=False)
    try:
        connected = registry.has_email_token(user_id, CALENDAR_ACCOUNT_TYPE)
    finally:
        registry.close()
    state = "[green]connected[/green]" if connected else "[dim]not connected[/dim]"
    console.print(f"Google Calendar: {state}")


def calendar_disconnect_command() -> None:
    settings, registry, user_id, _ = _bootstrap(require_secrets=False)
    try:
        registry.delete_email_token(user_id, CALENDAR_ACCOUNT_TYPE)
    finally:
        registry.close()
    console.print("[green]✓ Google Calendar disconnected.[/green]")
