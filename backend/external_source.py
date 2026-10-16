"""Generic EEG input source for real devices and bridge programs.

The web app does not talk to a vendor SDK directly. A device-specific bridge
normalizes samples to {timestamp, eeg} and pushes them into this source.
This keeps the dashboard independent of any single EEG manufacturer.
"""

import math
import time
from collections import deque

from backend.simulator import EEGSample


class ExternalEEGSource:
    """Receive timestamped EEG samples from HTTP/WebSocket device bridges."""

    def __init__(self, token: str | None = None):
        self.queue = __import__("asyncio").Queue(maxsize=4096)
        self.token = token or ""
        self.rate_history = deque(maxlen=128)
        self.last_received_monotonic: float | None = None
        self.accepted_samples = 0
        self.device_name = "等待设备"
        self.status = {
            "data_source": "external",
            "state": "waiting_for_device",
            "ready": False,
            "message": "等待 EEG 设备通过 WebSocket / HTTP 推送数据。",
            "channels": [],
            "sampling_rate": None,
            "device": self.device_name,
            "accepted_samples": 0,
        }

    @staticmethod
    def validate_sample(timestamp, eeg):
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("timestamp must be a finite number in seconds.")
        if not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite.")
        if not isinstance(eeg, dict) or not eeg or len(eeg) > 64:
            raise ValueError("eeg must be a non-empty object with at most 64 channels.")
        clean = {}
        for name, value in eeg.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("EEG channel names must be non-empty strings.")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"EEG value for {name!r} must be a finite number.")
            clean[name.strip()] = float(value)
        if len(clean) != len(eeg):
            raise ValueError("EEG channel names must be unique after trimming.")
        return float(timestamp), clean

    def push_sample(self, timestamp, eeg, device=None, sampling_rate=None) -> dict:
        timestamp, eeg = self.validate_sample(timestamp, eeg)

        # Device timestamps must be monotonic. This prevents an out-of-order
        # packet from corrupting the chart and CSV time axis.
        if self.rate_history and timestamp <= self.rate_history[-1]:
            raise ValueError("timestamp must increase for every EEG sample.")

        self.rate_history.append(timestamp)
        self.accepted_samples += 1
        self.last_received_monotonic = time.monotonic()

        if device:
            self.device_name = str(device)[:80]

        rate = None
        if isinstance(sampling_rate, (int, float)) and not isinstance(sampling_rate, bool):
            if math.isfinite(sampling_rate) and 0 < sampling_rate <= 4096:
                rate = float(sampling_rate)
        if rate is None and len(self.rate_history) >= 10:
            elapsed = self.rate_history[-1] - self.rate_history[0]
            if elapsed > 0:
                rate = (len(self.rate_history) - 1) / elapsed
        if rate is not None:
            self.status["sampling_rate"] = round(rate, 2)

        self.status.update(
            state="connected",
            ready=True,
            message=f"正在接收 {self.device_name} 的 EEG 原始采样数据。",
            channels=list(eeg.keys()),
            device=self.device_name,
            accepted_samples=self.accepted_samples,
        )

        sample: EEGSample = {"timestamp": timestamp, "eeg": eeg}
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(sample)
        return {
            "accepted": True,
            "timestamp": timestamp,
            "channels": list(eeg.keys()),
            "sampling_rate": self.status["sampling_rate"],
            "accepted_samples": self.accepted_samples,
        }

    async def stream(self):
        while True:
            sample = await self.queue.get()
            yield sample
