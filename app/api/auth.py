from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    passphrase: str


class LoginResponse(BaseModel):
    ok: bool


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    """Validate the local passphrase. Returns 200 on success, 401 on mismatch.

    If no SAGE_PASSPHRASE is configured, any passphrase is accepted.
    """
    configured = get_settings().sage_passphrase
    if configured and body.passphrase != configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect passphrase",
        )
    return LoginResponse(ok=True)
