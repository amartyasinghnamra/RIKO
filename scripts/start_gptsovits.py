#!/usr/bin/env python3
"""Launch the pinned GPT-SoVITS api_v2 server from portable configuration."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
config_path = ROOT / "private" / "gptsovits.yaml"
if not config_path.exists():
    config_path = ROOT / "config" / "gptsovits.cpu.example.yaml"
config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
engine = config["engine"]
python = ROOT / "vendor" / "gptsovits-env" / "bin" / "python"
api = ROOT / "vendor" / "GPT-SoVITS" / engine["api_entrypoint"]
gpt_root = ROOT / "vendor" / "GPT-SoVITS"
tts_config = gpt_root / engine["tts_config"]

if not python.exists() or not api.exists() or not tts_config.exists():
    raise SystemExit("GPT-SoVITS is not installed correctly; run ./install.sh.")

os.chdir(gpt_root)
os.execv(str(python), [
    str(python), str(api), "-a", str(engine["host"]), "-p", str(engine["port"]),
    "-c", str(tts_config),
])
