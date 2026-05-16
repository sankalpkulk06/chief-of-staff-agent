import re
import uuid
from datetime import datetime
from typing import Optional, Tuple

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console

from app.cli.commands_ask import (
    create_analytics_service,
    create_chat_service,
    create_news_service,
    create_reminders_service,
    create_web_search_service,
)
from app.config import get_settings
from app.core.habit_service import HabitService
from app.core.todo_parser import parse_due_date
from app.services.email_service import EmailService
from app.services.reminders_service import RemindersServiceError
from app.providers.ollama_chat import OllamaChatProvider
from app.providers.ollama_embeddings import OllamaProviderError
from app.ui.spinner import thinking_spinner

_EMAIL_TRIGGERS = {
    "check my email", "check email", "any emails", "any email",
    "show my email", "show email", "read my email", "read email",
    "email inbox", "check inbox", "what emails", "do i have email",
    "email triage", "triage email", "triage my email",
}
_EMAIL_ACTION_WORDS = {"check", "any", "show", "read", "triage", "fetch", "get", "what", "action"}


def _is_email_request(text: str) -> bool:
    t = text.lower()
    if t in _EMAIL_TRIGGERS:
        return True
    if ("email" in t or "inbox" in t) and any(w in t for w in _EMAIL_ACTION_WORDS):
        return True
    return False


def _handle_email_personal(console: Console, settings) -> None:
    paths = settings.resolve_paths()
    service = EmailService(credentials_dir=paths.credentials_dir, account_type="personal")
    chat_provider = OllamaChatProvider(base_url=settings.ollama_base_url, model=settings.ollama_chat_model)

    try:
        with thinking_spinner("fetching personal emails..."):
            emails = service.fetch_recent(max_results=settings.email_max_results)
    except FileNotFoundError as exc:
        console.print(f"\n[red]Setup required:[/red] {exc}\n")
        return
    except Exception as exc:
        console.print(f"\n[red]Error fetching emails:[/red] {exc}\n")
        return

    console.print()
    console.print("[bold cyan]╭─ Personal Email ─╮[/bold cyan]")
    console.print()

    if not emails:
        console.print("[dim]No emails found.[/dim]")
        console.print()
        return

    try:
        with thinking_spinner("triaging with AI..."):
            triaged = service.triage(emails, chat_provider)
    except Exception as exc:
        console.print(f"\n[red]Error during triage:[/red] {exc}\n")
        return

    action_items = [t for t in triaged if t.category == "action"]
    fyi_items = [t for t in triaged if t.category == "fyi"]

    if action_items:
        console.print(f"[bold red]ACTION NEEDED ({len(action_items)})[/bold red]")
        for i, item in enumerate(action_items, 1):
            console.print(f"  [dim]{i}.[/dim] [bold]{item.email.sender}[/bold] — {item.email.subject}")
            console.print(f"     [red]→[/red] {item.reason}")
        console.print()
    else:
        console.print("[dim]No action needed.[/dim]")
        console.print()

    if fyi_items:
        console.print(f"[bold yellow]FYI ({len(fyi_items)})[/bold yellow]")
        for i, item in enumerate(fyi_items, 1):
            console.print(f"  [dim]{i}.[/dim] [bold]{item.email.sender}[/bold] — {item.email.subject}")
            console.print(f"     [yellow]→[/yellow] {item.reason}")
        console.print()

    console.print("[bold cyan]╰──────────────────╯[/bold cyan]")
    console.print()

console = Console()

# Custom key bindings: Enter submits, Shift+Enter creates newline
def create_key_bindings():
    bindings = KeyBindings()

    @bindings.add(Keys.Enter, eager=True)
    def _(event):
        # Enter submits the input
        event.app.exit(result=event.app.current_buffer.text)

    @bindings.add(Keys.Escape, Keys.Enter, eager=True)
    def _(event):
        # Escape+Enter creates a newline
        event.current_buffer.insert_text('\n')

    @bindings.add(Keys.ControlM, eager=True)
    def _(event):
        # Ctrl+M (alternative Enter) also submits
        event.app.exit(result=event.app.current_buffer.text)

    return bindings

key_bindings = create_key_bindings()


