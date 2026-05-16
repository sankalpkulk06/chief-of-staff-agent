from fastapi import Depends, Header, HTTPException, Request, status

from app.config import get_settings
from app.core.chat_service import ChatService
from app.storage.sqlite_registry import SQLiteRegistry


def get_chat_service(request: Request) -> ChatService:
    svc = getattr(request.app.state, "chat_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Chat service not initialised")
    return svc


def get_registry(request: Request) -> SQLiteRegistry:
    svc = get_chat_service(request)
    return svc.get_registry()


def require_auth(x_sage_key: str = Header(default="")) -> None:
    passphrase = get_settings().sage_passphrase
    if not passphrase:
        return  # auth disabled when no passphrase configured
    if x_sage_key != passphrase:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Sage-Key header",
        )
