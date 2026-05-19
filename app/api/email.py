"""Gmail OAuth connect/disconnect and connection-status endpoints."""
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.deps import get_current_user, get_registry
from app.config import get_settings
from app.config.settings import get_google_client_secrets
from app.services.email_service import EmailService

router = APIRouter(prefix="/email", tags=["email"])


def _redirect_uri(request: Request) -> str:
    settings = get_settings()
    base = settings.sage_public_url.rstrip("/") if settings.sage_public_url else str(request.base_url).rstrip("/")
    return f"{base}/api/v1/email/callback"


def _get_email_service() -> EmailService:
    settings = get_settings()
    secrets = get_google_client_secrets(settings)
    if not secrets:
        raise HTTPException(status_code=503, detail="Gmail OAuth is not configured on this server.")
    return EmailService(client_secrets=secrets)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status")
def email_status(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    connected = registry.has_email_token(current_user["user_id"])
    return {"connected": connected}


# ---------------------------------------------------------------------------
# OAuth start — redirect user to Google consent screen
# ---------------------------------------------------------------------------

@router.get("/oauth/start")
def oauth_start(
    request: Request,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    email_service = _get_email_service()
    state = str(uuid.uuid4())
    registry.store_oauth_state(state, current_user["user_id"])
    auth_url = email_service.get_oauth_url(
        redirect_uri=_redirect_uri(request),
        state=state,
    )
    return {"auth_url": auth_url}


# ---------------------------------------------------------------------------
# OAuth callback — Google redirects here after consent
# ---------------------------------------------------------------------------

@router.get("/callback")
def oauth_callback(
    request: Request,
    code: str,
    state: str,
    registry: Any = Depends(get_registry),
) -> RedirectResponse:
    user_id = registry.pop_oauth_state(state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state. Please try connecting again.")

    email_service = _get_email_service()
    try:
        token_json = email_service.exchange_code(
            code=code,
            redirect_uri=_redirect_uri(request),
            state=state,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to exchange OAuth code: {exc}")

    registry.upsert_email_token(user_id, token_json)

    # Redirect back to the chat UI with a success flag
    settings = get_settings()
    base = settings.sage_public_url.rstrip("/") if settings.sage_public_url else str(request.base_url).rstrip("/")
    return RedirectResponse(url=f"{base}/?email_connected=1")


# ---------------------------------------------------------------------------
# Disconnect — remove stored token
# ---------------------------------------------------------------------------

@router.delete("/disconnect")
def disconnect_email(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    registry.delete_email_token(current_user["user_id"])
    return {"ok": True}
