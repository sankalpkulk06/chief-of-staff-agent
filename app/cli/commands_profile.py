"""CLI profile: `sage profile show` (summary) and `sage profile delete` (account wipe)."""
import getpass

import typer
from rich.console import Console

from app.cli.session import auth_enabled, clear_session, resolve_cli_user
from app.config import get_settings
from app.core.profile_service import build_profile, delete_profile_and_data
from app.storage.factory import create_registry

console = Console()


def profile_show_command() -> None:
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    user = resolve_cli_user(settings, registry, console)
    try:
        p = build_profile(registry, user["user_id"], user["username"])
    finally:
        registry.close()

    streak = f"{p['longest_streak']} days" + (f" ({p['longest_streak_habit']})" if p["longest_streak_habit"] else "")
    console.print()
    console.print(f"[bold cyan]{p['username']}[/bold cyan]  [dim]· joined {p['joined'] or '—'}[/dim]")
    console.print(f"  Sessions        [bold]{p['total_sessions']}[/bold]  [dim]· {p['days_active']} days active[/dim]")
    console.print(f"  Facts           [bold]{p['facts_personal'] + p['facts_work']}[/bold]  [dim]· {p['facts_personal']} personal · {p['facts_work']} work[/dim]")
    console.print(f"  Longest streak  [bold]{streak or '—'}[/bold]")
    console.print(f"  Knowledge base  [bold]{p['total_docs']}[/bold] docs  [dim]· {p['total_chunks']} chunks[/dim]")
    console.print()


def profile_delete_command() -> None:
    from app.cli.commands_chat import _prompt_yes_no

    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    user = resolve_cli_user(settings, registry, console)
    user_id, username = user["user_id"], user["username"]

    console.print(f"\n[bold red]This permanently deletes the account “{username}” and ALL its data[/bold red] "
                  "(sessions, facts, habits, todos, documents). This cannot be undone.")

    try:
        # Identity re-check: password in auth mode, typed-username in local mode.
        if auth_enabled(settings):
            pw = getpass.getpass("Re-enter your password to confirm: ")
            verified = registry.verify_password(username, pw)
            if not verified or verified["user_id"] != user_id:
                console.print("[red]Incorrect password. Aborted.[/red]")
                raise typer.Exit(code=1)
        else:
            typed = input(f'Type your username "{username}" to confirm: ').strip()
            if typed != username:
                console.print("[red]Username did not match. Aborted.[/red]")
                raise typer.Exit(code=1)

        if not _prompt_yes_no(console, "Delete this account and all its data?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

        deleted = delete_profile_and_data(registry, user_id)
    finally:
        registry.close()

    clear_session(paths)  # account is gone → log out locally
    summary = ", ".join(f"{v} {k}" for k, v in deleted.items() if v) or "no data"
    console.print(f"[green]✓ Account deleted.[/green] Removed: {summary}. You have been logged out.")
