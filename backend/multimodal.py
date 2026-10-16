"""Shared infrastructure for EEG, EMG, vision metadata, synchronization and dataset export."""

import asyncio
import csv
import json
import math
import re
import time
import zipfile
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class MultimodalStream:
    """Normalize one signal source and broadcast the same samples to the UI."""

    def __init__(self, source, modality: str, recorder):
        self.source = source
        self.modality = modality
        self.recorder = recorder
        self.last_sample_time: float | None = None
        self.failure: str | None = None
        self.subscribers: set[asyncio.Queue] = set()

    def status(self) -> dict:
        result = dict(self.source.status)
        fresh = self.last_sample_time is not None and time.monotonic() - self.last_sample_time < 5
        result["ready"] = bool(result.get("ready") and fresh)
        result["modality"] = self.modality
        if not result["ready"] and self.source.status.get("ready"):
            result.update(state="no_data", message=f"等待{self.modality.upper()}最新数据。")
        if self.failure:
            result.update(ready=False, state="source_error", message=self.failure)
        return result

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def publish(self, sample: dict) -> None:
        self.last_sample_time = time.monotonic()
        self.recorder.append_signal(self.modality, sample)
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(sample)

    async def run(self) -> None:
        try:
            async for sample in self.source.stream():
                self.publish(sample)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failure = f"{self.modality.upper()} 数据源异常停止，请检查设备或数据桥接程序。"


class VisionStream:
    """Keep lightweight camera/frame metadata; actual video can remain on the camera side."""

    def __init__(self, recorder):
        self.recorder = recorder
        self.subscribers: set[asyncio.Queue] = set()
        self.frame_count = 0
        self.last_timestamp: float | None = None
        self.last_action = "等待动作"
        self.device = "浏览器摄像头"
        self.ready = False

    def status(self) -> dict:
        fresh = self.last_timestamp is not None and time.time() - self.last_timestamp < 5
        return {
            "data_source": "browser_or_external",
            "state": "connected" if fresh else "waiting_for_frame",
            "ready": self.ready or fresh,
            "message": "视觉帧时间戳已接入，可与 EEG / EMG 对齐。" if fresh else "可打开摄像头或由视觉桥接程序推送帧元数据。",
            "device": self.device,
            "sampling_rate": None,
            "frame_count": self.frame_count,
            "last_action": self.last_action,
        }

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=128)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def push_frame(self, timestamp: float, frame_index: int, action: str | None = None,
                   confidence: float | None = None, device: str | None = None) -> dict:
        if not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite.")
        if frame_index < 0:
            raise ValueError("frame_index must be >= 0.")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1.")
        self.frame_count += 1
        self.last_timestamp = float(timestamp)
        self.last_action = (action or "动作未标注")[:80]
        if device:
            self.device = str(device)[:80]
        self.ready = True
        frame = {
            "timestamp": float(timestamp),
            "frame_index": int(frame_index),
            "action": self.last_action,
            "confidence": confidence,
            "device": self.device,
        }
        self.recorder.append_vision(frame)
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(frame)
        return {"accepted": True, **frame, "frame_count": self.frame_count}


