from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry

router = APIRouter(prefix="/todos", tags=["todos"])


class TodoOut(BaseModel):
    id: str
    title: str
    list_name: Optional[str] = None
    due_at: Optional[str] = None
    created_at: Optional[str] = None


class CreateTodoRequest(BaseModel):
    title: str
    list_name: Optional[str] = None
    due_at: Optional[str] = None  # ISO 8601 string, e.g. "2026-05-20T09:00:00"


def _serialize(row: dict) -> TodoOut:
    return TodoOut(
        id=str(row["id"]),
        title=str(row["title"]),
        list_name=str(row["list_name"]) if row.get("list_name") else None,
        due_at=str(row["due_at"]) if row.get("due_at") else None,
        created_at=str(row["created_at"]) if row.get("created_at") else None,
    )


@router.get("", response_model=List[TodoOut])
async def list_todos(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> List[TodoOut]:
    rows = registry.list_todos(user_id=current_user["user_id"])
    return [_serialize(r) for r in rows]


@router.post("", response_model=TodoOut, status_code=status.HTTP_201_CREATED)
async def create_todo(
    body: CreateTodoRequest,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> TodoOut:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")

    due_at: Optional[datetime] = None
    if body.due_at:
        try:
            due_at = datetime.fromisoformat(body.due_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="due_at must be ISO 8601 format")

    row = registry.create_todo(
        title=title,
        list_name=body.list_name or None,
        due_at=due_at,
        user_id=current_user["user_id"],
    )
    return _serialize(row)


@router.patch("/{todo_id}/complete", status_code=status.HTTP_200_OK)
async def complete_todo(
    todo_id: str,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    row = registry.get_todo(todo_id)
    if not row or str(row.get("user_id", "")) != current_user["user_id"]:
        raise HTTPException(status_code=404, detail="Todo not found")
    registry.mark_todo_completed(todo_id)
    return {"ok": True}


@router.delete("/{todo_id}", status_code=status.HTTP_200_OK)
async def delete_todo(
    todo_id: str,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    deleted = registry.delete_todo(todo_id, user_id=current_user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"ok": True}
