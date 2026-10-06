#!/usr/bin/env bash
set -euo pipefail

# Supported target: Ubuntu/Debian Linux, CPU mode.  This intentionally keeps
# every downloaded runtime inside this repository except the Ollama daemon.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
GPT_REF="48b1a0169a28582a8984402f82cf438d3bfa6aca"
cd "$ROOT"

if [[ "$(uname -s)" != Linux* ]] || ! command -v apt-get >/dev/null; then
  echo "install.sh currently supports Ubuntu/Debian Linux only." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git curl ffmpeg iproute2 portaudio19-dev libsndfile1

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if ! command -v ollama >/dev/null; then
  echo "Installing Ollama from its official installer..."
  curl -fsSL https://ollama.com/install.sh | sh
fi
ollama pull qwen3:4b-instruct

mkdir -p models vendor runtime audio/realtime private/voice
.venv/bin/python -c "from faster_whisper import download_model; download_model('base.en', output_dir='models/faster-whisper-base.en', revision='3d3d5dee26484f91867d81cb899cfcf72b96be6c')"

if [[ ! -d vendor/GPT-SoVITS/.git ]]; then
  git clone https://github.com/RVC-Boss/GPT-SoVITS.git vendor/GPT-SoVITS
fi
git -C vendor/GPT-SoVITS fetch --tags
git -C vendor/GPT-SoVITS checkout --detach "$GPT_REF"

# GPT-SoVITS' upstream installer expects Conda.  Install Miniforge locally when
# absent, then provision a repository-local CPU environment and public weights.
if [[ ! -x vendor/miniforge/bin/conda ]]; then
  curl -fsSL -o runtime/Miniforge.sh "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-$(uname -m).sh"
  bash runtime/Miniforge.sh -b -p "$ROOT/vendor/miniforge"
fi
"$ROOT/vendor/miniforge/bin/conda" create -y -p "$ROOT/vendor/gptsovits-env" python=3.10
"$ROOT/vendor/miniforge/bin/conda" run -p "$ROOT/vendor/gptsovits-env" bash -lc "cd '$ROOT/vendor/GPT-SoVITS' && bash install.sh --device CPU --source HF"

chmod +x install.sh run.sh scripts/first_run.py
echo "Install complete. Run ./run.sh"
