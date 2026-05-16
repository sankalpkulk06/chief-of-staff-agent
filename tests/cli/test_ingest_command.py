from pathlib import Path

from typer.testing import CliRunner

from app.cli.app import cli
from app.core.ingest_coordinator import IngestSummary


runner = CliRunner()


class _StubIngestCoordinator:
    def __init__(self, summary: IngestSummary):
        self._summary = summary

    def ingest(self, _input_path: Path) -> IngestSummary:
        return self._summary


def test_ingest_command_happy_path(monkeypatch, tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "sample.txt").write_text("hello", encoding="utf-8")

    summary = IngestSummary(
        files_discovered=1,
        files_processed=1,
        files_skipped=0,
        chunks_created=2,
    )
    monkeypatch.setattr(
        "app.cli.commands_ingest.create_ingest_coordinator",
        lambda: _StubIngestCoordinator(summary),
    )

    result = runner.invoke(cli, ["ingest", "--path", docs_dir.as_posix()])

    assert result.exit_code == 0
    assert "Ingestion summary" in result.stdout
    assert "files_processed: 1" in result.stdout
    assert "chunks_created: 2" in result.stdout


def test_ingest_command_invalid_path():
    result = runner.invoke(cli, ["ingest", "--path", "/path/does/not/exist"])

    assert result.exit_code == 1
    assert "path does not exist" in result.stdout

