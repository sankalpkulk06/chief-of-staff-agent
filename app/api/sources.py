from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry
from app.config.settings import get_settings
from app.storage.factory import create_vector_store

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


@router.delete("/{document_id}")
async def delete_source(
    document_id: str,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    settings = get_settings()
    paths = settings.resolve_paths()
    vector_store = create_vector_store(settings.database_url, paths.chroma_dir, settings.embedding_dimension)
    try:
        vector_store.delete_user_records(current_user["user_id"], document_ids=[document_id])
    finally:
        try:
            vector_store.close()
        except Exception:
            pass
    registry.delete_document(document_id, user_id=current_user["user_id"])
    return {"ok": True}
