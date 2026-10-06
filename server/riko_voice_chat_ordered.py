import os
import time
import uuid
import queue
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel

from realtime_riko_ordered import (
    stream_ollama,
    audio_queue,
    playback_worker,
    stop_event,
    OLLAMA_MODEL,
    CPU_THREADS,
    prepare_fillers,
)
from llm.qwen import USER_NAME

from riko_control import (
    recording_enabled,
    set_state,
    start_control_server,
)


# ============================================================
# CONFIG
# ============================================================

AUDIO_DIR = Path("audio/realtime")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_MS = 30
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_MS / 1000)

# How loud speech must be compared with background noise.
# Lower = more sensitive.
ENERGY_MULTIPLIER = 2.2

# Minimum RMS level so absolute silence doesn't trigger.
MIN_RMS = 0.008

# Speech must continue for this long before we accept it.
MIN_SPEECH_SECONDS = 0.25

# Silence after speech = end of utterance.
END_SILENCE_SECONDS = 0.85

# Ignore tiny noises before declaring speech.
START_TIMEOUT = 3.0

# ============================================================
# FAST RESPONSE ROUTER
#
# Common conversational intents bypass Qwen completely.
#
# If a matching WAV exists:
#
#     STT → intent → WAV → speaker
#
# Otherwise we fall back to the normal LLM pipeline.
# ============================================================

FAST_RESPONSE_DIR = AUDIO_DIR / "fast_responses"
FAST_RESPONSE_DIR.mkdir(parents=True, exist_ok=True)


FAST_RESPONSES = {

    "audio_check": {
        "patterns": (
            "am i audible",
            "can you hear me",
            "can you hear",
            "do you hear me",
            "are you hearing me",
            "is my voice audible",
            "can you hear my voice",
            "can you still hear me",
        ),
        "wav": "audio_check.wav",
    },

    "presence_check": {
        "patterns": (
            "are you there",
            "are you still there",
            "you there",
            "riko are you there",
            "riko you there",
        ),
        "wav": "im_here.wav",
    },

    "greeting": {
        "patterns": (
            "hello",
            "hi riko",
            "hey riko",
            "hello riko",
            "good morning",
            "good afternoon",
            "good evening",
        ),
        "wav": "hello.wav",
    },

    "understanding_check": {
        "patterns": (
            "did you understand",
            "do you understand",
            "did you get that",
            "do you get that",
            "got that",
            "did you hear that",
        ),
        "wav": "got_it.wav",
    },

    "repeat_request": {
        "patterns": (
            "say that again",
            "repeat that",
            "can you repeat that",
            "repeat yourself",
            "what did you say",
        ),
        "wav": "repeat_that.wav",
    },

    "thanks": {
        "patterns": (
            "thank you",
            "thanks",
            "thanks riko",
            "thank you riko",
        ),
        "wav": "youre_welcome.wav",
    },

}


def _normalize_fast_text(text):
    import re

    text = text.lower().strip()

    # Remove punctuation while keeping spaces.
    text = re.sub(r"[^a-z0-9\s']", " ", text)

    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def detect_fast_response(text):
    """
    Return a fast-response intent if the utterance is
    clearly one of our deterministic interactions.

    Exact phrase matching is deliberately conservative.
    We don't want Riko accidentally bypassing Qwen.
    """

    normalized = _normalize_fast_text(text)

    if not normalized:
        return None

    for intent, config in FAST_RESPONSES.items():

        for pattern in config["patterns"]:

            pattern = _normalize_fast_text(pattern)

            if normalized == pattern:
                return intent

    return None


def play_fast_response(intent):
    """
    Play a pre-generated response WAV.

    Returns True only when the WAV actually exists and was
    queued. Otherwise the caller should use the normal LLM.
    """

    config = FAST_RESPONSES.get(intent)

    if not config:
        return False

    filename = config["wav"]
    path = FAST_RESPONSE_DIR / filename

    if not path.exists():
        print(
            f"⚡ Fast response detected [{intent}], "
            f"but WAV is missing: {path.name}"
        )
        return False

    if path.stat().st_size <= 1000:
        print(
            f"⚠️ Fast response WAV invalid: {path.name}"
        )
        return False

    print(
        f"⚡ FAST RESPONSE [{intent}] → {path.name}"
    )

    audio_queue.put(str(path))

    # Do not reopen microphone while the instant response
    # is actually playing.
    audio_queue.join()

    print(
        f"✓ Fast response finished: {intent}"
    )

    return True


# ============================================================
# PROACTIVE IDLE CONVERSATION
# ============================================================

IDLE_ENABLED = True

# First test: Riko speaks after 12 seconds of silence.
IDLE_SECONDS = 15.0

# Prevent repeated proactive messages.


# ============================================================
# MICROPHONE VAD
# ============================================================

