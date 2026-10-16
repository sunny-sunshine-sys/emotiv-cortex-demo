"""Small Tk desktop UI, launched with pythonw on Windows (no terminal)."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from runtime import DesktopRuntime, LauncherError, default_mode

ROOT = Path(__file__).resolve().parent.parent


class Launcher:
    def __init__(self, window):
        self.window = window
        self.events = queue.Queue()
        self.runtime = DesktopRuntime(ROOT, report=lambda text: self.events.put(('progress', text)))
        self.busy = False
        self.was_running = False
        window.title('多模态采集工作台 — 启动器')
        window.geometry('680x480')
        window.minsize(620, 480)
        window.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(window, padding=24)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='多模态数据采集工作台', font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='选择数据模式，然后点击启动。无需输入终端命令。').pack(anchor='w', pady=(8, 18))
        mode_row = ttk.Frame(frame)
        mode_row.pack(anchor='w')
        self.mode = tk.StringVar(value=default_mode(ROOT))
        self.mode_buttons = []
        for title, value in [('模拟演示', 'simulation'), ('读取设备配置', 'configured')]:
            button = ttk.Radiobutton(mode_row, text=title, variable=self.mode, value=value)
            button.pack(side='left', padx=(0, 20))
            self.mode_buttons.append(button)
        row = ttk.Frame(frame)
        row.pack(anchor='w', pady=20)
        self.start_button = ttk.Button(row, text='启动并打开网页', command=self.start)
        self.start_button.pack(side='left', padx=(0, 10))
        self.stop_button = ttk.Button(row, text='停止服务', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=(0, 10))
        self.open_button = ttk.Button(row, text='打开网页', command=self.open_browser, state='disabled')
        self.open_button.pack(side='left')
        self.status = tk.StringVar(value='尚未启动。首次运行会自动安装依赖，需要连接网络。')
        ttk.Label(frame, textvariable=self.status, wraplength=550).pack(anchor='w', pady=(0, 12))
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.pack(fill='x')
        tools = ttk.Frame(frame)
        tools.pack(anchor='w', pady=14)
        ttk.Button(tools, text='设备配置 (.env)', command=self.open_config).pack(side='left', padx=(0, 10))
        ttk.Button(tools, text='查看日志', command=self.open_log).pack(side='left')
        ttk.Label(frame, text='关闭浏览器不会停止服务。退出此启动器会安全停止服务并保存实验数据集。', wraplength=550).pack(anchor='w')
        window.after(100, self.poll)

    def controls(self):
        running = self.runtime.running
        self.start_button.configure(state='disabled' if self.busy or running else 'normal')
        self.stop_button.configure(state='normal' if running and not self.busy else 'disabled')
        self.open_button.configure(state='normal' if running and not self.busy else 'disabled')
        for button in self.mode_buttons:
            button.configure(state='disabled' if running or self.busy else 'normal')

    def background(self, operation, event):
        self.busy = True
        self.progress.start()
        self.controls()
        def work():
            try:
                result = operation()
                self.events.put((event, result))
            except Exception as error:
                try:
                    with self.runtime.log_path.open('a', encoding='utf-8') as log:
                        traceback.print_exc(file=log)
                except OSError:
                    pass
                message = str(error) if isinstance(error, LauncherError) else '操作失败，请查看日志，并确认项目文件夹可写。'
                self.events.put(('error', message))
        threading.Thread(target=work, daemon=True).start()

    def start(self):
        mode = self.mode.get()
        self.background(lambda: self.runtime.start(mode), 'started')

    def stop(self):
        self.background(self.runtime.stop, 'stopped')

    def close(self):
        if self.busy:
            messagebox.showinfo('请稍候', '当前操作尚未完成，请等待后再关闭。')
        elif self.runtime.running:
            self.background(self.runtime.stop, 'closed')
        else:
            self.window.destroy()

    def open_browser(self):
        if not webbrowser.open(self.runtime.url):
            messagebox.showinfo('打开网页', f'请在浏览器地址栏输入：{self.runtime.url}')

    def open_text(self, path):
        try:
            if os.name == 'nt':
                subprocess.Popen(['notepad.exe', str(path)])
            else:
                webbrowser.open(path.as_uri())
        except OSError:
            messagebox.showinfo('文件位置', str(path))

    def open_config(self):
        path = ROOT / '.env'
        try:
            if not path.exists():
                with path.open('x', encoding='utf-8') as file:
                    file.write((ROOT / '.env.example').read_text(encoding='utf-8'))
            self.open_text(path)
        except OSError:
            messagebox.showerror('配置文件', '无法打开配置文件，请确认项目文件夹可写。')

    def open_log(self):
        try:
            self.runtime.log_path.touch(exist_ok=True)
            self.open_text(self.runtime.log_path)
        except OSError:
            messagebox.showerror('日志文件', '无法打开日志，请确认项目文件夹可写。')

    def poll(self):
        try:
            while True:
                event, result = self.events.get_nowait()
                if event == 'progress':
                    self.status.set(result)
                    continue
                self.busy = False
                self.progress.stop()
                if event == 'started':
                    self.was_running = True
                    self.status.set(f'服务已运行：{result}')
                    self.open_browser()
                elif event == 'stopped':
                    self.was_running = False
                    self.status.set('服务已停止，记录文件已关闭。')
                elif event == 'closed':
                    self.window.destroy()
                    return
                elif event == 'error':
                    self.status.set(result)
                    messagebox.showerror('多模态采集工作台', result)
                self.controls()
        except queue.Empty:
            pass
        if self.was_running and not self.busy and not self.runtime.running:
            self.was_running = False
            self.status.set('服务已退出，请查看日志后重新启动。')
            self.controls()
        self.window.after(100, self.poll)


if __name__ == '__main__':
    window = tk.Tk()
    if sys.version_info < (3, 10):
        window.withdraw()
        messagebox.showerror('Python 版本', '请安装 Python 3.10 或更新版本，包含 Tcl/Tk。')
    else:
        Launcher(window)
        window.mainloop()
