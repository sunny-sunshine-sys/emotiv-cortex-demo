"""One producer feeds both the CSV recorder and all WebSocket subscribers."""

import asyncio
from contextlib import aclosing

from backend.recorder import EEGRecorder
from backend.simulator import EEGSample, EEGSource


class EEGStream:
    def __init__(self, source: EEGSource, recorder: EEGRecorder):
        self.source = source
        self.recorder = recorder
        self.subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def publish(self, sample: EEGSample) -> None:
        # Exactly the same sample is written once and sent to every client.
        self.recorder.append(sample)
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()  # slow displays drop old data, never block recording
            queue.put_nowait(sample)

    async def run(self) -> None:
        async with aclosing(self.source.stream()) as samples:
            async for sample in samples:
                self.publish(sample)
