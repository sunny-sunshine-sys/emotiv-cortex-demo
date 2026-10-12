"""FastAPI entry point for shared simulated EEG streaming and CSV recording."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from backend.recorder import EEGRecorder
from backend.simulator import EEGSource, SimulatedEEGSource
from backend.stream import EEGStream

PROJECT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"


def create_eeg_source() -> EEGSource:
    """Replace this factory to use a future hardware source."""
    return SimulatedEEGSource()


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


app = FastAPI(title="EMOTIV EEG Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/api/recording/start")
async def start_recording(request: Request):
    try:
        return request.app.state.recorder.start()
    except OSError as exc:
        raise HTTPException(500, "Cannot create CSV; check data directory permissions and disk space.") from exc


@app.post("/api/recording/stop")
async def stop_recording(request: Request):
    recorder = request.app.state.recorder
    result = recorder.stop()
    if result is None:
        raise HTTPException(409, "No recording has been started.")
    return {**result, "is_recording": False, "error": recorder.error}


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
                # receive_disconnect may change state while send_json is awaiting.
                if (websocket.client_state is WebSocketState.DISCONNECTED or
                        websocket.application_state is WebSocketState.DISCONNECTED):
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
