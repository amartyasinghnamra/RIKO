import os
import re
import json
import time
import queue
import threading
import subprocess
from pathlib import Path

from core.state import runtime_state
from llm.qwen import (
    CPU_THREADS,
    OLLAMA_MODEL,
    build_chat_payload,
    open_chat_stream,
    save_completed_turn,
)
from process.tts_func.riko_cpu_tts import riko_cpu_tts
from tts.chunker import clean_for_speech, extract_chunks, is_speakable

# ============================================================
# RIKO REALTIME CONFIG
# ============================================================

# ============================================================
# ORDERED TTS PIPELINE
# ============================================================

# 1 = streamed chunking
# 0 = complete-response TTS
USE_CHUNKING = os.getenv("RIKO_CHUNKING", "1") != "0"

# Start with 1.
# 2+ experimentally allows concurrent GPT-SoVITS inference.
TTS_WORKERS = max(
    1,
    int(os.getenv("RIKO_TTS_WORKERS", "9"))
)

# Completed WAVs:
#     (response_id, chunk_id) -> WAV path
ordered_audio = runtime_state.ordered_audio
ordered_audio_lock = runtime_state.ordered_audio_lock
ordered_audio_event = runtime_state.ordered_audio_event

# ============================================================
# LOW-LATENCY ROLLING AUDIO BUFFER
# ============================================================

# We intentionally start quickly, then build a much deeper
# runway while playback is already happening.

BUFFER_TARGET_SECONDS = float(
    os.getenv("RIKO_BUFFER_TARGET_SECONDS", "12.0")
)

BUFFER_MIN_SECONDS = float(
    os.getenv("RIKO_BUFFER_MIN_SECONDS", "2.5")
)

BUFFER_MAX_SECONDS = float(
    os.getenv("RIKO_BUFFER_MAX_SECONDS", "20.0")
)

# Estimated/generated duration of each ordered WAV.
ordered_audio_duration = runtime_state.ordered_audio_duration



# Existing code increments chunk_index before queueing,
# therefore the first actual chunk is #1.
# Number of completed real chunks we prefer to have available
# before starting speech for a response.
#
# 1 = lowest first-audio latency
# 2 = better continuity
# 3+ = stronger buffering, higher first-audio latency
START_BUFFER_CHUNKS = max(
    1,
    int(os.getenv("RIKO_START_BUFFER_CHUNKS", "2"))
)

# Once playback has started, don't deliberately wait for more
# than the exact next chunk. This keeps the stream responsive.


# Response chunking:
# 1 = stream text into multiple TTS chunks
# 0 = wait for the complete response and synthesize it as one piece
USE_CHUNKING = os.getenv("RIKO_CHUNKING", "1") != "0"

TTS_DIR = Path("audio/realtime")
TTS_DIR.mkdir(parents=True, exist_ok=True)

# MIN_WORDS = 3
# MAX_WORDS = 7

# Only one GPT-SoVITS inference at a time.
# Multiple simultaneous inference jobs fight over the same CPU.
tts_lock = threading.Lock()

audio_queue = runtime_state.audio_queue
stop_event = runtime_state.stop_event

# ============================================================
# PLAYBACK GAP FILLERS
# ============================================================

GAP_FILLER_ENABLED = os.getenv(
    "RIKO_GAP_FILLER",
    "1",
) != "0"

# Small grace period after real audio finishes.
# This prevents fillers from appearing during tiny normal gaps.
GAP_FILLER_DELAY = float(
    os.getenv("RIKO_GAP_FILLER_DELAY", "0.35")
)

# Never allow a filler to chain immediately into another filler.
GAP_FILLER_COOLDOWN = float(
    os.getenv("RIKO_GAP_FILLER_COOLDOWN", "1.5")
)

playback_state_lock = threading.Lock()
last_real_audio_time = 0.0
last_filler_time = 0.0

# True while Riko is actively generating a response.
response_active = runtime_state.response_active

# True once Qwen + TTS have finished generating all chunks.
response_generation_done = runtime_state.response_generation_done

# ============================================================
# CACHED THINKING FILLERS
# ============================================================

FILLER_ENABLED = os.getenv("RIKO_FILLER_WORDS", "0") != "0"
FILLER_DELAY = float(os.getenv("RIKO_FILLER_DELAY", "0.50"))

