"""`sage stats` — terminal analytics dashboard (sparklines, bars, heatmap, insights).

Fed by the shared AnalyticsService so it matches the web dashboard exactly.
"""
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console

from app.cli.session import resolve_cli_user
from app.config import get_settings
from app.core.analytics_service import AnalyticsService
from app.storage.factory import create_registry
from app.ui.spinner import thinking_spinner

console = Console()

_BLOCKS = "▁▂▃▄▅▆▇█"
# 5-level heat ramp (dim → sage), matches the web palette.
_HEAT = ["#182126", "#243c39", "#2f5b4f", "#3f8a70", "#5fb89a"]
_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def spark(values: List[float]) -> str:
    if not values:
        return ""
    mx = max(values)
    if mx <= 0:
        return "[grey37]" + _BLOCKS[0] * len(values) + "[/]"
    return "".join(_BLOCKS[min(7, int(v / mx * 7))] for v in values)


def bar(pct: float, width: int = 18, color: str = "green") -> str:
    filled = max(0, min(width, int(round(pct / 100 * width))))
    return f"[{color}]{'█' * filled}[/][grey35]{'░' * (width - filled)}[/]"


def _heat_level(count: int, mx: int) -> int:
    if count <= 0 or mx <= 0:
        return 0
    return min(4, 1 + int(count / mx * 3.99))


def render_dashboard(console: Console, data: Dict[str, Any], insights: Optional[str] = None) -> None:
    k = data.get("kpis", {})
    win = data.get("window_days", 30)

    peak = k.get("peak_hour")
    peak_s = f"{peak:02d}:00 UTC" if peak is not None else "—"
    console.print()
    console.rule(f"[bold cyan]Sage · last {win} days[/bold cyan]", style="cyan")
    console.print(
        f"  [bold]{k.get('sessions', 0)}[/bold] sessions · "
        f"[bold]{k.get('days_active', 0)}[/bold] days active · "
        f"peak [bold]{peak_s}[/bold] · "
        f"[bold]{k.get('facts_total', 0)}[/bold] facts"
    )

    # Habits
    habits = data.get("habits") or []
    if habits:
        console.print("\n[bold]HABITS[/bold]")
        name_w = max(len(h["name"]) for h in habits)
        for h in habits:
            streak = f"[yellow]🔥 {h['streak']}[/yellow]" if h["streak"] > 0 else "[grey37]streak 0[/grey37]"
            console.print(
                f"  {h['name']:<{name_w}}  {spark(h['series'])}  "
                f"{bar(h['pct'], 14)} [cyan]{h['pct']:>3}%[/cyan]  {streak}"
            )

    # Todos
    t = data.get("todos") or {}
    if t.get("total"):
        avg = f" · avg {t['avg_days']}d" if t.get("avg_days") is not None else ""
        console.print("\n[bold]TODOS[/bold]")
        console.print(
            f"  {bar(t['pct'], 22)} [cyan]{t['pct']}%[/cyan]  "
            f"({t['done']}/{t['total']})  "
            f"[red]{t['overdue']} overdue[/red]{avg}"
        )
        console.print(f"  throughput  {spark(t.get('throughput_completed') or [])}  [grey50](completed/day)[/grey50]")

    # Feature usage
    agents = data.get("agents") or []
    if agents:
        console.print("\n[bold]FEATURE USAGE[/bold]")
        name_w = max(len(a["name"].replace("_agent", "")) for a in agents)
        for a in agents:
            nm = a["name"].replace("_agent", "")
            console.print(f"  {nm:<{name_w}}  {bar(a['pct'], 20, 'magenta')} [cyan]{a['pct']:>3}%[/cyan]")

    # Activity heatmap
    hm = (data.get("usage") or {}).get("heatmap")
    if hm:
        mx = max((max(row) for row in hm), default=0)
        if mx > 0:
            console.print(f"\n[bold]ACTIVITY[/bold]  [grey50]CLI {data['usage']['source'].get('cli',0)} · "
                          f"WhatsApp {data['usage']['source'].get('whatsapp',0)}[/grey50]")
            console.print("       [grey50]00      06      12      18      23[/grey50]")
            for d, row in enumerate(hm):
                cells = "".join(f"[on {_HEAT[_heat_level(c, mx)]}] [/]" for c in row)
                console.print(f"  [grey62]{_DAYS[d]}[/grey62]  {cells}")

    # Insights
    if insights:
        console.print("\n[bold]💡 INSIGHTS[/bold]")
        console.print(f"  [italic]{insights}[/italic]")
    console.print()


def _run(window: int) -> None:
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    user = resolve_cli_user(settings, registry, console)
    svc = AnalyticsService(registry)
    try:
        with thinking_spinner("crunching your stats..."):
            data = svc.get_dashboard(user["user_id"], window_days=window)
            insights = svc.insights(user["user_id"], window_days=window)
    finally:
        registry.close()
    render_dashboard(console, data, insights)


def stats_command(window: int = 30) -> None:
    if window < 1:
        typer.echo("window must be a positive number of days")
        raise typer.Exit(code=1)
    _run(window)
