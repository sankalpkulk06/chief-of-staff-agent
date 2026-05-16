from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from app.cli.app import cli
from app.schemas.document import ParsedDocument
from app.storage.sqlite_registry import SQLiteRegistry


runner = CliRunner()


class _Settings:
    def __init__(self, db_path):
        self._db_path = db_path

    def resolve_paths(self):
        return SimpleNamespace(sqlite_db_path=self._db_path)


def _document(path, filename):
    return ParsedDocument(
        source_path=path,
        filename=filename,
        extension=path.suffix,
        checksum_sha256="c" * 64,
        parser_name="txt",
        content="hello",
        char_count=5,
        metadata={},
    )


def test_sources_command_lists_url_and_local_sources(tmp_path, monkeypatch):
    db_path = tmp_path / "registry.db"
    registry = SQLiteRegistry(db_path)
    try:
        local_path = tmp_path / "notes.txt"
        registry.upsert_document("doc-local", _document(local_path, "notes.txt"))
        registry.upsert_document("doc-url", _document(Path("/url/test"), "Article Title"))
        registry.set_document_source(
            "doc-url",
            source_type="url",
            source_url="https://example.com/article",
        )
    finally:
        registry.close()

    monkeypatch.setattr("app.cli.app.get_settings", lambda: _Settings(db_path))

    result = runner.invoke(cli, ["sources"])

    assert result.exit_code == 0
    assert "Saved sources (2)" in result.stdout
    assert "Article Title" in result.stdout
    assert "example.com" in result.stdout
    assert "notes.txt" in result.stdout
