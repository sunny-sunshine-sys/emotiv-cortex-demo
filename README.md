# EMOTIV EEG Monitor

简易 EMOTIV Cortex EEG 数据监控系统的第一阶段：FastAPI 后端和 HTML、CSS、原生 JavaScript 仪表盘骨架。

当前页面显示 Device Status（Disconnected）、Battery（--）、Sampling Rate（128 Hz）、Channels（5）及 EEG Signal 占位区域。采样率和通道数量仅为预设显示值，不代表已连接设备。

本阶段不生成模拟 EEG、不绘制曲线、不连接真实设备、不调用 Cortex API，也不记录数据。

## 项目结构

```text
backend/
    main.py          # HTTP、静态文件和 WebSocket 基础入口
    simulator.py     # 占位文件，尚未实现模拟功能
frontend/
    index.html
    app.js
    style.css
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
- `WS /ws`：预留 WebSocket 通道，收到文本消息后仅回复 `{"device_status":"Disconnected"}`。不发送 EEG 数据；首页暂不连接该通道。

静态文件路径基于 `main.py` 的位置解析。首页应通过 FastAPI 访问，不要直接打开 HTML 文件。

下一阶段可独立实现模拟 EEG 数据和 WebSocket 推送，再加入曲线显示。
