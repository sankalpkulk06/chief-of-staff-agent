"""CLI auth commands: login, logout, whoami."""
from rich.console import Console

from app.cli.session import auth_enabled, clear_session, load_session, resolve_cli_user
from app.config import get_settings
from app.storage.factory import create_registry

console = Console()


def _registry(settings):
    paths = settings.resolve_paths()
    return create_registry(settings.database_url, paths.sqlite_db_path)


def login_command() -> None:
    """Log in (or sign up) and persist the session for future commands."""
    settings = get_settings()
    registry = _registry(settings)
    try:
        user = resolve_cli_user(settings, registry, console, force_prompt=auth_enabled(settings))
    finally:
        registry.close()

    if auth_enabled(settings):
        console.print(f"[green]✓ Logged in as {user['username']}.[/green]")
    else:
        console.print(f'[green]✓ Auth disabled — using local user "{user["username"]}".[/green]')


def logout_command() -> None:
    """Clear the persisted CLI session."""
    settings = get_settings()
    paths = settings.resolve_paths()
    if clear_session(paths):
        console.print("[green]✓ Logged out.[/green]")
    else:
        console.print("[dim]No active session.[/dim]")


def whoami_command() -> None:
    """Show the currently logged-in CLI user without prompting."""
    settings = get_settings()
    paths = settings.resolve_paths()
    session = load_session(paths)
    if session:
        console.print(f"{session['username']}   [dim](user_id: {session['user_id']})[/dim]")
    else:
        console.print("[dim]Not logged in. Run `sage login`.[/dim]")
