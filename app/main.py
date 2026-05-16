import sys

from app.cli.app import cli
from app.cli.commands_chat import chat_command


def run() -> None:
    cli()


def run_sage() -> None:
    """Entry point for 'sage' command - launches chat by default."""
    if len(sys.argv) == 1:
        # No arguments: launch chat mode
        chat_command()
    elif len(sys.argv) == 3 and sys.argv[1] == "--resume":
        # Special case: sage --resume <session_id>
        session_id = sys.argv[2]
        chat_command(session_id=session_id)
    else:
        # With other arguments: use normal CLI
        cli()


if __name__ == "__main__":
    run()

