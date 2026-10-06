# Riko Portable

Local voice companion template for Ubuntu/Debian Linux. It uses Ollama for the
conversation model, Faster-Whisper for speech recognition, and GPT-SoVITS
`api_v2.py` for synthesis.

## Quick start

```bash
chmod +x install.sh run.sh
./install.sh
./run.sh
```

`install.sh` installs system packages, a local Python environment, Ollama with
`qwen3:4b-instruct`, Faster-Whisper `base.en`, and the pinned GPT-SoVITS source
revision plus its public pretrained assets. It needs `sudo`, internet access,
and substantial disk space. It targets CPU mode; GPU support should be added as
a separate, explicitly tested install profile.

On the first `./run.sh`, Riko asks for a local WAV and its exact transcript.
Only supply a recording you own or are explicitly allowed to synthesize. The
recording and all user-specific settings are copied into `private/`, which is
ignored by Git.

## Private versus tracked files

Tracked examples:

- `config/gptsovits.cpu.example.yaml` — pinned GPT-SoVITS v2 engine settings.
- `config/tts_infer.cpu.yaml` — the checked-in CPU (`is_half: false`) TTS
  configuration used by this package.
- `character_config.example.yaml` — request fields sent to `POST /tts`.
- `requirements.txt` — pinned Riko runtime packages.

Never commit `private/`, `models/`, `vendor/`, `audio/`, or `runtime/`.
`private/gptsovits.yaml` is created at first run; it is where a user may select
their own licensed GPT and SoVITS checkpoint paths.

## GPT-SoVITS v2 contract

The synthesis client uses `POST /tts` with `text`, `text_lang`,
`ref_audio_path`, `prompt_text`, and `prompt_lang`. This matches `api_v2.py`.
The server is started from the pinned revision recorded in
`config/gptsovits.cpu.example.yaml`.

## Security

This directory contains no API token, system prompt tied to a real person,
reference audio, custom voice weights, model cache, logs, or prior Git history.
Review remote installer URLs before running them. Rotate credentials that were
ever present in an earlier repository history before making any repository
public.
