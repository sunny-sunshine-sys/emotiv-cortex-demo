import asyncio
import csv
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from starlette.websockets import WebSocketState
from backend.main import websocket_eeg
from pathlib import Path

from backend.recorder import CSV_FIELDS, EEGRecorder
from backend.simulator import SimulatedEEGSource
from backend.stream import EEGStream


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.recorder = EEGRecorder(Path(self.temp.name))
        self.addCleanup(self.recorder.stop)

    def test_exact_samples_and_header(self):
        self.recorder.start()
        source = SimulatedEEGSource(seed=42)
        samples = [source.sample(i / 128, 1700000000 + i / 128) for i in range(128)]
        for sample in samples:
            self.recorder.append(sample)
        result = self.recorder.stop()
        self.assertEqual(result['sample_count'], 128)
        self.assertFalse(self.recorder.status()['is_recording'])
        with (self.recorder.directory / result['filename']).open(newline='') as file:
            reader = csv.DictReader(file)
            self.assertEqual(tuple(reader.fieldnames), CSV_FIELDS)
            rows = list(reader)
        self.assertEqual(len(rows), len(samples))
        for sample, row in zip(samples, rows):
            self.assertEqual(float(row['timestamp']), sample['timestamp'])
            self.assertEqual({ch: float(row[ch]) for ch in CSV_FIELDS[1:]}, sample['eeg'])

    def test_repeated_operations_and_unique_files(self):
        first = self.recorder.start()['filename']
        self.assertEqual(self.recorder.start()['filename'], first)
        self.assertIsNone(self.recorder.download_path(first))
        stopped = self.recorder.stop()
        self.assertEqual(self.recorder.stop(), stopped)
        self.assertEqual(self.recorder.download_path(first).name, first)
        second = self.recorder.start()['filename']
        self.assertNotEqual(first, second)
        self.assertTrue((self.recorder.directory / first).is_file())
        self.assertIsNone(self.recorder.download_path('../README.md'))
        self.assertIsNone(self.recorder.download_path('unknown.csv'))

    def test_disk_write_failure_stops_recording_without_crash(self):
        self.recorder.start()
        self.recorder.writer = Mock()
        self.recorder.writer.writerow.side_effect = OSError("disk full")
        self.recorder.append(SimulatedEEGSource().sample(0, 1))
        self.assertFalse(self.recorder.is_recording)
        self.assertIn("write failed", self.recorder.status()['error'])

    def test_close_flushes_without_browser(self):
        self.recorder.start()
        self.recorder.append(SimulatedEEGSource().sample(0, 1))
        result = self.recorder.stop()
        self.assertEqual(len((self.recorder.directory / result['filename']).read_text().splitlines()), 2)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_samples_and_bounded_slow_subscriber(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = EEGRecorder(Path(directory))
            recorder.start()
            source = SimulatedEEGSource(seed=7)
            stream = EEGStream(source, recorder)
            first, second = stream.subscribe(), stream.subscribe()
            sample = source.sample(0, 1)
            stream.publish(sample)
            self.assertIs(await first.get(), sample)
            self.assertIs(await second.get(), sample)
            for i in range(1000):
                stream.publish(source.sample(i / 128, 2 + i / 128))
            self.assertEqual(first.qsize(), 256)
            self.assertEqual(second.qsize(), 256)
            stream.unsubscribe(first)
            stream.unsubscribe(second)
            stream.publish(source.sample(10, 20))
            self.assertEqual(recorder.stop()['sample_count'], 1002)
            self.assertFalse(stream.subscribers)


class DisconnectRaceTests(unittest.IsolatedAsyncioTestCase):
    async def check_race(self, disconnected, transport_closed=False):
        with tempfile.TemporaryDirectory() as directory:
            recorder = EEGRecorder(Path(directory))
            stream = EEGStream(SimulatedEEGSource(), recorder)
            socket = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(streams={"eeg": stream})),
                client_state=WebSocketState.CONNECTED,
                application_state=WebSocketState.CONNECTED,
            )
            async def accept():
                pass
            async def receive():
                await asyncio.Event().wait()
            async def send(sample):
                if disconnected:
                    socket.client_state = WebSocketState.DISCONNECTED
                if transport_closed:
                    raise RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close' or response already completed.")
                raise RuntimeError("send failed during disconnect")
            socket.accept, socket.receive_text, socket.send_json = accept, receive, send
            task = asyncio.create_task(websocket_eeg(socket))
            await asyncio.sleep(0)
            stream.publish(SimulatedEEGSource().sample(0, 1))
            if disconnected or transport_closed:
                await asyncio.wait_for(task, 1)
            else:
                with self.assertRaises(RuntimeError):
                    await asyncio.wait_for(task, 1)
            self.assertFalse(stream.subscribers)

    async def test_disconnect_during_send_cleans_subscription(self):
        await self.check_race(True)

    async def test_unrelated_send_error_is_not_hidden(self):
        await self.check_race(False)

    async def test_transport_closed_before_client_state_updates(self):
        await self.check_race(False, transport_closed=True)
