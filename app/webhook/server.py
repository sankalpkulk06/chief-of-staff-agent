import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from twilio.request_validator import RequestValidator

from app.api.router import api_router
from app.cli.commands_ask import create_chat_service, create_news_service
from app.config import get_settings
from app.config.validation import validate_runtime_configuration
from app.core.habit_service import HabitService
from app.scheduler.scheduler import build_scheduler, schedule_todo_reminder
from app.storage.factory import create_registry
from app.services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

_chat_service = None
_registry = None
_whatsapp_service: Optional[WhatsAppService] = None
_habit_service: Optional[HabitService] = None
_whatsapp_user_id: Optional[str] = None

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


async def _validate_twilio_signature(
    request: Request,
    x_twilio_signature: str = Header(default=""),
) -> None:
    settings = get_settings()
    if not settings.twilio_auth_token:
        return
    validator = RequestValidator(settings.twilio_auth_token)
    form = await request.form()
    params = dict(form)
    if settings.sage_public_url:
        url = f"{settings.sage_public_url.rstrip('/')}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
    else:
        url = str(request.url)
    if not validator.validate(url, params, x_twilio_signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _chat_service, _registry, _whatsapp_service, _habit_service, _whatsapp_user_id
    settings = get_settings()
    paths = settings.resolve_paths()
    validate_runtime_configuration(settings)

    _registry = create_registry(settings.database_url, paths.sqlite_db_path)

    # Seed default user from settings if not already in DB
    username = (settings.sage_username or "sage").strip()
    password = settings.sage_passphrase.strip()
    if username and password:
        existing = _registry.get_user_by_username(username)
        if not existing:
            _registry.create_user(username, password)
            logger.info("Created default user '%s'", username)
        elif not _registry.verify_password(username, password):
            logger.warning(
                "SAGE_PASSPHRASE changed but user '%s' already exists — "
                "password NOT updated. Remove the user row manually to reset.",
                username,
            )

    app.state.registry = _registry

    # Resolve which user owns incoming WhatsApp messages
    if username:
        user_row = _registry.get_user_by_username(username)
        if user_row:
            _whatsapp_user_id = user_row["user_id"]
            logger.info("WhatsApp messages will be owned by user '%s' (%s)", username, _whatsapp_user_id)
        else:
            logger.warning(
                "SAGE_USERNAME='%s' not found in DB — WhatsApp messages will NOT be "
                "associated with any user. Ensure SAGE_PASSPHRASE is set so the user "
                "is auto-seeded on startup.",
                username,
            )
    else:
        logger.warning(
            "SAGE_USERNAME is not set — WhatsApp messages will NOT be associated with "
            "any user. Set SAGE_USERNAME in your environment."
        )

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
            try:
                schedule_todo_reminder(
                    app.state.scheduler,
                    todo,
                    _whatsapp_service,
                    _registry,
                    settings.your_whatsapp_number,
                )
            except Exception:
                logger.exception("Failed to schedule todo reminder for todo %s", todo.get("id"))

    app.state.schedule_todo_callback = schedule_created_todo
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
    app.state.registry = _registry

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


@app.post("/webhook", dependencies=[Depends(_validate_twilio_signature)])
async def webhook(
    From: Optional[str] = Form(None),
    Body: str = Form(""),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None),
):
    if not From:
        raise HTTPException(status_code=400, detail="Missing From field")

    if not _whatsapp_user_id:
        logger.error(
            "Received WhatsApp message from %s but no user is configured. "
            "Set SAGE_USERNAME and SAGE_PASSPHRASE so a user exists at startup.",
            From,
        )
        return Response(content="", media_type="application/xml")

    phone = From
    body_lower = Body.strip().lower()

    # HITL approval — check before habit nudge so yes/no isn't swallowed by habit logic
    pending_hitl_id = _registry.get_whatsapp_hitl_context(phone)
    if pending_hitl_id:
        if body_lower in ("yes", "y", "approve", "confirm"):
            reply = _resolve_hitl_whatsapp(pending_hitl_id, approved=True)
        elif body_lower in ("no", "n", "reject", "cancel"):
            reply = _resolve_hitl_whatsapp(pending_hitl_id, approved=False)
        else:
            reply = None  # not a yes/no — fall through to normal chat

        if reply is not None:
            _registry.clear_whatsapp_hitl_context(phone)
            if _whatsapp_service:
                _safe_send(phone, reply)
            _registry.update_whatsapp_last_active(phone)
            return Response(content="", media_type="application/xml")

    # Habit nudge fast-reply
    pending_habit_id = _registry.get_nudge_context(phone)
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

    session_id = _registry.get_or_create_whatsapp_session(phone, user_id=_whatsapp_user_id)

    result = _chat_service.answer_in_session(
        session_id=session_id,
        question=Body,
        response_style="whatsapp",
        user_id=_whatsapp_user_id,
    )
    reply = result.answer

    # If the agent raised a HITL request, store it so the next yes/no resolves it
    if result.hitl_pending and result.hitl_id:
        _registry.set_whatsapp_hitl_context(phone, result.hitl_id)
        reply = f"{reply}\n\nReply *yes* to confirm or *no* to cancel."

    if _whatsapp_service:
        _safe_send(phone, reply)
    else:
        logger.warning("WhatsApp service unavailable; reply not sent to %s", phone)

    _registry.update_whatsapp_last_active(phone)

    return Response(content="", media_type="application/xml")


@app.get("/health")
async def health():
    return {"status": "ok"}


def _resolve_hitl_whatsapp(hitl_id: str, approved: bool) -> str:
    from datetime import datetime, timezone
    from app.providers.factory import create_chat_provider, agent_model_specs

    row = _registry.get_hitl_request(hitl_id)
    if not row:
        return "That approval request no longer exists."
    if row["status"] != "pending":
        return "That request has already been resolved."
    expires_at = row.get("expires_at")
    if expires_at and datetime.now(timezone.utc) > expires_at:
        _registry.resolve_hitl_request(hitl_id, "expired")
        return "That request expired — action was not taken."

    if not approved:
        context = (row.get("action_payload") or {}).get("__hitl_context") or {}
        _registry.resolve_hitl_request(hitl_id, "rejected")
        continuation = context.get("continuation_output", "")
        return f"Rejected — action was not taken.{chr(10) + chr(10) + continuation if continuation else ''}"

    settings = get_settings()
    specs = agent_model_specs(settings)
    chat_provider = create_chat_provider(settings, specs["action_agent"])
    from app.agents.action_agent import ActionAgent
    agent = ActionAgent(
        chat_provider=chat_provider,
        registry=_registry,
        schedule_todo_callback=None,
    )
    context = (row.get("action_payload") or {}).get("__hitl_context") or {}
    result = agent.execute_approved(hitl_id, row["user_id"])
    _registry.resolve_hitl_request(hitl_id, "approved")
    continuation = context.get("continuation_output", "")
    reply = result.output or "Done."
    if continuation:
        reply = f"{reply}\n\n{continuation}"
    return reply


def _safe_send(to: str, body: str) -> bool:
    if not _whatsapp_service:
        return False
    try:
        _whatsapp_service.send_message(to=to, body=body)
        return True
    except Exception:
        logger.exception("Failed to send WhatsApp reply to %s", to)
        return False
