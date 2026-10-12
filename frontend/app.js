"use strict";

// Initial dashboard state only. No device or WebSocket connection in stage one.
const initialState = {
    "device-status": "Disconnected",
    battery: "--",
    "sampling-rate": "128 Hz",
    channels: "5",
};

for (const [id, value] of Object.entries(initialState)) {
    document.getElementById(id).textContent = value;
}
