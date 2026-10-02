from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from fatigue import FatigueEngine
from vision import VisionProcessor


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
MAX_FRAME_BYTES = 1_500_000
engine = FatigueEngine()
vision_processor: VisionProcessor | None = None
vision_error: str | None = None
engine_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global vision_processor, vision_error
    try:
        vision_processor = VisionProcessor()
        vision_error = None
    except Exception as error:
        vision_error = str(error)
    yield
    if vision_processor is not None:
        vision_processor.close()


app = FastAPI(title="AI Driver Guardian", version="0.1.0", lifespan=lifespan)


class VoiceRequest(BaseModel):
    text: str


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "index.html")


@app.get("/style.css")
async def stylesheet() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "style.css", media_type="text/css")


@app.get("/app.js")
async def javascript() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "app.js", media_type="text/javascript")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "model_ready": vision_processor is not None, "model_error": vision_error}


@app.get("/status")
async def status() -> dict[str, Any]:
    return engine.snapshot()


@app.get("/events")
async def events() -> list[dict[str, str | float]]:
    return list(engine.events)


@app.post("/reset")
async def reset() -> dict[str, str]:
    global engine
    async with engine_lock:
        engine.reset()
        if vision_processor is not None:
            vision_processor.reset()
    return {"status": "reset", "message": "New calibration session started"}


@app.post("/voice")
async def fallback_voice(request: VoiceRequest) -> dict[str, str]:
    """Speak locally on the server only when browser speech synthesis is unavailable."""
    try:
        import pyttsx3

        await asyncio.to_thread(_speak, pyttsx3, request.text)
        return {"status": "spoken"}
    except Exception as error:
        return {"status": "unavailable", "detail": str(error)}


def _speak(module: Any, text: str) -> None:
    speaker = module.init()
    matching_voice = next(
        (voice for voice in speaker.getProperty("voices") if "en" in voice.id.lower()), None
    )
    if matching_voice is not None:
        speaker.setProperty("voice", matching_voice.id)
    speaker.say(text)
    speaker.runAndWait()


@app.websocket("/ws")
async def websocket_frames(websocket: WebSocket) -> None:
    await websocket.accept()
    if vision_processor is None:
        await websocket.send_json({"type": "error", "message": vision_error or "Vision model unavailable"})
        await websocket.close(code=1011)
        return

    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "frame" or not isinstance(payload.get("image"), str):
                await websocket.send_json({"type": "error", "message": "Expected a JPEG frame"})
                continue
            try:
                frame = base64.b64decode(payload["image"], validate=True)
                if not frame or len(frame) > MAX_FRAME_BYTES:
                    raise ValueError("Frame is empty or exceeds the 1.5 MB limit")
                async with engine_lock:
                    measurements = await asyncio.to_thread(vision_processor.process, frame)
                    state = engine.update(measurements)
                await websocket.send_json({"type": "state", **state})
            except (ValueError, binascii.Error) as error:
                await websocket.send_json({"type": "error", "message": str(error)})
    except WebSocketDisconnect:
        return
