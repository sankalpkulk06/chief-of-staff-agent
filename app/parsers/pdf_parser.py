from pathlib import Path

from pypdf import PdfReader

from app.parsers.base import BaseParser, FileParsingError
from app.schemas.document import ParsedDocument


class PdfParser(BaseParser):
    parser_name = "pdf"
    supported_extensions = (".pdf",)

    def parse(self, file_path: Path) -> ParsedDocument:
        if not file_path.exists() or not file_path.is_file():
            raise FileParsingError(f"PDF file not found: {file_path}")

        try:
            reader = PdfReader(str(file_path))
        except Exception as exc:
            raise FileParsingError(f"Failed to open PDF file: {file_path}") from exc

        pages_text = []
        extracted_pages = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                extracted_pages += 1
            pages_text.append(page_text)

        metadata = {
            "page_count": len(reader.pages),
            "extracted_pages": extracted_pages,
        }
        content = "\n\n".join(pages_text)
        return self._build_document(file_path=file_path, content=content, metadata=metadata)

