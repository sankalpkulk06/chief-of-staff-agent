import asyncio
import json
import time
import uuid
import re
import shutil
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.trace import broker as trace_broker
from app.api.deps import get_chat_service, get_current_user, get_registry
from app.config import get_settings
from app.core.chat_service import ChatService
from app.core.ingest_coordinator import IngestCoordinator
from app.ingestion.chunker import Chunker, ChunkingConfig
from app.ingestion.ingest_service import IngestService
from app.parsers.router import ParserRouter
from app.providers.factory import create_embeddings_provider
from app.providers.ollama_embeddings import OllamaProviderError
from app.storage.factory import create_vector_store

router = APIRouter(prefix="/sessions", tags=["sessions"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

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


class RagasOut(BaseModel):
    faithfulness: Optional[float] = None
    answer_relevancy: Optional[float] = None
    evaluated: bool
    contexts_count: int
    error: Optional[str] = None


class HitlItemOut(BaseModel):
    id: str
    summary: str
    action_type: str = ""


class ChatResponse(BaseModel):
    reply: str
    sources: List[SourceOut] = []
    steps: List[StepOut] = []
    latency_ms: int
    hitl_pending: bool = False
    hitl_id: Optional[str] = None
    hitl_items: List[HitlItemOut] = []
    ragas: Optional[RagasOut] = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class PatchSessionRequest(BaseModel):
    title: str


class ChatRequest(BaseModel):
    message: str
    trace_id: Optional[str] = None


def _safe_upload_name(filename: str) -> str:
    name = Path(filename or "upload.txt").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return safe or "upload.txt"


def _build_ingest_coordinator(registry: Any) -> IngestCoordinator:
    settings = get_settings()
    paths = settings.resolve_paths()
    parser_router = ParserRouter()
    return IngestCoordinator(
        ingest_service=IngestService(
            parser_router=parser_router,
            chunker=Chunker(
                ChunkingConfig(
                    chunk_size=settings.chunk_size,
                    chunk_overlap=settings.chunk_overlap,
                )
            ),
        ),
        embeddings_provider=create_embeddings_provider(settings),
        registry=registry,
        vector_store=create_vector_store(
            settings.database_url,
            paths.chroma_dir,
            settings.embedding_dimension,
        ),
        supported_extensions=parser_router.supported_extensions,
    )


def _record_upload_exchange(
    registry: Any,
    session_id: str,
    filename: str,
    reply: str,
) -> None:
    history = registry.get_session_turns(session_id)
    turn_index = len(history)
    registry.append_turn(
        session_id=session_id,
        turn_id=str(uuid.uuid4()),
        role="user",
        content=f"Uploaded document: {filename}",
        turn_index=turn_index,
    )
    registry.append_turn(
        session_id=session_id,
        turn_id=str(uuid.uuid4()),
        role="assistant",
        content=reply,
        turn_index=turn_index + 1,
    )


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


# ---------------------------------------------------------------------------
# Trace endpoints — live agent event streaming
# ---------------------------------------------------------------------------


@router.post("/{session_id}/trace/start")
async def trace_start(
    session_id: str,
    current_user: Dict = Depends(get_current_user),
) -> dict:
    """Create a trace slot and return a trace_id to pass into the chat request."""
    trace_id = uuid.uuid4().hex[:16]
    loop = asyncio.get_event_loop()
    trace_broker.create(trace_id, loop)
    return {"trace_id": trace_id}


@router.get("/{session_id}/trace/{trace_id}/stream")
async def trace_stream(
    session_id: str,
    trace_id: str,
    current_user: Dict = Depends(get_current_user),
) -> StreamingResponse:
    """SSE endpoint — streams TraceEvents until the pipeline completes."""

    async def _generator() -> AsyncGenerator[str, None]:
        async for event in trace_broker.subscribe(trace_id):
            yield f"data: {json.dumps(event.to_dict())}\n\n"
        yield "data: {\"type\":\"stream_end\"}\n\n"

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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

    trace_id = body.trace_id
    trace_cb = trace_broker.make_callback(trace_id) if trace_id else None

    t0 = time.monotonic()
    try:
        if trace_id:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: chat_service.answer_in_session(
                    session_id=session_id,
                    question=body.message,
                    response_style="web",
                    user_id=current_user["user_id"],
                    trace_callback=trace_cb,
                ),
            )
            trace_broker.complete(trace_id)
        else:
            result = chat_service.answer_in_session(
                session_id=session_id,
                question=body.message,
                response_style="web",
                user_id=current_user["user_id"],
            )
    except OllamaProviderError as exc:
        if trace_id:
            trace_broker.complete(trace_id)
        latency_ms = int((time.monotonic() - t0) * 1000)
        detail = str(exc).lower()
        if "429" in detail or "quota" in detail or "resource_exhausted" in detail:
            reply = (
                "The model provider is rate-limited or out of quota right now. "
                "Wait a bit and retry, or switch Sage to another chat model."
            )
        else:
            reply = "The model provider failed while generating a response. Try again in a moment."
        return ChatResponse(
            reply=reply,
            sources=[],
            steps=[
                StepOut(
                    agent="provider",
                    task="generate response",
                    success=False,
                    error=str(exc).splitlines()[0][:240],
                )
            ],
            latency_ms=latency_ms,
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

    ragas_out = None
    if result.ragas_result is not None:
        r = result.ragas_result
        ragas_out = RagasOut(
            faithfulness=r.faithfulness,
            answer_relevancy=r.answer_relevancy,
            evaluated=r.evaluated,
            contexts_count=r.contexts_count,
            error=r.error,
        )

    return ChatResponse(
        reply=result.answer,
        sources=sources,
        steps=steps,
        latency_ms=latency_ms,
        hitl_pending=result.hitl_pending,
        hitl_id=result.hitl_id,
        hitl_items=[
            HitlItemOut(id=it["id"], summary=it.get("summary", ""),
                        action_type=it.get("action_type", ""))
            for it in (getattr(result, "hitl_items", None) or [])
        ],
        ragas=ragas_out,
    )


@router.post("/{session_id}/upload", response_model=ChatResponse)
async def upload_document(
    session_id: str,
    file: UploadFile = File(...),
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> ChatResponse:
    filename = _safe_upload_name(file.filename or "upload")
    suffix = Path(filename).suffix.lower()
    parser_router = ParserRouter()
    if suffix not in parser_router.supported_extensions:
        supported = ", ".join(parser_router.supported_extensions)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type '{suffix or '(none)'}'. Supported: {supported}",
        )

    registry.create_session(session_id=session_id, title="", user_id=current_user["user_id"])

    settings = get_settings()
    paths = settings.resolve_paths()
    upload_dir = paths.data_dir / "uploads" / current_user["user_id"] / uuid.uuid4().hex
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_path = upload_dir / filename

    t0 = time.monotonic()
    total = 0
    try:
        with stored_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Upload is too large. Max file size is 25 MB.",
                    )
                out.write(chunk)

        coordinator = _build_ingest_coordinator(registry)
        virtual_source_path = Path("/uploads") / current_user["user_id"] / filename
        try:
            summary = coordinator.ingest(
                stored_path,
                user_id=current_user["user_id"],
                source_type="upload",
                source_path_override=virtual_source_path,
                extra_metadata={
                    "source_type": "upload",
                    "original_filename": filename,
                    "uploaded_via": "chat",
                },
            )
        finally:
            vector_store = getattr(coordinator, "_vector_store", None)
            close = getattr(vector_store, "close", None)
            if callable(close):
                close()
    except HTTPException:
        if stored_path.exists():
            stored_path.unlink()
        raise
    except OllamaProviderError as exc:
        if stored_path.exists():
            stored_path.unlink()
        raise HTTPException(status_code=503, detail=f"Embedding provider failed: {exc}") from exc
    except Exception as exc:
        if stored_path.exists():
            stored_path.unlink()
        raise HTTPException(status_code=500, detail=f"Upload ingestion failed: {exc}") from exc
    finally:
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)
        user_upload_dir = upload_dir.parent
        try:
            user_upload_dir.rmdir()
        except OSError:
            pass

    latency_ms = int((time.monotonic() - t0) * 1000)
    if summary.files_processed:
        reply = (
            f"Uploaded and indexed `{filename}`. "
            f"Created {summary.chunks_created} chunk"
            f"{'' if summary.chunks_created == 1 else 's'} in the knowledge base."
        )
    elif summary.files_skipped and summary.warnings:
        reply = f"`{filename}` was already indexed, so I skipped re-ingesting it."
    else:
        detail = "; ".join(summary.errors or summary.warnings) or "No chunks were created."
        reply = f"I couldn't index `{filename}`. {detail}"

    _record_upload_exchange(registry, session_id, filename, reply)

    return ChatResponse(
        reply=reply,
        sources=[],
        steps=[
            StepOut(
                agent="ingest",
                task=f"upload document: {filename}",
                success=summary.files_processed > 0 or summary.files_skipped > 0,
                error="; ".join(summary.errors) if summary.errors else None,
            )
        ],
        latency_ms=latency_ms,
    )
