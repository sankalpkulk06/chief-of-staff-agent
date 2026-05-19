"""HITL (Human-in-the-Loop) resolve endpoint."""
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.agents.action_agent import ActionAgent
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
        from app.config import get_settings
        from app.providers.factory import create_chat_provider, agent_model_specs

        settings = get_settings()
        specs = agent_model_specs(settings)
        chat_provider = create_chat_provider(settings, specs["action_agent"])

        agent = ActionAgent(
            chat_provider=chat_provider,
            registry=registry,
            schedule_todo_callback=getattr(request.app.state, "schedule_todo_callback", None),
        )
        result = agent.execute_approved(hitl_id, current_user["user_id"])
        registry.resolve_hitl_request(hitl_id, "approved")
        return {"status": "approved", "output": result.output, "success": result.success}

    registry.resolve_hitl_request(hitl_id, "rejected")
    return {"status": "rejected"}
