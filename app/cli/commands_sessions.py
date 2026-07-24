"""CLI session management: list / rename / delete / resume chat sessions."""
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console

from app.cli.session import resolve_cli_user
from app.config import get_settings
from app.storage.factory import create_registry

console = Console()


def _bootstrap():
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    user = resolve_cli_user(settings, registry, console)
    return settings, registry, user["user_id"]


def _user_sessions(registry: Any, user_id: str) -> List[Dict]:
    return registry.list_sessions(limit=1000, user_id=user_id)


def _resolve(id_or_prefix: str, sessions: List[Dict]) -> Optional[Dict]:
    """Match a full session id or a unique prefix. Prints an error and returns None on
    no-match or ambiguous match."""
    key = (id_or_prefix or "").strip()
    if not key:
        console.print("[red]Provide a session id (or a unique prefix from `sage sessions list`).[/red]")
        return None
    exact = [s for s in sessions if s["session_id"] == key]
    if exact:
        return exact[0]
    matches = [s for s in sessions if s["session_id"].startswith(key)]
    if not matches:
        console.print(f"[red]No session of yours matches '{key}'.[/red] Run `sage sessions list`.")
        return None
    if len(matches) > 1:
        console.print(f"[red]'{key}' is ambiguous[/red] — matches {len(matches)} sessions. Use more characters.")
        return None
    return matches[0]


def sessions_list_command() -> None:
    settings, registry, user_id = _bootstrap()
    try:
        sessions = _user_sessions(registry, user_id)
        default_alias = registry.get_or_create_named_session(f"cli:{user_id}:default", user_id=user_id)
        rows = [
            (s, len(registry.get_session_turns(s["session_id"])))
            for s in sessions
        ]
    finally:
        registry.close()

    if not sessions:
        console.print("[dim]No sessions yet. Start one with `sage chat`.[/dim]")
        return

    console.print()
    console.print("[bold cyan]Your sessions[/bold cyan]")
    for s, turns in rows:
        sid = s["session_id"]
        is_default = sid == default_alias
        title = "[green]current chat[/green]" if is_default else (s.get("title") or "[dim](untitled)[/dim]")
        updated = str(s.get("updated_at") or "").split("T")[0].split(" ")[0]
        marker = "[green]●[/green]" if is_default else " "
        console.print(f"  {marker} [bold]{sid[:8]}[/bold]  {title}  [dim]· {turns} turns · {updated}[/dim]")
    console.print("\n[dim]Use the 8-char id (or a unique prefix) with rename / delete / resume.[/dim]")


def sessions_rename_command(id_or_prefix: str, title: str) -> None:
    title = (title or "").strip()
    if not title:
        console.print("[red]Provide a new title.[/red]")
        raise typer.Exit(code=1)
    settings, registry, user_id = _bootstrap()
    try:
        session = _resolve(id_or_prefix, _user_sessions(registry, user_id))
        if not session:
            raise typer.Exit(code=1)
        registry.update_session_title(session["session_id"], title)
    finally:
        registry.close()
    console.print(f"[green]✓ Renamed[/green] {session['session_id'][:8]} → “{title}”.")


def sessions_delete_command(id_or_prefix: str) -> None:
    from app.cli.commands_chat import _prompt_yes_no
    settings, registry, user_id = _bootstrap()
    try:
        session = _resolve(id_or_prefix, _user_sessions(registry, user_id))
        if not session:
            raise typer.Exit(code=1)
        label = session.get("title") or session["session_id"][:8]
        if not _prompt_yes_no(console, f"Delete session “{label}” and its messages?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return
        registry.delete_session(session["session_id"])
    finally:
        registry.close()
    console.print(f"[green]✓ Deleted[/green] {session['session_id'][:8]}.")


def sessions_resume_command(id_or_prefix: str) -> None:
    settings, registry, user_id = _bootstrap()
    try:
        session = _resolve(id_or_prefix, _user_sessions(registry, user_id))
    finally:
        registry.close()
    if not session:
        raise typer.Exit(code=1)
    from app.cli.commands_chat import chat_command
    chat_command(session_id=session["session_id"])
