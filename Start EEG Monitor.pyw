"""Double-click with the standard Python windowed file association."""
import runpy
import sys
from pathlib import Path

launcher = Path(__file__).resolve().parent / 'desktop' / 'launcher.py'
sys.path.insert(0, str(launcher.parent))
try:
    runpy.run_path(str(launcher), run_name='__main__')
except Exception:
    message = '无法打开启动器。请完整解压项目到可写文件夹，并确认已安装 Python 3.10 或以上版本及 Tcl/Tk 组件。'
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, '多模态采集工作台', 16)
    else:
        raise
