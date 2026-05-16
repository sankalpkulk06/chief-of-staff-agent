from typer.testing import CliRunner

from app.cli.app import cli


runner = CliRunner()


def test_cli_help_works():
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Local-first personal RAG agent CLI." in result.stdout
    assert "config" in result.stdout
    assert "chat" in result.stdout


def test_cli_version_flag():
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "personal-rag-study-agent" in result.stdout
