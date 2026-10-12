"""Bounded, pull-based EEG source interface and synthetic implementation."""

import asyncio
import math
import random
import time
from collections.abc import AsyncGenerator
from typing import Protocol, TypedDict

CHANNELS = ("AF3", "AF4", "T7", "T8", "Pz")
SAMPLING_RATE = 128


class EEGSample(TypedDict):
    timestamp: float
    eeg: dict[str, float]


class EEGSource(Protocol):
    status: dict

    def stream(self) -> AsyncGenerator[EEGSample, None]:
        """Yield timestamped microvolt samples; release resources on close."""
        ...


class SimulatedEEGSource:
    """Independent channel oscillations, amplitude drift and Gaussian noise."""

    def __init__(self, seed: int | None = None):
        self.status = {'data_source': 'simulation', 'state': 'connected', 'ready': True,
                       'message': 'Receiving simulated EEG.', 'channels': list(CHANNELS),
                       'sampling_rate': SAMPLING_RATE}
        self.random = random.Random(seed)
        self.amplitudes = [42.0 + 3 * i for i in range(len(CHANNELS))]

    def sample(self, elapsed: float, timestamp: float) -> EEGSample:
        eeg = {}
        for i, channel in enumerate(CHANNELS):
            # Slowly varying bounded amplitude, with an independent random walk.
            self.amplitudes[i] = max(30.0, min(60.0, self.amplitudes[i] + self.random.uniform(-0.3, 0.3)))
            phase = i * 0.7
            base = self.amplitudes[i] * math.sin(2 * math.pi * (8 + i * 0.8) * elapsed + phase)
            small = 12 * math.sin(2 * math.pi * (17 + i * 1.3) * elapsed + phase * 2)
            slow = 6 * math.sin(2 * math.pi * (1.2 + i * 0.2) * elapsed)
            value = base + small + slow + self.random.gauss(0, 4)
            eeg[channel] = round(max(-100.0, min(100.0, value)), 3)
        return {"timestamp": timestamp, "eeg": eeg}

    async def stream(self) -> AsyncGenerator[EEGSample, None]:
        started = time.monotonic()
        wall_time = time.time()
        tick = 0
        while True:
            await asyncio.sleep(max(0, started + tick / SAMPLING_RATE - time.monotonic()))
            elapsed = time.monotonic() - started
            yield self.sample(elapsed, wall_time + elapsed)
            # Slow consumers skip missed ticks instead of building a queue or burst.
            tick = max(tick + 1, math.floor((time.monotonic() - started) * SAMPLING_RATE) + 1)
