"use strict";

const WINDOW_SECONDS = 8;
const MAX_HISTORY = 1600;
const state = {
    eeg: {channels: [], samples: [], socket: null, lastTimestamp: null, colorOffset: 0},
    emg: {channels: [], samples: [], socket: null, lastTimestamp: null, colorOffset: 3},
    vision: {stream: null, timer: null, frameIndex: 0, demo: false},
    recording: null,
    markers: [],
    stopped: false,
};

const COLORS = ["#67d6c0", "#79b8ff", "#c3a6ff", "#f0ba76", "#f68bad", "#8bd3ff", "#d6e68a", "#e8a8ff"];
const $ = (id) => document.getElementById(id);

function formatNumber(value) {
    return Number(value || 0).toLocaleString();
}

function setDot(id, on) {
    $(id).classList.toggle("on", Boolean(on));
}

function configureChannels(modality, names) {
    const target = state[modality];
    if (!Array.isArray(names) || !names.length) return;
    if (names.join(",") === target.channels.join(",")) return;
    target.channels = names;
    target.samples = [];
    target.lastTimestamp = null;

    const root = $(modality === "eeg" ? "eeg-waveforms" : "emg-waveforms");
    root.replaceChildren();
    names.forEach((channel, index) => {
        const row = document.createElement("div");
        row.className = "channel-row";
        const title = document.createElement("h3");
        title.textContent = channel;
        const canvas = document.createElement("canvas");
        canvas.setAttribute("aria-label", channel + " " + modality.toUpperCase() + " waveform");
        row.append(title, canvas);
        root.append(row);
        target.colorOffset = target.colorOffset || index;
    });
}

function pushSample(modality, sample) {
    const target = state[modality];
    const values = sample?.[modality];
    if (!sample || !Number.isFinite(sample.timestamp) || !values) return;
    const names = Object.keys(values);
    if (!names.length || names.length > 64) return;
    if (!names.every((name) => Number.isFinite(values[name]))) return;
    if (target.lastTimestamp !== null && sample.timestamp <= target.lastTimestamp) return;
    configureChannels(modality, names);
    target.lastTimestamp = sample.timestamp;
    target.samples.push(sample);
    if (target.samples.length > MAX_HISTORY) target.samples.splice(0, target.samples.length - MAX_HISTORY);
}

function connectSignal(modality) {
    const target = state[modality];
    if (target.socket && target.socket.readyState < WebSocket.CLOSING) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(protocol + "//" + location.host + "/ws/" + modality);
    target.socket = ws;

    ws.onmessage = (event) => {
        if (target.socket !== ws) return;
        try { pushSample(modality, JSON.parse(event.data)); } catch {}
    };
    ws.onclose = () => {
        if (target.socket !== ws || state.stopped) return;
        setTimeout(() => connectSignal(modality), 1200);
    };
    ws.onerror = () => ws.close();
}

function drawWaveform(modality, channel, canvas, now) {
    const target = state[modality];
    const ctx = canvas.getContext("2d");
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const ratio = window.devicePixelRatio || 1;
    if (!width || !height) return;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const left = 46, top = 10, right = width - 8, bottom = height - 22;
    const minTime = now - WINDOW_SECONDS;
    const x = (time) => left + (time - minTime) / WINDOW_SECONDS * (right - left);
    let low = Infinity, high = -Infinity;

    for (const sample of target.samples) {
        if (sample.timestamp < minTime || !(channel in sample[modality])) continue;
        const value = sample[modality][channel];
        low = Math.min(low, value);
        high = Math.max(high, value);
    }

    if (!Number.isFinite(low) || high - low < 0.01) {
        low = modality === "emg" ? 0 : -80;
        high = modality === "emg" ? 1 : 80;
    } else {
        const center = (low + high) / 2;
        const span = Math.max(modality === "emg" ? 0.25 : 30, (high - low) * 1.25);
        low = center - span / 2;
        high = center + span / 2;
    }

    const y = (value) => top + (high - value) / (high - low) * (bottom - top);
    ctx.font = "10px system-ui";
    ctx.lineWidth = 1;
    [low, (low + high) / 2, high].forEach((value) => {
        ctx.strokeStyle = "#1d2a3c";
        ctx.beginPath();
        ctx.moveTo(left, y(value));
        ctx.lineTo(right, y(value));
        ctx.stroke();
        ctx.fillStyle = "#71849d";
        ctx.fillText(modality === "emg" ? value.toFixed(2) : Math.round(value), 5, y(value) + 3);
    });

    for (let seconds = -8; seconds <= 0; seconds += 2) {
        const px = x(now + seconds);
        ctx.strokeStyle = "#142235";
        ctx.beginPath();
        ctx.moveTo(px, top);
        ctx.lineTo(px, bottom);
        ctx.stroke();
        ctx.fillStyle = "#71849d";
        ctx.fillText(seconds + "s", px - 8, height - 5);
    }

    ctx.save();
    ctx.beginPath();
    ctx.rect(left, top, right - left, bottom - top);
    ctx.clip();
    ctx.strokeStyle = COLORS[(target.colorOffset + target.channels.indexOf(channel)) % COLORS.length];
    ctx.lineWidth = 1.35;
    ctx.beginPath();

    let previous = null;
    for (const sample of target.samples) {
        if (sample.timestamp < minTime || !(channel in sample[modality])) continue;
        const px = x(sample.timestamp);
        const py = y(sample[modality][channel]);
        if (previous === null || sample.timestamp - previous > 0.15) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
        previous = sample.timestamp;
    }
    ctx.stroke();
    ctx.restore();
}