def record_until_silence():

    frames = []

    speaking = False
    speech_started = None
    last_voice = None

    noise_samples = []

    start_time = time.monotonic()

    # Timestamp used by the proactive idle-conversation timer.
    last_user_activity = time.monotonic()

    print()
    print("🎙️ Listening...")

    # --------------------------------------------------------
    # Keep ONE microphone stream alive for both:
    #
    #   calibration
    #       ↓
    #   speech detection
    #       ↓
    #   recording
    #
    
    # --------------------------------------------------------

    try:

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=BLOCK_SIZE,
        ) as stream:

            # ------------------------------------------------
            # Quick background-noise calibration.
            # ------------------------------------------------

            print(
                "   calibrating microphone...",
                end="",
                flush=True,
            )

            calibration_end = (
                time.monotonic() + 0.45
            )

            while (
                time.monotonic()
                < calibration_end
            ):

                data, overflowed = stream.read(
                    BLOCK_SIZE
                )

                mono = data[:, 0].copy()

                rms = float(
                    np.sqrt(
                        np.mean(
                            np.square(mono)
                        ) + 1e-12
                    )
                )

                noise_samples.append(rms)

            if noise_samples:

                noise_floor = float(
                    np.median(noise_samples)
                )

            else:

                noise_floor = 0.0

            threshold = max(
                MIN_RMS,
                noise_floor * ENERGY_MULTIPLIER,
            )

            print(
                f"\r   mic threshold: "
                f"{threshold:.4f}       "
            )

            # ------------------------------------------------
            # Actual conversation listening.
            # ------------------------------------------------

            while True:

                data, overflowed = stream.read(
                    BLOCK_SIZE
                )

                mono = data[:, 0].copy()

                rms = float(
                    np.sqrt(
                        np.mean(
                            np.square(mono)
                        ) + 1e-12
                    )
                )

                now = time.monotonic()

                voiced = rms > threshold

                if voiced:

                    if not speaking:

                        speaking = True
                        speech_started = now

                        print(
                            "🔴 Speaking...",
                            flush=True,
                        )

                    last_voice = now
                    frames.append(mono)

                elif speaking:

                    # Keep a little silence at the end so
                    # Whisper gets natural word endings.
                    frames.append(mono)

                    silence_time = (
                        now - last_voice
                        if last_voice is not None
                        else 0
                    )

                    speech_time = (
                        now - speech_started
                        if speech_started is not None
                        else 0
                    )

                    if (
                        speech_time
                        >= MIN_SPEECH_SECONDS
                        and
                        silence_time
                        >= END_SILENCE_SECONDS
                    ):
                        break

                elif (
                    now - start_time
                    > START_TIMEOUT
                ):

                    print(
                        "\n⚠️ No speech detected."
                    )

                    return None

    except Exception as exc:

        print(
            f"\n❌ Microphone stream error: "
            f"{exc}"
        )

        # Returning None lets the main voice loop recover
        # instead of repeatedly reading from a dead stream.
        return None

    if not frames:
        return None

    audio = np.concatenate(frames)

    if len(audio) < int(
        SAMPLE_RATE * MIN_SPEECH_SECONDS
    ):
        return None

    path = (
        AUDIO_DIR /
        f"voice_input_{uuid.uuid4().hex}.wav"
    )

    sf.write(
        str(path),
        audio,
        SAMPLE_RATE,
        subtype="PCM_16",
    )

    return path


# ============================================================
# STT
# ============================================================

print("🎙️ Loading Whisper...")

WHISPER_MODEL_PATH = Path(os.getenv(
    "RIKO_WHISPER_MODEL_PATH",
    "models/faster-whisper-base.en",
))

whisper_model = WhisperModel(
    str(WHISPER_MODEL_PATH),
    device="cpu",
    compute_type="float32",
)

print("✓ Whisper ready.")


