"""
Minimal synchronous event bus.

Modules never import each other directly. Instead:
  - ui/window.py publishes "user_message" when the user hits send
  - llm/ollama_client.py subscribes to "user_message", publishes "ai_token" / "ai_done"
  - ui/chat_view.py subscribes to "ai_token" / "ai_done" to render streaming text

This keeps every module swappable later (e.g. Ollama -> another backend)
without touching UI code.
"""

from collections import defaultdict
from typing import Callable, Any


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, callback: Callable) -> None:
        self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable) -> None:
        if callback in self._subscribers[event]:
            self._subscribers[event].remove(callback)

    def publish(self, event: str, *args: Any, **kwargs: Any) -> None:
        for callback in list(self._subscribers[event]):
            callback(*args, **kwargs)


# Single shared instance imported by every module
bus = EventBus()
