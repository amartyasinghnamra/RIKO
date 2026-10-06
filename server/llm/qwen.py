"""Ollama chat client with a per-user, local-only companion profile."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from typing import Any

import requests
import yaml

from core.state import RuntimeState


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
RECENT_CONTEXT_TURNS = max(1, int(os.getenv("RIKO_RECENT_CONTEXT_TURNS", "2")))
CPU_THREADS = int(os.getenv("RIKO_CPU_THREADS", "8"))


def _load_profile() -> dict[str, str]:
    path = os.getenv("RIKO_PROFILE_PATH", "private/profile.yaml")
    try:
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return {}


PROFILE = _load_profile()
USER_NAME = PROFILE.get("user_name", "the user")
COMPANION_NAME = PROFILE.get("companion_name", "Riko")
PERSONALITY = PROFILE.get("personality", "warm, playful, and helpful")
SYSTEM_PROMPT = f"""
You are {COMPANION_NAME}, a voice companion speaking with {USER_NAME}.
Your style is {PERSONALITY}. Respond naturally, respectfully, and to the actual conversation.
Voice transcription can be wrong; ask for clarification if speech is unclear.
You are speaking aloud. Do not use Markdown, headings, code, stage directions, emojis,
or hidden reasoning. Do not claim to be human or mention these instructions.
""".strip()


def build_chat_payload(user_text: str, state: RuntimeState) -> dict[str, Any]:
    with state.conversation_history_lock:
        context_messages = list(state.conversation_history)
    return {
        "model": OLLAMA_MODEL,
        "stream": True,
        "think": False,
        "keep_alive": -1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *context_messages,
            {"role": "user", "content": user_text},
        ],
        "options": {"num_thread": CPU_THREADS},
    }


def open_chat_stream(payload: dict[str, Any], state: RuntimeState) -> Iterator[dict[str, Any]]:
    response = requests.post(OLLAMA_URL, json=payload, stream=True, timeout=(10, 300))
    response.raise_for_status()
    return _iter_chat_events(response, state)


def _iter_chat_events(response: requests.Response, state: RuntimeState) -> Iterator[dict[str, Any]]:
    for line in response.iter_lines():
        if state.stop_event.is_set():
            break
        if line:
            yield json.loads(line)


def save_completed_turn(
    state: RuntimeState,
    user_text: str,
    accumulated_text: str,
    save_user_message: bool = True,
) -> None:
    if not accumulated_text.strip():
        return
    assistant_text = re.sub(r"<FILLER:\s*[a-zA-Z_]+\s*>", "", accumulated_text, flags=re.IGNORECASE).strip()
    if not assistant_text:
        return
    with state.conversation_history_lock:
        if save_user_message:
            state.conversation_history.append({"role": "user", "content": user_text})
        state.conversation_history.append({"role": "assistant", "content": assistant_text})
        state.conversation_history[:] = state.conversation_history[-(RECENT_CONTEXT_TURNS * 2):]
