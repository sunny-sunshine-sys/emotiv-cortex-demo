"""Dependency preparation and owned backend lifecycle for the desktop launcher."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4


class LauncherError(Exception):
    pass


def default_mode(root: Path) -> str:
    try:
        mode = json.loads((root / '.launcher/settings.json').read_text())['mode']
        if mode in ('simulation', 'configured'):
            return mode
    except (OSError, ValueError, KeyError, TypeError):
        pass
    sources = {}
    try:
        for line in (root / '.env').read_text(encoding='utf-8-sig').splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() in ('EEG_SOURCE', 'EMG_SOURCE'):
                sources[key.strip()] = value.split('#', 1)[0].strip().strip('"\'').lower()
    except (OSError, UnicodeError):
        pass
    for name in ('EEG_SOURCE', 'EMG_SOURCE'):
        if name in os.environ:
            sources[name] = os.environ[name].strip().lower()
    return 'configured' if 'external' in sources.values() else 'simulation'


class DesktopRuntime:
    def __init__(self, root: Path, report=lambda message: None, port=8000):
        self.root = root.resolve()
        self.report = report
        self.port = port
        self.runtime_dir = self.root / '.launcher'
        self.runtime_dir.mkdir(exist_ok=True)
        self.log_path = self.runtime_dir / 'launcher.log'
        self.python = self.root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.process = None
        self.url = f'http://127.0.0.1:{port}/'
        self.opener = build_opener(ProxyHandler({}))  # local health checks bypass proxies

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def _options(self):
        return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

    def _run(self, args):
        with self.log_path.open('a', encoding='utf-8') as log:
            result = subprocess.run(args, cwd=self.root, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, **self._options())
        return result.returncode

    def ensure_environment(self):
        if sys.version_info < (3, 10):
            raise LauncherError('需要 Python 3.10 或更新版本，请重新安装 Python。')
        if not self.python.exists():
            self.report('首次运行：正在创建 Python 虚拟环境…')
            if self._run([sys.executable, '-m', 'venv', str(self.root / '.venv')]):
                raise LauncherError('创建虚拟环境失败，请查看日志，并确认已安装完整版本的 Python。')
        if self._run([str(self.python), '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)']):
            raise LauncherError('现有 .venv 不可用或 Python 版本过旧。请在文件管理器删除 .venv 文件夹后重试。')
        requirements = self.root / 'requirements.txt'
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        marker = self.root / '.venv/.emotiv-requirements.sha256'
        try:
            installed = marker.read_text().strip() == digest
        except OSError:
            installed = False
        if installed:
            installed = self._run([str(self.python), '-c', 'import fastapi, uvicorn, websockets, dotenv']) == 0
        if not installed:
            self.report('正在安装依赖，首次运行需要联网，请稍候…')
            if self._run([str(self.python), '-m', 'pip', 'install', '--disable-pip-version-check',
                          '--no-input', '-r', str(requirements)]):
                raise LauncherError('依赖安装失败。请检查网络连接，然后重试或查看日志。')
        self.report('正在检查运行环境…')
        if self._run([str(self.python), '-m', 'pip', 'check']):
            raise LauncherError('依赖检查未通过，请查看日志。')
        marker.write_text(digest, encoding='ascii')

    def start(self, mode):
        if mode not in ('simulation', 'configured'):
            raise LauncherError('请选择模拟演示或读取设备配置。')
        if self.running:
            return self.url
        # Never reuse or terminate an unknown service on this port.
        try:
            with socket.create_connection(('127.0.0.1', self.port), timeout=0.5):
                occupied = True
        except OSError:
            occupied = False
        if occupied:
            raise LauncherError(f'{self.port} 端口已被占用。请先关闭之前的启动器或服务，再重试。')
        self.ensure_environment()
        self.report('正在启动多模态采集工作台…')
        instance = uuid4().hex  # identifies this child; no network shutdown endpoint
        environment = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        if mode == 'simulation':
            environment.update(DATA_SOURCE='simulation', EEG_SOURCE='simulation', EMG_SOURCE='simulation', VISION_SOURCE='browser')
        with self.log_path.open('a', encoding='utf-8') as log:
            self.process = subprocess.Popen(
                [str(self.python), str(self.root / 'desktop/server.py'), '--port', str(self.port), '--instance', instance],
                cwd=self.root, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', env=environment, **self._options())
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if not self.running:
                raise LauncherError('服务启动失败，请点击“查看日志”检查原因。')
            try:
                with self.opener.open(self.url + '_desktop/health', timeout=1) as response:
                    document = json.load(response)
                    healthy = isinstance(document, dict) and document.get('instance') == instance
                if healthy and self.running:
                    try:
                        (self.runtime_dir / 'settings.json').write_text(json.dumps({'mode': mode}), encoding='utf-8')
                    except OSError:
                        pass  # preferences must not prevent an otherwise healthy launch
                    return self.url
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        self.stop()
        raise LauncherError('服务启动超时，请查看日志后重试。')

    def stop(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            self.report('正在停止服务并保存记录…')
            try:
                process.stdin.write('stop\n')
                process.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired as exc:
                raise LauncherError('服务尚未安全退出，请稍后再次停止。不要强制关闭正在记录的服务。') from exc
        if process.stdin:
            process.stdin.close()
        self.process = None
