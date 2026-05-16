from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_registry, require_auth
from app.storage.sqlite_registry import SQLiteRegistry

router = APIRouter(prefix="/facts", tags=["facts"], dependencies=[Depends(require_auth)])


class FactOut(BaseModel):
    id: str
    category: str
    content: str
    created_at: str


@router.get("", response_model=List[FactOut])
async def list_facts(
    category: Optional[str] = None,
    registry: SQLiteRegistry = Depends(get_registry),
) -> List[FactOut]:
    rows = registry.list_facts(category=category)
    return [
        FactOut(
            id=r["fact_id"],
            category=r["category"],
            content=r["content"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


@router.delete("/{fact_id}")
async def delete_fact(
    fact_id: str,
    registry: SQLiteRegistry = Depends(get_registry),
) -> dict:
    registry.delete_fact(fact_id)
    return {"ok": True}
