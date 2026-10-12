"""One producer feeds both the CSV recorder and all WebSocket subscribers."""

import asyncio
import time
from contextlib import aclosing

from backend.recorder import EEGRecorder
from backend.simulator import EEGSample, EEGSource


class EEGStream:
    def __init__(self, source: EEGSource, recorder: EEGRecorder):
        self.source = source
        self.recorder = recorder
        self.last_sample_time = None
        self.failure = None
        self.subscribers: set[asyncio.Queue] = set()

    def status(self) -> dict:
        result = dict(self.source.status)
        result['ready'] = bool(result['ready'] and self.last_sample_time is not None and time.monotonic() - self.last_sample_time < 5)
        if result.get('ready') is False and self.source.status.get('ready') and self.last_sample_time is not None:
            result.update(state='no_data', message='No recent EEG samples; check the data source.')
        if self.failure:
            result.update(ready=False, state='source_error', message=self.failure)
        return result

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def publish(self, sample: EEGSample) -> None:
        # Exactly the same sample is written once and sent to every client.
        self.last_sample_time = time.monotonic()
        self.recorder.append(sample)
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()  # slow displays drop old data, never block recording
            queue.put_nowait(sample)

    async def run(self) -> None:
        try:
            async with aclosing(self.source.stream()) as samples:
                async for sample in samples:
                    self.publish(sample)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failure = "EEG source stopped unexpectedly; restart and check configuration."
            self.recorder.stop()
