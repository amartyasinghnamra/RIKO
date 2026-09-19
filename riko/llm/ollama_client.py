"""
Wraps Ollama's /api/chat endpoint with streaming.

Listens for "user_message" on the bus, runs the request in a background
thread (so the GTK main loop never blocks), and publishes "ai_token" for
each streamed chunk plus "ai_done" when the response is complete.
"""

import json
import threading
import urllib.request

from riko.core.bus import bus
from riko.config.settings import settings


class OllamaClient:
    def __init__(self):
        self._history: list[dict] = []
        bus.subscribe("user_message", self.handle_user_message)

    def handle_user_message(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})
        thread = threading.Thread(target=self._stream_response, daemon=True)
        thread.start()

    def _stream_response(self) -> None:
        url = f"{settings.ollama_host}/api/chat"
        payload = {
            "model": settings.model_name,
            "messages": self._history,
            "stream": True,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )

        full_reply = ""
        try:
            with urllib.request.urlopen(req) as response:
                for line in response:
                    if not line.strip():
                        continue
                    chunk = json.loads(line.decode("utf-8"))
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_reply += token
                        bus.publish("ai_token", token)
                    if chunk.get("done"):
                        break
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            bus.publish("ai_error", str(exc))
            return

        self._history.append({"role": "assistant", "content": full_reply})
        bus.publish("ai_done", full_reply)


# Instantiating this subscribes it to the bus immediately
ollama_client = OllamaClient()