function renderWaveforms() {
    ["eeg", "emg"].forEach((modality) => {
        const target = state[modality];
        const latest = target.samples[target.samples.length - 1];
        if (!latest) return;
        const now = latest.timestamp;
        const root = $(modality === "eeg" ? "eeg-waveforms" : "emg-waveforms");
        [...root.querySelectorAll("canvas")].forEach((canvas, index) => {
            const channel = target.channels[index];
            if (channel) drawWaveform(modality, channel, canvas, now);
        });
    });
    if (!state.stopped) requestAnimationFrame(renderWaveforms);
}

function updateSignalUI(modality, status) {
    const ready = Boolean(status?.ready);
    const name = modality === "eeg" ? "eeg" : "emg";
    setDot(name + "-dot", ready);
    $(name + "-status").textContent = ready ? "已连接" : "等待数据";
    $(name + "-device").textContent =
        (status?.device || "未检测到设备") + " · " + (Number(status?.sampling_rate) > 0 ? status.sampling_rate + " Hz" : "-- Hz");
    $(name + "-meta").textContent =
        (status?.channels?.length || 0) + " 通道 · " + formatNumber(status?.accepted_samples) + " samples";
}

function updateSessionUI(payload) {
    const modalities = payload.modalities || {};
    updateSignalUI("eeg", modalities.eeg);
    updateSignalUI("emg", modalities.emg);

    const vision = modalities.vision || {};
    setDot("vision-dot", Boolean(vision.ready));
    $("vision-status").textContent = vision.ready ? "已接入" : "等待摄像头";
    $("vision-detail").textContent = formatNumber(vision.frame_count) + " 帧 · " + (vision.last_action || "等待动作");

    const sync = payload.sync || {};
    const aligned = sync.state === "aligned";
    setDot("sync-dot", aligned);
    $("sync-status").textContent = aligned ? "已对齐" : "校准中";
    $("sync-detail").textContent = (sync.spread_ms ?? "--") + " ms · " + (sync.method || "统一时间轴");

    const recording = payload.recording || {};
    state.recording = recording;
    const active = Boolean(recording.is_recording);
    $("recording-status").textContent = active ? "正在采集" : "未开始实验";
    $("recording-status").classList.toggle("active", active);
    const seconds = Math.floor(Number(recording.duration_seconds || 0));
    $("recording-time").textContent =
        String(Math.floor(seconds / 60)).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0");

    $("start-recording").disabled = active || !modalities.eeg?.ready || !modalities.emg?.ready;
    $("stop-recording").disabled = !active;

    const counts = recording.signal_counts || {};
    $("eeg-count").textContent = formatNumber(counts.eeg);
    $("emg-count").textContent = formatNumber(counts.emg);
    $("vision-count").textContent = formatNumber(recording.vision_count);
    $("event-count").textContent = formatNumber(recording.event_count);
    $("label-count").textContent = formatNumber(recording.event_count) + " 个标记";

    const last = recording.last_recording;
    $("download-dataset").disabled = !last || active;
    $("dataset-file").textContent = last
        ? last.filename + " · " + last.duration_seconds.toFixed(2) + " s · 可导出"
        : "完成一次实验后可导出 signals.csv、vision.csv、events.csv 和 manifest.json";
    $("dataset-state").textContent = active ? "正在构建" : last ? "可导出" : "等待采集";
    $("dataset-state").classList.toggle("ready", Boolean(last) && !active);
    $("recording-error").textContent = recording.error || "";

    $("ready-modalities").textContent =
        [modalities.eeg, modalities.emg, modalities.vision].filter((item) => item?.ready).length + "/3";
    $("sync-spread").textContent = (sync.spread_ms ?? "--") + " ms";
    $("quality-state").textContent =
        [modalities.eeg, modalities.emg, modalities.vision].filter((item) => item?.ready).length >= 2 &&
        Number(sync.spread_ms) < 100 ? "GOOD" : "CHECKING";
}

