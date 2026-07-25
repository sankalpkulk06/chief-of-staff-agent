from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry
from app.core.profile_service import build_profile, delete_profile_and_data

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileOut(BaseModel):
    username: str
    joined: Optional[str]
    days_active: int
    total_sessions: int
    facts_personal: int
    facts_work: int
    longest_streak: int
    longest_streak_habit: str
    total_docs: int
    total_chunks: int


class DeleteProfileRequest(BaseModel):
    username: str
    password: str


class DeleteProfileOut(BaseModel):
    ok: bool
    deleted: Dict[str, int]


@router.get("", response_model=ProfileOut)
async def get_profile(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> ProfileOut:
    return ProfileOut(**build_profile(registry, current_user["user_id"], current_user["username"]))


@router.delete("", response_model=DeleteProfileOut)
async def delete_profile(
    body: DeleteProfileRequest,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> DeleteProfileOut:
    username = body.username.strip()
    if username != current_user["username"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username does not match the signed-in profile",
        )

    verified = registry.verify_password(username, body.password)
    if verified is None or verified["user_id"] != current_user["user_id"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    deleted = delete_profile_and_data(registry, current_user["user_id"])
    return DeleteProfileOut(ok=True, deleted=deleted)
