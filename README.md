# EMOTIV EEG Monitor

简易 EMOTIV Cortex EEG 数据监控系统的第二阶段：FastAPI 后端、模拟 EEG 数据源及原生 Canvas 实时五通道仪表盘。

当前页面显示连接状态、Battery（--）、Sampling Rate（128 Hz）、Channels（5），并绘制 AF3、AF4、T7、T8、Pz 五个通道最近 8 秒的模拟波形。Connected 表示 WebSocket 连接成功，不表示真实设备已连接。

本阶段仅使用模拟数据，不连接真实设备、不调用 Cortex API、不记录 CSV、不使用数据库或用户系统。

## 项目结构

```text
backend/
    main.py          # HTTP、静态文件及 /ws/eeg 推送入口
    simulator.py     # 数据源接口及模拟信号实现
frontend/
    index.html
    app.js
    style.css
tests/
    test_simulator.py
requirements.txt
.gitignore
README.md
```

## Windows 启动步骤

需要 Python 3.10 或更新版本。以下命令均在仓库根目录运行。

1. 创建虚拟环境：

   ```powershell
   py -3 -m venv .venv
   ```

2. 安装依赖（直接使用虚拟环境的 Python，无需激活）：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. 启动 FastAPI：

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
   ```

4. 浏览器访问 `http://127.0.0.1:8000/`，应看到 **EMOTIV EEG Monitor** 仪表盘。

按 `Ctrl+C` 停止服务。如果希望激活虚拟环境，PowerShell 使用 `.\.venv\Scripts\Activate.ps1`，CMD 使用 `.venv\Scripts\activate.bat`；不激活也可完成上述所有步骤。

## Linux / 云环境

在仓库根目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

云环境服务监听 `0.0.0.0:8000`。若平台提供端口转发或网页预览，请选择端口 `8000`、路径 `/`。浏览器中的 `127.0.0.1` 指向浏览器所在机器，不能直接访问云容器；`0.0.0.0` 仅为监听地址，也不是浏览器访问地址。环境重新启动后需要重新启动服务。

## 路由与检查

- `GET /`：仪表盘首页。
- `GET /static/style.css` 和 `GET /static/app.js`：前端资源。
- `/docs`：FastAPI 自动生成的接口文档。
- `WS /ws/eeg`：以目标 128 Hz 持续推送模拟 EEG。前端自动连接，断线后以 1～10 秒指数退避重连。
- `WS /ws`：保留第一阶段的静态状态回复接口。

静态文件路径基于 `main.py` 的位置解析。首页应通过 FastAPI 访问，不要直接打开 HTML 文件。

## 模拟信号与数据协议

每个通道使用不同的基础正弦频率（8～11.2 Hz）、小幅高频振荡（17～22.2 Hz）、低频振荡、独立高斯噪声和受限随机幅值变化。结果限幅到 ±100 μV。这些波形仅用于功能演示，不代表真实生理信号。

`/ws/eeg` 每条消息格式：

```json
{"timestamp": 1700000000.123, "eeg": {"AF3": 12.3, "AF4": -8.5, "T7": 22.1, "T8": -15.2, "Pz": 3.8}}
```

时间戳为秒，数据单位为 μV。`EEGSource.stream()` 提供异步迭代接口；未来可通过 `backend/main.py` 中的 `create_eeg_source()` 替换实现。每个连接拥有独立模拟源，后端不保留历史队列；慢客户端会跳过错过的采样时刻，断线时关闭生成器。128 Hz 是目标发送速率，实际速率受调度和网络影响。

前端使用原生 Canvas，绘制横轴为相对时间、纵轴为 ±100 μV 的五幅波形。最多保留 1024 条样本（128 × 8 秒），使用环形缓冲区覆盖旧数据。绘制频率最多 30 帧/秒，ResizeObserver 负责尺寸适配。HTTPS 页面自动使用 WSS；WebSocket 地址沿用页面的主机和端口，无第三方图表 CDN。

## 测试

Windows：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Linux：

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check frontend/app.js
```

浏览器打开首页后应显示 Connected，并出现五通道波形。停止服务应显示 Disconnected；重新启动后页面自动重连。下一阶段可增加暂停、显示窗口设置与信号质量提示，再单独规划真实 Cortex 接入。
