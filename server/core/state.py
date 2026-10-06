"""Shared runtime state for Riko's existing voice pipeline.

This module intentionally contains no pipeline behavior or service imports.
Later extraction stages will pass ``runtime_state`` to services instead of
having them import mutable globals from one another.
"""

from __future__ import annotations

import os
import queue
import threading


class RuntimeState:
    """Own the mutable state shared by the voice pipeline's services.

    Values and queue capacities mirror the current module-level runtime state.
    Chunk indexes remain local to an individual response turn.
    """

    def __init__(self) -> None:
        # LLM/chunker -> TTS worker boundary.
        self.tts_work_queue: queue.Queue = queue.Queue(
            maxsize=int(os.getenv("RIKO_TTS_QUEUE_SIZE", "100"))
        )

        # Filler and fast-response -> playback boundary.
        self.audio_queue: queue.Queue = queue.Queue(
            maxsize=int(os.getenv("RIKO_AUDIO_QUEUE_SIZE", "100"))
        )

        # Process-wide shutdown signal.
        self.stop_event = threading.Event()

        # Completed response audio: (response_id, chunk_id) -> WAV path.
        self.ordered_audio: dict[tuple[int, int], str] = {}
        self.ordered_audio_duration: dict[tuple[int, int], float] = {}
        self.ordered_audio_lock = threading.Lock()
        self.ordered_audio_event = threading.Event()

        # Response sequencing. Chunk indexes intentionally are not global.
        self.CURRENT_RESPONSE_ID = 0
        self.RESPONSE_SEQUENCE_LOCK = threading.Lock()

        # Ordered-playback cursor. Its current initial value is one.
        self.next_playback_chunk = 1

        # Completion accounting for the active conversation turn.
        self.turn_tts_pending = 0
        self.turn_audio_finished = threading.Event()
        self.turn_audio_lock = threading.Lock()

        # Active-response lifecycle signals.
        self.response_active = threading.Event()
        self.response_generation_done = threading.Event()

        # Short-term LLM conversation memory.
        self.conversation_history: list[dict[str, str]] = []
        self.conversation_history_lock = threading.Lock()

    def allocate_next_response_id(self) -> int:
        """Atomically allocate and return the next monotonic response ID."""
        with self.RESPONSE_SEQUENCE_LOCK:
            self.CURRENT_RESPONSE_ID += 1
            return self.CURRENT_RESPONSE_ID


# The future pipeline receives this one shared instance explicitly.
runtime_state = RuntimeState()
