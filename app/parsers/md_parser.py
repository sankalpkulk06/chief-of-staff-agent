from pathlib import Path

from app.parsers.base import BaseParser, FileParsingError
from app.schemas.document import ParsedDocument


class MarkdownParser(BaseParser):
    parser_name = "md"
    supported_extensions = (".md",)

    def parse(self, file_path: Path) -> ParsedDocument:
        if not file_path.exists() or not file_path.is_file():
            raise FileParsingError(f"Markdown file not found: {file_path}")

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise FileParsingError(f"Failed to read markdown file: {file_path}") from exc

        return self._build_document(file_path=file_path, content=content, metadata={})

