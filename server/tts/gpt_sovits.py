"""Synchronous GPT-SoVITS synthesis client for Riko.

This module performs one HTTP synthesis request per call.  Worker lifecycle,
queueing, response ordering, playback, and turn accounting remain outside it.
"""

import os
import wave

import requests
import yaml


CONFIG_PATH = os.getenv("RIKO_CHARACTER_CONFIG", "character_config.yaml")

with open(CONFIG_PATH, "r") as f:
    char_config = yaml.safe_load(f)


# GPT-SoVITS api_v2.py exposes synthesis at POST /tts.  The old api.py
# endpoint used a different URL and different JSON field names.
SOVITS_URL = os.getenv("RIKO_SOVITS_URL", "http://127.0.0.1:9880/tts")


def synthesize(in_text, output_wav_pth="output.wav"):
    """Generate one validated WAV using the existing GPT-SoVITS API contract."""
    config = char_config["sovits_ping_config"]

    payload = {
        "text": in_text,
        "text_lang": config["text_lang"],
        "ref_audio_path": config["ref_audio_path"],
        "prompt_text": config["prompt_text"],
        "prompt_lang": config["prompt_lang"],
        "text_split_method": config.get("text_split_method", "cut5"),
        "batch_size": config.get("batch_size", 1),
        "batch_threshold": config.get("batch_threshold", 0.75),
        "split_bucket": config.get("split_bucket", True),
        "speed_factor": config.get("speed_factor", 1.0),
        "fragment_interval": config.get("fragment_interval", 0.3),
        "seed": config.get("seed", -1),
        "media_type": "wav",
        "streaming_mode": False,
        "parallel_infer": config.get("parallel_infer", True),
        "repetition_penalty": config.get("repetition_penalty", 1.35),
        "sample_steps": config.get("sample_steps", 32),
        "super_sampling": config.get("super_sampling", False),
    }

    print("Riko CPU TTS")
    print("Reference:", config["ref_audio_path"])
    print("Text:", in_text)

    try:
        with requests.post(
            SOVITS_URL,
            json=payload,
            stream=True,
            timeout=(10, 300),
        ) as response:

            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            print("GPT-SoVITS:", response.status_code, content_type)

            with open(output_wav_pth, "wb") as f:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)

        if not os.path.exists(output_wav_pth):
            raise RuntimeError("GPT-SoVITS produced no output file")

        size = os.path.getsize(output_wav_pth)

        if size < 1000:
            raise RuntimeError(
                f"GPT-SoVITS returned unexpectedly small file: {size} bytes"
            )

        with wave.open(output_wav_pth, "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()

        if frames <= 0:
            raise RuntimeError("GPT-SoVITS returned a WAV with zero frames")

        duration = frames / sample_rate

        print("✓ Riko CPU voice generated")
        print("  Output:", output_wav_pth)
        print(
            f"  WAV: {sample_rate} Hz / "
            f"{channels} ch / "
            f"{sample_width * 8}-bit / "
            f"{duration:.3f}s"
        )

        return output_wav_pth

    except Exception as e:
        print("❌ Riko CPU TTS failed:", e)
        return None
