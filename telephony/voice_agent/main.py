"""FastAPI app: HTTP routes and the Exotel Voicebot WebSocket endpoint."""

from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, WebSocket

from .config import get_settings
from .gemini import create_client
from .logging_setup import configure_logging
from .session import CallSession

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("voice_agent.main")

if settings.env_path:
    log.info(".env loaded from: %s", settings.env_path)
else:
    log.warning("No .env file found; using process environment only.")

client = create_client(settings)
app = FastAPI(title="Gemini + Exotel AI Phone Agent")


@app.get("/")
async def exotel_resolver():
    """Exotel dynamic-URL resolver: returns the Voicebot WebSocket URL."""
    return {"url": settings.exotel_ws_url}


@app.get("/health")
async def health():
    return {
        "status": "online",
        "service": "Gemini Exotel Voice Agent",
        "model": settings.gemini_model,
    }


@app.websocket("/exotel/media")
async def exotel_media(websocket: WebSocket):
    # Optional shared-secret check: add ?token=... to the Exotel WebSocket URL.
    if settings.exotel_ws_token:
        supplied = websocket.query_params.get("token", "")
        if not hmac.compare_digest(supplied, settings.exotel_ws_token):
            log.warning("Rejected WebSocket connection: bad or missing token.")
            await websocket.close(code=1008)
            return

    await websocket.accept()
    log.info("Exotel WebSocket connected.")
    await CallSession(websocket, settings, client).run()
