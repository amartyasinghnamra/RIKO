const API_BASE = "http://127.0.0.1:8765/api";

async function apiRequest(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {})
        }
    });

    if (!response.ok) {
        throw new Error(
            `Riko API ${response.status}: ${response.statusText}`
        );
    }

    return response.json();
}

async function startRecording() {
    return apiRequest("/start", {
        method: "POST"
    });
}

async function stopRecording() {
    return apiRequest("/stop", {
        method: "POST"
    });
}

async function getStatus() {
    return apiRequest("/status");
}