async function refreshSession() {
    try {
        const response = await fetch("/api/session/status", {cache: "no-store"});
        if (!response.ok) throw new Error("session status " + response.status);
        updateSessionUI(await response.json());
        $("system-state").textContent = "SYSTEM READY";
    } catch (error) {
        $("system-state").textContent = "SERVER OFFLINE";
        $("source-message").textContent = "无法连接采集服务，请确认 FastAPI 已启动。";
    }
}

async function recordingAction(action) {
    $("start-recording").disabled = $("stop-recording").disabled = true;
    try {
        const body = action === "start" ? {
            participant_id: $("participant-id").value.trim() || "P001",
            task: $("task").value,
            repetition: Number($("repetition").value) || 1,
        } : undefined;
        const response = await fetch("/api/recording/" + action, {
            method: "POST",
            headers: body ? {"Content-Type": "application/json"} : undefined,
            body: body ? JSON.stringify(body) : undefined,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "请求失败");
        updateSessionUI({
            modalities: {
                eeg: {ready: true, device: "采集服务", channels: [], sampling_rate: null},
                emg: {ready: true, device: "采集服务", channels: [], sampling_rate: null},
                vision: {ready: state.vision.stream !== null},
            },
            sync: {state: "waiting", spread_ms: 0},
            recording: action === "stop" ? {is_recording: false, last_recording: result, ...result} : result,
        });
        if (action === "stop") addMarkerLocal(Date.now() / 1000, "采集结束");
    } catch (error) {
        $("recording-error").textContent = error.message;
    } finally {
        await refreshSession();
    }
}

async function markEvent(event, label) {
    const timestamp = Date.now() / 1000;
    try {
        const response = await fetch("/api/events/mark", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({timestamp, event, label, note: "browser marker"}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "标记失败");
        addMarkerLocal(result.timestamp, label);
        $("source-message").textContent = "已标记：" + label + " · " + new Date(result.timestamp * 1000).toLocaleTimeString();
    } catch (error) {
        $("source-message").textContent = "标记失败：" + error.message;
    }
}

function addMarkerLocal(timestamp, label) {
    state.markers.push({timestamp, label});
    if (state.markers.length > 30) state.markers.shift();
    $("timeline-empty").style.display = "none";
    const item = document.createElement("div");
    item.className = "timeline-marker";
    item.innerHTML = "<span></span><b>" + label + "</b><small>" +
        new Date(timestamp * 1000).toLocaleTimeString() + "</small>";
    $("timeline").append(item);
}

async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
        $("vision-message").textContent = "当前浏览器不支持摄像头访问，请使用视觉模拟或设备桥接程序。";
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({video: {width: {ideal: 1280}, height: {ideal: 720}}, audio: false});
        state.vision.stream = stream;
        state.vision.demo = false;
        const video = $("camera");
        video.srcObject = stream;
        $("camera-state").textContent = "摄像头已打开";
        $("camera-start").disabled = true;
        $("camera-stop").disabled = false;
        $("vision-message").textContent = "摄像头预览已启动；视频帧时间戳将作为视觉模态的同步参考。";
        startVisionFrames();
    } catch (error) {
        $("vision-message").textContent = "摄像头未打开：" + error.message + "。可直接使用“视觉模拟”。";
    }
}

function stopCamera() {
    if (state.vision.stream) {
        state.vision.stream.getTracks().forEach((track) => track.stop());
        state.vision.stream = null;
    }
    $("camera").srcObject = null;
    $("camera-state").textContent = "未启动";
    $("camera-start").disabled = false;
    $("camera-stop").disabled = true;
    clearInterval(state.vision.timer);
    state.vision.timer = null;
    state.vision.demo = false;
}