def _parse_task_list_and_due_date(input_str: str) -> Tuple[str, Optional[str], Optional[datetime]]:
    """Parse task, optional list name, and optional due date from /todo input.

    Supports formats:
    - /todo Buy milk
    - /todo Buy milk #Shopping
    - /todo Buy milk @tomorrow
    - /todo Buy milk #Shopping @tomorrow
    - /todo Buy milk @tomorrow #Shopping

    Args:
        input_str: The input string without '/todo' prefix

    Returns:
        Tuple of (task, list_name, due_date) where list_name and due_date are None if not specified
    """
    working_str = input_str.strip()
    list_name = None
    due_date = None

    # Extract list name FIRST if # is present (must be before date parsing to avoid fuzzy parser eating #)
    if "#" in working_str:
        task_part, list_part = working_str.rsplit("#", 1)
        # If there's a date part after the list, extract just the list name
        if "@" in list_part:
            list_only, date_only = list_part.split("@", 1)
            list_name = list_only.strip()
            # Reconstruct: task @ date
            working_str = f"{task_part.strip()} @{date_only}"
        else:
            list_name = list_part.strip()
            working_str = task_part.strip()

    # Extract due date if @ is present
    if "@" in working_str:
        task_part, date_part = working_str.rsplit("@", 1)
        date_str = date_part.strip().lower()

        if date_str:
            try:
                due_date = parse_due_date(date_str)
            except (ValueError, TypeError, OverflowError):
                # If parsing fails, ignore and keep working_str as-is
                task_part = working_str

        working_str = task_part.strip()

    return working_str, list_name, due_date


def _print_analytics_dashboard(stats) -> None:
    """Print a formatted analytics dashboard."""
    console.print()
    console.print("[bold cyan]╭─ Analytics Dashboard ─╮[/bold cyan]")
    console.print()

    # Session metrics
    console.print("[bold yellow]📊 Conversation Overview[/bold yellow]")
    console.print(f"  Total Sessions:         [bold]{stats.total_sessions}[/bold]")
    console.print(f"  Total Turns:            [bold]{stats.total_turns}[/bold]")
    console.print(f"  Avg Turns per Session:  [bold]{stats.average_turns_per_session:.1f}[/bold]")
    console.print(f"  Longest Session:        [bold]{stats.longest_session_turns} turns[/bold]")
    console.print()

    # Activity metrics
    console.print("[bold yellow]📈 Activity Patterns[/bold yellow]")
    if stats.first_session:
        console.print(f"  First Session:          [bold]{stats.first_session}[/bold]")
    if stats.last_session:
        console.print(f"  Last Session:           [bold]{stats.last_session}[/bold]")
    console.print(f"  Days Active:            [bold]{stats.days_active}[/bold]")
    console.print(f"  Sessions per Day:       [bold]{stats.sessions_per_day_avg:.2f}[/bold]")
    if stats.most_active_day:
        console.print(f"  Most Active Day:        [bold]{stats.most_active_day}[/bold]")
    if stats.most_active_hour is not None:
        console.print(f"  Most Active Hour:       [bold]{stats.most_active_hour:02d}:00[/bold]")
    console.print()

    # Commands
    if stats.top_commands:
        console.print("[bold yellow]⚡ Top Commands[/bold yellow]")
        for cmd, count in stats.top_commands:
            console.print(f"  {cmd:<20} [dim]{count} times[/dim]")
        console.print()

    # Question patterns
    if stats.top_question_words:
        console.print("[bold yellow]💬 Top Question Words[/bold yellow]")
        for word, count in stats.top_question_words:
            console.print(f"  {word:<20} [dim]{count} times[/dim]")
        console.print()

    # Facts
    if stats.fact_categories_count:
        console.print("[bold yellow]🧠 Learned Facts by Category[/bold yellow]")
        for category, count in stats.fact_categories_count.items():
            console.print(f"  {category:<20} [dim]{count} facts[/dim]")
        console.print()

    console.print("[bold cyan]╰──────────────────────╯[/bold cyan]")
    console.print()


def _parse_habit_reminder_time(args: str) -> tuple[str, str]:
    """Split '/habit add <name> [@<time>]' into (name, reminder_time)."""
    match = re.search(r"@(\S+)", args)
    if match:
        time_str = match.group(1)
        name = args[: match.start()].strip()
        return name, time_str
    return args.strip(), "21:00"


