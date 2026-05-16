import subprocess
from datetime import datetime
from typing import Optional


class RemindersServiceError(Exception):
    """Raised when a reminder could not be created in macOS Reminders."""


class RemindersService:
    """Create reminders in the native macOS Reminders app."""

    def __init__(
        self,
        default_list_name: str = "Reminders",
        osascript_path: str = "/usr/bin/osascript",
    ):
        self._default_list_name = default_list_name.strip() or "Reminders"
        self._osascript_path = osascript_path

    @property
    def default_list_name(self) -> str:
        return self._default_list_name

    def add_reminder(
        self, task: str, list_name: Optional[str] = None, due_date: Optional[datetime] = None
    ) -> str:
        reminder_name = task.strip()
        if not reminder_name:
            raise ValueError("task must not be empty")

        target_list = (list_name or self._default_list_name).strip() or "Reminders"
        script = self._build_script(task=reminder_name, list_name=target_list, due_date=due_date)

        try:
            subprocess.run(
                [self._osascript_path, "-e", script],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RemindersServiceError(
                "Apple Reminders integration is only available on macOS with osascript installed."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RemindersServiceError(self._format_error(exc.stderr or exc.stdout or "")) from exc

        return target_list

    @staticmethod
    def _escape_applescript(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _build_script(self, task: str, list_name: str, due_date: Optional[datetime] = None) -> str:
        escaped_task = self._escape_applescript(task)
        escaped_list = self._escape_applescript(list_name)

        # Build properties dict for the reminder
        properties = f'name:"{escaped_task}"'
        if due_date:
            # Format: date "Monday, January 1, 2025 at 9:00:00 AM"
            date_str = due_date.strftime("%A, %B %d, %Y at %I:%M:%S %p")
            properties += f', due date:date "{date_str}"'

        return f'''
tell application "Reminders"
    if not (exists list "{escaped_list}") then
        make new list with properties {{name:"{escaped_list}"}}
    end if
    tell list "{escaped_list}"
        make new reminder with properties {{{properties}}}
    end tell
end tell
'''.strip()

    @staticmethod
    def _format_error(raw_error: str) -> str:
        message = " ".join(raw_error.strip().split())
        if not message:
            return "Could not create the reminder in Apple Reminders."

        lowered = message.lower()
        if "not authorized" in lowered or "not permitted" in lowered or "1743" in lowered:
            return "Reminders access was denied. Please allow access for this app in macOS and try again."

        return f"Could not create the reminder: {message}"
