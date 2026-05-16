import uvicorn


def serve_command(port: int = 8000, reload: bool = False) -> None:
    """Start the WhatsApp webhook server."""
    uvicorn.run("app.webhook.server:app", host="0.0.0.0", port=port, reload=reload)
