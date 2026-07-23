"""Google Calendar + Tasks OAuth connect/disconnect and status endpoints.

Mirrors app/api/email.py but requests the combined Calendar+Tasks scopes and stores
the token under account_type='google_calendar' (separate from the Gmail token).
"""
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.deps import get_current_user, get_registry
from app.config import get_settings
from app.config.settings import get_google_client_secrets
from app.services.calendar_service import CalendarService

router = APIRouter(prefix="/calendar", tags=["calendar"])

CALENDAR_ACCOUNT_TYPE = "google_calendar"


def _redirect_uri(request: Request) -> str:
    settings = get_settings()
    base = settings.sage_public_url.rstrip("/") if settings.sage_public_url else str(request.base_url).rstrip("/")
    return f"{base}/api/v1/calendar/callback"


def _get_calendar_service() -> CalendarService:
    settings = get_settings()
    secrets = get_google_client_secrets(settings)
    if not secrets:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured on this server.")
    return CalendarService(client_secrets=secrets)


@router.get("/status")
def calendar_status(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    return {"connected": registry.has_email_token(current_user["user_id"], CALENDAR_ACCOUNT_TYPE)}


@router.get("/oauth/start")
def oauth_start(
    request: Request,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    calendar_service = _get_calendar_service()
    state = str(uuid.uuid4())
    registry.store_oauth_state(state, current_user["user_id"])
    auth_url = calendar_service.get_oauth_url(redirect_uri=_redirect_uri(request), state=state)
    return {"auth_url": auth_url}


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

    calendar_service = _get_calendar_service()
    try:
        token_json = calendar_service.exchange_code(
            code=code, redirect_uri=_redirect_uri(request), state=state
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to exchange OAuth code: {exc}")

    registry.upsert_email_token(user_id, token_json, CALENDAR_ACCOUNT_TYPE)

    settings = get_settings()
    base = settings.sage_public_url.rstrip("/") if settings.sage_public_url else str(request.base_url).rstrip("/")
    return RedirectResponse(url=f"{base}/?calendar_connected=1")


@router.delete("/disconnect")
def disconnect_calendar(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    registry.delete_email_token(current_user["user_id"], CALENDAR_ACCOUNT_TYPE)
    return {"ok": True}
