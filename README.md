# 脑电、肌电与视觉多模态数据采集与处理系统

面向大学生创新创业项目的多模态生理信号采集与训练数据构建 Demo。系统围绕 EEG 脑电 + EMG 肌电 + Vision 视觉三路信息，重点实现设备接入、统一时间轴、实验事件标记、数据质量记录和训练数据集导出，为后续多模态动作识别模型训练提供可追溯的数据基础。

本版本不绑定任何一家脑电厂商。真实设备只需要通过设备桥接程序，把采样数据转换为统一 JSON 后推送到本系统。

## 项目对应的研究路线

~~~text
实验任务设计
      ↓
EEG ─────────┐
EMG ─────────┼──→ 数据接入 → 统一时间轴 → 质量检查
Vision ──────┘                         ↓
                                  实验事件标注
                                       ↓
                               多模态训练数据集
                                       ↓
                           单模态 / 多模态模型验证
~~~

项目计划书中的核心研究内容包括：

1. 多模态采集硬件集成
2. 多源数据同步与关联
3. 数据预处理与质量管理
4. 实验标注与数据集构建
5. 多模态模型训练与验证

当前网页 Demo 重点把前四项中的“采集—同步—标注—数据集”链路跑通；模型训练接口已预留，不在没有真实实验数据时虚构准确率或 F1 结果。

## 当前可演示功能

### 1. EEG 脑电

- 内置模拟 EEG 数据源，无需硬件即可演示。
- 5 通道实时波形。
- 记录采样率、设备名称、通道数和样本数。
- 支持真实 EEG 设备通过 HTTP / WebSocket 接入。
- 原始数据按时间戳进入统一数据流。

### 2. EMG 肌电

- 内置模拟表面肌电数据源。
- 4 通道实时活动波形。
- 支持真实肌电手环通过 HTTP / WebSocket 接入。
- 与 EEG 使用同一套时间戳和实验标记体系。

### 3. Vision 视觉

- 浏览器可直接打开摄像头进行实时预览。
- 记录视觉帧时间戳和帧编号。
- 可切换“视觉模拟”用于比赛现场无摄像头时的完整演示。
- 实际项目中可由摄像头或树莓派数据桥接程序替换视觉输入。

> 浏览器摄像头预览与服务端数据记录是两个层次：当前版本把视频帧元数据写入数据集，后续可继续接入视频文件或视频流存储。

### 4. 时间同步

系统同时保留：

- 设备时间戳
- 主机接收时间
- 实验事件标记

网页实时计算当前三类数据的时间差，并显示统一时间轴状态。实际研究阶段可进一步加入起止同步事件、漂移估计和对齐误差统计。

### 5. 实验标注

网页提供：

- 准备
- 静息
- 动作开始
- 动作结束
- 质量检查

每个标记带 Unix 时间戳，并写入 events.csv，用于将 EEG、EMG 和视觉片段关联到同一次动作任务。

### 6. 多模态数据集导出

一次实验结束后可以导出 ZIP，包含：

~~~text
signals.csv
vision.csv
events.csv
manifest.json
~~~

其中 signals.csv 采用长表格式：

~~~text
timestamp,modality,channel,value,device,sampling_rate,session_id
...
~~~

这样 EEG 与 EMG 即使采样率不同，也可以在后续预处理阶段按照统一时间窗口重新关联。

## 真实设备接入

### EEG

设置：

~~~dotenv
EEG_SOURCE=external
~~~

然后重启服务。

HTTP：

~~~text
POST /api/eeg/ingest
~~~

JSON：

~~~json
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
~~~

WebSocket：

~~~text
ws://电脑IP:8000/ws/eeg/ingest
~~~

### EMG

设置：

~~~dotenv
EMG_SOURCE=external
~~~

HTTP：

~~~text
POST /api/emg/ingest
~~~

JSON：

~~~json
{
  "timestamp": 1760612400.123,
  "emg": {
    "FCR": 0.42,
    "ECR": 0.31,
    "BIC": 0.18,
    "TRI": 0.22
  },
  "device": "My EMG Band",
  "sampling_rate": 200
}
~~~

WebSocket：

~~~text
ws://电脑IP:8000/ws/emg/ingest
~~~

如果设置 EEG_INGEST_TOKEN，HTTP 请求需要携带：

~~~text
X-EEG-Token: 你的令牌
~~~

WebSocket 则使用：

~~~text
?token=你的令牌
~~~

## Windows 双击启动（推荐）

