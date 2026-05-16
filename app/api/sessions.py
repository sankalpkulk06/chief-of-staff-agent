import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_chat_service, get_registry, require_auth
from app.core.chat_service import ChatService
from app.storage.sqlite_registry import SQLiteRegistry

router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    dependencies=[Depends(require_auth)],
)

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


class ChatResponse(BaseModel):
    reply: str
    sources: List[SourceOut] = []
    latency_ms: int


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
    registry: SQLiteRegistry = Depends(get_registry),
) -> List[SessionSummary]:
    """List recent chat sessions, newest first."""
    rows = registry.list_sessions(limit=limit)
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
    registry: SQLiteRegistry = Depends(get_registry),
) -> SessionSummary:
    """Create a new chat session and return it."""
    session_id = str(uuid.uuid4())
    title = body.title or ""
    registry.create_session(session_id=session_id, title=title)
    rows = registry.list_sessions(limit=100)
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
    registry: SQLiteRegistry = Depends(get_registry),
) -> List[MessageOut]:
    """Return all turns for a session in chronological order."""
    turns = registry.get_session_turns(session_id)
    if turns is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return [
        MessageOut(
            role=t["role"],
            content=t["content"],
            created_at=str(t["created_at"]),
        )
        for t in turns
    ]


@router.patch("/{session_id}", response_model=SessionSummary)
async def update_session_title(
    session_id: str,
    body: PatchSessionRequest,
    registry: SQLiteRegistry = Depends(get_registry),
) -> SessionSummary:
    """Rename a session."""
    registry.update_session_title(session_id=session_id, title=body.title)
    rows = registry.list_sessions(limit=200)
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
) -> dict:
    """Ask the LLM to summarise the session in ≤5 words and persist the result."""
    title = chat_service.generate_session_title(session_id)
    return {"title": title}


@router.delete("/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(
    session_id: str,
    registry: SQLiteRegistry = Depends(get_registry),
) -> dict:
    """Delete a session and all its messages."""
    registry.delete_session(session_id=session_id)
    return {"ok": True}


@router.post("/{session_id}/chat", response_model=ChatResponse)
async def chat(
    session_id: str,
    body: ChatRequest,
    chat_service: ChatService = Depends(get_chat_service),
    registry: SQLiteRegistry = Depends(get_registry),
) -> ChatResponse:
    """Send a message in a session and get Sage's reply.

    Creates the session row if it doesn't yet exist (e.g. the client called
    POST /sessions then immediately sends a message).
    """
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message must not be empty")

    # create_session is INSERT OR IGNORE — safe to call even if it already exists
    registry.create_session(session_id=session_id, title="")

    t0 = time.monotonic()
    result = chat_service.answer_in_session(
        session_id=session_id,
        question=body.message,
        response_style="web",
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

    return ChatResponse(
        reply=result.answer,
        sources=sources,
        latency_ms=latency_ms,
    )
