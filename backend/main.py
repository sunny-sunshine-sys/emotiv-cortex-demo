"""FastAPI service for a vendor-neutral multimodal acquisition and dataset demo."""

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState
from pydantic import BaseModel, Field

from backend.external_source import ExternalEEGSource, ExternalEMGSource
from backend.multimodal import MultimodalStream, SessionRecorder, VisionStream, sync_status
from backend.simulator import SimulatedEEGSource, SimulatedEMGSource

PROJECT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
load_dotenv(PROJECT_DIR / ".env", override=False)


class EEGPacket(BaseModel):
    timestamp: float = Field(description="Unix seconds; strictly increasing for each device stream.")
    eeg: dict[str, float] = Field(min_length=1, max_length=64, description="EEG channel -> value.")
    device: str | None = Field(default=None, max_length=80)
    sampling_rate: float | None = Field(default=None, gt=0, le=4096)


class EMGPacket(BaseModel):
    timestamp: float = Field(description="Unix seconds; strictly increasing for each device stream.")
    emg: dict[str, float] = Field(min_length=1, max_length=64, description="EMG channel -> value.")
    device: str | None = Field(default=None, max_length=80)
    sampling_rate: float | None = Field(default=None, gt=0, le=4096)


class VisionFrame(BaseModel):
    timestamp: float
    frame_index: int = Field(ge=0)
    action: str | None = Field(default=None, max_length=80)
    confidence: float | None = Field(default=None, ge=0, le=1)
    device: str | None = Field(default=None, max_length=80)


class EventMarker(BaseModel):
    timestamp: float
    event: str = Field(min_length=1, max_length=50)
    label: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=200)


class SessionStart(BaseModel):
    participant_id: str = Field(default="P001", min_length=1, max_length=32)
    task: str = Field(default="上肢动作", min_length=1, max_length=80)
    repetition: int = Field(default=1, ge=1, le=999)


def signal_mode(modality: str) -> str:
    """Use the new explicit setting; ignore the old external mode unless explicitly re-enabled."""
    explicit = os.getenv(f"{modality.upper()}_SOURCE", "").strip().lower()
    if explicit in {"simulation", "external"}:
        return explicit
    if modality == "eeg":
        legacy = os.getenv("DATA_SOURCE", "simulation").strip().lower()
        return "simulation" if legacy != "simulation" else legacy
    return "simulation"


def create_source(modality: str, token: str | None):
    mode = signal_mode(modality)
    if modality == "eeg":
        return SimulatedEEGSource(seed=11) if mode == "simulation" else ExternalEEGSource(token)
    return SimulatedEMGSource(seed=23) if mode == "simulation" else ExternalEMGSource(token)


def source_for(request: Request, modality: str):
    return request.app.state.streams[modality].source


