#!/usr/bin/env python3

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ============================================================
# RIKO FRONTEND CONTROL STATE
# ============================================================

recording_enabled = threading.Event()
recording_enabled.set()	
backend_state = "idle"

_state_lock = threading.Lock()


def set_state(state):
    global backend_state

    with _state_lock:
        backend_state = state


def get_state():
    with _state_lock:
        return backend_state


# ============================================================
# HTTP API
# ============================================================

class RikoHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):

        body = json.dumps(data).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.end_headers()

        self.wfile.write(body)


    def do_OPTIONS(self):

        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.end_headers()


    def do_GET(self):

        if self.path == "/api/status":

            self._send_json({
                "state": get_state(),
                "recording_enabled":
                    recording_enabled.is_set()
            })

            return


        self._send_json(
            {"error": "Not found"},
            404
        )


    def do_POST(self):

        if self.path == "/api/start":

            recording_enabled.set()
            set_state("listening")

            print("🌐 Frontend → START")

            self._send_json({
                "success": True,
                "state": "listening"
            })

            return


        if self.path == "/api/stop":

            recording_enabled.clear()
            set_state("idle")

            print("🌐 Frontend → STOP")

            self._send_json({
                "success": True,
                "state": "idle"
            })

            return


        self._send_json(
            {"error": "Not found"},
            404
        )


    def log_message(self, format, *args):
        # Keep the Riko console clean.
        pass


def start_control_server(
    host="127.0.0.1",
    port=8765,
):

    server = ThreadingHTTPServer(
        (host, port),
        RikoHandler,
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="riko-control-server",
    )

    thread.start()

    print(
        f"🌐 Riko control API: "
        f"http://{host}:{port}"
    )

    return server