FILLER_DIR = TTS_DIR / "voice_fillers"
FILLER_DIR.mkdir(parents=True, exist_ok=True)

FILLERS = [
    "Hmm...",
    "thinking...",
    "Wait...",
    "Umm...",
]

filler_paths = {}



def prepare_fillers():

    if not FILLER_ENABLED:
        return

    print("\n🎀 Loading Riko voice fillers...")

    filler_paths.clear()

    # Load EVERYTHING in the folder.
    # This means we can eventually have hundreds of WAVs
    # without changing Python code.
    for path in sorted(FILLER_DIR.glob("*.wav")):

        try:
            if path.stat().st_size <= 1000:
                continue

            filler_paths[path.stem] = str(path)

            print(f"✓ Filler: {path.name}")

        except Exception as exc:
            print(
                f"⚠️ Could not load "
                f"{path.name}: {exc}"
            )

    print(
        f"✓ Loaded {len(filler_paths)} "
        f"cached fillers.\n"
    )



def play_filler():

    if not FILLER_ENABLED or not filler_paths:
        return False

    import random

    preferred = FILLER_CONTEXTS.get(
        CURRENT_FILLER_CONTEXT,
        FILLER_CONTEXTS["thinking"],
    )

    available = [
        name for name in preferred
        if name in filler_paths
    ]

    if not available:
        available = list(filler_paths.keys())

    text = random.choice(available)
    source = filler_paths[text]

    # IMPORTANT:
    # Put the ORIGINAL cached WAV directly into the queue.
    # Do not copy it and do not delete it after playback.
    try:
        print(
            f"💭 Riko filler "
            f"[{CURRENT_FILLER_CONTEXT}]: {text}"
        )

        audio_queue.put(source)
        return True

    except Exception as exc:
        print(f"⚠️ Filler playback failed: {exc}")
        return False



def generate_tts(text, index, response_id):

    filename = (
        TTS_DIR /
        f"response_{response_id}_chunk_{index}_{time.time_ns()}.wav"
    )

    started = time.perf_counter()

    try:

        if TTS_WORKERS == 1:
            with tts_lock:
                result = riko_cpu_tts(
                    text,
                    str(filename)
                )
        else:
            # Experimental concurrent inference mode.
            result = riko_cpu_tts(
                text,
                str(filename)
            )

        elapsed = time.perf_counter() - started

        if not result or not Path(result).exists():
            print(
                f"\n❌ TTS failed "
                f"[response={response_id} chunk={index}]: {text}"
            )
            return None

        size = Path(result).stat().st_size

        if size <= 1000:
            print(
                f"\n⚠️ Ignoring invalid TTS output "
                f"[response={response_id} chunk={index}]: {text}"
            )
            Path(result).unlink(missing_ok=True)
            return None

        print(
            f"\n🔊 TTS "
            f"[response={response_id} chunk={index}] "
            f"{elapsed:.2f}s | {text}"
        )

        return str(result)

    except Exception as exc:

        print(
            f"\n❌ TTS error "
            f"[response={response_id} chunk={index}]: {exc}"
        )

        return None


def register_ordered_audio(
    response_id,
    chunk_index,
    wav_path,
):

    if not wav_path:
        return

    path = Path(wav_path)

    if not path.exists():
        return

    with ordered_audio_lock:
        ordered_audio[
            (response_id, chunk_index)
        ] = str(path)

        try:

            with wave.open(str(path), 'rb') as _w:

                _frames = _w.getnframes()

                _rate = _w.getframerate()

                ordered_audio_duration[(response_id, chunk_index)
        ] = (_frames / _rate) if _rate else 0.0

        except Exception:

            ordered_audio_duration[(response_id, chunk_index)
        ] = 0.0

    ordered_audio_event.set()
def mark_real_audio_finished():
    with runtime_state.turn_audio_lock:
        if runtime_state.turn_tts_pending > 0:
            runtime_state.turn_tts_pending -= 1

        if runtime_state.turn_tts_pending == 0:
            runtime_state.turn_audio_finished.set()


