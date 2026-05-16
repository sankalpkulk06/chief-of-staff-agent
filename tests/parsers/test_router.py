import pytest

from app.parsers.base import UnsupportedFileTypeError
from app.parsers.router import ParserRouter


def test_router_parses_supported_file(docs_dir):
    router = ParserRouter()

    parsed = router.parse(docs_dir / "sample.txt")

    assert parsed.parser_name == "txt"
    assert parsed.filename == "sample.txt"


def test_router_raises_clear_error_for_unsupported_file(tmp_path):
    file_path = tmp_path / "unsupported.docx"
    file_path.write_text("content", encoding="utf-8")
    router = ParserRouter()

    with pytest.raises(UnsupportedFileTypeError) as exc_info:
        router.parse(file_path)

    message = str(exc_info.value)
    assert "Unsupported file type '.docx'" in message
    assert ".md" in message
    assert ".pdf" in message
    assert ".txt" in message

