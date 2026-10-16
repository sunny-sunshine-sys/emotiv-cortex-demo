"""Desktop core tests; Windows GUI/file associations require local validation."""
import csv
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener

from desktop.runtime import DesktopRuntime, LauncherError, default_mode


class DesktopTests(unittest.TestCase):
    def test_mode_precedence_and_invalid_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('EEG_SOURCE="external"\nEMG_SOURCE=simulation\n')
            with patch.dict(os.environ, {'EEG_SOURCE': '', 'EMG_SOURCE': 'external'}):
                self.assertEqual(default_mode(root), 'configured')
                (root / '.launcher').mkdir()
                (root / '.launcher/settings.json').write_text('{"mode":"simulation"}')
                self.assertEqual(default_mode(root), 'simulation')
                (root / '.launcher/settings.json').write_text('invalid json')
                self.assertEqual(default_mode(root), 'configured')
            with patch.dict(os.environ, {'EEG_SOURCE': 'simulation', 'EMG_SOURCE': 'simulation'}):
                self.assertEqual(default_mode(root), 'simulation')

    def test_occupied_port_does_not_touch_existing_service(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            manager = DesktopRuntime(Path(directory), port=listener.getsockname()[1])
            with patch.object(manager, 'ensure_environment') as prepare:
                with self.assertRaises(LauncherError):
                    manager.start('simulation')
                prepare.assert_not_called()
                self.assertIsNone(manager.process)
            self.assertNotEqual(listener.fileno(), -1)

    def test_invalid_mode_rejected_without_process(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = DesktopRuntime(Path(directory))
            with self.assertRaises(LauncherError):
                manager.start('invalid')
            self.assertFalse(manager.running)

    def test_stop_without_process_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = DesktopRuntime(Path(directory))
            manager.stop()
            self.assertFalse(manager.running)
