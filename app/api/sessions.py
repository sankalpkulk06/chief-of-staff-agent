import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_chat_service, get_current_user, get_registry
from app.core.chat_service import ChatService

router = APIRouter(prefix="/sessions", tags=["sessions"])

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    role: str        # "user" | "assistant"
    content: str
    created_at: str


class SourceOut(BaseModel):
    document_id: str
    file_name: Optional[str] = None
    source_url: Optional[str] = None
    source_type: str


class StepOut(BaseModel):
    agent: str
    task: str
    success: bool
    error: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    sources: List[SourceOut] = []
    steps: List[StepOut] = []
    latency_ms: int
    hitl_pending: bool = False
    hitl_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class PatchSessionRequest(BaseModel):
    title: str


class ChatRequest(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=List[SessionSummary])
async def list_sessions(
    limit: int = 20,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> List[SessionSummary]:
    rows = registry.list_sessions(limit=limit, user_id=current_user["user_id"])
    return [
        SessionSummary(
            id=r["session_id"],
            title=r["title"] or "Untitled",
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]


@router.post("", response_model=SessionSummary, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest = CreateSessionRequest(),
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> SessionSummary:
    session_id = str(uuid.uuid4())
    title = body.title or ""
    registry.create_session(session_id=session_id, title=title, user_id=current_user["user_id"])
    rows = registry.list_sessions(limit=100, user_id=current_user["user_id"])
    row = next((r for r in rows if r["session_id"] == session_id), None)
    if row is None:
        raise HTTPException(status_code=500, detail="Session creation failed")
    return SessionSummary(
        id=row["session_id"],
        title=row["title"] or "Untitled",
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


@router.get("/{session_id}/messages", response_model=List[MessageOut])
async def get_messages(
    session_id: str,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> List[MessageOut]:
    turns = registry.get_session_turns(session_id)
    if turns is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return [
        MessageOut(role=t["role"], content=t["content"], created_at=str(t["created_at"]))
        for t in turns
    ]


@router.patch("/{session_id}", response_model=SessionSummary)
async def update_session_title(
    session_id: str,
    body: PatchSessionRequest,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> SessionSummary:
    registry.update_session_title(session_id=session_id, title=body.title)
    rows = registry.list_sessions(limit=200, user_id=current_user["user_id"])
    row = next((r for r in rows if r["session_id"] == session_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionSummary(
        id=row["session_id"],
        title=row["title"] or "Untitled",
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


@router.post("/{session_id}/generate-title")
async def generate_title(
    session_id: str,
    chat_service: ChatService = Depends(get_chat_service),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    title = chat_service.generate_session_title(session_id)
    return {"title": title}


@router.delete("/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(
    session_id: str,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> dict:
    registry.delete_session(session_id=session_id)
    return {"ok": True}


@router.post("/{session_id}/chat", response_model=ChatResponse)
async def chat(
    session_id: str,
    body: ChatRequest,
    chat_service: ChatService = Depends(get_chat_service),
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> ChatResponse:
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message must not be empty")

    registry.create_session(session_id=session_id, title="", user_id=current_user["user_id"])

    t0 = time.monotonic()
    result = chat_service.answer_in_session(
        session_id=session_id,
        question=body.message,
        response_style="web",
        user_id=current_user["user_id"],
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    sources = [
        SourceOut(
            document_id=s.document_id,
            file_name=getattr(s, "file_name", None),
            source_url=getattr(s, "source_url", None),
            source_type=getattr(s, "source_type", "file"),
        )
        for s in result.sources
    ]

    steps = [
        StepOut(
            agent=s["agent"],
            task=s["task"],
            success=s["success"],
            error=s.get("error"),
        )
        for s in result.steps
    ]

    return ChatResponse(
        reply=result.answer,
        sources=sources,
        steps=steps,
        latency_ms=latency_ms,
        hitl_pending=result.hitl_pending,
        hitl_id=result.hitl_id,
    )
