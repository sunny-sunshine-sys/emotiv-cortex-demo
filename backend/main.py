"""FastAPI entry point for the vendor-neutral EEG acquisition dashboard."""

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
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState

from backend.external_source import ExternalEEGSource
from backend.recorder import EEGRecorder
from backend.simulator import EEGSource, SimulatedEEGSource
from backend.stream import EEGStream

PROJECT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
load_dotenv(PROJECT_DIR / ".env", override=False)


class EEGPacket(BaseModel):
    """Normalized device packet accepted by the dashboard."""

    timestamp: float = Field(description="Sample timestamp in Unix seconds.")
    eeg: dict[str, float] = Field(min_length=1, max_length=64, description="EEG channel values in μV.")
    device: str | None = Field(default=None, max_length=80)
    sampling_rate: float | None = Field(default=None, gt=0, le=4096)


def create_eeg_source() -> EEGSource:
    mode = os.getenv("DATA_SOURCE", "simulation").strip().lower()
    if mode == "simulation":
        return SimulatedEEGSource()
    if mode == "external":
        return ExternalEEGSource(os.getenv("EEG_INGEST_TOKEN", "").strip() or None)
    raise ValueError("DATA_SOURCE must be simulation or external.")


def external_source(request: Request) -> ExternalEEGSource:
    source = request.app.state.eeg_stream.source
    if not isinstance(source, ExternalEEGSource):
        raise HTTPException(
            409,
            "External EEG input is disabled. Set DATA_SOURCE=external and restart the server.",
        )
    return source


def check_ingest_token(request: Request, provided: str | None) -> None:
    source = external_source(request)
    if source.token and not secrets.compare_digest(provided or "", source.token):
        raise HTTPException(401, "Invalid EEG ingest token.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    recorder = EEGRecorder(PROJECT_DIR / "data")
    stream = EEGStream(create_eeg_source(), recorder)
    app.state.recorder = recorder
    app.state.eeg_stream = stream
    producer = asyncio.create_task(stream.run())
    try:
        yield
    finally:
        producer.cancel()
        try:
            await producer
        except asyncio.CancelledError:
            pass
        finally:
            recorder.stop()


app = FastAPI(
    title="Multimodal EEG Data Acquisition Platform",
    description="A vendor-neutral real-time EEG dashboard and acquisition gateway.",
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/ingest/info")
async def ingest_info(request: Request):
    source = external_source(request)
    scheme = "wss" if request.url.scheme == "https" else "ws"
    http_scheme = "https" if request.url.scheme == "https" else "http"
    return {
        "enabled": True,
        "websocket_url": f"{scheme}://{request.url.netloc}/ws/eeg/ingest",
        "http_url": f"{http_scheme}://{request.url.netloc}/api/eeg/ingest",
        "format": {
            "timestamp": "Unix seconds, increasing for every sample",
            "eeg": "object of channel name -> microvolts",
            "device": "optional device name",
            "sampling_rate": "optional Hz",
        },
        "token_required": bool(source.token),
    }


@app.post("/api/eeg/ingest")
async def ingest_eeg(packet: EEGPacket, request: Request):
    check_ingest_token(request, request.headers.get("x-eeg-token"))
    source = external_source(request)
    try:
        result = source.push_sample(
            packet.timestamp,
            packet.eeg,
            packet.device,
            packet.sampling_rate,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


@app.websocket("/ws/eeg/ingest")
async def websocket_ingest(websocket: WebSocket):
    source = websocket.app.state.eeg_stream.source
    if not isinstance(source, ExternalEEGSource):
        await websocket.close(code=1008, reason="External EEG input is disabled.")
        return

    provided = websocket.query_params.get("token")
    if source.token and not secrets.compare_digest(provided or "", source.token):
        await websocket.close(code=1008, reason="Invalid EEG ingest token.")
        return

    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_text()
            try:
                packet = EEGPacket.model_validate(json.loads(payload))
                source.push_sample(
                    packet.timestamp,
                    packet.eeg,
                    packet.device,
                    packet.sampling_rate,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                await websocket.send_json({"accepted": False, "error": str(exc)})
                continue
            await websocket.send_json({"accepted": True})
    except WebSocketDisconnect:
        pass


@app.post("/api/recording/start")
async def start_recording(request: Request):
    recorder = request.app.state.recorder
    if recorder.is_recording:
        return recorder.status()
    source_status = request.app.state.eeg_stream.status()
    if not source_status["ready"]:
        raise HTTPException(409, source_status["message"])
    try:
        return recorder.start(source_status["channels"])
    except OSError as exc:
        raise HTTPException(500, "Cannot create CSV; check data directory permissions and disk space.") from exc


@app.post("/api/recording/stop")
async def stop_recording(request: Request):
    recorder = request.app.state.recorder
    result = recorder.stop()
    if result is None:
        raise HTTPException(409, "No recording has been started.")
    return {**result, "is_recording": False, "error": recorder.error}


@app.get("/api/source/status")
async def source_status(request: Request):
    return request.app.state.eeg_stream.status()


@app.get("/api/recording/status")
async def recording_status(request: Request):
    return request.app.state.recorder.status()


@app.get("/api/recording/download/{filename}")
async def download_recording(filename: str, request: Request):
    path = request.app.state.recorder.download_path(filename)
    if path is None:
        raise HTTPException(404, "Completed CSV recording not found.")
    return FileResponse(path, media_type="text/csv", filename=filename)


@app.websocket("/ws")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
            await websocket.send_json({"device_status": "Disconnected"})
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/eeg")
async def websocket_eeg(websocket: WebSocket):
    await websocket.accept()
    stream = websocket.app.state.eeg_stream
    queue = stream.subscribe()

    async def send_samples():
        while True:
            sample = await queue.get()
            if websocket.client_state is WebSocketState.DISCONNECTED:
                return
            try:
                await websocket.send_json(sample)
            except RuntimeError:
                if (
                    websocket.client_state is WebSocketState.DISCONNECTED
                    or websocket.application_state is WebSocketState.DISCONNECTED
                ):
                    return
                raise

    async def receive_disconnect():
        while True:
            await websocket.receive_text()

    tasks = [asyncio.create_task(send_samples()), asyncio.create_task(receive_disconnect())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, OSError):
        pass
    finally:
        stream.unsubscribe(queue)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
