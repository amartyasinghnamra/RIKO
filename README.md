# Riko Portable

A portable, self-contained version of **Riko**, an anime-style AI voice companion with local LLM chat, speech recognition, and GPT-SoVITS text-to-speech.

The goal of this project is simple:

> **Clone the repository → install dependencies → run Riko locally.**

---

## Features

- Local LLM conversation through **Ollama**
- Qwen-based conversational brain
- Real-time voice interaction
- Speech-to-text using **faster-whisper**
- Text-to-speech using **GPT-SoVITS**
- CPU-compatible GPT-SoVITS configuration
- Local web frontend
- Character/profile configuration
- Portable project layout
- Separate private configuration and runtime data

---

## Architecture

```text
                    ┌──────────────────────┐
                    │      Riko Frontend   │
                    │   HTML / CSS / JS    │
                    │      :8080           │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    Riko Backend      │
                    │  Voice + Chat Logic  │
                    │      :8765           │
                    └───────┬───────┬──────┘
                            │       │
                 ┌──────────┘       └──────────┐
                 ▼                             ▼
        ┌─────────────────┐          ┌─────────────────┐
        │     Ollama      │          │   GPT-SoVITS    │
        │    Qwen 3       │          │      :9880      │
        │     :11434      │          │      TTS        │
        └─────────────────┘          └─────────────────┘
                 ▲                             ▲
                 │                             │
                 │                    ┌────────┴────────┐
                 │                    │  Reference     │
                 │                    │     Voice      │
                 │                    └─────────────────┘
                 │
        ┌─────────────────┐
        │ faster-whisper  │
        │      STT         │
        └─────────────────┘
```

---

## Project Structure

```text
riko-portable/
├── audio/
│   └── realtime/
│
├── character_config.example.yaml
│
├── config/
│   ├── gptsovits.cpu.example.yaml
│   └── tts_infer.cpu.yaml
│
├── frontend/
│   ├── api.js
│   ├── app.js
│   ├── assets/
│   │   ├── riko.png
│   │   └── viriko.png
│   ├── index.html
│   ├── README-INTEGRATION.txt
│   └── styles.css
│
├── install.sh
├── requirements.txt
│
├── models/
│   └── faster-whisper-base.en/
│
├── private/
│   ├── character_config.yaml
│   ├── gptsovits.yaml
│   ├── profile.yaml
│   └── voice/
│       └── reference.wav
│
├── runtime/
│
├── scripts/
│   ├── first_run.py
│   └── start_gptsovits.py
│
├── server/
│   ├── core/
│   ├── llm/
│   ├── process/
│   ├── tts/
│   ├── realtime_riko_ordered.py
│   ├── riko_control.py
│   └── riko_voice_chat_ordered.py
│
└── vendor/
    ├── GPT-SoVITS/
    ├── gptsovits-env/
    └── miniforge/
```

Large runtime assets, private configuration, downloaded models and vendor environments are intentionally excluded from Git.

---

## Requirements

Currently supported target:

- Linux
- Ubuntu/Debian-based distributions
- Python 3
- `apt`
- Git
- FFmpeg
- PortAudio
- Ollama

The installer currently targets CPU-based GPT-SoVITS operation.

No NVIDIA CUDA installation is required for the CPU configuration.

---

## Installation

Clone the repository:

```bash
git clone https://github.com/amartyasinghnamra/RIKO.git
cd RIKO
```

Make the installer executable:

```bash
chmod +x install.sh run.sh
```

Run:

```bash
./install.sh
```

The installer prepares:

- Python virtual environment
- Python dependencies
- Ollama
- Qwen model
- faster-whisper model
- GPT-SoVITS source
- GPT-SoVITS environment
- required project directories

---

## First Run

After installation:

```bash
./run.sh
```

The launcher starts the required services and then launches Riko.

Open:

```text
http://127.0.0.1:8080
```

---

## Services

| Service | Port | Purpose |
|---|---:|---|
| Riko Web UI | `8080` | Frontend |
| Riko Backend | `8765` | Voice/chat backend |
| Ollama | `11434` | Local LLM |
| GPT-SoVITS | `9880` | Text-to-speech |

---

## Configuration

Private configuration is created automatically during first run.

Important files:

```text
private/character_config.yaml
private/profile.yaml
private/gptsovits.yaml
private/voice/reference.wav
```

Example configurations are kept in the repository:

```text
character_config.example.yaml
config/gptsovits.cpu.example.yaml
config/tts_infer.cpu.yaml
```

Do not commit private voice/configuration data.

---

## GPT-SoVITS

Riko currently uses GPT-SoVITS v2 through its `api_v2.py` API.

The portable configuration uses:

```yaml
device: cpu
is_half: false
```

The GPT-SoVITS server listens on:

```text
127.0.0.1:9880
```

The project pins the GPT-SoVITS repository revision used by the portable setup rather than relying on an arbitrary latest checkout.

---

## CPU PyTorch Environment

The working CPU environment uses:

```text
torch       2.6.0+cpu
torchaudio  2.6.0+cpu
```

and:

```text
CUDA: None
CUDA available: False
```

This is intentional.

The GPT-SoVITS environment previously encountered an incompatibility between newer `torchaudio` and CPU Torch installations, as well as a `transformers` security requirement requiring Torch >= 2.6 when loading certain model files.

The portable environment therefore uses the CPU builds of Torch and Torchaudio.

> The installer should be kept synchronized with these versions when rebuilding the environment from scratch.

---

## Ollama

Riko uses Ollama for the local conversational model.

The current configuration uses:

```text
qwen3:4b-instruct
```

Ollama normally listens on:

```text
127.0.0.1:11434
```

You can check it with:

```bash
ss -ltn | grep ':11434'
```

---

## Speech Recognition

Riko uses `faster-whisper` for speech recognition.

The portable setup uses the:

```text
base.en
```

model.

The downloaded model is stored under:

```text
models/faster-whisper-base.en/
```

Models are excluded from Git because of their size.

---

## Runtime Files

Runtime logs are stored in:

```text
runtime/
```

Examples:

```text
runtime/gptsovits.log
runtime/riko-backend.log
runtime/riko-web.log
```

Runtime files are ignored by Git.

---

## Git / Repository Hygiene

The following are intentionally ignored:

```text
.venv/
audio/
runtime/
private/
models/
vendor/
__pycache__/
*.py[cod]
```

This keeps the repository focused on source code and reproducible configuration rather than large models, private data, generated files, or complete dependency environments.

---

## Troubleshooting

### GPT-SoVITS does not start

Check:

```bash
cat runtime/gptsovits.log
```

Check the port:

```bash
ss -ltnp | grep ':9880'
```

Test the environment:

```bash
vendor/gptsovits-env/bin/python -c '
import torch
import torchaudio

print("torch:", torch.__version__)
print("torchaudio:", torchaudio.__version__)
print("CUDA:", torch.version.cuda)
print("CUDA available:", torch.cuda.is_available())
'
```

Expected CPU configuration:

```text
torch: 2.6.0+cpu
torchaudio: 2.6.0+cpu
CUDA: None
CUDA available: False
```

### Port already in use

Check:

```bash
ss -ltnp | grep -E ':8765|:9880|:11434|:8080'
```

Identify the process:

```bash
ps -fp <PID>
```

Do not blindly kill processes; verify that they belong to Riko/GPT-SoVITS first.

### Riko backend does not start

Check:

```bash
cat runtime/riko-backend.log
```

and:

```bash
ss -ltnp | grep ':8765'
```

### Frontend does not load

Check:

```bash
cat runtime/riko-web.log
```

Then open:

```text
http://127.0.0.1:8080
```

---

## Development

The main backend is:

```text
server/riko_voice_chat_ordered.py
```

The GPT-SoVITS launcher is:

```text
scripts/start_gptsovits.py
```

First-run initialization is handled by:

```text
scripts/first_run.py
```

The frontend lives under:

```text
frontend/
```

---

## Philosophy

Riko is designed around a simple idea:

> **Local AI should feel like an application, not a collection of commands.**

The long-term goal of Riko Portable is to make the complete voice companion reproducible on another Linux machine without requiring the original development environment.

---

## Status

**Working prototype**

Current working components:

- Local LLM — working
- Ollama integration — working
- Speech recognition — working
- GPT-SoVITS CPU inference — working
- Voice pipeline — working
- Web frontend — working
- Portable launcher — working

The installation process is still being hardened for clean-machine reproducibility.

---

## License

See the repository and upstream component licenses before redistribution.

Riko contains third-party components, including GPT-SoVITS and its associated models/dependencies. Their respective licenses and terms apply.