def check_token(request: Request, provided: str | None) -> None:
    token = request.app.state.ingest_token
    if token and not secrets.compare_digest(provided or "", token):
        raise HTTPException(401, "Invalid ingest token.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.getenv("EEG_INGEST_TOKEN", "").strip() or None
    recorder = SessionRecorder(PROJECT_DIR / "data")
    streams = {
        "eeg": MultimodalStream(create_source("eeg", token), "eeg", recorder),
        "emg": MultimodalStream(create_source("emg", token), "emg", recorder),
    }
    vision = VisionStream(recorder)
    app.state.ingest_token = token
    app.state.recorder = recorder
    app.state.streams = streams
    app.state.vision = vision
    tasks = [asyncio.create_task(stream.run()) for stream in streams.values()]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        recorder.stop()


app = FastAPI(
    title="脑电、肌电与视觉多模态数据采集与处理系统",
    description=(
        "Vendor-neutral multimodal acquisition demo for EEG, EMG and vision. "
        "The platform focuses on timestamp alignment, experiment markers, quality records and dataset export."
    ),
    version="2.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/session/status")
async def session_status(request: Request):
    streams = request.app.state.streams
    return {
        "system": "multimodal",
        "modalities": {
            "eeg": streams["eeg"].status(),
            "emg": streams["emg"].status(),
            "vision": request.app.state.vision.status(),
        },
        "sync": sync_status(streams, request.app.state.vision),
        "recording": request.app.state.recorder.status(),
    }


@app.get("/api/source/status")
async def source_status(request: Request):
    """Backward-compatible EEG status endpoint."""
    return request.app.state.streams["eeg"].status()


@app.get("/api/ingest/info")
async def ingest_info(request: Request):
    scheme = "wss" if request.url.scheme == "https" else "ws"
    http_scheme = "https" if request.url.scheme == "https" else "http"
    token_required = bool(request.app.state.ingest_token)
    return {
        "eeg": {
            "http_url": f"{http_scheme}://{request.url.netloc}/api/eeg/ingest",
            "websocket_url": f"{scheme}://{request.url.netloc}/ws/eeg/ingest",
            "format": {"timestamp": "Unix seconds", "eeg": "channel -> value"},
        },
        "emg": {
            "http_url": f"{http_scheme}://{request.url.netloc}/api/emg/ingest",
            "websocket_url": f"{scheme}://{request.url.netloc}/ws/emg/ingest",
            "format": {"timestamp": "Unix seconds", "emg": "channel -> value"},
        },
        "token_required": token_required,
    }


@app.post("/api/eeg/ingest")
async def ingest_eeg(packet: EEGPacket, request: Request):
    check_token(request, request.headers.get("x-eeg-token"))
    source = source_for(request, "eeg")
    if not isinstance(source, ExternalEEGSource):
        raise HTTPException(409, "EEG external input is disabled. Set EEG_SOURCE=external and restart.")
    try:
        return source.push_sample(packet.timestamp, packet.eeg, packet.device, packet.sampling_rate)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/emg/ingest")
async def ingest_emg(packet: EMGPacket, request: Request):
    check_token(request, request.headers.get("x-eeg-token"))
    source = source_for(request, "emg")
    if not isinstance(source, ExternalEMGSource):
        raise HTTPException(409, "EMG external input is disabled. Set EMG_SOURCE=external and restart.")
    try:
        return source.push_sample(packet.timestamp, packet.emg, packet.device, packet.sampling_rate)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def websocket_ingest(websocket: WebSocket, modality: str):
    source = websocket.app.state.streams[modality].source
    expected = ExternalEEGSource if modality == "eeg" else ExternalEMGSource
    if not isinstance(source, expected):
        await websocket.close(code=1008, reason=f"External {modality.upper()} input is disabled.")
        return

    token = websocket.app.state.ingest_token
    provided = websocket.query_params.get("token")
    if token and not secrets.compare_digest(provided or "", token):
        await websocket.close(code=1008, reason="Invalid ingest token.")
        return

    await websocket.accept()
    model = EEGPacket if modality == "eeg" else EMGPacket
    value_key = "eeg" if modality == "eeg" else "emg"
    try:
        while True:
            payload = await websocket.receive_text()
            try:
                packet = model.model_validate(json.loads(payload))
                values = getattr(packet, value_key)
                source.push_sample(packet.timestamp, values, packet.device, packet.sampling_rate)
            except (json.JSONDecodeError, ValueError) as exc:
                await websocket.send_json({"accepted": False, "error": str(exc)})
                continue
            await websocket.send_json({"accepted": True})
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/eeg/ingest")
async def websocket_eeg_ingest(websocket: WebSocket):
    await websocket_ingest(websocket, "eeg")


@app.websocket("/ws/emg/ingest")
async def websocket_emg_ingest(websocket: WebSocket):
    await websocket_ingest(websocket, "emg")


async def websocket_signal(websocket: WebSocket, modality: str):
    await websocket.accept()
    stream = websocket.app.state.streams[modality]
    queue = stream.subscribe()
    try:
        while True:
            sample = await queue.get()
            await websocket.send_json(sample)
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        transport_closed = str(exc).startswith("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'")
        if (not transport_closed and websocket.client_state is not WebSocketState.DISCONNECTED and
                websocket.application_state is not WebSocketState.DISCONNECTED):
            raise
    finally:
        stream.unsubscribe(queue)


@app.websocket("/ws/eeg")
async def websocket_eeg(websocket: WebSocket):
    await websocket_signal(websocket, "eeg")


@app.websocket("/ws/emg")
async def websocket_emg(websocket: WebSocket):
    await websocket_signal(websocket, "emg")


@app.post("/api/vision/frame")
async def vision_frame(frame: VisionFrame, request: Request):
    try:
        return request.app.state.vision.push_frame(
            frame.timestamp, frame.frame_index, frame.action, frame.confidence, frame.device
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/vision/status")
async def vision_status(request: Request):
    return request.app.state.vision.status()


@app.post("/api/events/mark")
async def mark_event(marker: EventMarker, request: Request):
    try:
        return request.app.state.recorder.add_event(
            marker.timestamp, marker.event, marker.label, marker.note
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/recording/start")
async def start_recording(request: Request, payload: SessionStart | None = None):
    recorder = request.app.state.recorder
    if recorder.is_recording:
        return recorder.status()

    payload = payload or SessionStart()
    eeg_ready = request.app.state.streams["eeg"].status()["ready"]
    emg_ready = request.app.state.streams["emg"].status()["ready"]
    if not (eeg_ready and emg_ready):
        raise HTTPException(409, "EEG 与 EMG 数据源都需要先进入实时状态。")

    try:
        return recorder.start(payload.participant_id, payload.task, payload.repetition)
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"无法开始数据集记录：{exc}") from exc


@app.post("/api/recording/stop")
async def stop_recording(request: Request):
    result = request.app.state.recorder.stop()
    if result is None:
        raise HTTPException(409, "当前没有正在记录的实验。")
    return {**result, "is_recording": False, "error": request.app.state.recorder.error}


@app.get("/api/recording/status")
async def recording_status(request: Request):
    return request.app.state.recorder.status()


@app.get("/api/recording/download/{filename}")
async def download_recording(filename: str, request: Request):
    path = request.app.state.recorder.download_path(filename)
    if path is None:
        raise HTTPException(404, "Completed dataset package not found.")
    return FileResponse(path, media_type="application/zip", filename=filename)


@app.get("/api/quality/status")
async def quality_status(request: Request):
    streams = request.app.state.streams
    sync = sync_status(streams, request.app.state.vision)
    recording = request.app.state.recorder.status()
    active = [streams["eeg"].status(), streams["emg"].status(), request.app.state.vision.status()]
    ready_count = sum(1 for item in active if item["ready"])
    return {
        "ready_modalities": ready_count,
        "total_modalities": 3,
        "sync_spread_ms": sync["spread_ms"],
        "sync_state": sync["state"],
        "recording": recording["is_recording"],
        "labels": recording["event_count"],
        "quality_state": "good" if ready_count >= 2 and sync["spread_ms"] < 100 else "checking",
    }
