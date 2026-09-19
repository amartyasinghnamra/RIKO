# RIKO

A personalized desktop-based AI companion with text and voice interaction, built on locally hosted LLMs.

## Status

**v0.1.0 — Basic text chat.** Streams responses from a local Ollama model through a minimal GTK4 window.

## Stack

- **UI:** GTK4 (Python / PyGObject)
- **LLM:** [Ollama](https://ollama.com) — default model: `qwen3:4b-instruct`
- **Architecture:** modular, event-bus-driven (`riko/core/bus.py`) so UI, LLM, voice, and audio layers stay decoupled

## Roadmap

- [x] Phase 1 — Basic text chat with streaming Ollama responses
- [ ] Phase 2 — Configurable system prompt / personality layer
- [ ] Phase 3 — Speech-to-text input (Faster-Whisper)
- [ ] Phase 4 — Full live voice chat (TTS, streaming audio, Read Aloud)

## Setup (Ubuntu)

```bash
sudo apt install libgtk-4-dev python3-gi python3-gi-cairo gir1.2-gtk-4.0
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:4b-instruct

python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python3 main.py
```

## Project structure

```
RIKO/
├── main.py
├── riko/
│   ├── ui/          # GTK4 window and chat view
│   ├── llm/          # Ollama client
│   ├── voice/        # STT/TTS (Phase 3+)
│   ├── audio/        # mic capture, playback (Phase 3+)
│   ├── config/        # settings, behaviour profile
│   └── core/          # event bus
```
