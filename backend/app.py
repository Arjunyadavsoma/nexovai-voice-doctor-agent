"""FastAPI server — dashboard, REST API, dashboard WebSocket, and the
WebRTC signalling endpoint the voice pipeline uses.

Run:  ./run.sh        (or: uvicorn backend.app:app --port 8000)
Then open http://localhost:8000
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from . import config, demo, events, store
from .agent import run_pipeline

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

_webrtc_handler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    global _webrtc_handler
    try:
        from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler
        _webrtc_handler = SmallWebRTCRequestHandler()
    except Exception as exc:  # aiortc missing etc. — REST + dashboard still work
        logger.warning(f"WebRTC signalling unavailable: {exc}")
    yield
    if _webrtc_handler is not None:
        await _webrtc_handler.close()


app = FastAPI(title="CityCare Voice Booking", lifespan=lifespan)


# ------------------------------------------------------------------ REST

class BookRequest(BaseModel):
    phone: str
    slot_id: int


class CancelRequest(BaseModel):
    phone: str
    appointment_id: str | None = None


class ChatRequest(BaseModel):
    session_id: str = "demo"
    text: str


@app.get("/api/status")
def status():
    return {"voice_ready": config.VOICE_READY,
            "llm": config.GEMINI_MODEL,
            "stt": "ElevenLabs " + config.ELEVENLABS_STT_MODEL,
            "tts": "ElevenLabs " + config.ELEVENLABS_TTS_MODEL,
            "doctors": len(store.list_doctors()),
            "appointments": len(store.list_appointments())}


@app.get("/api/doctors")
def doctors():
    out = []
    for d in store.list_doctors():
        slots = store.list_availability(doctor_id=d["id"])
        out.append({**d, "open_slots": len(slots),
                    "next_slot": f"{slots[0]['date']} {slots[0]['time']}" if slots else None})
    return out


@app.get("/api/availability")
def availability(doctor_id: int | None = None, date: str | None = None):
    return store.list_availability(doctor_id=doctor_id, day=date)


@app.get("/api/appointments")
def appointments(phone: str | None = None):
    return store.list_appointments(phone)


@app.post("/api/book")
def book(req: BookRequest):
    result = store.book_appointment(req.phone, req.slot_id)
    if result.get("ok"):
        events.publish("appointment_booked", {
            "appointment": {"id": result["appointment_id"],
                            "doctor_name": result["doctor_name"],
                            "speciality": result["speciality"],
                            "date": result["date"], "time": result["time"],
                            "phone": result["phone"], "status": "booked"}})
    return result


@app.post("/api/cancel")
def cancel(req: CancelRequest):
    result = store.cancel_appointment(req.phone, req.appointment_id)
    if result.get("ok"):
        events.publish("appointment_cancelled", {
            "appointment": {"id": result["appointment_id"],
                            "doctor_name": result["doctor_name"],
                            "date": result["date"], "time": result["time"],
                            "status": "cancelled"}})
    return result


@app.post("/api/chat")
def chat(req: ChatRequest):
    """Text demo mode (see backend/demo.py). Not the voice pipeline."""
    events.publish("transcript", {"who": "caller", "text": req.text})
    result = demo.reply(req.session_id, req.text)
    events.publish("transcript", {"who": "agent", "text": result["reply"]})
    return result


# ------------------------------------------------------- dashboard WebSocket

@app.websocket("/ws")
async def dashboard_ws(ws: WebSocket):
    await ws.accept()
    q = events.subscribe()
    try:
        await ws.send_json({"event": "snapshot",
                            "doctors": doctors(),
                            "appointments": appointments()})
        while True:
            payload = await q.get()
            await ws.send_text(payload)
    except WebSocketDisconnect:
        pass
    finally:
        events.unsubscribe(q)


# ------------------------------------------------------------------ WebRTC
# Mirrors Pipecat's own SmallWebRTC FastAPI routes: the browser posts its
# SDP offer, the handler answers, and each connection starts one pipeline.

@app.post("/api/offer")
async def webrtc_offer(request: Request):
    if _webrtc_handler is None:
        return JSONResponse({"error": "WebRTC signalling is not available on this server."},
                            status_code=503)
    if not config.VOICE_READY:
        return JSONResponse({"error": "Voice keys are not set. Set ELEVENLABS_API_KEY and "
                                     "GEMINI_API_KEY in .env, then restart. Text demo still works."},
                            status_code=503)
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest
    body = await request.json()
    webrtc_request = SmallWebRTCRequest.from_dict(body)

    async def connection_callback(connection):
        asyncio.create_task(run_pipeline(connection))

    return await _webrtc_handler.handle_web_request(
        request=webrtc_request, webrtc_connection_callback=connection_callback)


@app.patch("/api/offer")
async def webrtc_ice(request: Request):
    if _webrtc_handler is None:
        return JSONResponse({"error": "WebRTC signalling is not available on this server."},
                            status_code=503)
    from pipecat.transports.smallwebrtc.request_handler import (
        IceCandidate, SmallWebRTCPatchRequest)
    body = await request.json()
    patch = SmallWebRTCPatchRequest(
        pc_id=body["pc_id"],
        candidates=[IceCandidate(**c) for c in body.get("candidates", [])])
    await _webrtc_handler.handle_patch_request(patch)
    return {"status": "success"}


# ---------------------------------------------------------------- frontend

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")
