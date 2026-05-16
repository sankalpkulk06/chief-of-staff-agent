# Phase 5 — WhatsApp Voice Notes (Whisper)

**Est. effort:** 2–3 days  
**Dependencies:** Phase 2 (WhatsApp text must be working)  
**Status:** Not started

---

## Goal

When a user sends a voice note via WhatsApp, the webhook detects the `MediaUrl0` field, downloads the OGG audio, transcribes it with local Whisper, and passes the transcript to `ChatService` exactly like a text message.

---

## New Files

- `app/services/whisper_service.py`

## Modified Files

- `app/webhook/server.py` — detect media and route to `WhisperService`
- `app/config/settings.py` — add `WHISPER_MODEL`
- `.env.example` — document new env var
- `setup.py` / `pyproject.toml` — add `openai-whisper`

## System Dependencies

- `ffmpeg` — required by Whisper for audio format conversion. Must be installed on the host OS.
  ```bash
  brew install ffmpeg          # macOS
  sudo apt install ffmpeg      # Ubuntu/Debian
  ```

---

## Tasks

### 5.1 — `WhisperService`

**File:** `app/services/whisper_service.py`

```python
import whisper
import requests
import tempfile
import subprocess
from pathlib import Path

class WhisperService:
    def __init__(self, model_name: str = "base", twilio_sid: str = "", twilio_token: str = "")
    
    def transcribe(self, audio_url: str) -> str:
        # 1. Download OGG from Twilio MediaUrl (auth required)
        # 2. Save to temp file
        # 3. Convert OGG → WAV with ffmpeg
        # 4. Run whisper.transcribe(wav_path)
        # 5. Clean up temp files
        # 6. Return transcript string
    
    def _download_audio(self, url: str, dest: Path) -> None:
        # Use requests with Twilio basic auth (SID:Token)
    
    def _convert_to_wav(self, src: Path, dest: Path) -> None:
        # subprocess.run(["ffmpeg", "-i", src, "-ar", "16000", dest])
        # Raise if ffmpeg not found (clear error message)
```

**Model loading:** Load the Whisper model once in `__init__` (not per request) — the `base` model is ~140MB and takes a few seconds to load.

**AC:**
- [ ] `transcribe()` returns a non-empty string for a valid voice note URL
- [ ] Temp files are always cleaned up (use `finally` block)
- [ ] Raises `RuntimeError("ffmpeg not found...")` with install instructions if ffmpeg is missing
- [ ] Works offline (no internet needed after model download)

---

### 5.2 — Route voice notes in webhook

**File:** `app/webhook/server.py`

Update the `POST /webhook` handler to check for voice notes before the text path:

```python
@app.post("/webhook")
async def webhook(
    From: str = Form(...),
    Body: str = Form(""),
    MediaUrl0: str | None = Form(None),
    MediaContentType0: str | None = Form(None),
):
    phone = From
    session_id = registry.get_or_create_whatsapp_session(phone)
    registry.update_whatsapp_last_active(phone)
    
    # Voice note path
    if MediaUrl0 and MediaContentType0 and "audio" in MediaContentType0:
        if whisper_service is None:
            whatsapp_service.send_message(phone, "Voice notes are not enabled on this server.")
            return Response(content="", media_type="application/xml")
        
        transcript = whisper_service.transcribe(MediaUrl0)
        message = transcript
    else:
        message = Body
    
    # Nudge reply check (from Phase 4) goes here
    # ...
    
    # Normal chat routing
    reply = chat_service.chat(message=message, session_id=session_id)
    whatsapp_service.send_message(to=phone, body=reply)
    
    return Response(content="", media_type="application/xml")
```

**AC:**
- [ ] Voice notes (any `audio/*` MIME type) are transcribed and routed to `ChatService`
- [ ] Text messages still work unchanged
- [ ] If `WhisperService` is not configured, sends a friendly fallback message instead of crashing

---

### 5.3 — Settings + env vars

**File:** `app/config/settings.py`

```python
WHISPER_MODEL: str = "base"    # "tiny" | "base" | "small" | "medium" | "large"
```

**File:** `.env.example`

```env
WHISPER_MODEL=base
```

**Model size reference:**

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| tiny  | 75MB | ~32x  | Basic |
| base  | 140MB | ~16x | Good for personal use |
| small | 460MB | ~6x  | Better accuracy |

Default `base` is the right tradeoff for personal use.

---

### 5.4 — Dependencies

```
openai-whisper>=20231117
```

Note: `openai-whisper` downloads model weights on first use (~140MB for `base`). Weights are cached in `~/.cache/whisper/`.

---

### 5.5 — Lazy initialization

`WhisperService` should only be instantiated if `WHISPER_MODEL` is set and `openai-whisper` is installed. In `app/webhook/server.py`:

```python
try:
    from app.services.whisper_service import WhisperService
    whisper_service = WhisperService(
        model_name=settings.WHISPER_MODEL,
        twilio_sid=settings.TWILIO_ACCOUNT_SID,
        twilio_token=settings.TWILIO_AUTH_TOKEN,
    )
except ImportError:
    whisper_service = None
```

This keeps the webhook server startable without `openai-whisper` installed.

---

## Acceptance Criteria (phase complete)

- [ ] `WhisperService.transcribe()` downloads, converts, and transcribes a voice note URL
- [ ] Transcript passed to `ChatService` exactly like a text message
- [ ] Voice notes routed correctly when `MediaContentType0` contains `audio/`
- [ ] Text messages unaffected by this change
- [ ] Temp audio files cleaned up after transcription
- [ ] Graceful fallback when Whisper not installed (`whisper_service = None`)
- [ ] Clear error if `ffmpeg` is missing (with install instructions)
- [ ] `WHISPER_MODEL=tiny` works for faster but lower-accuracy transcription
