# 基于脑电、肌电与视觉多模态融合的神经信号采集与分析系统

简易 EMOTIV Cortex EEG 数据监控系统的第四阶段：FastAPI、simulation / EMOTIV Cortex 数据源、动态通道实时仪表盘及 CSV 记录与下载。

默认 simulation 模式显示 AF3、AF4、T7、T8、Pz 五通道、128 Hz 和最近 8 秒波形。emotiv 模式按 Cortex 返回的实际通道生成图表和 CSV 表头。Device Status 的 Connected 表示浏览器连接正常且数据源正在提供样本；具体模式和错误显示在页面标题下。Battery 暂未接入电池状态，仍显示 --。

保留 simulation 的全部功能；仅在 `DATA_SOURCE=emotiv` 时连接本机 Cortex。不使用数据库或用户系统。此实现已通过本地 mock 测试，尚未经过真实设备验证。

## 项目结构

```text
backend/
    main.py          # HTTP、静态文件及 /ws/eeg 推送入口
    simulator.py     # 数据源接口及模拟信号实现
    cortex_client.py # 验证 TLS 的 WSS / JSON-RPC 客户端
    cortex_source.py # Cortex 认证流程及 EEG 适配
    certificates/
        rootCA.pem   # 官方公开 Cortex CA，非私钥
    stream.py        # 统一数据流与有限队列广播
    recorder.py      # 流式 CSV 记录
frontend/
    index.html
    app.js
    style.css
tests/
    test_simulator.py
    test_recorder.py
    test_cortex.py
data/                # 本地 CSV（Git 仅保存 .gitkeep）
requirements.txt
.env.example
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
- `WS /ws/eeg`：持续推送所选数据源的统一 EEG JSON。simulation 目标速率为 128 Hz。前端自动连接，断线后以 1～10 秒指数退避重连。
- `GET /api/source/status`：数据源模式、状态、可读提示、实际通道、采样率和 `ready`。不返回任何认证信息。
- `WS /ws`：保留第一阶段的静态状态回复接口。

静态文件路径基于 `main.py` 的位置解析。首页应通过 FastAPI 访问，不要直接打开 HTML 文件。

## 模拟信号与数据协议

每个通道使用不同的基础正弦频率（8～11.2 Hz）、小幅高频振荡（17～22.2 Hz）、低频振荡、独立高斯噪声和受限随机幅值变化。结果限幅到 ±100 μV。这些波形仅用于功能演示，不代表真实生理信号。

`/ws/eeg` 每条消息格式：

```json
{"timestamp": 1700000000.123, "eeg": {"AF3": 12.3, "AF4": -8.5, "T7": 22.1, "T8": -15.2, "Pz": 3.8}}
```

时间戳为秒，数据单位为 μV。`EEGSource.stream()` 提供异步迭代接口；未来可通过 `backend/main.py` 中的 `create_eeg_source()` 替换实现。应用生命周期中仅启动一个模拟源，同一个样本先写入正在记录的 CSV，再广播给所有 WebSocket 客户端；不为记录生成第二套数据。每个客户端队列最多 256 条，慢客户端丢弃旧显示样本，不阻塞记录。断线时释放订阅，生成器与记录仍继续运行。128 Hz 是目标发送速率，实际速率受调度和网络影响。

前端使用原生 Canvas，为实际通道分别绘图。横轴为相对时间，纵轴单位 μV；simulation 保持 ±100 μV，真实数据带较大直流偏置时自动缩放，不裁剪原始值。环形缓冲区按实际采样率 × 8 秒配置，硬上限 8192 条；simulation 仍为 1024 条。绘制频率最多 30 帧/秒，ResizeObserver 负责尺寸适配。HTTPS 页面自动使用 WSS；WebSocket 地址沿用页面的主机和端口，无第三方图表 CDN。

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

浏览器打开首页后应显示 Connected，并出现五通道波形。停止服务应显示 Disconnected；重新启动后页面自动重连。单元测试覆盖信号、记录格式、精确数据一致性、唯一文件名、关闭刷新、慢客户端队列、Cortex JSON-RPC/WSS、认证及错误场景。Cortex 测试需要 `openssl` 命令生成临时测试证书；只建立本地 WSS mock，不接触真实 Launcher、账号或设备。生产运行无需 openssl 命令。Windows 可使用 Git for Windows 附带的 openssl，或仅先运行 simulation / recorder 测试。

## CSV 记录与下载

1. 点击 **Start Recording**，状态变为 **Recording**，显示记录时长，开始按钮禁用、停止按钮启用。
2. 点击 **Stop Recording**，文件关闭后显示文件名、记录时长和样本数，启用 **Download CSV**。
3. 点击 **Download CSV** 下载刚完成的文件。新的记录不会覆盖之前的文件。

记录是当前服务的全局操作，多浏览器共享同一记录状态。即使所有网页关闭或 WebSocket 断开，记录仍持续；直到点击停止或服务正常退出。请按需停止记录，避免长期占用磁盘。前端每秒查询后端状态，状态接口暂时不可用时禁用操作并显示错误。

文件保存到项目的 `data/`，文件名为 `eeg_YYYY-MM-DD_HHMMSS_<唯一后缀>.csv`，命名时间为 UTC。CSV 表头为：

```text
timestamp,AF3,AF4,T7,T8,Pz
```

simulation 表头保持上述五通道，emotiv 的表头为 timestamp 加实际订阅电极通道。若掉线恢复后通道布局改变，当前记录自动关闭并提示开启新记录，防止写入错列。数据源掉线期间没有样本行，不补造数据；数据恢复且布局相同时可继续同一记录。数据直接逐行写入文件，不保存在无限增长的内存数组中；每秒刷新写缓冲，停止或正常关闭服务时关闭并刷新文件。磁盘写入失败时停止记录并在状态中报告错误。下载只允许已关闭的 CSV，不允许路径穿越或下载正在写入的文件。`data/` 数据文件由 `.gitignore` 排除，只有 `.gitkeep` 纳入版本控制。

| 接口 | 作用 |
| --- | --- |
| `POST /api/recording/start` | 开始记录；重复开始返回当前记录，不创建第二个文件 |
| `POST /api/recording/stop` | 停止并返回 `filename`、`duration_seconds`、`sample_count`、`download_url` |
| `GET /api/recording/status` | 返回 `is_recording`、当前时长、样本数、`last_recording` 及错误信息 |
| `GET /api/recording/download/{filename}` | 下载已关闭的 CSV 附件 |

尚未开始过记录时停止返回 HTTP 409。记录状态和最后一次记录元数据在内存中，服务重启后重置，已有 CSV 保留，可使用原下载路径下载。必须使用单个 Uvicorn worker（默认即为一个）；多个 worker 会创建独立数据源和记录状态。开发模式 `--reload` 重启会结束当前记录。`Ctrl+C`、正常 SIGTERM 会安全关闭文件；强制结束进程或断电无法保证缓冲数据完全保存。


## EMOTIV 真机运行（Windows 本机）

云容器的 `localhost` 与你的 Windows 电脑不同，云端无法访问个人电脑的 `wss://localhost:6868`。以下步骤必须在安装 Launcher、连接头环的同一台 Windows 电脑上执行。

