# 基于脑电、肌电与视觉多模态融合的神经信号采集与分析系统

面向大学生创新创业项目的多模态生理信号采集 Demo。当前版本重点完成 **EEG 原始数据的实时接入、波形显示、记录与 CSV 数据集生成**，同时预留 EMG 肌电和视觉数据的扩展接口，为后续多模态时间同步与模型训练提供统一的数据入口。

## 当前功能

- **模拟 EEG 数据源**：无需硬件即可演示完整采集流程。
- **外部 EEG 接入**：任何能通过 HTTP / WebSocket 发送标准 EEG 数据的设备或桥接程序都可以接入。
- **实时波形**：自动识别通道，Canvas 实时绘制最近 8 秒数据。
- **状态监控**：设备名称、采样率、通道数、已接收样本数。
- **数据记录**：开始/停止采集，并生成带时间戳的 CSV。
- **快速演示**：网页按钮可以注入一组标准测试 EEG 样本。
- **多模态扩展**：界面预留 EEG、EMG、Vision、Fusion 四个模块。

## 数据链路

```text
EEG设备 ──┐
          │
EMG设备 ──┼──→ 设备桥接/树莓派/电脑 ──→ 本系统 ──→ 实时波形
          │                              │
摄像头 ───┘                              └──→ CSV数据集
                                                   │
                                                   ↓
                                            时间同步 / 模型训练
```

网页本身不绑定某一家 EEG 厂商。设备端只需要把数据转换成统一格式：

```json
{
  "timestamp": 1760612400.123,
  "eeg": {
    "C3": 12.4,
    "C4": -8.2,
    "P3": 6.7,
    "P4": -4.1
  },
  "device": "My EEG Device",
  "sampling_rate": 250
}
```

其中：

- `timestamp`：秒，且同一设备的数据必须递增。
- `eeg`：通道名 → 电压值，单位 μV。
- `device`：可选，设备名称。
- `sampling_rate`：可选，Hz。

## 项目结构

```text
backend/
    main.py             # FastAPI 服务、EEG 接入接口和 WebSocket
    external_source.py  # 通用外部 EEG 数据源
    simulator.py        # 模拟 EEG 数据源
    stream.py           # 数据流广播
    recorder.py         # CSV 数据记录
frontend/
    index.html           # 项目网页
    app.js               # 实时波形、设备状态和记录控制
    style.css             # 页面样式
tests/
    test_simulator.py
    test_recorder.py
data/                    # 本地 CSV 数据
requirements.txt
.env.example
README.md
```

## Windows 启动

需要 Python 3.10 或更新版本。

在项目根目录 PowerShell 执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器访问：

```text
http://127.0.0.1:8000/
```

默认使用模拟数据：

```dotenv
DATA_SOURCE=simulation
```

## 真实 EEG 接入

将 `.env` 中的数据源切换为：

```dotenv
DATA_SOURCE=external
```

然后重启服务。

### HTTP

向：

```text
POST /api/eeg/ingest
```

发送上面的 JSON 数据即可。

如果设置了 `EEG_INGEST_TOKEN`，需要同时发送：

```text
X-EEG-Token: 你的令牌
```

### WebSocket

连接：

```text
ws://电脑IP:8000/ws/eeg/ingest
```

然后逐条发送标准 EEG JSON。

网页端会自动连接：

```text
ws://电脑IP:8000/ws/eeg
```

并显示接收到的数据。

## 数据记录

网页点击：

1. **开始记录**
2. 进行 EEG 数据采集
3. **停止记录**
4. **下载 CSV**

文件保存于 `data/`。

CSV 基本格式：

```text
timestamp,C3,C4,P3,P4
1760612400.123,12.4,-8.2,6.7,-4.1
...
```

原始数据保留在 CSV 中，网页波形只是可视化，不作为数据源。

## 接口

| 接口 | 作用 |
| --- | --- |
| `GET /` | 项目网页 |
| `GET /api/source/status` | 查看 EEG 数据源状态 |
| `POST /api/eeg/ingest` | HTTP 接收外部 EEG |
| `WS /ws/eeg/ingest` | WebSocket 接收外部 EEG |
| `WS /ws/eeg` | 向网页推送实时 EEG |
| `POST /api/recording/start` | 开始 CSV 记录 |
| `POST /api/recording/stop` | 停止 CSV 记录 |
| `GET /api/recording/status` | 查看记录状态 |
| `GET /api/recording/download/{filename}` | 下载已完成 CSV |

FastAPI 自动接口文档：

```text
http://127.0.0.1:8000/docs
```

## 测试

Windows：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check frontend/app.js
```

GitHub Actions 还包含模拟数据冒烟测试，会自动验证服务启动、模拟 EEG 数据、记录和 CSV 下载链路。

## 项目定位

本版本首先解决：

> **“有没有一个可以真正接收 EEG 原始数据并形成训练数据集的网页系统？”**

答案是有。

后续只需要增加对应设备的数据桥接程序，就可以逐步接入：

- EEG
- EMG 肌电
- 摄像头/视觉
- 时间同步
- 多模态数据融合
- AI 模型训练与推理

因此网页前端和采集后端不需要绑定某一种硬件，可以随着你们最终确定的 EEG、EMG 和摄像头硬件继续扩展。