def _format_weekly_summary(summaries) -> str:
    from datetime import date
    today = date.today()
    week_label = today.strftime("Week of %b %-d, %Y")
    lines = [f"\n[bold cyan]📊 Habit Summary — {week_label}[/bold cyan]\n"]

    if not summaries:
        lines.append("  [dim]No habits tracked yet. Add one with[/dim] [bold]/habit add <name>[/bold]\n")
        return "\n".join(lines)

    total_done = 0
    total_possible = len(summaries) * 7

    for s in summaries:
        filled = round(s.days_done / 7 * 10)
        bar = "█" * filled + "░" * (10 - filled)
        total_done += s.days_done

        if s.streak > 0 and s.logged_today:
            streak_label = f"[bold yellow]🔥 {s.streak}-day streak[/bold yellow]"
        elif s.streak > 0:
            streak_label = f"[yellow]🔥 {s.streak}-day streak[/yellow]"
        else:
            streak_label = "[red]❌ streak broken[/red]"

        lines.append(
            f"  [bold]{s.habit.name:<14}[/bold] [green]{bar}[/green]   "
            f"[dim]{s.days_done}/7 days[/dim]   {streak_label}"
        )

    lines.append(f"\n[dim]Total logged this week: {total_done}/{total_possible}[/dim]\n")
    return "\n".join(lines)


def _print_help() -> None:
    console.print("\n[bold cyan]━━━━━━━━━━ Available Commands ━━━━━━━━━━[/bold cyan]")
    console.print()
    commands = [
        ("/help", "Show this help message"),
        ("/topk <n>", "Set retrieval depth (default: 5)"),
        ("/session", "Show current session ID"),
        ("/sessions", "List recent chat sessions"),
        ("/analytics", "View usage statistics and patterns"),
        ("/remember-personal <fact>", "Remember a personal fact"),
        ("/remember-work <fact>", "Remember a work fact"),
        ("/facts [category]", "List facts (personal|work)"),
        ("/forget <fact-id>", "Delete a fact"),
        ("/email", "Check personal email and triage action items"),
        ("/news [query]", "Fetch live news"),
        ("/search <query>", "Search the web for current information"),
        ("/usage", "Show today's Twilio WhatsApp usage"),
        ("/todo <task> [#list] [@due]", "Add a Sage reminder"),
        ("/apple-reminder <task> [#list] [@due]", "Add a task to Apple Reminders"),
        ("/habit add <name> [@time]", "Track a new habit (optional reminder time)"),
        ("/habit log <name> [skipped]", "Log a habit as done or skipped"),
        ("/habit unlog <name>", "Remove today's log entry for a habit"),
        ("/habit delete <name>", "Stop tracking a habit"),
        ("/habits", "Show weekly habit summary with streaks"),
        ("exit | quit", "Exit chat mode"),
    ]
    for cmd, desc in commands:
        console.print(f"[bold green]{cmd:<30}[/bold green] {desc}")
    console.print()