def transcribe(path):

    started = time.perf_counter()

    segments, info = whisper_model.transcribe(
        str(path),
        language="en",
        beam_size=1,
        best_of=1,
        temperature=0,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    text = " ".join(
        segment.text.strip()
        for segment in segments
    ).strip()

    elapsed = time.perf_counter() - started

    print(
        f"🎯 STT: {elapsed:.2f}s"
    )

    return text



# ============================================================
# PROACTIVE IDLE CONVERSATION
# ============================================================

def proactive_riko():

    print("\n💭 Riko has been quiet for a while...")

    proactive_prompt = (
        f"{USER_NAME} has been silent for a while. "
        "Start a brief, natural conversation on your own. "
        "Sound like a playful anime companion who simply noticed "
        "that the user went quiet." 
        "Sometimes just continue the conversation yourself"
        "Maybe ask whether the user is okay or continue the topic naturally. "
        "MAKE HIM FEEL COMFORTABLE AND WELCOME."
         "Sometimes even roast him badly"

    )

    try:

        print("🧠 Riko is initiating a conversation...")

        stream_ollama(proactive_prompt)

        # Wait until all generated aud , io has finished.
        audio_queue.join()

        print("✓ Riko proactive message finished.")

    except Exception as exc:

        print(f"⚠️ Proactive Riko error: {exc}")


# ============================================================
# PLAYBACK
# ============================================================

playback_thread = threading.Thread(
    target=playback_worker,
    daemon=True,
)

playback_thread.start()


# ============================================================
# MAIN
# ============================================================

print()
print("=" * 54)
print("          🎀 RIKO — HANDS-FREE VOICE")
print("=" * 54)
print()
print(f"Brain       : {OLLAMA_MODEL}")
print("STT         : faster-whisper base.en")
print("TTS         : GPT-SoVITS")
print(f"CPU threads : {CPU_THREADS}")
print()
print("🎙️ Speak naturally.")
print("🤫 Pause for about 0.85s when you're finished.")
print("🚪 Say 'exit' or 'goodbye' to stop.")
print()
print("Everything is local.")
print()

# Generate/cache Riko's thinking sounds before listening starts.
prepare_fillers()

# ------------------------------------------------------------
# FRONTEND CONTROL SERVER
# ------------------------------------------------------------

control_server = start_control_server(
    host="127.0.0.1",
    port=8765,
)

print()
print("🌐 Riko frontend control enabled.")
print("   Waiting for TALK button...")
print()

# Tracks the last meaningful user activity while the
# microphone is enabled.
last_user_activity = time.monotonic()

try:

    while True:

        # Frontend controls whether the microphone is active.
        if not recording_enabled.is_set():

            # Idle conversation only exists while the user has
            # explicitly enabled the microphone.
            last_user_activity = time.monotonic()

            set_state("idle")
            time.sleep(0.05)
            continue

        recording = None

        try:

            # ------------------------------------------------
            # Automatically wait for speech.
            # ------------------------------------------------

            set_state("listening")

            recording = record_until_silence()

            if recording is None:

                # The microphone timed out without detecting speech.
                # This is our opportunity for proactive conversation.
                if IDLE_ENABLED:

                    now = time.monotonic()

                    if (
                        now - last_user_activity
                        >= IDLE_SECONDS
                    ):
                        proactive_riko()

                        # Riko just initiated a conversation.
                        # Start a fresh idle period after her speech.
                        last_user_activity = time.monotonic()

                continue

            # ------------------------------------------------
            # Transcribe.
            # ------------------------------------------------

            set_state("thinking")

            print("🎯 Transcribing...")

            user_text = transcribe(recording)

            try:
                recording.unlink(missing_ok=True)
            except Exception:
                pass

            if not user_text:
                print("⚠️ I didn't catch that.")
                continue

            # User has spoken — restart the proactive idle clock.
            last_user_activity = time.monotonic()

            # User has spoken: restart proactive-idle timer.
            last_user_activity = time.monotonic()

            print()
            print(f"🗣️ You: {user_text}")

            # ------------------------------------------------
            # FAST RESPONSE ROUTER
            #
            # Common phrases bypass Qwen entirely.
            # This is intentionally before the LLM path.
            # ------------------------------------------------

            fast_intent = detect_fast_response(user_text)

            if fast_intent:

                if play_fast_response(fast_intent):
                    # We already handled this utterance.
                    # No Qwen. No GPT-SoVITS. No filler.
                    last_user_activity = time.monotonic()
                    continue

            # ------------------------------------------------
            # Exit.
            # ------------------------------------------------

            if user_text.lower().strip(
                " .!?,'\""
            ) in {
                "exit",
                "quit",
                "goodbye",
                "bye",
            }:
                print(f"🎀 Riko: See you later, {USER_NAME}.")
                break

            # ------------------------------------------------
            # Riko.
            # ------------------------------------------------

            print("🧠 Riko...")

            started = time.perf_counter()

            set_state("speaking")

            stream_ollama(user_text)

            # Don't reopen the microphone while Riko is
            # still speaking.
            audio_queue.join()

            elapsed = time.perf_counter() - started

            print(
                f"\n✓ Riko finished ({elapsed:.2f}s)"
            )

            set_state("idle")

        except KeyboardInterrupt:
            raise

        except Exception as exc:

            print()
            print(f"❌ Voice loop error: {exc}")

            if recording:
                try:
                    recording.unlink(missing_ok=True)
                except Exception:
                    pass

            time.sleep(0.5)


except KeyboardInterrupt:

    print("\n\n🎀 Riko voice chat stopped.")


finally:

    stop_event.set()

    # Clean leftover recordings.
    for path in AUDIO_DIR.glob("voice_input_*.wav"):
        try:
            path.unlink()
        except Exception:
            pass
