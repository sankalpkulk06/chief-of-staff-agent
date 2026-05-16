from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app.parsers.base import BaseParser, UnsupportedFileTypeError
from app.parsers.md_parser import MarkdownParser
from app.parsers.pdf_parser import PdfParser
from app.parsers.txt_parser import TextParser
from app.schemas.document import ParsedDocument


class ParserRouter:
    def __init__(self, parsers: Optional[Iterable[BaseParser]] = None):
        parser_list = list(parsers) if parsers is not None else [TextParser(), MarkdownParser(), PdfParser()]
        self._parser_by_extension: Dict[str, BaseParser] = {}

        for parser in parser_list:
            for extension in parser.supported_extensions:
                self._parser_by_extension[extension.lower()] = parser

    @property
    def supported_extensions(self) -> List[str]:
        return sorted(self._parser_by_extension.keys())

    def parse(self, file_path: Path) -> ParsedDocument:
        extension = file_path.suffix.lower()
        parser = self._parser_by_extension.get(extension)
        if parser is None:
            supported = ", ".join(self.supported_extensions)
            raise UnsupportedFileTypeError(
                f"Unsupported file type '{extension or '<none>'}'. Supported types: {supported}"
            )
        return parser.parse(file_path)

