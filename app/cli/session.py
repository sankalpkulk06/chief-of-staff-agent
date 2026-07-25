"""Shared CLI identity: persisted login session + user resolution.

The CLI proves identity with a local session file (``data/session.json``, mode 600)
holding only ``{user_id, username}`` — no password on disk. Its presence is treated as
proof of a prior successful login (the trust boundary is the local machine / OS user).

When ``SAGE_PASSPHRASE`` is unset the app treats auth as disabled for local installs, so
the CLI silently uses/creates a single default local user instead of prompting.
"""
import getpass
import json
import os
import secrets
import sys
from typing import Any, Optional

import typer
from rich.console import Console

from app.config.paths import AppPaths

SESSION_FILENAME = "session.json"


def _session_path(paths: AppPaths):
    return paths.data_dir / SESSION_FILENAME


def load_session(paths: AppPaths) -> Optional[dict]:
    """Return the persisted ``{user_id, username}`` dict, or None if absent/corrupt."""
    path = _session_path(paths)
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return None
    if isinstance(data, dict) and data.get("user_id") and data.get("username"):
        return {"user_id": data["user_id"], "username": data["username"]}
    return None


def save_session(paths: AppPaths, user: dict) -> None:
    """Persist ``{user_id, username}`` to a mode-600 session file."""
    path = _session_path(paths)
    path.write_text(json.dumps({"user_id": user["user_id"], "username": user["username"]}))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_session(paths: AppPaths) -> bool:
    """Delete the session file. Return True if one existed."""
    path = _session_path(paths)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def prompt_auth(registry: Any, console: Console) -> dict:
    """Show the interactive login/signup menu and return the authenticated user dict."""
    if not sys.stdin.isatty():
        # Non-interactive mode requires a real user — cannot prompt.
        console.print("[red]Not logged in.[/red] Run `sage login`.")
        raise typer.Exit(code=1)

    console.print()
    console.print("[bold cyan]Welcome to Sage[/bold cyan]")
    console.print("[dim]━━━━━━━━━━━━━━━━━━━━━━[/dim]")

    while True:
        console.print(r"[bold]\[1][/bold] Login  [bold]\[2][/bold] Sign up  [bold]\[3][/bold] Exit" + "\n")
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            raise typer.Exit()

        if choice == "3":
            raise typer.Exit()

        elif choice == "1":
            # --- Login ---
            while True:
                username = input("Username: ").strip()
                password = getpass.getpass("Password: ")
                user = registry.verify_password(username, password)
                if user:
                    console.print(f"\n[green]Logged in as {username}.[/green]")
                    return user
                console.print("[red]Incorrect username or password. Try again.[/red]")

        elif choice == "2":
            # --- Sign up ---
            while True:
                username = input("Username: ").strip()
                if len(username) < 3:
                    console.print("[red]Username must be at least 3 characters.[/red]")
                    continue
                if registry.get_user_by_username(username):
                    console.print("[red]Username already taken. Choose another.[/red]")
                    continue
                password = getpass.getpass("Password: ")
                if len(password) < 6:
                    console.print("[red]Password must be at least 6 characters.[/red]")
                    continue
                confirm = getpass.getpass("Confirm password: ")
                if password != confirm:
                    console.print("[red]Passwords do not match.[/red]")
                    continue
                user = registry.create_user(username, password)
                console.print(f"\n[green]Account created. Welcome, {username}![/green]")
                return user
        else:
            console.print("[yellow]Please enter 1, 2, or 3.[/yellow]")


def get_or_create_local_user(registry: Any, settings: Any) -> dict:
    """Return the single default local user (auth-disabled mode), creating it if needed.

    Identity is proven by the local session file, so the password is a random,
    never-reused value.
    """
    username = (settings.sage_username or "local").strip() or "local"
    existing = registry.get_user_by_username(username)
    if existing:
        return {"user_id": existing["user_id"], "username": existing["username"]}
    return registry.create_user(username, secrets.token_urlsafe(24))


def auth_enabled(settings: Any) -> bool:
    """Auth is enabled only when a passphrase is configured (the server convention)."""
    return bool((settings.sage_passphrase or "").strip())


def resolve_cli_user(
    settings: Any,
    registry: Any,
    console: Console,
    *,
    allow_prompt: bool = True,
    force_prompt: bool = False,
) -> dict:
    """Resolve the current CLI user, persisting the session for future invocations.

    - Reuses a valid persisted session (validated against the DB) unless ``force_prompt``.
    - Auth disabled → single local user, no prompt.
    - Auth enabled + TTY → interactive login/signup.
    - Auth enabled + non-interactive with no session → clean exit.
    """
    paths = settings.resolve_paths()

    if not force_prompt:
        session = load_session(paths)
        if session and registry.get_user_by_id(session["user_id"]):
            return session

    if not auth_enabled(settings):
        user = get_or_create_local_user(registry, settings)
        save_session(paths, user)
        return user

    if allow_prompt:
        user = prompt_auth(registry, console)
        save_session(paths, user)
        return user

    console.print("[red]Not logged in.[/red] Run `sage login`.")
    raise typer.Exit(code=1)
