import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Tuple

from app.schemas.document import ParsedDocument


class FileParsingError(Exception):
    """Raised when a file cannot be parsed."""


class UnsupportedFileTypeError(FileParsingError):
    """Raised when no parser supports a file extension."""


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def compute_file_checksum(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with file_path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


class BaseParser(ABC):
    parser_name: str = "base"
    supported_extensions: Tuple[str, ...] = tuple()

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        raise NotImplementedError

    def _build_document(self, file_path: Path, content: str, metadata: Dict[str, Any]) -> ParsedDocument:
        resolved = file_path.resolve()
        normalized_content = normalize_text(content)
        return ParsedDocument(
            source_path=resolved,
            filename=resolved.name,
            extension=resolved.suffix.lower(),
            checksum_sha256=compute_file_checksum(resolved),
            parser_name=self.parser_name,
            content=normalized_content,
            char_count=len(normalized_content),
            metadata=metadata,
        )

