from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from app.ingestion.ids import build_chunk_id, build_document_id
from app.schemas.chunk import DocumentChunk
from app.schemas.document import ParsedDocument


def approximate_token_count(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


class ChunkingConfig(BaseModel):
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)

    @model_validator(mode="after")
    def validate_overlap(self) -> "ChunkingConfig":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class Chunker:
    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or ChunkingConfig()

    def chunk_document(self, document: ParsedDocument, document_id: Optional[str] = None) -> List[DocumentChunk]:
        content = document.content
        if not content.strip():
            return []

        doc_id = document_id or build_document_id(document.source_path, document.checksum_sha256)
        step = self.config.chunk_size - self.config.chunk_overlap

        chunks: List[DocumentChunk] = []
        chunk_index = 0
        start = 0
        total_len = len(content)

        while start < total_len:
            end = min(start + self.config.chunk_size, total_len)
            chunk_text = content[start:end]
            if chunk_text.strip():
                chunk_id = build_chunk_id(
                    document_id=doc_id,
                    chunk_index=chunk_index,
                    char_start=start,
                    char_end=end,
                    text=chunk_text,
                )
                chunks.append(
                    DocumentChunk(
                        chunk_id=chunk_id,
                        document_id=doc_id,
                        document_checksum_sha256=document.checksum_sha256,
                        source_path=document.source_path,
                        file_name=document.filename,
                        chunk_index=chunk_index,
                        text=chunk_text,
                        token_count=approximate_token_count(chunk_text),
                        char_start=start,
                        char_end=end,
                        metadata={
                            "source_path": document.source_path.as_posix(),
                            "file_name": document.filename,
                            "parser_name": document.parser_name,
                        },
                    )
                )
                chunk_index += 1

            if end >= total_len:
                break
            start += step

        return chunks

