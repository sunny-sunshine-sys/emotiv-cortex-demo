"""FastAPI entry point for the simulated EEG dashboard."""

from contextlib import aclosing
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.simulator import EEGSource, SimulatedEEGSource

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="EMOTIV EEG Monitor")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.websocket("/ws")
async def websocket_status(websocket: WebSocket):
    """Transport scaffold: report static status only, without EEG data."""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
            await websocket.send_json({"device_status": "Disconnected"})
    except WebSocketDisconnect:
        pass


def create_eeg_source() -> EEGSource:
    """Replace this factory to use a future hardware source."""
    return SimulatedEEGSource()


@app.websocket("/ws/eeg")
async def websocket_eeg(websocket: WebSocket):
    await websocket.accept()
    source = create_eeg_source()
    try:
        async with aclosing(source.stream()) as samples:
            async for sample in samples:
                await websocket.send_json(sample)
    except (WebSocketDisconnect, OSError):
        # The generator is closed when the browser leaves, with no retained queue.
        pass
