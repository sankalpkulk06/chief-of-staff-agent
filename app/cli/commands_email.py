from typing import Optional

import typer
from rich.console import Console
from rich.rule import Rule

from app.cli.session import resolve_cli_user
from app.config import get_settings
from app.config.settings import get_google_client_secrets
from app.services.email_service import AccountType, EmailService
from app.providers.factory import create_default_chat_provider
from app.storage.factory import create_registry
from app.ui.spinner import thinking_spinner

console = Console()


def _create_chat_provider():
    settings = get_settings()
    return create_default_chat_provider(settings)


def _run_email_command(
    account_type: AccountType,
    label: str,
    max_results: int,
    no_triage: bool,
) -> None:
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)

    user = resolve_cli_user(settings, registry, console)
    user_id = user["user_id"]

    token_json = registry.get_email_token(user_id, account_type)
    if not token_json:
        typer.echo(
            f"Gmail ({account_type}) isn't connected. "
            "Run `sage email connect` (coming soon) or connect via the web UI."
        )
        raise typer.Exit(code=1)

    client_secrets = get_google_client_secrets(settings)
    if not client_secrets:
        typer.echo("Gmail OAuth is not configured (set GOOGLE_CLIENT_SECRETS_JSON).")
        raise typer.Exit(code=1)

    service = EmailService(client_secrets=client_secrets, account_type=account_type)

    try:
        with thinking_spinner(f"fetching {label.lower()} emails..."):
            emails, refreshed_token = service.fetch_recent(token_json, max_results=max_results)
    except Exception as exc:
        typer.echo(f"Error fetching emails: {exc}")
        raise typer.Exit(code=1)

    # Persist a refreshed token if Google rotated it.
    if refreshed_token:
        try:
            registry.upsert_email_token(user_id, refreshed_token, account_type)
        except Exception:
            pass

    console.print()
    console.print(Rule(f"[bold]{label}[/bold]"))

    if not emails:
        console.print("[dim]No emails found.[/dim]")
        return

    if no_triage:
        for i, email in enumerate(emails, 1):
            console.print(f"[dim]{i}.[/dim] [bold]{email.sender}[/bold] — {email.subject}")
        return

    chat_provider = _create_chat_provider()

    try:
        with thinking_spinner("triaging with AI..."):
            triaged = service.triage(emails, chat_provider)
    except Exception as exc:
        typer.echo(f"Error during triage: {exc}")
        raise typer.Exit(code=1)

    action_items = [t for t in triaged if t.category == "action"]
    fyi_items = [t for t in triaged if t.category == "fyi"]

    if action_items:
        console.print(f"\n[bold red]ACTION NEEDED ({len(action_items)})[/bold red]")
        for i, item in enumerate(action_items, 1):
            console.print(f"  [dim]{i}.[/dim] [bold]{item.email.sender}[/bold] — {item.email.subject}")
            console.print(f"     [red]→[/red] {item.reason}")
    else:
        console.print("\n[dim]No action needed.[/dim]")

    if fyi_items:
        console.print(f"\n[bold yellow]FYI ({len(fyi_items)})[/bold yellow]")
        for i, item in enumerate(fyi_items, 1):
            console.print(f"  [dim]{i}.[/dim] [bold]{item.email.sender}[/bold] — {item.email.subject}")
            console.print(f"     [yellow]→[/yellow] {item.reason}")

    console.print()


def email_personal_command(
    max_results: Optional[int] = typer.Option(None, "--max-results", "-n", help="Max emails to fetch."),
    no_triage: bool = typer.Option(False, "--no-triage", help="Skip AI triage, list emails only."),
) -> None:
    settings = get_settings()
    email_max = max_results or settings.email_max_results
    _run_email_command("personal", "Personal Email", email_max, no_triage)


def email_work_command(
    max_results: Optional[int] = typer.Option(None, "--max-results", "-n", help="Max emails to fetch."),
    no_triage: bool = typer.Option(False, "--no-triage", help="Skip AI triage, list emails only."),
) -> None:
    settings = get_settings()
    email_max = max_results or settings.email_max_results
    _run_email_command("work", "Work Email", email_max, no_triage)
