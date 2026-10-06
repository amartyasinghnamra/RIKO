#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Run ./install.sh first." >&2
  exit 1
fi

.venv/bin/python scripts/first_run.py

if ! command -v ollama >/dev/null; then
  echo "Ollama is missing. Run ./install.sh again." >&2
  exit 1
fi

mkdir -p runtime audio/realtime

if ! ss -ltn 2>/dev/null | grep -q ':11434 '; then
  ollama serve > runtime/ollama.log 2>&1 &
fi

if ! ss -ltn 2>/dev/null | grep -q ':9880 '; then
  .venv/bin/python scripts/start_gptsovits.py > runtime/gptsovits.log 2>&1 &
fi

for _ in {1..60}; do
  ss -ltn 2>/dev/null | grep -q ':11434 ' && ss -ltn 2>/dev/null | grep -q ':9880 ' && break
  sleep 1
done
if ! ss -ltn 2>/dev/null | grep -q ':11434 ' || ! ss -ltn 2>/dev/null | grep -q ':9880 '; then
  echo "A service did not become ready. Check runtime/ollama.log and runtime/gptsovits.log." >&2
  exit 1
fi

export PYTHONPATH="$ROOT/server"
export RIKO_CHARACTER_CONFIG="$ROOT/private/character_config.yaml"
export RIKO_PROFILE_PATH="$ROOT/private/profile.yaml"
export RIKO_WHISPER_MODEL_PATH="$ROOT/models/faster-whisper-base.en"
export RIKO_TTS_WORKERS="${RIKO_TTS_WORKERS:-1}"
.venv/bin/python server/riko_voice_chat_ordered.py > runtime/riko-backend.log 2>&1 &
python3 -m http.server 8080 --directory frontend > runtime/riko-web.log 2>&1 &
echo "Riko is running: http://127.0.0.1:8080"
wait