1. 安装 [Python 3.10 或更新版本](https://www.python.org/downloads/windows/)，保留 Tcl/Tk、pip、Python Launcher 组件；此版本不是独立 exe。
2. 下载 ZIP 后完整解压到可写目录，不要在 ZIP 内直接打开文件。
3. 双击根目录 **`Start EEG Monitor.pyw`**，打开中文启动器，无需输入终端命令。
4. 默认选择 **模拟演示**，点击 **启动并打开网页**。首次自动创建 `.venv` 并联网安装依赖；之后复用环境。
5. 网页自动打开 `http://127.0.0.1:8000/`，可体验 EEG、EMG、视觉、实验标注与数据集导出。
6. 完成后点击 **停止服务** 或关闭启动器，程序会正常停止后台并完成正在记录的数据集。关闭浏览器不会停止服务。

**读取设备配置** 模式保留进程环境及 `.env` 配置，不会强制覆盖 EEG_SOURCE、EMG_SOURCE 或 VISION_SOURCE。点击 **设备配置 (.env)** 用记事本编辑；文件不存在时只复制 `.env.example`，不会覆盖已有配置。按上方设备桥接协议将需要的 EEG_SOURCE / EMG_SOURCE 设为 external，其余可保持 simulation。当前版本采用厂商无关的外部输入，不再直接连接旧版 Cortex API。切换模式需先停止服务。

启动器提供 **打开网页**、**查看日志**，并在服务就绪后才打开浏览器。日志和模式偏好位于被 Git 忽略的 `.launcher/`，不保存设备令牌。端口 8000 被占用时会提示，不会结束其他进程。启动器通过内部管道安全停止自己的后台进程，不开放网络停止接口。

默认仅监听本机 127.0.0.1，设备桥接可在同机运行；若需要其他电脑或树莓派从局域网推送，请使用手动方式并设置 `--host 0.0.0.0`，按外部接入说明配置令牌。

如果 `.pyw` 被编辑器关联，可双击备用入口 **`Start EEG Monitor.vbs`**（需要 Windows VBScript 可用）。云端已验证依赖准备、HTTP、记录保存与安全停止；Windows 窗口及双击关联仍需在本机验证。

## Windows 手动启动（备用）

需要 Python 3.10 或更新版本。

在项目根目录 PowerShell 执行：

~~~powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
~~~

浏览器访问：

~~~text
http://127.0.0.1:8000/
~~~

首次运行建议保持：

~~~dotenv
EEG_SOURCE=simulation
EMG_SOURCE=simulation
VISION_SOURCE=browser
~~~

这样无需真实硬件就可以完整展示三路数据、同步、标注和数据集导出。

## API

| 接口 | 作用 |
| --- | --- |
| GET / | 多模态采集网页 |
| GET /api/session/status | 获取三路设备、同步和记录状态 |
| GET /api/source/status | EEG 状态兼容接口 |
| POST /api/eeg/ingest | 接收外部 EEG |
| POST /api/emg/ingest | 接收外部 EMG |
| WS /ws/eeg/ingest | WebSocket 接收 EEG |
| WS /ws/emg/ingest | WebSocket 接收 EMG |
| WS /ws/eeg | 向网页推送 EEG |
| WS /ws/emg | 向网页推送 EMG |
| POST /api/vision/frame | 接收视觉帧元数据 |
| POST /api/events/mark | 写入实验事件标记 |
| POST /api/recording/start | 开始一次实验数据集记录 |
| POST /api/recording/stop | 结束实验并生成数据集 ZIP |
| GET /api/recording/status | 查看当前记录状态 |
| GET /api/recording/download/{filename} | 下载数据集 ZIP |
| GET /api/quality/status | 查看数据质量与同步状态 |

FastAPI 自动接口文档：

~~~text
http://127.0.0.1:8000/docs
~~~

## 项目结构

~~~text
backend/
    main.py                 # FastAPI 服务与多模态 API
    multimodal.py           # 多模态流、同步和数据集导出
    external_source.py      # EEG / EMG 外部设备桥接输入
    simulator.py            # EEG / EMG 模拟数据源
    stream.py               # 旧版 EEG 流兼容模块
    recorder.py             # 旧版 EEG CSV 记录兼容模块

desktop/                    # 中文启动器、依赖准备及安全停止
Start EEG Monitor.pyw       # Windows 双击入口
Start EEG Monitor.vbs       # 备用入口

frontend/
    index.html              # 多模态实验工作台
    app.js                  # 波形、摄像头、标注、记录控制
    style.css               # 页面样式

tests/
    test_recorder.py
    test_simulator.py
    test_desktop.py

data/
    # 本地实验数据与导出包

.env.example
README.md
requirements.txt
~~~

## 后续开发

按照项目计划，下一步建议依次完成：

1. 确定最终 EEG / EMG / 摄像头硬件及数据接口。
2. 为真实设备分别编写轻量级数据桥接程序。
3. 完善同步事件、时钟漂移估计和对齐误差记录。
4. 加入滤波、异常片段识别和质量标签。
5. 完善视频文件保存与动作片段截取。
6. 建立参与者、动作类别、重复次数和采集批次的数据规范。
7. 在真实数据集基础上加入 EEG、EMG、Vision 单模态基线及多模态融合模型。
8. 使用准确率、宏平均 F1 和混淆矩阵进行实验验证。

## 数据与隐私

实验参与者数据应在知情同意和学校相关要求下采集。建议使用匿名参与者编号，不在训练数据中直接保存姓名等身份信息，并对原始数据进行受控存储。
