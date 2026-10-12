"use strict";

const CHANNELS = ["AF3", "AF4", "T7", "T8", "Pz"];
const WINDOW_SECONDS = 8;
const MAX_SAMPLES = 128 * WINDOW_SECONDS;
const COLORS = ["#67d6c0", "#79b8ff", "#c3a6ff", "#f0ba76", "#f68bad"];
const samples = new Array(MAX_SAMPLES);
let head = 0;
let count = 0;
let socket = null;
let retryTimer = null;
let retryDelay = 1000;
let stopped = false;
let dirty = true;
const status = document.getElementById("device-status");
const charts = CHANNELS.map((channel, index) => {
    const row = document.createElement("div");
    row.className = "channel-row";
    const title = document.createElement("h3");
    title.textContent = channel;
    title.style.color = COLORS[index];
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-label", `${channel} simulated EEG waveform, microvolts over time`);
    canvas.setAttribute("role", "img");
    row.append(title, canvas);
    document.getElementById("waveforms").append(row);
    return { channel, canvas, color: COLORS[index], context: canvas.getContext("2d") };
});

function setConnected(connected) {
    status.textContent = connected ? "Connected" : "Disconnected";
    status.classList.toggle("disconnected", !connected);
    status.classList.toggle("connected", connected);
}

function connect() {
    if (stopped) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    if (socket && socket.readyState < WebSocket.CLOSING) return;
    const ws = new WebSocket(`${protocol}//${location.host}/ws/eeg`);
    socket = ws;
    ws.onopen = () => {
        if (socket !== ws || stopped) return;
        retryDelay = 1000;
        head = 0;
        count = 0;
        samples.fill(undefined);
        dirty = true;
        setConnected(true);
    };
    ws.onmessage = (event) => {
        if (socket !== ws || stopped) return;
        let sample;
        try { sample = JSON.parse(event.data); } catch { return; }
        if (!sample || !Number.isFinite(sample.timestamp) || !sample.eeg ||
            !CHANNELS.every(channel => Number.isFinite(sample.eeg[channel]))) return;
        if (count && sample.timestamp <= samples[(head - 1 + MAX_SAMPLES) % MAX_SAMPLES].timestamp) return;
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
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const left = 54, top = 12, right = width - 12, bottom = height - 28;
    const x = time => left + (time - (now - WINDOW_SECONDS)) / WINDOW_SECONDS * (right - left);
    const y = value => top + (100 - value) / 200 * (bottom - top);
    ctx.font = "11px system-ui";
    ctx.lineWidth = 1;
    for (const value of [-100, 0, 100]) {
        ctx.strokeStyle = "#2a374d";
        ctx.beginPath(); ctx.moveTo(left, y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
        ctx.fillStyle = "#9caec5";
        ctx.fillText(`${value}`, 6, y(value) + 4);
    }
    ctx.fillText("μV", 6, height - 7);
    for (let seconds = -8; seconds <= 0; seconds += 2) {
        const position = x(now + seconds);
        ctx.strokeStyle = "#202d40";
        ctx.beginPath(); ctx.moveTo(position, top); ctx.lineTo(position, bottom); ctx.stroke();
        ctx.fillStyle = "#9caec5";
        ctx.fillText(`${seconds}s`, position - 9, height - 7);
    }
    ctx.save();
    ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    let previous = null;
    for (let i = 0; i < count; i++) {
        const sample = samples[(head - count + i + MAX_SAMPLES) % MAX_SAMPLES];
        if (sample.timestamp < now - WINDOW_SECONDS) continue;
        const px = x(sample.timestamp), py = y(sample.eeg[channel]);
        if (!previous || sample.timestamp - previous > 0.1) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
        previous = sample.timestamp;
    }
    ctx.stroke(); ctx.restore();
}

let lastDraw = 0;
function render(time) {
    if (dirty && time - lastDraw >= 1000 / 30) {
        const now = count ? samples[(head - 1 + MAX_SAMPLES) % MAX_SAMPLES].timestamp : Date.now() / 1000;
        charts.forEach(chart => drawChart(chart, now));
        dirty = false;
        lastDraw = time;
    }
    if (!stopped) requestAnimationFrame(render);
}
new ResizeObserver(() => { dirty = true; }).observe(document.getElementById("waveforms"));
window.addEventListener("pagehide", () => {
    stopped = true;
    clearTimeout(retryTimer);
    if (socket) socket.close();
});
window.addEventListener("pageshow", event => {
    if (event.persisted) {
        stopped = false;
        connect();
        requestAnimationFrame(render);
    }
});
connect();
requestAnimationFrame(render);
