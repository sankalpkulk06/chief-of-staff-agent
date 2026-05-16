import subprocess

import pytest

from app.services.reminders_service import RemindersService, RemindersServiceError


def test_add_reminder_runs_osascript_with_expected_script(monkeypatch):
    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(
            {
                "cmd": cmd,
                "check": check,
                "capture_output": capture_output,
                "text": text,
            }
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("app.services.reminders_service.subprocess.run", fake_run)

    service = RemindersService(default_list_name="Errands")
    list_name = service.add_reminder('Buy "oat" milk')

    assert list_name == "Errands"
    assert len(calls) == 1
    assert calls[0]["cmd"][0] == "/usr/bin/osascript"
    assert calls[0]["cmd"][1] == "-e"
    script = calls[0]["cmd"][2]
    assert 'exists list "Errands"' in script
    assert 'make new list with properties {name:"Errands"}' in script
    assert 'make new reminder with properties {name:"Buy \\"oat\\" milk"}' in script


def test_add_reminder_uses_provided_list_name(monkeypatch):
    commands = []

    def fake_run(cmd, check, capture_output, text):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("app.services.reminders_service.subprocess.run", fake_run)

    service = RemindersService(default_list_name="Errands")
    list_name = service.add_reminder("Walk dog", list_name="Today")

    assert list_name == "Today"
    assert 'exists list "Today"' in commands[0][2]


def test_add_reminder_wraps_subprocess_error(monkeypatch):
    def fake_run(cmd, check, capture_output, text):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=cmd,
            stderr="Application isn't allowed to send Apple events to Reminders. (-1743)",
        )

    monkeypatch.setattr("app.services.reminders_service.subprocess.run", fake_run)

    service = RemindersService()

    with pytest.raises(RemindersServiceError) as exc_info:
        service.add_reminder("Buy oat milk")

    assert "Reminders access was denied" in str(exc_info.value)