def playback_worker():

    global last_real_audio_time
    global last_filler_time
    current_response = None
    playback_started = False

    while not stop_event.is_set():

        response_id = runtime_state.CURRENT_RESPONSE_ID

        # --------------------------------------------------------
        # New response
        # --------------------------------------------------------

        if response_id != current_response:

            current_response = response_id
            runtime_state.next_playback_chunk = 1
            playback_started = False

        # --------------------------------------------------------
        # How many real chunks are already available?
        # --------------------------------------------------------

        with ordered_audio_lock:

            ready_ids = {
                cid
                for (rid, cid) in ordered_audio
                if rid == response_id
            }

            next_ready = (
                response_id,
                runtime_state.next_playback_chunk
            ) in ordered_audio

        # --------------------------------------------------------
        # INITIAL BUFFER
        #
        # Don't begin real speech until we have at least
        # START_BUFFER_CHUNKS available, unless generation has
        # already finished and chunk 1 is ready.
        # --------------------------------------------------------

        if not playback_started:

            if (
                len(ready_ids) >= START_BUFFER_CHUNKS
                or (
                    response_generation_done.is_set()
                    and next_ready
                )
            ):
                playback_started = True

            else:

                # ONLY fillers may come from audio_queue.
                #
                # No real response WAV is allowed through this
                # path anymore.
                try:
                    path = audio_queue.get(
                        timeout=0.05
                    )

                except queue.Empty:

                    ordered_audio_event.wait(
                        timeout=0.05
                    )
                    ordered_audio_event.clear()

                    continue

                if path is None:
                    audio_queue.task_done()
                    break

                path_str = str(path)

                try:
                    subprocess.run(
                        [
                            "aplay",
                            "-q",
                            "-D",
                            "pipewire",
                            path_str,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )

                finally:

                    # Fillers are normally persistent assets.
                    # Only remove generated non-filler files.
                    if "voice_fillers" not in path_str:
                        Path(path_str).unlink(
                            missing_ok=True
                        )

                    audio_queue.task_done()

                continue

        # --------------------------------------------------------
        # STRICT REAL-AUDIO ORDER
        #
        # This is the ONLY path through which response WAVs
        # reach aplay.
        # --------------------------------------------------------

        key = (
            response_id,
            runtime_state.next_playback_chunk,
        )

        with ordered_audio_lock:

            path = ordered_audio.pop(
                key,
                None
            )

        if path is not None:

            path_str = str(path)

            try:

                print(
                    f"\n▶ PLAY response={response_id} "
                    f"chunk={runtime_state.next_playback_chunk}"
                )

                subprocess.run(
                    [
                        "aplay",
                        "-q",
                        "-D",
                        "pipewire",
                        path_str,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

                mark_real_audio_finished()

                with playback_state_lock:
                    last_real_audio_time = time.monotonic()

                runtime_state.next_playback_chunk += 1

            finally:

                Path(path_str).unlink(
                    missing_ok=True
                )

            continue

        # --------------------------------------------------------
        # NEXT REAL CHUNK IS NOT READY
        #
        # We can play a filler, but ONLY while the exact next
        # response chunk is unavailable.
        # --------------------------------------------------------

        try:

            path = audio_queue.get(
                timeout=0.05
            )

        except queue.Empty:

            ordered_audio_event.wait(
                timeout=0.05
            )
            ordered_audio_event.clear()

            continue

        if path is None:

            audio_queue.task_done()
            break

        path_str = str(path)

        try:

            subprocess.run(
                [
                    "aplay",
                    "-q",
                    "-D",
                    "pipewire",
                    path_str,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

        finally:

            if "voice_fillers" not in path_str:
                Path(path_str).unlink(
                    missing_ok=True
                )

            audio_queue.task_done()

def set_filler_context(user_text):
    """
    Cheap local classification of the user's utterance.

    This does NOT call Qwen.
    It exists only to give the cached filler system
    a conversational starting point.
    """

    global CURRENT_FILLER_CONTEXT

    text = user_text.lower().strip()

    if any(x in text for x in (
        "why",
        "how",
        "what if",
        "what about",
        "explain",
        "can you",
        "could you",
        "do you",
    )):
        CURRENT_FILLER_CONTEXT = "curious"

    elif any(x in text for x in (
        "really",
        "seriously",
        "no way",
        "you're kidding",
        "are you sure",
        "i can't believe",
    )):
        CURRENT_FILLER_CONTEXT = "surprised"

    elif any(x in text for x in (
        "yes",
        "yeah",
        "exactly",
        "right",
        "correct",
        "i agree",
    )):
        CURRENT_FILLER_CONTEXT = "agreement"

    elif any(x in text for x in (
        "sorry",
        "sad",
        "bad",
        "upset",
        "worried",
    )):
        CURRENT_FILLER_CONTEXT = "empathetic"

    else:
        CURRENT_FILLER_CONTEXT = "thinking"


# ============================================================
# ABSTRACT PLAYBACK GAP FILLER
# ============================================================

GAP_FILLER_DELAY = 1.0
GAP_FILLER_COOLDOWN = 4.0


FILLER_CONTEXTS = {

    "thinking": [
        "hmm",
        "hmm_let_me_think",
        "hmm_let_me_see",
        "let_me_think",
        "lets_see",
        "lets_think",
        "umm",
        "erm",
        "one_second",
        "give_me_a_second",
        "i_need_a_second",
    ],

    "curious": [
        "interesting",
        "hmm_interesting",
        "oh_really",
        "really",
        "is_that_so",
        "oh_i_see",
        "i_see",
        "i_see_what_you_mean",
        "huh",
        "wait_what",
        "tell_me_more",
        "oh_wow",
        "now_thats_interesting",
        "this_could_be_interesting",
        "very_interesting",
    ],

    "surprised": [
        "oh",
        "ohhh",
        "oh_wow",
        "whoa",
        "whoa_okay",
        "whoa_really",
        "really",
        "are_you_serious",
        "wait_really",
        "wait_what",
        "no_way",
        "oh_dear",
        "oh_no",
        "youre_kidding",
        "seriously",
    ],

    "agreement": [
        "exactly",
        "yeah",
        "yeah_exactly",
        "right",
        "right_right",
        "alright",
        "okay",
        "okay_i_get_it",
        "got_it",
        "i_got_you",
        "makes_sense",
        "that_makes_sense",
        "fair_enough",
        "thats_fair",
        "true",
        "i_understand",
    ],

    "empathetic": [
        "ah",
        "aww",
        "dont_worry",
        "its_okay",
        "thats_alright",
        "fair_enough",
        "i_understand",
        "i_got_you",
        "im_here",
        "im_listening",
        "no_rush",
    ],
}




def choose_gap_filler():
    """
    Choose a natural short cached reaction.

    This is deliberately independent of Qwen's response text.
    The filler exists only to cover a genuine playback gap.
    """

    import random

    context = CURRENT_FILLER_CONTEXT

    candidates = [
        name
        for name in FILLER_CONTEXTS.get(
            context,
            FILLER_CONTEXTS["thinking"],
        )
        if name in filler_paths
    ]

    if not candidates:
        candidates = list(filler_paths.keys())

    if not candidates:
        return None

    return random.choice(candidates)


def gap_filler_worker():
    """
    Watch for a real playback gap while TTS is still generating.
    """

    last_filler = 0.0
    gap_started = None

    while (
        response_active.is_set()
        and not stop_event.is_set()
    ):

        # Real audio is available.
        if not audio_queue.empty():

            gap_started = None

            time.sleep(0.05)
            continue

        # If generation is finished, there is no future gap.
        if response_generation_done.is_set():
            break

        now = time.monotonic()

        if gap_started is None:
            gap_started = now

        gap = now - gap_started

        if (
            gap >= GAP_FILLER_DELAY
            and now - last_filler >= GAP_FILLER_COOLDOWN
            and not response_generation_done.is_set()
        ):

            selected = choose_gap_filler()

            if selected:

                path = filler_paths.get(selected)

                if path and audio_queue.empty():

                    print(
                        f"💭 Gap filler: {selected}"
                    )

                    audio_queue.put(path)

                    last_filler = now

        time.sleep(0.05)




# ============================================================
# EXACT PHRASE CACHE
#
# Pre-generated conversational phrases bypass GPT-SoVITS.
#
# The matcher uses:
#   normalized phrase -> WAV
#
# and always prefers the LONGEST exact match.
# ============================================================

EXACT_PHRASE_CACHE_ENABLED = (
    os.getenv("RIKO_PHRASE_CACHE", "1") != "0"
)

EXACT_PHRASE_CACHE_DIR = Path(
    os.getenv(
        "RIKO_PHRASE_CACHE_DIR",
        str(
            Path(__file__).resolve().parents[1]
            / "audio/realtime/phrase_bank"
        ),
    )
)

EXACT_PHRASE_CACHE_MAP = (
    EXACT_PHRASE_CACHE_DIR / "phrase_map.json"
)

EXACT_PHRASE_CACHE = {}
EXACT_PHRASE_CACHE_MAX_WORDS = 0


def normalize_cached_phrase(text):
    """
    Canonical lookup representation.

    This does NOT modify what Qwen said.
    It only makes lookup case/punctuation/whitespace tolerant.
    """

    text = text.lower()

    text = (
        text.replace("’", "'")
            .replace("`", "'")
    )

    text = re.sub(
        r"[.!?,;:]+",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def load_exact_phrase_cache():

    global EXACT_PHRASE_CACHE
    global EXACT_PHRASE_CACHE_MAX_WORDS

    EXACT_PHRASE_CACHE.clear()
    EXACT_PHRASE_CACHE_MAX_WORDS = 0

    if not EXACT_PHRASE_CACHE_ENABLED:
        print("⚪ Exact phrase cache disabled.")
        return

    if not EXACT_PHRASE_CACHE_MAP.exists():
        print(
            "⚪ Phrase map does not exist:"
        )
        print(
            f"   {EXACT_PHRASE_CACHE_MAP}"
        )
        return

    try:

        with open(
            EXACT_PHRASE_CACHE_MAP,
            "r",
            encoding="utf-8",
        ) as f:

            mapping = json.load(f)

    except Exception as exc:

        print(
            f"⚠️ Phrase map load failed: {exc}"
        )
        return

    for phrase, filename in mapping.items():

        if phrase.startswith("_"):
            continue

        canonical = normalize_cached_phrase(
            phrase
        )

        if not canonical:
            continue

        wav = EXACT_PHRASE_CACHE_DIR / filename

        if not wav.exists():
            continue

        try:

            if wav.stat().st_size < 1000:
                continue

        except OSError:
            continue

        EXACT_PHRASE_CACHE[
            canonical
        ] = str(wav)

        EXACT_PHRASE_CACHE_MAX_WORDS = max(
            EXACT_PHRASE_CACHE_MAX_WORDS,
            len(canonical.split()),
        )

    print(
        f"⚡ Exact phrase cache loaded: "
        f"{len(EXACT_PHRASE_CACHE)} phrase(s)"
    )

    for phrase, wav in sorted(
        EXACT_PHRASE_CACHE.items(),
        key=lambda item: (
            -len(item[0].split()),
            item[0],
        ),
    ):

        print(
            f"   ✓ {phrase} "
            f"→ {Path(wav).name}"
        )


def split_exact_cached_phrases(text):

    if (
        not EXACT_PHRASE_CACHE_ENABLED
        or not EXACT_PHRASE_CACHE
    ):
        return [
            ("tts", text)
        ]

    words = text.split()

    if not words:
        return []

    normalized_words = [
        normalize_cached_phrase(word)
        for word in words
    ]

    segments = []

    i = 0

    while i < len(words):

        best_phrase = None
        best_length = 0

        max_length = min(
            EXACT_PHRASE_CACHE_MAX_WORDS,
            len(words) - i,
        )

        # Longest exact match wins.
        for length in range(
            max_length,
            0,
            -1,
        ):

            candidate = " ".join(
                normalized_words[
                    i:i + length
                ]
            )

            if candidate in EXACT_PHRASE_CACHE:

                best_phrase = candidate
                best_length = length

                break

        if best_phrase is not None:

            original_phrase = " ".join(
                words[
                    i:i + best_length
                ]
            )

            segments.append(
                (
                    "cache",
                    original_phrase,
                    EXACT_PHRASE_CACHE[
                        best_phrase
                    ],
                )
            )

            i += best_length

        else:

            # Collect ordinary text until the next
            # cached phrase boundary.
            start = i
            i += 1

            while i < len(words):

                found = False

                max_next = min(
                    EXACT_PHRASE_CACHE_MAX_WORDS,
                    len(words) - i,
                )

                for length in range(
                    max_next,
                    0,
                    -1,
                ):

                    candidate = " ".join(
                        normalized_words[
                            i:i + length
                        ]
                    )

                    if candidate in EXACT_PHRASE_CACHE:

                        found = True
                        break

                if found:
                    break

                i += 1

            ordinary = " ".join(
                words[start:i]
            ).strip()

            if ordinary:
                segments.append(
                    (
                        "tts",
                        ordinary,
                    )
                )

    return segments


def enqueue_phrase_aware_text(
    response_id,
    text,
    chunk_index,
):
    """
    Split one Qwen chunk into cached and GPT-SoVITS
    segments while preserving exact sequence order.

    Returns the NEXT unused chunk index.
    """

    segments = split_exact_cached_phrases(
        text
    )

    if not segments:
        return chunk_index

    for segment in segments:

        kind = segment[0]

        current_index = chunk_index

        if kind == "cache":

            _, phrase_text, wav_path = segment

            print(
                f"\n⚡ CACHE queued "
                f"#{current_index}: "
                f"{phrase_text}"
            )

            print(
                f"   ↳ {wav_path}"
            )

            with runtime_state.turn_audio_lock:
                runtime_state.turn_tts_pending += 1
                runtime_state.turn_audio_finished.clear()

            tts_work_queue.put(
                (
                    response_id,
                    current_index,
                    "__CACHE__",
                    wav_path,
                )
            )

        else:

            _, tts_text = segment

            if not tts_text.strip():
                continue

            print(
                f"\n📝 TTS queued "
                f"#{current_index}: "
                f"{tts_text}"
            )

            with runtime_state.turn_audio_lock:
                runtime_state.turn_tts_pending += 1
                runtime_state.turn_audio_finished.clear()

            tts_work_queue.put(
                (
                    response_id,
                    current_index,
                    tts_text,
                )
            )

        chunk_index += 1

    return chunk_index


# Load cached WAV index at startup.
try:
    load_exact_phrase_cache()
except Exception as exc:
    print(
        f"⚠️ Phrase cache initialization failed: "
        f"{exc}"
    )


# ============================================================
# ASYNC TTS PIPELINE
# ============================================================

# Qwen and GPT-SoVITS must NEVER block each other.
tts_work_queue = runtime_state.tts_work_queue

# True while there is text waiting for / being processed by TTS.
tts_generation_active = threading.Event()

# Real TTS audio currently queued for this conversational turn.
# Set when every real TTS WAV for the current turn has finished.
turn_audio_finished = runtime_state.turn_audio_finished

# Protects turn_tts_pending.
turn_audio_lock = runtime_state.turn_audio_lock

# Prevents multiple filler schedulers.
tts_pipeline_started = False


def tts_worker(worker_id=0):
    while not stop_event.is_set():

        try:

            profile_queue_get = time.perf_counter()

            item = tts_work_queue.get(
                timeout=0.2
            )

        except queue.Empty:
            continue

        if item is None:

            tts_work_queue.task_done()
            break

        # ====================================================
        # CACHE AUDIO
        # ====================================================

        if (
            isinstance(item, tuple)
            and len(item) == 4
            and item[2] == "__CACHE__"
        ):

            print(
                f"⏱️ PROFILE: worker pickup "
                f"worker={worker_id} "
                f"t={time.perf_counter():.6f}",
                flush=True,
            )

            response_id = item[0]
            chunk_index = item[1]
            cached_path = item[3]

            try:

                print(
                    f"\n🔹 CACHE AUDIO "
                    f"[response={response_id} "
                    f"chunk={chunk_index}]"
                )

                # IMPORTANT:
                # This is already a WAV.
                # GPT-SoVITS is NEVER called.
                register_ordered_audio(
                    response_id,
                    chunk_index,
                    cached_path,
                )

            except Exception as exc:

                print(
                    f"\n❌ Cached audio error: "
                    f"{exc}"
                )

                with runtime_state.turn_audio_lock:

                    if runtime_state.turn_tts_pending > 0:
                        runtime_state.turn_tts_pending -= 1

                    if (
                        runtime_state.turn_tts_pending == 0
                        and response_generation_done.is_set()
                    ):
                        turn_audio_finished.set()

            finally:

                tts_work_queue.task_done()

            continue

        # ====================================================
        # NORMAL GPT-SOVITS AUDIO
        # ====================================================

        if (
            not isinstance(item, tuple)
            or len(item) != 3
        ):

            print(
                f"\n⚠️ Invalid TTS queue item: "
                f"{item!r}"
            )

            tts_work_queue.task_done()
            continue

        response_id, chunk_index, text = item

        tts_generation_active.set()

        try:

            path = generate_tts(
                text,
                chunk_index,
                response_id,
            )

            if path:

                register_ordered_audio(
                    response_id,
                    chunk_index,
                    path,
                )

            else:

                with runtime_state.turn_audio_lock:

                    if runtime_state.turn_tts_pending > 0:
                        runtime_state.turn_tts_pending -= 1

                    if (
                        runtime_state.turn_tts_pending == 0
                        and response_generation_done.is_set()
                    ):
                        turn_audio_finished.set()

        except Exception as exc:

            print(
                f"\n❌ Async TTS worker "
                f"{worker_id} error: "
                f"{exc}"
            )

            with runtime_state.turn_audio_lock:

                if runtime_state.turn_tts_pending > 0:
                    runtime_state.turn_tts_pending -= 1

                if (
                    runtime_state.turn_tts_pending == 0
                    and response_generation_done.is_set()
                ):
                    turn_audio_finished.set()

        finally:

            tts_work_queue.task_done()

            if tts_work_queue.empty():
                tts_generation_active.clear()



def start_tts_worker():

    global tts_pipeline_started

    if tts_pipeline_started:
        return

    tts_pipeline_started = True

    for worker_id in range(TTS_WORKERS):

        thread = threading.Thread(
            target=tts_worker,
            args=(worker_id,),
            daemon=True,
            name=f"riko-tts-worker-{worker_id}",
        )

        thread.start()

        print(
            f"✓ TTS worker {worker_id} started."
        )

    print(
        f"✓ Ordered TTS pipeline active "
        f"with {TTS_WORKERS} worker(s)."
    )
def response_filler_from_text(text):
    """
    Choose a cached reaction from the text Qwen has already
    generated.

    This is deliberately cheap. Never call an LLM here.
    """

    if not filler_paths:
        return None

    text = text.lower().strip()

    if any(x in text for x in (
        "wow",
        "whoa",
        "oh!",
        "oh wow",
        "no way",
        "seriously",
        "really",
        "wait!",
        "what?!",
    )):
        context = "surprised"

    elif any(x in text for x in (
        "exactly",
        "you're right",
        "you are right",
        "correct",
        "yeah",
        "right",
        "got it",
        "okay",
    )):
        context = "agreement"

    elif any(x in text for x in (
        "interesting",
        "i see",
        "oh, i see",
        "huh",
        "actually",
        "curious",
    )):
        context = "curious"

    else:
        context = CURRENT_FILLER_CONTEXT

    preferred = FILLER_CONTEXTS.get(
        context,
        FILLER_CONTEXTS["thinking"],
    )

    available = [
        name
        for name in preferred
        if name in filler_paths
    ]

    if not available:
        available = list(filler_paths.keys())

    import random

    return random.choice(available)


def queue_predictive_filler(text):
    """
    Put ONE cached reaction into the playback queue.

    It is only used when the TTS engine is expected to have
    a meaningful delay.
    """

    if not FILLER_ENABLED:
        return False

    if not filler_paths:
        return False

    selected = response_filler_from_text(text)

    if not selected:
        return False

    path = filler_paths[selected]

    try:

        print(
            f"💭 Predictive filler: {selected}"
        )

        audio_queue.put(path)

        return True

    except Exception as exc:

        print(
            f"⚠️ Predictive filler failed: {exc}"
        )

        return False


# ============================================================
# OLLAMA
# ============================================================


def stream_ollama(user_text, save_user_message=True):
    set_filler_context(user_text)

    start_tts_worker()

    payload = build_chat_payload(user_text, runtime_state)

    started = time.perf_counter()
    first_token = None

    # Start a completely fresh audio turn.
    with runtime_state.turn_audio_lock:
        runtime_state.turn_tts_pending = 0
        runtime_state.turn_audio_finished.clear()

    runtime_state.allocate_next_response_id()

    runtime_state.next_playback_chunk = 1

    with ordered_audio_lock:
        ordered_audio.clear()

        ordered_audio_duration.clear()

    ordered_audio_event.clear()

    response_active.set()
    response_generation_done.clear()

    gap_monitor = threading.Thread(
        target=gap_filler_worker,
        daemon=True,
        name="riko-gap-filler",
    )

    gap_monitor.start()

    events = open_chat_stream(payload, runtime_state)

    buffer = ""
    # Ordered playback uses 1-based chunk IDs.
    # Keep 0 as the "nothing allocated yet" state.
    chunk_index = 1

    # --------------------------------------------------------
    # Predictive filler state
    # --------------------------------------------------------

    predictive_filler_played = False
    accumulated_text = ""
    response_completed = False


    try:

        for data in events:

            token = data.get(
                "message",
                {},
            ).get(
                "content",
                "",
            )

            if token:

                if first_token is None:

                    first_token = (
                        time.perf_counter()
                        - started
                    )

                    print(
                        f"\n⚡ First LLM token: "
                        f"{first_token:.2f}s"
                    )

                accumulated_text += token

                # ------------------------------------------------
                # Private filler directives are NEVER spoken.
                #
                # Keep an incomplete <FILLER:...> tag in the
                # buffer until the closing ">" arrives.
                # ------------------------------------------------

                buffer += token

                while True:

                    start = buffer.find("<FILLER:")

                    if start == -1:
                        break

                    end = buffer.find(">", start)

                    # Tag is split across streamed tokens.
                    if end == -1:
                        break

                    buffer = (
                        buffer[:start]
                        + buffer[end + 1:]
                    )

                # ------------------------------------------------
                # We now know what Riko is about to say.
                #
                # Use that information immediately instead of
                # blindly choosing "Hmm".
                # ------------------------------------------------

                # ------------------------------------------------
                # IMPORTANT:
                #
                # TTS generation is NO LONGER performed here.
                #
                # Qwen is free to continue streaming.
                # ------------------------------------------------

                if USE_CHUNKING:
                    buffer, chunks = extract_chunks(buffer)

                    for chunk in chunks:

                        # enqueue_phrase_aware_text owns the sequence
                        # number and pending-audio accounting.
                        chunk_index = enqueue_phrase_aware_text(
                            runtime_state.CURRENT_RESPONSE_ID,
                            chunk,
                            chunk_index,
                        )

            if data.get("done"):
                response_completed = True
                break

        # --------------------------------------------------------
        # Flush final partial phrase.
        # --------------------------------------------------------

        # Final safety cleanup for any complete filler tag.
        import re

        buffer = re.sub(
            r"<FILLER:\s*[a-zA-Z_]+\s*>",
            "",
            buffer,
            flags=re.IGNORECASE,
        )

        if USE_CHUNKING:
            buffer, chunks = extract_chunks(
                buffer,
                final=True,
            )
        else:
            buffer = clean_for_speech(buffer)
            chunks = [buffer] if buffer and is_speakable(buffer) else []

        for chunk in chunks:

            # Same numbering/accounting path as streaming chunks.
            chunk_index = enqueue_phrase_aware_text(
                runtime_state.CURRENT_RESPONSE_ID,
                chunk,
                chunk_index,
            )

        # --------------------------------------------------------
        # SAVE SHORT-TERM CONVERSATION CONTEXT
        # --------------------------------------------------------

        if response_completed:
            save_completed_turn(
                runtime_state,
                user_text,
                accumulated_text,
                save_user_message,
            )

    finally:

        # Qwen has finished producing text.
        # GPT-SoVITS may still be generating audio.
        response_generation_done.set()

        # If all queued chunks have already physically finished,
        # release the turn immediately.
        with runtime_state.turn_audio_lock:
            if runtime_state.turn_tts_pending == 0:
                runtime_state.turn_audio_finished.set()

        print(
            "\n⏳ Waiting for Riko audio playback to finish..."
        )

        # IMPORTANT:
        # This waits for THIS turn's real chunks, rather than
        # looking at the global audio queue.
        runtime_state.turn_audio_finished.wait()

        print(
            "✓ Riko audio fully finished."
        )

        # Only now may the next conversation turn begin.
        response_active.clear()
