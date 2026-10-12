"""Run with: python -m unittest discover -s tests -v."""

import asyncio
import time
import unittest

from backend.simulator import CHANNELS, SAMPLING_RATE, SimulatedEEGSource


class SimulatorTests(unittest.TestCase):
    def test_signal_format_range_and_independence(self):
        source = SimulatedEEGSource(seed=42)
        unique_values = {channel: set() for channel in CHANNELS}
        for tick in range(12800):
            sample = source.sample(tick / SAMPLING_RATE, 1700000000 + tick / SAMPLING_RATE)
            self.assertEqual(set(sample), {"timestamp", "eeg"})
            self.assertEqual(tuple(sample['eeg']), CHANNELS)
            self.assertEqual(len(set(sample['eeg'].values())), 5)
            for channel, value in sample['eeg'].items():
                self.assertGreaterEqual(value, -100)
                self.assertLessEqual(value, 100)
                unique_values[channel].add(value)
        for values in unique_values.values():
            self.assertGreater(len(values), 1000)

    def test_seeded_source_is_repeatable(self):
        first, second = SimulatedEEGSource(seed=7), SimulatedEEGSource(seed=7)
        self.assertEqual(first.sample(1.5, 100), second.sample(1.5, 100))


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_pacing_and_close(self):
        stream = SimulatedEEGSource(seed=1).stream()
        samples = [await anext(stream) for _ in range(32)]
        duration = samples[-1]['timestamp'] - samples[0]['timestamp']
        self.assertGreater(duration, 0.20)
        self.assertLess(duration, 0.6)
        await stream.aclose()
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_slow_consumer_does_not_replay_backlog(self):
        stream = SimulatedEEGSource().stream()
        first = await anext(stream)
        await asyncio.sleep(0.12)
        second = await anext(stream)
        self.assertGreater(second['timestamp'] - first['timestamp'], 0.1)
        before = time.monotonic()
        third = await anext(stream)
        self.assertGreater(third['timestamp'], second['timestamp'])
        self.assertGreater(time.monotonic() - before, 0.0005)
        await stream.aclose()
