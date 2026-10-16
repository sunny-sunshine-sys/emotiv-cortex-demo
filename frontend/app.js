"use strict";

let CHANNELS = [];
const WINDOW_SECONDS = 8;
let MAX_SAMPLES = 1024;
let samples = new Array(MAX_SAMPLES);
let head = 0;
let count = 0;
let socket = null;
let retryTimer = null;
let retryDelay = 1000;
let stopped = false;
let dirty = true;
let sourceReady = false;
let charts = [];
let lastTimestamp = null;
let demoPhase = 0;

const COLORS = ["#67d6c0", "#79b8ff", "#c3a6ff", "#f0ba76", "#f68bad", "#8bd3ff", "#d6e68a", "#e8a8ff"];

const $ = (id) => document.getElementById(id);
const statusEl = $("device-status");

function configureChannels(names) {
    CHANNELS = names;
    $("waveforms").replaceChildren();
    head = count = 0;
    samples.fill(undefined);
    $("channels").textContent = String(names.length);
    $("channel-list").textContent = names.length ? names.join(" · ") : "等待数据";
    charts = CHANNELS.map((channel, index) => {
        const row = document.createElement("div");
        row.className = "channel-row";
        const title = document.createElement("h3");
        title.textContent = channel;
        const canvas = document.createElement("canvas");
        canvas.setAttribute("aria-label", channel + " EEG waveform");
        row.append(title, canvas);
        $("waveforms").append(row);
        return { channel, canvas, color: COLORS[index % COLORS.length], context: canvas.getContext("2d") };
    });
    dirty = true;
}

function applySource(state) {
    sourceReady = Boolean(state.ready);
    $("source-message").textContent = state.message || "";
    $("mode-badge").textContent = state.data_source === "simulation" ? "SIMULATION" : "EXTERNAL EEG";
    $("device-name").textContent = state.device || (state.data_source === "simulation" ? "模拟数据源" : "等待设备");
    const rate = Number(state.sampling_rate);
    $("sampling-rate").textContent = rate > 0 ? rate.toFixed(rate % 1 ? 1 : 0) + " Hz" : "--";
    $("sample-count").textContent = Number(state.accepted_samples || 0).toLocaleString();
    if (rate > 0) {
        const capacity = Math.min(8192, Math.max(128, Math.ceil(rate * WINDOW_SECONDS)));
        if (capacity !== MAX_SAMPLES) {
            MAX_SAMPLES = capacity;
            samples = new Array(capacity);
            head = count = 0;
            dirty = true;
        }
    }
    if (Array.isArray(state.channels) && state.channels.join(",") !== CHANNELS.join(",")) {
        configureChannels(state.channels);
    }
    setConnected(sourceReady && socket?.readyState === WebSocket.OPEN);
    $("input-dot").classList.toggle("on", sourceReady);
}

function setConnected(connected) {
    statusEl.textContent = connected ? "已连接" : "等待连接";
    statusEl.classList.toggle("connected", connected);
    statusEl.classList.toggle("disconnected", !connected);
}

function connect() {
    if (stopped) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    if (socket && socket.readyState < WebSocket.CLOSING) return;
    const ws = new WebSocket(protocol + "//" + location.host + "/ws/eeg");
    socket = ws;
    ws.onopen = () => {
        if (socket !== ws || stopped) return;
        retryDelay = 1000;
        setConnected(sourceReady);
    };
    ws.onmessage = (event) => {
        if (socket !== ws || stopped) return;
        let sample;
        try { sample = JSON.parse(event.data); } catch { return; }
        if (!sample || !Number.isFinite(sample.timestamp) || !sample.eeg) return;
        const names = Object.keys(sample.eeg);
        if (!names.length || names.length > 64) return;
        if (!names.every((name) => Number.isFinite(sample.eeg[name]))) return;
        if (names.join(",") !== CHANNELS.join(",")) configureChannels(names);
        if (lastTimestamp !== null && sample.timestamp <= lastTimestamp) return;
        lastTimestamp = sample.timestamp;
        samples[head] = sample;
        head = (head + 1) % MAX_SAMPLES;
        count = Math.min(count + 1, MAX_SAMPLES);
        dirty = true;
    };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
        if (socket !== ws) return;
        setConnected(false);
        if (!stopped) {
            retryTimer = setTimeout(connect, retryDelay);
            retryDelay = Math.min(retryDelay * 2, 10000);
        }
    };
}

