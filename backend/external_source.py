"""Generic external signal sources for real EEG/EMG devices and bridge programs.

Device-specific software only needs to normalize samples to:
    {"timestamp": <unix seconds>, "<modality>": {"channel": value}}
and push them to the corresponding HTTP/WebSocket endpoint.
"""

import asyncio
import math
import time
from collections import deque

from backend.simulator import EEGSample


class ExternalSignalSource:
    """Receive timestamped signal samples from HTTP/WebSocket bridges."""

    def __init__(self, modality: str, value_key: str, token: str | None = None):
        self.modality = modality
        self.value_key = value_key
        self.queue = asyncio.Queue(maxsize=4096)
        self.token = token or ""
        self.rate_history = deque(maxlen=128)
        self.last_received_monotonic: float | None = None
        self.accepted_samples = 0
        self.device_name = "等待设备"
        self.status = {
            "data_source": "external",
            "state": "waiting_for_device",
            "ready": False,
            "message": f"等待设备通过 WebSocket / HTTP 推送 {value_key} 原始采样数据。",
            "channels": [],
            "sampling_rate": None,
            "device": self.device_name,
            "accepted_samples": 0,
        }

    def validate_sample(self, timestamp, values):
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("timestamp must be a finite number in seconds.")
        if not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite.")
        if not isinstance(values, dict) or not values or len(values) > 64:
            raise ValueError(f"{self.value_key} must be a non-empty object with at most 64 channels.")
        clean = {}
        for name, value in values.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Channel names must be non-empty strings.")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Signal value for {name!r} must be a finite number.")
            clean[name.strip()] = float(value)
        if len(clean) != len(values):
            raise ValueError("Channel names must be unique after trimming.")
        return float(timestamp), clean

    def push_sample(self, timestamp, values, device=None, sampling_rate=None) -> dict:
        timestamp, values = self.validate_sample(timestamp, values)

        if self.rate_history and timestamp <= self.rate_history[-1]:
            raise ValueError("timestamp must increase for every sample.")

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
            message=f"正在接收 {self.device_name} 的 {self.value_key} 原始采样数据。",
            channels=list(values.keys()),
            device=self.device_name,
            accepted_samples=self.accepted_samples,
        )

        sample = {
            "timestamp": timestamp,
            self.value_key.lower(): values,
            "device": self.device_name,
            "sampling_rate": self.status["sampling_rate"],
        }
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(sample)
        return {
            "accepted": True,
            "timestamp": timestamp,
            "channels": list(values.keys()),
            "sampling_rate": self.status["sampling_rate"],
            "accepted_samples": self.accepted_samples,
        }

    async def stream(self):
        while True:
            yield await self.queue.get()


class ExternalEEGSource(ExternalSignalSource):
    def __init__(self, token: str | None = None):
        super().__init__("eeg", "EEG", token)


class ExternalEMGSource(ExternalSignalSource):
    def __init__(self, token: str | None = None):
        super().__init__("emg", "EMG", token)
