from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceOut(BaseModel):
    id: str
    title: str
    source_type: str
    source_path: Optional[str] = None
    source_url: Optional[str] = None
    chunk_count: int
    ingested_at: Optional[str] = None


@router.get("", response_model=List[SourceOut])
async def list_sources(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> List[SourceOut]:
    result = []
    for r in registry.list_all_sources(user_id=current_user["user_id"]):
        chunk_count = len(registry.get_chunks_for_document(r["document_id"]))
        title = r["file_name"] or r["source_url"] or r["source_path"] or r["document_id"]
        result.append(SourceOut(
            id=r["document_id"],
            title=title,
            source_type=r["source_type"] or "file",
            source_path=r["source_path"],
            source_url=r["source_url"],
            chunk_count=chunk_count,
            ingested_at=str(r["ingested_at"]) if r["ingested_at"] else None,
        ))
    return result
