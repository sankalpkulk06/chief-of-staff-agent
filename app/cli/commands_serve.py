import logging
import os

import uvicorn


def serve_command(port: int = 8000, reload: bool = False) -> None:
    """Start the WhatsApp webhook server."""
    # Surface app.* logs (e.g. the passive fact-learner's per-turn summary) alongside
    # uvicorn's. Root defaults to WARNING and uvicorn only configures its own loggers, so
    # without this our INFO lines never print. Override with LOG_LEVEL=DEBUG/WARNING.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    uvicorn.run("app.webhook.server:app", host="0.0.0.0", port=port, reload=reload)
