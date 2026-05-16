from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_registry, require_auth
from app.storage.sqlite_registry import SQLiteRegistry

router = APIRouter(prefix="/sources", tags=["sources"], dependencies=[Depends(require_auth)])


class SourceOut(BaseModel):
    id: str
    title: str
    source_type: str          # "file" | "url"
    source_path: Optional[str] = None
    source_url: Optional[str] = None
    chunk_count: int
    ingested_at: Optional[str] = None


@router.get("", response_model=List[SourceOut])
async def list_sources(
    registry: SQLiteRegistry = Depends(get_registry),
) -> List[SourceOut]:
    # Fetch documents with their chunk counts via a single join query
    rows = registry._connection.execute(
        """
        SELECT d.document_id, d.file_name, d.source_path, d.source_type,
               d.source_url, d.ingested_at,
               COUNT(c.chunk_id) AS chunk_count
        FROM documents d
        LEFT JOIN chunks c ON c.document_id = d.document_id
        GROUP BY d.document_id
        ORDER BY d.ingested_at DESC, d.created_at DESC
        """
    ).fetchall()

    result = []
    for r in rows:
        title = r["file_name"] or r["source_url"] or r["source_path"] or r["document_id"]
        result.append(SourceOut(
            id=r["document_id"],
            title=title,
            source_type=r["source_type"] or "file",
            source_path=r["source_path"],
            source_url=r["source_url"],
            chunk_count=r["chunk_count"],
            ingested_at=str(r["ingested_at"]) if r["ingested_at"] else None,
        ))
    return result
