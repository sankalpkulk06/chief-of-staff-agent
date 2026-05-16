# Phase 2 — WhatsApp Text Integration

**Est. effort:** 4–5 days  
**Dependencies:** Existing `ChatService`, `SQLiteRegistry`  
**Status:** Not started

---

## Goal

`sage serve` starts a FastAPI webhook server. Twilio forwards WhatsApp messages to it. Each phone number gets a persistent Sage session. All existing commands work over WhatsApp.

---

## New Files

- `app/webhook/__init__.py`
- `app/webhook/server.py` — FastAPI app with `/webhook` and `/health` endpoints
- `app/services/__init__.py`
- `app/services/whatsapp_service.py` — Twilio send/receive wrapper
- `app/cli/commands_serve.py` — `sage serve` Typer command

## Modified Files

- `app/storage/sql_schema.sql` — add `whatsapp_sessions` table
- `app/storage/sqlite_registry.py` — add WhatsApp session CRUD methods
- `app/main.py` — register `serve` command
- `app/config/settings.py` — add Twilio env vars
- `.env.example` — document new env vars
- `setup.py` / `pyproject.toml` — add `fastapi`, `uvicorn`, `twilio`

---

## Tasks

### 2.1 — SQLite schema: `whatsapp_sessions` table

**File:** `app/storage/sql_schema.sql`

```sql
CREATE TABLE IF NOT EXISTS whatsapp_sessions (
    phone_number  TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_active   DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**File:** `app/storage/sqlite_registry.py`

Add methods:
```python
def get_or_create_whatsapp_session(self, phone_number: str) -> str:
    # Returns session_id, creating a new one if needed
    # Also creates the session row in the existing `sessions` table

def update_whatsapp_last_active(self, phone_number: str) -> None
```

**AC:**
- [ ] Table created automatically via `initialize_schema()` on first run
- [ ] Same phone number always returns the same `session_id`
- [ ] New phone numbers get a fresh UUID `session_id`

---

### 2.2 — `WhatsAppService`

**File:** `app/services/whatsapp_service.py`

```python
class WhatsAppService:
    def __init__(self, account_sid: str, auth_token: str, from_number: str)
    
    def send_message(self, to: str, body: str) -> None:
        # Splits body at sentence boundaries if > 1600 chars
        # Sends each chunk as a separate Twilio message
    
    def send_media(self, to: str, media_url: str, caption: str = "") -> None
    
    def split_message(self, text: str, limit: int = 1600) -> list[str]:
        # Split at sentence boundary (". ") nearest to limit
```

**AC:**
- [ ] `send_message()` sends via Twilio `client.messages.create()`
- [ ] Messages > 1600 chars split at sentence boundaries (not mid-word)
- [ ] `split_message()` is unit-testable in isolation (no Twilio calls needed)

---

### 2.3 — FastAPI webhook server

**File:** `app/webhook/server.py`

```python
app = FastAPI()

@app.post("/webhook")
async def webhook(
    From: str = Form(...),
    Body: str = Form(""),
    MediaUrl0: str | None = Form(None),
    MediaContentType0: str | None = Form(None),
):
    phone = From  # e.g. "whatsapp:+14155551234"
    session_id = registry.get_or_create_whatsapp_session(phone)
    
    # Text message path (voice handled in Phase 5)
    reply = chat_service.chat(message=Body, session_id=session_id)
    whatsapp_service.send_message(to=phone, body=reply)
    
    return Response(content="", media_type="application/xml")

@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Notes:**
- Return empty TwiML (not a string reply) — the reply is sent proactively via `send_message`, not via TwiML response body. This allows splitting long replies.
- Validate Twilio signature in production using `twilio.request_validator.RequestValidator`.

**AC:**
- [ ] `POST /webhook` with a Twilio-shaped form body routes to `ChatService` and replies via `send_message`
- [ ] `GET /health` returns `{"status": "ok"}`
- [ ] Unknown/missing `From` field returns 400

---

### 2.4 — `sage serve` CLI command

**File:** `app/cli/commands_serve.py`

```python
@app.command()
def serve(
    port: int = typer.Option(8000, help="Port to listen on"),
    reload: bool = typer.Option(False, help="Enable auto-reload for dev"),
):
    """Start the WhatsApp webhook server."""
    uvicorn.run("app.webhook.server:app", host="0.0.0.0", port=port, reload=reload)
```

**File:** `app/main.py`

Register `serve` command alongside existing `chat`, `ask`, `ingest`, `email`.

**AC:**
- [ ] `sage serve` starts uvicorn on port 8000
- [ ] `sage serve --port 9000` uses port 9000
- [ ] Ctrl-C shuts down cleanly

---

### 2.5 — Settings + env vars

**File:** `app/config/settings.py`

```python
TWILIO_ACCOUNT_SID: str = ""
TWILIO_AUTH_TOKEN: str = ""
TWILIO_WHATSAPP_NUMBER: str = ""   # e.g. "whatsapp:+14155238886"
WEBHOOK_PORT: int = 8000
WHATSAPP_ENABLED: bool = True
```

**File:** `.env.example`

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
WEBHOOK_PORT=8000
WHATSAPP_ENABLED=true
```

**AC:**
- [ ] `sage serve` starts without Twilio creds set (logs a warning, still serves `/health`)
- [ ] `WHATSAPP_ENABLED=false` skips Twilio client init

---

### 2.6 — Dependencies

```
fastapi>=0.110
uvicorn[standard]>=0.29
twilio>=9.0
python-multipart>=0.0.9   # required for FastAPI Form parsing
```

---

## Local Dev Setup (ngrok)

```bash
# Terminal 1
sage serve

# Terminal 2
ngrok http 8000
# Copy the https URL → Twilio Console → WhatsApp Sandbox → Webhook URL: https://xxx.ngrok.io/webhook
```

Document in README under "WhatsApp Setup".

---

## Acceptance Criteria (phase complete)

- [ ] `sage serve` starts FastAPI server receiving Twilio webhooks
- [ ] Text messages routed through `ChatService` with persistent session per phone number
- [ ] All existing commands work over WhatsApp (`/todo`, `/news`, `/facts`, `/remember-*`)
- [ ] Responses > 1600 chars split across multiple messages
- [ ] `WhatsAppService.send_message()` usable by scheduler (Phase 4) for proactive messages
- [ ] Session persists across conversations (same `session_id` for same phone number)
- [ ] ngrok dev setup documented in README
