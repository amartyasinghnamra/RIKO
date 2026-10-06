#!/usr/bin/env python3
"""Create per-user settings once; nothing written here is tracked by Git."""

from __future__ import annotations

import shutil
import sys
import wave
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "private"
PROFILE = PRIVATE / "profile.yaml"
CHARACTER = PRIVATE / "character_config.yaml"
GPT_CONFIG = PRIVATE / "gptsovits.yaml"
VOICE = PRIVATE / "voice" / "reference.wav"


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def require_reference_audio() -> tuple[Path, str]:
    while True:
        source = Path(ask("Path to a WAV recording you have permission to use")).expanduser()
        if not source.is_file():
            print("That file does not exist. Try again.")
            continue
        try:
            with wave.open(str(source), "rb") as audio:
                if audio.getnframes() == 0:
                    raise ValueError("empty WAV")
        except (wave.Error, ValueError) as error:
            print(f"Not a usable WAV: {error}")
            continue
        transcript = ask("Exact words spoken in that recording")
        if transcript:
            return source, transcript
        print("A transcript is required by GPT-SoVITS.")


def main() -> int:
    if PROFILE.exists() and CHARACTER.exists() and VOICE.exists():
        return 0

    print("Riko first-run setup. Your answers and voice recording stay in private/.")
    print("Use only a voice you own or have explicit permission to synthesize.")
    user_name = ask("Your preferred name", "friend")
    companion_name = ask("Companion name", "Riko")
    personality = ask("Companion style", "warm, playful, and helpful")
    source, transcript = require_reference_audio()

    VOICE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, VOICE)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PROFILE.write_text(yaml.safe_dump({
        "user_name": user_name,
        "companion_name": companion_name,
        "personality": personality,
    }, sort_keys=False), encoding="utf-8")
    CHARACTER.write_text(yaml.safe_dump({"sovits_ping_config": {
        "text_lang": "en", "prompt_lang": "en", "ref_audio_path": str(VOICE),
        "prompt_text": transcript, "text_split_method": "cut5", "batch_size": 1,
        "batch_threshold": 0.75, "split_bucket": True, "speed_factor": 1.0,
        "fragment_interval": 0.3, "seed": -1, "parallel_infer": True,
        "repetition_penalty": 1.35, "sample_steps": 32, "super_sampling": False,
    }}, sort_keys=False), encoding="utf-8")
    if not GPT_CONFIG.exists():
        shutil.copy2(ROOT / "config" / "gptsovits.cpu.example.yaml", GPT_CONFIG)
    print("Private profile created. You can edit private/profile.yaml later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
