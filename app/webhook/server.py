import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.router import api_router
from app.cli.commands_ask import create_chat_service, create_news_service
from app.config import get_settings
from app.core.habit_service import HabitService
from app.scheduler.scheduler import build_scheduler, schedule_todo_reminder
from app.storage.sqlite_registry import SQLiteRegistry
from app.services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

_chat_service = None
_registry = None
_whatsapp_service: Optional[WhatsAppService] = None
_habit_service: Optional[HabitService] = None

REPLY_MAP = {
    "done": "done",
    "yeah": "done",
    "yep": "done",
    "did it": "done",
    "skipped": "skipped",
    "nope": "skipped",
    "skip": "skipped",
    "no": "skipped",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _chat_service, _registry, _whatsapp_service, _habit_service
    settings = get_settings()
    paths = settings.resolve_paths()

    _registry = SQLiteRegistry(paths.sqlite_db_path)

    if (
        settings.whatsapp_enabled
        and settings.twilio_account_sid
        and settings.twilio_auth_token
    ):
        _whatsapp_service = WhatsAppService(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_whatsapp_number,
            usage_registry=_registry,
            usage_alert_to=settings.your_whatsapp_number,
            daily_message_limit=settings.twilio_daily_message_limit,
        )
    else:
        logger.warning(
            "Twilio credentials not set or WHATSAPP_ENABLED=false — "
            "/health still serves but messages will not be sent"
        )

    def schedule_created_todo(todo: dict) -> None:
        if (
            hasattr(app.state, "scheduler")
            and _whatsapp_service
            and settings.your_whatsapp_number
        ):
            schedule_todo_reminder(
                app.state.scheduler,
                todo,
                _whatsapp_service,
                _registry,
                settings.your_whatsapp_number,
            )

    _chat_service = create_chat_service(schedule_todo_callback=schedule_created_todo)
    _habit_service = _chat_service.get_habit_service() or HabitService(_registry)

    if (
        settings.scheduler_enabled
        and _whatsapp_service
        and settings.your_whatsapp_number
    ):
        scheduler = build_scheduler(
            habit_service=_habit_service,
            whatsapp_service=_whatsapp_service,
            news_service=create_news_service(),
            registry=_registry,
            your_number=settings.your_whatsapp_number,
            morning_briefing_time=settings.morning_briefing_time,
            habit_nudge_time=settings.habit_nudge_time,
        )
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Scheduler started")
    else:
        logger.info("Scheduler disabled or missing WhatsApp destination/config")

    app.state.chat_service = _chat_service

    try:
        yield
    finally:
        if hasattr(app.state, "scheduler"):
            app.state.scheduler.shutdown(wait=False)
        if _registry:
            _registry.close()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # local network — tighten if exposed beyond LAN
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

_frontend_index = Path(__file__).resolve().parents[2] / "frontend" / "index.html"


@app.get("/")
async def serve_frontend() -> FileResponse:
    """Serve the Sage web UI."""
    if not _frontend_index.exists():
        raise HTTPException(status_code=404, detail="Frontend not built")
    return FileResponse(str(_frontend_index), media_type="text/html")


@app.post("/webhook")
async def webhook(
    From: Optional[str] = Form(None),
    Body: str = Form(""),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None),
):
    if not From:
        raise HTTPException(status_code=400, detail="Missing From field")

    phone = From

    pending_habit_id = _registry.get_nudge_context(phone)
    body_lower = Body.strip().lower()
    if pending_habit_id and body_lower in REPLY_MAP and _habit_service:
        status = REPLY_MAP[body_lower]
        habit = _habit_service.get_habit_by_id(pending_habit_id)
        if habit:
            _habit_service.log_habit_by_id(pending_habit_id, status=status)
            _registry.clear_nudge_context(phone)
            reply = f"Logged *{habit.name}* as {status} for today!"
            if _whatsapp_service:
                _safe_send(phone, reply)
            _registry.update_whatsapp_last_active(phone)
            return Response(content="", media_type="application/xml")

    session_id = _registry.get_or_create_whatsapp_session(phone)

    result = _chat_service.answer_in_session(
        session_id=session_id,
        question=Body,
        response_style="whatsapp",
    )
    reply = result.answer

    if _whatsapp_service:
        _safe_send(phone, reply)
    else:
        logger.warning("WhatsApp service unavailable; reply not sent to %s", phone)

    _registry.update_whatsapp_last_active(phone)

    return Response(content="", media_type="application/xml")


@app.get("/health")
async def health():
    return {"status": "ok"}


def _safe_send(to: str, body: str) -> bool:
    if not _whatsapp_service:
        return False
    try:
        _whatsapp_service.send_message(to=to, body=body)
        return True
    except Exception:
        logger.exception("Failed to send WhatsApp reply to %s", to)
        return False