function drawChart({ canvas, context: ctx, channel, color }, now) {
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

    const left = 48, top = 12, right = width - 10, bottom = height - 26;
    const x = (time) => left + (time - (now - WINDOW_SECONDS)) / WINDOW_SECONDS * (right - left);
    let low = Infinity, high = -Infinity;

    for (let i = 0; i < count; i++) {
        const sample = samples[(head - count + i + MAX_SAMPLES) % MAX_SAMPLES];
        if (sample && sample.timestamp >= now - WINDOW_SECONDS && channel in sample.eeg) {
            low = Math.min(low, sample.eeg[channel]);
            high = Math.max(high, sample.eeg[channel]);
        }
    }
    if (!Number.isFinite(low) || high - low < 1) {
        low = -100; high = 100;
    } else {
        const center = (low + high) / 2;
        const span = Math.max(40, (high - low) * 1.2);
        low = center - span / 2; high = center + span / 2;
    }

    const y = (value) => top + (high - value) / (high - low) * (bottom - top);
    ctx.font = "11px system-ui";
    ctx.lineWidth = 1;
    for (const value of [low, (low + high) / 2, high]) {
        ctx.strokeStyle = "#25344a";
        ctx.beginPath(); ctx.moveTo(left, y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
        ctx.fillStyle = "#8fa2bc";
        ctx.fillText(Math.round(value), 5, y(value) + 4);
    }
    for (let seconds = -8; seconds <= 0; seconds += 2) {
        const position = x(now + seconds);
        ctx.strokeStyle = "#1b283b";
        ctx.beginPath(); ctx.moveTo(position, top); ctx.lineTo(position, bottom); ctx.stroke();
        ctx.fillStyle = "#8fa2bc";
        ctx.fillText(seconds + "s", position - 9, height - 7);
    }

    ctx.save();
    ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    let previous = null;
    for (let i = 0; i < count; i++) {
        const sample = samples[(head - count + i + MAX_SAMPLES) % MAX_SAMPLES];
        if (!sample || sample.timestamp < now - WINDOW_SECONDS || !(channel in sample.eeg)) continue;
        const px = x(sample.timestamp), py = y(sample.eeg[channel]);
        if (!previous || sample.timestamp - previous > 0.1) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
        previous = sample.timestamp;
    }
    ctx.stroke();
    ctx.restore();
}

let lastDraw = 0;
function render(time) {
    if (dirty && time - lastDraw >= 1000 / 30) {
        const latest = count ? samples[(head - 1 + MAX_SAMPLES) % MAX_SAMPLES] : null;
        const now = latest?.timestamp ?? Date.now() / 1000;
        charts.forEach((chart) => drawChart(chart, now));
        dirty = false;
        lastDraw = time;
    }
    if (!stopped) requestAnimationFrame(render);
}

async function refreshSource() {
    try {
        const response = await fetch("/api/source/status", { cache: "no-store" });
        applySource(await response.json());
    } catch {
        sourceReady = false;
        setConnected(false);
    }
}

async function sendDemoSample() {
    const now = Date.now() / 1000;
    if (lastTimestamp !== null && now <= lastTimestamp) return;
    demoPhase += 0.12;
    const eeg = {};
    const names = CHANNELS.length ? CHANNELS : ["C3", "C4", "P3", "P4"];
    names.forEach((name, i) => {
        eeg[name] = Number((25 * Math.sin(demoPhase * (1 + i * 0.04)) + 7 * Math.sin(demoPhase * 3 + i) + (Math.random() - 0.5) * 5).toFixed(3));
    });
    try {
        const response = await fetch("/api/eeg/ingest", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({timestamp: now, eeg, device: "Demo EEG Device", sampling_rate: 128})
        });
        const result = await response.json();
        $("ingest-result").textContent = response.ok ? "✓ 测试样本已接收：" + result.channels.join(", ") : "× " + (result.detail || "接收失败");
    } catch (error) {
        $("ingest-result").textContent = "× " + error.message;
    }
}

$("send-demo").addEventListener("click", sendDemoSample);

function recordingUI(state) {
    const active = Boolean(state.is_recording);
    $("recording-status").textContent = active ? "正在记录" : "未记录";
    $("recording-status").classList.toggle("active", active);
    const seconds = Math.floor(active ? state.duration_seconds : (state.last_recording?.duration_seconds || 0));
    $("recording-time").textContent = String(Math.floor(seconds / 60)).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0");
    const last = state.last_recording;
    $("recording-file").textContent = last
        ? last.filename + " · " + last.sample_count + " samples · " + last.duration_seconds.toFixed(2) + " s"
        : "尚无完成的记录";
    $("recording-error").textContent = state.error || "";
    $("start-recording").disabled = active || !sourceReady;
    $("stop-recording").disabled = !active;
    $("download-csv").disabled = active || !last;
}

async function refreshRecording() {
    try {
        const [recording, source] = await Promise.all([
            fetch("/api/recording/status", {cache: "no-store"}).then((r) => r.json()),
            fetch("/api/source/status", {cache: "no-store"}).then((r) => r.json())
        ]);
        applySource(source);
        recordingUI(recording);
    } catch (error) {
        $("recording-error").textContent = "无法读取记录状态：" + error.message;
    }
}

async function recordingAction(action) {
    $("start-recording").disabled = $("stop-recording").disabled = $("download-csv").disabled = true;
    try {
        const response = await fetch("/api/recording/" + action, {method: "POST"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "请求失败");
        if (action === "stop") {
            recordingUI({is_recording: false, last_recording: result, error: result.error});
        } else {
            recordingUI(result);
        }
    } catch (error) {
        $("recording-error").textContent = error.message;
    } finally {
        await refreshRecording();
    }
}

$("start-recording").addEventListener("click", () => recordingAction("start"));
$("stop-recording").addEventListener("click", () => recordingAction("stop"));
$("download-csv").addEventListener("click", () => {
    const link = document.createElement("a");
    link.href = "/api/recording/download/" + encodeURIComponent(window.lastRecordingFilename || "");
    link.click();
});

setInterval(refreshSource, 1000);
setInterval(refreshRecording, 1000);
configureChannels([]);
connect();
refreshSource();
refreshRecording();
requestAnimationFrame(render);

window.addEventListener("pagehide", () => {
    stopped = true;
    clearTimeout(retryTimer);
    if (socket) socket.close();
});