1. 安装并启动 **EMOTIV Launcher**（包含 Cortex 服务）。
2. 在 Launcher 登录 **EmotivID**，确认账号和许可证支持所需设备与 raw EEG 流。
3. 在 [EMOTIV 开发者入口](https://www.emotiv.com/developer/) 注册 **Cortex App**。
4. 获取该 App 的 **Client ID / Client Secret**。不要写入 Python、JavaScript、README 或提交到 Git。
5. 在仓库根目录将 `.env.example` 复制为 `.env`，用本地编辑器填入凭据。不要覆盖已有 `.env`。`.env` 已被 Git 忽略。
6. 设置 `.env` 中 `DATA_SOURCE=emotiv`：

   ```dotenv
   DATA_SOURCE=emotiv
   EMOTIV_CLIENT_ID=
   EMOTIV_CLIENT_SECRET=
   EMOTIV_CA_CERT=
   EMOTIV_HEADSET_ID=
   ```

   空凭据仅为示例，需在本地填写；本项目不创建密钥。`EMOTIV_HEADSET_ID` 可选，不填时优先使用已 connected 的设备。进程环境变量优先于 `.env`，修改配置后需要重启服务。

7. 按前述 Windows 步骤创建虚拟环境、安装依赖，然后运行：

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
   ```

8. 首次运行时，在 **EMOTIV Launcher 批准此应用**。尚未批准时网页显示提示，后端每 5 秒自动重试。
9. 打开并连接头环，保持 Launcher 登录。程序扫描设备，必要时调用 connect；无设备时显示 **No EMOTIV headset detected.**，服务继续运行。
10. 在本机浏览器访问 `http://127.0.0.1:8000/`。看到 **EMOTIV Cortex mode**、Connected 及真实通道波形后，可开始记录并下载 CSV。

要切回模拟模式，只需设置 `DATA_SOURCE=simulation` 并重启；模拟模式不需要 Launcher 或任何凭据。DATA_SOURCE 仅接受 simulation / emotiv，无隐式回退模拟，以免误将模拟数据当作真实数据。

### Cortex API 流程与错误

连接本机 WSS → getCortexInfo → requestAccess → controlDevice(refresh) → queryHeadsets → 必要时 controlDevice(connect) 并等待 connected → authorize → createSession(status=active，验证返回 active/activated) → subscribe(streams=["eeg"])。

单一接收任务按唯一 JSON-RPC id 分发请求响应，同时把流消息放入有限队列。请求具有超时，断线会结束等待中的请求。订阅返回的 `cols` 决定电极名和下标；COUNTER、INTERPOLATED、RAW_CQ、MARKERS 等非电极字段不进入 EEG 或 CSV。支持标准 EEG 电极命名（如五通道 Insight 和十四通道 EPOC）；未知/重复布局明确报错，不猜测下标。时间戳直接使用 Cortex 的 `time`，过滤缺失、非有限值和非递增样本。

会话持有的 cortexToken 仅保留在后端内存；不发到前端、不写入 CSV 或日志。退出时尽力 unsubscribe、updateSession(status=close)，然后关闭 WSS；已断开的连接无法保证服务端收到清理请求。Cortex/头环断线、超时和 API 错误均显示状态并重试，不终止 FastAPI。账号/许可证缺少 EEG 权限时显示 **Raw EEG stream is not available for this Cortex license.**。未就绪时开始记录返回 HTTP 409，不生成空的“真机记录”。实际采样率优先采用设备/订阅元数据，缺失时由流时间戳估算；不强行假设所有真机都是 128 Hz。

### 自签名证书与安全

默认连接 `wss://localhost:6868`，使用随项目附带的官方公开 EMOTIV CA `backend/certificates/rootCA.pem`。保留证书链验证（CERT_REQUIRED）和 localhost 主机名验证；不使用 CERT_NONE，不禁用全局 TLS 校验。连接 Cortex 时显式绕过 HTTP 代理，只连接本机服务。

CA 来自 [EMOTIV 官方示例](https://github.com/Emotiv/cortex-v2-example/blob/c7f561b91346f746d174a2ea5ddf4247e6fe43b9/certificates/rootCA.pem)，它是公开信任证书，不包含私钥或 Client Secret。如果 Launcher 升级更换 CA，先从可信官方渠道核对新证书，再通过 `EMOTIV_CA_CERT` 指向可信 PEM 文件（例如 `C:/trusted/emotiv-rootCA.pem`）并重启。若仍有证书或主机名错误，检查 Launcher 版本及证书配置，不通过关闭校验绕过错误。

### 本阶段验证范围

已实现真实数据源代码和本地 verified WSS mock 验证，但**没有完成真实 EMOTIV 设备测试**。Mock 测试不能证明个人账号、许可证、Launcher 版本或实际硬件的兼容性；这些需按以上步骤在 Windows 本机验证。
