from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field


class ParsedDocument(BaseModel):
    source_path: Path
    filename: str
    extension: str
    checksum_sha256: str
    parser_name: str
    content: str
    char_count: int
    metadata: Dict[str, Any] = Field(default_factory=dict)

