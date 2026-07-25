"""HITL (Human-in-the-Loop) resolve endpoint."""
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry

router = APIRouter(prefix="/hitl", tags=["hitl"])


class ResolveRequest(BaseModel):
    approved: bool


@router.post("/{hitl_id}/resolve")
def resolve_hitl(
    request: Request,
    hitl_id: str,
    body: ResolveRequest,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    row = registry.get_hitl_request(hitl_id)

    # 404 for both not-found and wrong-user to avoid leaking existence
    if not row or row["user_id"] != current_user["user_id"]:
        raise HTTPException(status_code=404, detail="HITL request not found")

    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="HITL request already resolved")

    expires_at = row.get("expires_at")
    if expires_at and datetime.now(timezone.utc) > expires_at:
        registry.resolve_hitl_request(hitl_id, "expired")
        raise HTTPException(status_code=410, detail="HITL request expired — action was not taken")

    if body.approved:
        from app.agents.hitl_dispatch import execute_approved_by_type

        context = (row.get("action_payload") or {}).get("__hitl_context") or {}
        result = execute_approved_by_type(
            row,
            current_user["user_id"],
            registry=registry,
            schedule_todo_callback=getattr(request.app.state, "schedule_todo_callback", None),
        )
        registry.resolve_hitl_request(hitl_id, "approved")
        final_reply = result.output
        continuation = context.get("continuation_output")
        if continuation:
            final_reply = f"{result.output}\n\n{continuation}" if result.output else continuation
        return {
            "status": "approved",
            "output": result.output,
            "final_reply": final_reply,
            "success": result.success,
        }

    context = (row.get("action_payload") or {}).get("__hitl_context") or {}
    registry.resolve_hitl_request(hitl_id, "rejected")
    continuation = context.get("continuation_output")
    final_reply = f"Rejected — action was not taken.\n\n{continuation}" if continuation else "Rejected — action was not taken."
    return {"status": "rejected", "final_reply": final_reply}
