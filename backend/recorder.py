"""Stream EEG samples to an exclusive CSV file without retaining history."""

import csv
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.simulator import CHANNELS, EEGSample

CSV_FIELDS = ("timestamp", *CHANNELS)
FILE_PATTERN = re.compile(r"eeg_\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{32}\.csv")


class EEGRecorder:
    """Called on the application's single event loop; recordings are global."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file = None
        self.writer = None
        self.filename = None
        self.started = None
        self.sample_count = 0
        self.last_recording = None
        self.error = None
        self.last_flush = 0.0

    @property
    def is_recording(self) -> bool:
        return self.file is not None

    def status(self) -> dict:
        return {
            "is_recording": self.is_recording,
            "filename": self.filename if self.is_recording else None,
            "duration_seconds": time.monotonic() - self.started if self.is_recording else 0.0,
            "sample_count": self.sample_count if self.is_recording else 0,
            "last_recording": self.last_recording,
            "error": self.error,
        }

    def start(self) -> dict:
        if self.is_recording:
            return self.status()
        # Exclusive creation plus a UUID prevents overwriting earlier recordings.
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        filename = f"eeg_{stamp}_{uuid4().hex}.csv"
        file = (self.directory / filename).open("x", newline="", encoding="utf-8")
        try:
            writer = csv.writer(file)
            writer.writerow(CSV_FIELDS)
            file.flush()
        except BaseException:
            file.close()
            raise
        self.file, self.writer, self.filename = file, writer, filename
        self.started = time.monotonic()
        self.last_flush = self.started
        self.sample_count = 0
        self.error = None
        return self.status()

    def append(self, sample: EEGSample) -> None:
        if not self.is_recording:
            return
        try:
            self.writer.writerow([sample['timestamp'], *(sample['eeg'][name] for name in CHANNELS)])
            self.sample_count += 1
            if time.monotonic() - self.last_flush >= 1:
                self.file.flush()
                self.last_flush = time.monotonic()
        except OSError:
            self.error = "CSV write failed; recording stopped. Check disk space and permissions."
            self.stop()

    def stop(self) -> dict | None:
        if not self.is_recording:
            return self.last_recording
        metadata = {
            "filename": self.filename,
            "duration_seconds": time.monotonic() - self.started,
            "sample_count": self.sample_count,
            "download_url": f"/api/recording/download/{self.filename}",
        }
        file = self.file
        self.file = self.writer = None
        try:
            file.close()  # flushes the buffer, including on graceful app shutdown
        except OSError:
            self.error = "CSV close failed; the file may be incomplete."
        self.last_recording = metadata
        return metadata

    def download_path(self, filename: str) -> Path | None:
        if not FILE_PATTERN.fullmatch(filename):
            return None
        path = self.directory / filename
        if self.is_recording and filename == self.filename:
            return None
        if path.is_symlink() or not path.is_file():
            return None
        return path