function startVisionFrames() {
    clearInterval(state.vision.timer);
    state.vision.timer = setInterval(sendVisionFrame, 100);
}

async function sendVisionFrame() {
    const timestamp = Date.now() / 1000;
    const frameIndex = ++state.vision.frameIndex;
    const demo = state.vision.demo;
    const action = demo ? $("task").value : "待标注";
    const confidence = demo ? 0.92 : null;
    try {
        const response = await fetch("/api/vision/frame", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                timestamp,
                frame_index: frameIndex,
                action,
                confidence,
                device: demo ? "视觉模拟器" : "浏览器摄像头",
            }),
        });
        if (!response.ok) return;
        $("frame-count").textContent = "Frame " + String(frameIndex).padStart(6, "0");
        $("vision-action").textContent = demo ? "演示动作 · " + action : "视觉帧 · 待标注";
        if (demo) drawVisionDemo();
    } catch {}
}

function startVisionDemo() {
    stopCamera();
    state.vision.demo = true;
    $("camera-state").textContent = "视觉模拟";
    $("camera-start").disabled = false;
    $("camera-stop").disabled = false;
    $("vision-message").textContent = "视觉模拟用于报名 Demo：模拟视频帧、动作标签与时间戳，不代表真实识别结果。";
    $("camera").style.display = "none";
    $("vision-canvas").style.display = "block";
    startVisionFrames();
}

function drawVisionDemo() {
    const canvas = $("vision-canvas");
    const width = canvas.clientWidth || 600;
    const height = canvas.clientHeight || 320;
    const ratio = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const t = performance.now() / 1000;
    const cx = width * 0.5;
    const shoulderY = height * 0.42;
    const elbowX = cx + Math.sin(t * 1.8) * width * 0.18;
    const elbowY = shoulderY + height * 0.14;
    const wristX = elbowX + Math.sin(t * 1.8 + 0.7) * width * 0.12;
    const wristY = elbowY - height * 0.10;

    ctx.strokeStyle = "#67d6c0";
    ctx.lineWidth = 5;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.arc(cx, height * 0.25, height * 0.10, 0, Math.PI * 2);
    ctx.moveTo(cx, height * 0.35);
    ctx.lineTo(cx, shoulderY + height * 0.17);
    ctx.moveTo(cx - width * 0.15, shoulderY);
    ctx.lineTo(cx + width * 0.15, shoulderY);
    ctx.moveTo(cx + width * 0.15, shoulderY);
    ctx.lineTo(elbowX, elbowY);
    ctx.lineTo(wristX, wristY);
    ctx.stroke();

    ctx.strokeStyle = "#79b8ff";
    ctx.lineWidth = 2;
    ctx.strokeRect(width * 0.27, height * 0.13, width * 0.46, height * 0.62);
    ctx.fillStyle = "#b8c9df";
    ctx.font = "12px system-ui";
    ctx.fillText("VISION DEMO · FRAME " + String(state.vision.frameIndex).padStart(6, "0"), 16, 22);
    requestAnimationFrame(() => state.vision.demo && drawVisionDemo());
}

$("start-recording").addEventListener("click", () => recordingAction("start"));
$("stop-recording").addEventListener("click", () => recordingAction("stop"));
$("download-dataset").addEventListener("click", () => {
    const filename = state.recording?.last_recording?.filename;
    if (!filename) return;
    const link = document.createElement("a");
    link.href = "/api/recording/download/" + encodeURIComponent(filename);
    link.click();
});
$("camera-start").addEventListener("click", startCamera);
$("camera-stop").addEventListener("click", stopCamera);
$("vision-demo").addEventListener("click", startVisionDemo);

document.querySelectorAll("[data-event]").forEach((button) => {
    button.addEventListener("click", () => markEvent(button.dataset.event, button.dataset.label));
});

$("task").addEventListener("change", () => {
    if (state.vision.demo) $("vision-action").textContent = "演示动作 · " + $("task").value;
});

setInterval(refreshSession, 800);
connectSignal("eeg");
connectSignal("emg");
refreshSession();
requestAnimationFrame(renderWaveforms);

window.addEventListener("pagehide", () => {
    state.stopped = true;
    stopCamera();
    ["eeg", "emg"].forEach((modality) => state[modality].socket?.close());
});