def chat_command(top_k: Optional[int] = None, session_id: Optional[str] = None) -> None:
    """Run an interactive chat session with conversation history."""
    service = create_chat_service()
    fact_service = service.get_fact_service()
    web_search_service = service.get_web_search_service()
    news_service = create_news_service()
    reminders_service = create_reminders_service()
    habit_service = service.get_habit_service() or HabitService(service.get_registry())
    registry = service.get_registry()

    # Create chat provider for news summary generation
    settings = get_settings()
    chat_provider = OllamaChatProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_chat_model,
    )

    session_top_k = top_k

    if session_id is None:
        session_id = service.get_registry().get_or_create_named_session("cli:default")
    else:
        service.create_session(session_id=session_id)

    console.print()
    console.print("[bold cyan]╭─ Sage — Your Personal AI ─╮[/bold cyan]")
    console.print(f"[dim]│ Session: {session_id[:8]}...[/dim]")
    console.print(f"[dim]│ Resume: sage --resume {session_id}[/dim]")
    console.print("[bold cyan]╰──────────────────────────╯[/bold cyan]")
    console.print()
    console.print("[green]💡 Tip:[/green] Type [bold]/help[/bold] for commands | [bold]Shift+Enter[/bold] for multi-line")
    console.print()

    # Create prompt session with history
    session = PromptSession(
        history=InMemoryHistory(),
        enable_history_search=True,
    )

    while True:
        try:
            # Print colored prompt with newline for proper cursor placement
            console.print("[bold blue]you[/bold blue]")
            user_input = session.prompt(
                "> ",  # Input indicator on new line
                multiline=True,
                editing_mode=EditingMode.EMACS,
                key_bindings=key_bindings,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            break

        question = user_input.strip()
        if not question:
            continue

        lowered = question.lower()
        if lowered in ("exit", "quit"):
            typer.echo("bye")
            break
        if lowered == "/help":
            _print_help()
            continue
        if lowered == "/session":
            console.print(f"\n[dim]Session ID:[/dim] [bold]{session_id}[/bold]\n")
            continue
        if lowered == "/sessions":
            sessions = service.list_sessions(limit=10)
            if sessions:
                console.print()
                console.print("[bold cyan]━━━━━━━━━━ Recent Sessions ━━━━━━━━━━[/bold cyan]")
                console.print()
                for i, s in enumerate(sessions, 1):
                    title = s["title"] or "(untitled)"
                    sid = s["session_id"][:8]
                    updated = s["updated_at"].split("T")[0]  # Extract date
                    console.print(f"[bold]{i}.[/bold] {sid}...  [dim]{updated}[/dim]")
                console.print()
            else:
                console.print("\n[dim]No sessions found.[/dim]\n")
            continue
        if lowered == "/analytics":
            analytics_service = create_analytics_service()
            stats = analytics_service.get_analytics()
            _print_analytics_dashboard(stats)
            continue
        if lowered.startswith("/topk "):
            maybe_value = question.split(maxsplit=1)[1].strip()
            try:
                parsed = int(maybe_value)
                if parsed <= 0:
                    raise ValueError("top_k must be positive")
                session_top_k = parsed
                console.print(f"\n[green]✓[/green] Retrieval depth set to [bold]{session_top_k}[/bold]\n")
            except ValueError:
                console.print("\n[red]✗[/red] Invalid value. Usage: /topk 5\n")
            continue
        if lowered.startswith("/remember-personal "):
            fact_text = question[len("/remember-personal "):].strip()
            if fact_text:
                fact_service.remember(content=fact_text, category="personal")
                console.print(f"\n[green]✓[/green] [bold magenta]Personal fact[/bold magenta] saved: {fact_text}\n")
            else:
                console.print("\n[yellow]Usage:[/yellow] /remember-personal <fact>\n")
            continue
        if lowered.startswith("/remember-work "):
            fact_text = question[len("/remember-work "):].strip()
            if fact_text:
                fact_service.remember(content=fact_text, category="work")
                console.print(f"\n[green]✓[/green] [bold cyan]Work fact[/bold cyan] saved: {fact_text}\n")
            else:
                console.print("\n[yellow]Usage:[/yellow] /remember-work <fact>\n")
            continue
        if lowered.startswith("/facts"):
            parts = lowered.split()
            category = parts[1] if len(parts) > 1 else None
            facts = fact_service.list_facts(category=category)
            if facts:
                cat_icon = {"personal": "👤", "work": "💼"}.get(category, "🧠")
                cat_label = f" {cat_icon} {category.title()}" if category else " 🧠 All"
                console.print(f"\n[bold cyan]━━━━━━ Learned Facts{cat_label} ━━━━━━[/bold cyan]")
                console.print()
                for i, fact in enumerate(facts[:20], 1):
                    fact_id = fact.fact_id[:8]
                    date = fact.created_at[:10]
                    console.print(f"[bold green][{i}][/bold green] {fact.content}")
                    console.print(f"[dim]    {fact_id}... | {date}[/dim]")
                    console.print()
            else:
                console.print("\n[dim]No facts learned yet. Use /remember-personal or /remember-work[/dim]\n")
            continue
        if lowered.startswith("/forget "):
            fact_id = question[len("/forget "):].strip()
            try:
                fact_service.forget(fact_id)
                console.print(f"\n[green]✓[/green] Fact forgotten\n")
            except Exception as e:
                console.print(f"\n[red]✗[/red] Error: {e}\n")
            continue
        if lowered in ("/email", "/email-personal") or _is_email_request(lowered):
            _handle_email_personal(console, settings)
            continue
        if lowered.startswith("/news"):
            query = question[len("/news"):].strip()
            try:
                with thinking_spinner("fetching news..."):
                    if query:
                        articles = news_service.search_news(query)
                    else:
                        articles = news_service.get_top_news()

                if articles:
                    news_title = f"News: {query}" if query else "Top News Today"
                    console.print(f"\n[bold cyan]━━━━━━ {news_title} ━━━━━━[/bold cyan]\n")

                    # Generate and display summary
                    with thinking_spinner("generating summary..."):
                        summary = news_service.generate_summary(articles, chat_provider)

                    console.print("[bold yellow]📋 Summary[/bold yellow]")
                    console.print(summary)
                    console.print()

                    # Display articles
                    console.print("[bold yellow]📰 Articles[/bold yellow]")
                    for i, article in enumerate(articles, 1):
                        console.print(f"[bold green][{i}][/bold green] [bold]{article.title}[/bold]")
                        console.print(f"[yellow]{article.source}[/yellow] [dim]| {article.published}[/dim]")
                        console.print(f"[blue underline]{article.url}[/blue underline]")
                        console.print()
                else:
                    console.print("\n[dim]No news found for your query.[/dim]\n")
            except Exception as e:
                console.print(f"\n[red]Error fetching news:[/red] {e}\n")
            continue
        if lowered.startswith("/search"):
            query = question[len("/search"):].strip()
            if not query:
                console.print("\n[yellow]Usage:[/yellow] /search <query>\n")
                continue
            if not web_search_service:
                console.print("\n[red]Web search is not configured.[/red]\n")
                continue
            try:
                with thinking_spinner("searching the web..."):
                    results = web_search_service.search(query)
                if results:
                    console.print(f"\n[bold cyan]━━━━━━ Web Search: {query} ━━━━━━[/bold cyan]\n")
                    for i, r in enumerate(results, 1):
                        console.print(f"[bold green][{i}][/bold green] [bold]{r.title}[/bold]")
                        console.print(f"[blue underline]{r.url}[/blue underline]")
                        console.print(f"[dim]{r.snippet[:200]}...[/dim]" if len(r.snippet) > 200 else f"[dim]{r.snippet}[/dim]")
                        console.print()
                else:
                    console.print(f"\n[dim]No results found for '{query}'.[/dim]\n")
            except Exception as e:
                console.print(f"\n[red]Error:[/red] {e}\n")
            continue
        if lowered == "/habits":
            summaries = habit_service.get_weekly_summary()
            console.print(_format_weekly_summary(summaries))
            continue
        if lowered.startswith("/habit add "):
            args = question[len("/habit add "):].strip()
            if not args:
                console.print("\n[yellow]Usage:[/yellow] /habit add <name> [@time]\n")
                continue
            name, reminder_time = _parse_habit_reminder_time(args)
            if not name:
                console.print("\n[yellow]Usage:[/yellow] /habit add <name> [@time]\n")
                continue
            habit = habit_service.add_habit(name=name, reminder_time=reminder_time)
            console.print(f"\n[green]✓[/green] Habit [bold]{habit.name}[/bold] added (reminder at {habit.reminder_time})\n")
            continue
        if lowered.startswith("/habit log "):
            args = question[len("/habit log "):].strip()
            parts = args.split(None, 1)
            name = parts[0] if parts else ""
            status = "skipped" if len(parts) > 1 and parts[1].strip().lower() == "skipped" else "done"
            if not name:
                console.print("\n[yellow]Usage:[/yellow] /habit log <name> [skipped]\n")
                continue
            try:
                log = habit_service.log_habit(name=name, status=status)
                verb = "skipped" if log.status == "skipped" else "logged"
                console.print(f"\n[green]✓[/green] Habit [bold]{name}[/bold] {verb} for today\n")
            except ValueError as exc:
                console.print(f"\n[red]✗[/red] {exc}\n")
            continue
        if lowered.startswith("/habit unlog "):
            name = question[len("/habit unlog "):].strip()
            if not name:
                console.print("\n[yellow]Usage:[/yellow] /habit unlog <name>\n")
                continue
            try:
                deleted = habit_service.unlog_habit(name)
                if deleted:
                    console.print(f"\n[green]✓[/green] Removed today's log for [bold]{name}[/bold]\n")
                else:
                    console.print(f"\n[dim]No log found for '{name}' today.[/dim]\n")
            except ValueError as exc:
                console.print(f"\n[red]✗[/red] {exc}\n")
            continue
        if lowered.startswith("/habit delete "):
            name = question[len("/habit delete "):].strip()
            if not name:
                console.print("\n[yellow]Usage:[/yellow] /habit delete <name>\n")
                continue
            deleted = habit_service.delete_habit(name)
            if deleted:
                console.print(f"\n[green]✓[/green] Habit [bold]{name}[/bold] removed\n")
            else:
                console.print(f"\n[red]✗[/red] Habit '{name}' not found\n")
            continue
        if lowered.startswith("/habit"):
            console.print("\n[yellow]Habit commands:[/yellow]")
            console.print("  [bold]/habit add <name> [@time][/bold]  — start tracking a habit")
            console.print("  [bold]/habit log <name> [skipped][/bold] — mark done or skipped")
            console.print("  [bold]/habit unlog <name>[/bold]          — remove today's log")
            console.print("  [bold]/habit delete <name>[/bold]         — stop tracking")
            console.print("  [bold]/habits[/bold]                       — weekly summary\n")
            continue
        if lowered == "/todo" or lowered.startswith("/todo "):
            task_input = question[len("/todo"):].strip()
            if not task_input:
                console.print("\n[yellow]Usage:[/yellow] /todo <task> [#list] [@due-date]\n")
                console.print("[dim]Examples:\n")
                console.print("[dim]  /todo Buy milk\n")
                console.print("[dim]  /todo Buy milk #Shopping\n")
                console.print("[dim]  /todo Call mom @tomorrow\n")
                console.print("[dim]  /todo Meeting #Work @next Tuesday at 3pm\n")
                continue

            try:
                task, list_name, due_date = _parse_task_list_and_due_date(task_input)
                if not task:
                    console.print("\n[yellow]Usage:[/yellow] /todo <task> [#list] [@due-date]\n")
                    continue

                registry.create_todo(title=task, list_name=list_name, due_at=due_date)
                due_date_str = f" due {due_date.strftime('%a, %b %d at %I:%M%p')}" if due_date else ""
                console.print(
                    f"\n[green]✓[/green] Added Sage reminder: {task}{due_date_str}\n"
                )
            except Exception as exc:
                console.print(f"\n[red]✗[/red] Could not add Sage reminder: {exc}\n")
            continue

        if lowered == "/apple-reminder" or lowered.startswith("/apple-reminder "):
            task_input = question[len("/apple-reminder"):].strip()
            if not task_input:
                console.print("\n[yellow]Usage:[/yellow] /apple-reminder <task> [#list] [@due-date]\n")
                continue

            try:
                task, list_name, due_date = _parse_task_list_and_due_date(task_input)
                if not task:
                    console.print("\n[yellow]Usage:[/yellow] /apple-reminder <task> [#list] [@due-date]\n")
                    continue

                target_list = reminders_service.add_reminder(task=task, list_name=list_name, due_date=due_date)
                due_date_str = f" due {due_date.strftime('%a, %b %d at %I:%M%p')}" if due_date else ""
                console.print(
                    f"\n[green]✓[/green] Added Apple Reminder to [bold]{target_list}[/bold]: {task}{due_date_str}\n"
                )
            except RemindersServiceError as exc:
                console.print(f"\n[red]✗[/red] {exc}\n")
            continue

        try:
            with thinking_spinner("generating answer..."):
                result = service.answer_in_session(
                    session_id=session_id, question=question, top_k=session_top_k
                )
        except OllamaProviderError as exc:
            typer.echo(f"error: Ollama unavailable: {exc}")
            continue
        except Exception as exc:
            typer.echo(f"error: ask failed: {exc}")
            continue

        console.print()
        console.print("[bold cyan]╭─ Sage ─╮[/bold cyan]")
        console.print()
        console.print(result.answer)
        console.print()

        if result.web_sources or result.news_sources or (result.sources_used and result.sources):
            console.print("[bold cyan]─ Sources ─[/bold cyan]")
            if result.web_sources:
                for i, source in enumerate(result.web_sources, 1):
                    console.print(f"[green]🌐 [{i}][/green] {source['title']}")
                    console.print(f"    [blue underline]{source['url']}[/blue underline]")
            if result.news_sources:
                for i, source in enumerate(result.news_sources, 1):
                    console.print(f"[yellow]📰 [{i}][/yellow] {source['title']}")
                    console.print(f"    [dim]{source['source']}[/dim]")
            if result.sources_used and result.sources:
                shown = set()
                for source in result.sources:
                    if source.source_type == "url" and source.source_url:
                        from urllib.parse import urlparse

                        domain = urlparse(source.source_url).netloc
                        source_label = f"{source.file_name or 'untitled'} — {domain} 🌐"
                    else:
                        source_label = f"{source.file_name or source.source_path or source.document_id} 📄"
                    if source_label in shown:
                        continue
                    shown.add(source_label)
                    console.print(f"[cyan]{source_label}[/cyan]")
            console.print()

        console.print("[bold cyan]╰──────────╯[/bold cyan]")
        console.print()
