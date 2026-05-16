import random
import time
from contextlib import contextmanager
from typing import Generator

from rich.console import Console
from rich.spinner import Spinner
from rich.live import Live

console = Console()

WITTY_COMPLETIONS = [
    "✨ sparkling neurons",
    "🧠 deep in thought",
    "⚡ neural firing",
    "🔮 consulting the crystal ball",
    "🌀 spinning up the hamster wheel",
    "🎯 locking onto the answer",
    "🚀 powering up the synapses",
    "🎪 juggling words",
    "🎨 painting the answer",
    "🎵 composing a response",
]


@contextmanager
def thinking_spinner(message: str = "Thinking...") -> Generator[None, None, None]:
    """Show a spinner while the LLM is thinking/generating."""
    spinner = Spinner("dots", text=f"[cyan]{message}[/cyan]", style="cyan")
    live = Live(spinner, console=console, refresh_per_second=12.5)
    live.start()
    try:
        yield
    finally:
        # Replace with witty completion message
        witty = random.choice(WITTY_COMPLETIONS)
        live.update(Spinner("dots", text=f"[green]{witty}[/green]", style="green"))
        time.sleep(0.5)  # Brief pause to show completion message
        live.stop()
        console.print()  # Add a newline for spacing
