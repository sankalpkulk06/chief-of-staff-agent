from typing import Any, Dict

from fastapi import Header, HTTPException, Request, status

from app.core.chat_service import ChatService

_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid credentials",
    headers={"WWW-Authenticate": "Basic"},
)


def get_chat_service(request: Request) -> ChatService:
    svc = getattr(request.app.state, "chat_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Chat service not initialised")
    return svc


def get_registry(request: Request) -> Any:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Registry not initialised")
    return registry


def get_current_user(
    request: Request,
    x_sage_username: str = Header(default=""),
    x_sage_key: str = Header(default=""),
) -> Dict[str, str]:
    """Validate username + password on every authenticated request."""
    registry = get_registry(request)
    if not x_sage_username or not x_sage_key:
        raise _UNAUTH
    user = registry.verify_password(x_sage_username, x_sage_key)
    if user is None:
        raise _UNAUTH
    return user  # {"user_id": "...", "username": "..."}


def require_auth(user: Dict = None) -> None:
    """Kept for router-level dependency declarations; real auth is in get_current_user."""
    pass
