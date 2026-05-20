import datetime
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry

router = APIRouter(prefix="/facts", tags=["facts"])


class FactOut(BaseModel):
    id: str
    category: str
    content: str
    created_at: str


class FactIn(BaseModel):
    content: str
    category: str  # "personal" | "work"


@router.get("", response_model=List[FactOut])
async def list_facts(
    category: Optional[str] = None,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> List[FactOut]:
    rows = registry.list_facts(category=category, user_id=current_user["user_id"])
    return [
        FactOut(
            id=r["fact_id"],
            category=r["category"],
            content=r["content"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


@router.post("", response_model=FactOut)
async def create_fact(
    payload: FactIn,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> FactOut:
    fact_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow()
    registry.insert_fact(
        fact_id=fact_id,
        content=payload.content,
        category=payload.category,
        source="user",
        user_id=current_user["user_id"],
    )
    return FactOut(
        id=fact_id,
        category=payload.category,
        content=payload.content,
        created_at=str(now),
    )


@router.delete("/{fact_id}")
async def delete_fact(
    fact_id: str,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    registry.delete_fact(fact_id, user_id=current_user["user_id"])
    return {"ok": True}
