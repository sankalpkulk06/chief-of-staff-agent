from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    document_checksum_sha256: str
    source_path: Path
    file_name: str
    chunk_index: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    metadata: Dict[str, Any] = Field(default_factory=dict)