class SessionRecorder:
    """Write a reproducible multimodal session and package it as a ZIP dataset."""

    FILE_PATTERN = re.compile(r"dataset_\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{32}\.zip")

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session_id: str | None = None
        self.session_dir: Path | None = None
        self.signal_file = None
        self.signal_writer = None
        self.vision_file = None
        self.vision_writer = None
        self.event_file = None
        self.event_writer = None
        self.started_monotonic: float | None = None
        self.started_wall: float | None = None
        self.participant_id = "P001"
        self.task = "上肢动作"
        self.repetition = 1
        self.signal_counts = {"eeg": 0, "emg": 0}
        self.vision_count = 0
        self.event_count = 0
        self.last_recording = None
        self.error = None

    @property
    def is_recording(self) -> bool:
        return self.session_dir is not None

    def status(self) -> dict:
        duration = time.monotonic() - self.started_monotonic if self.is_recording else 0.0
        return {
            "is_recording": self.is_recording,
            "session_id": self.session_id,
            "participant_id": self.participant_id if self.is_recording else None,
            "task": self.task if self.is_recording else None,
            "repetition": self.repetition if self.is_recording else None,
            "duration_seconds": duration,
            "signal_counts": dict(self.signal_counts),
            "vision_count": self.vision_count,
            "event_count": self.event_count,
            "last_recording": self.last_recording,
            "error": self.error,
        }

    def start(self, participant_id: str = "P001", task: str = "上肢动作", repetition: int = 1) -> dict:
        if self.is_recording:
            return self.status()
        participant_id = re.sub(r"[^A-Za-z0-9_-]", "", participant_id)[:32] or "P001"
        task = str(task).strip()[:80] or "上肢动作"
        if repetition < 1 or repetition > 999:
            raise ValueError("repetition must be between 1 and 999.")

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        session_id = f"{stamp}_{uuid4().hex}"
        session_dir = self.directory / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=False)

        try:
            signal_file = (session_dir / "signals.csv").open("x", newline="", encoding="utf-8")
            vision_file = (session_dir / "vision.csv").open("x", newline="", encoding="utf-8")
            event_file = (session_dir / "events.csv").open("x", newline="", encoding="utf-8")
            signal_writer = csv.writer(signal_file)
            vision_writer = csv.writer(vision_file)
            event_writer = csv.writer(event_file)
            signal_writer.writerow(("timestamp", "modality", "channel", "value", "device", "sampling_rate", "session_id"))
            vision_writer.writerow(("timestamp", "frame_index", "action", "confidence", "device", "session_id"))
            event_writer.writerow(("timestamp", "event", "label", "note", "session_id"))
            signal_file.flush()
            vision_file.flush()
            event_file.flush()
        except BaseException:
            for file in (locals().get("signal_file"), locals().get("vision_file"), locals().get("event_file")):
                if file:
                    file.close()
            session_dir.rmdir()
            raise

        self.session_id = session_id
        self.session_dir = session_dir
        self.signal_file, self.signal_writer = signal_file, signal_writer
        self.vision_file, self.vision_writer = vision_file, vision_writer
        self.event_file, self.event_writer = event_file, event_writer
        self.started_monotonic = time.monotonic()
        self.started_wall = time.time()
        self.participant_id, self.task, self.repetition = participant_id, task, repetition
        self.signal_counts = {"eeg": 0, "emg": 0}
        self.vision_count = self.event_count = 0
        self.error = None
        self.add_event(time.time(), "session_start", task, f"participant={participant_id}; repetition={repetition}")
        return self.status()

    def append_signal(self, modality: str, sample: dict) -> None:
        if not self.is_recording:
            return
        key = modality.lower()
        values = sample.get(key)
        if not isinstance(values, dict):
            return
        try:
            for channel, value in values.items():
                self.signal_writer.writerow([
                    sample["timestamp"], key, channel, value,
                    sample.get("device", ""), sample.get("sampling_rate", ""), self.session_id
                ])
            self.signal_counts[key] = self.signal_counts.get(key, 0) + 1
            if (self.signal_counts.get(key, 0) + self.vision_count) % 128 == 0:
                self.signal_file.flush()
        except OSError as exc:
            self.error = f"多模态数据写入失败：{exc}"
            self.stop()

    def append_vision(self, frame: dict) -> None:
        if not self.is_recording:
            return
        try:
            self.vision_writer.writerow([
                frame["timestamp"], frame["frame_index"], frame.get("action", ""),
                frame.get("confidence", ""), frame.get("device", ""), self.session_id
            ])
            self.vision_count += 1
            if self.vision_count % 30 == 0:
                self.vision_file.flush()
        except OSError as exc:
            self.error = f"视觉元数据写入失败：{exc}"
            self.stop()

    def add_event(self, timestamp: float, event: str, label: str, note: str = "") -> dict:
        if not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite.")
        result = {
            "timestamp": timestamp,
            "event": str(event)[:50],
            "label": str(label)[:80],
            "note": str(note)[:200],
            "session_id": self.session_id,
        }
        if self.is_recording:
            try:
                self.event_writer.writerow((
                    result["timestamp"], result["event"], result["label"], result["note"], self.session_id
                ))
                self.event_file.flush()
                self.event_count += 1
            except OSError as exc:
                self.error = f"事件标记写入失败：{exc}"
        return result

    def stop(self) -> dict | None:
        if not self.is_recording:
            return self.last_recording

        self.add_event(time.time(), "session_stop", "采集结束")
        for file in (self.signal_file, self.vision_file, self.event_file):
            try:
                file.flush()
                file.close()
            except OSError:
                self.error = "数据文件关闭失败，导出包可能不完整。"

        manifest = {
            "session_id": self.session_id,
            "participant_id": self.participant_id,
            "task": self.task,
            "repetition": self.repetition,
            "started_at_unix": self.started_wall,
            "duration_seconds": time.monotonic() - self.started_monotonic,
            "signal_samples": self.signal_counts,
            "vision_frames": self.vision_count,
            "events": self.event_count,
            "schema": {
                "signals.csv": "long-format timestamped EEG/EMG samples",
                "vision.csv": "timestamped video-frame metadata",
                "events.csv": "experimental task and label markers",
            },
        }
        (self.session_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        zip_name = f"dataset_{self.session_id}.zip"
        zip_path = self.directory / zip_name
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.session_dir.iterdir()):
                archive.write(path, arcname=path.name)

        metadata = {
            "filename": zip_name,
            "download_url": f"/api/recording/download/{zip_name}",
            "session_id": self.session_id,
            "participant_id": self.participant_id,
            "task": self.task,
            "repetition": self.repetition,
            "duration_seconds": manifest["duration_seconds"],
            "signal_counts": dict(self.signal_counts),
            "vision_count": self.vision_count,
            "event_count": self.event_count,
        }
        self.last_recording = metadata
        self.session_id = None
        self.session_dir = None
        self.signal_file = self.signal_writer = None
        self.vision_file = self.vision_writer = None
        self.event_file = self.event_writer = None
        self.started_monotonic = self.started_wall = None
        return metadata

    def download_path(self, filename: str) -> Path | None:
        if not self.FILE_PATTERN.fullmatch(filename):
            return None
        path = self.directory / filename
        if self.is_recording and filename == f"dataset_{self.session_id}.zip":
            return None
        if path.is_symlink() or not path.is_file():
            return None
        return path


def sync_status(streams: dict[str, MultimodalStream], vision: VisionStream) -> dict:
    latest = {}
    for name, stream in streams.items():
        if stream.last_sample_time is not None:
            latest[name] = time.time() - (time.monotonic() - stream.last_sample_time)
    if vision.last_timestamp is not None:
        latest["vision"] = vision.last_timestamp
    spread_ms = 0.0
    if len(latest) >= 2:
        spread_ms = (max(latest.values()) - min(latest.values())) * 1000
    return {
        "latest_timestamps": latest,
        "spread_ms": round(max(0.0, spread_ms), 2),
        "state": "aligned" if len(latest) >= 2 and spread_ms < 100 else "waiting",
        "method": "设备时间戳 + 主机接收时间 + 实验事件标记",
    }
