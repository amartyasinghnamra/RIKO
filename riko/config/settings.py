"""
App-level settings. Loaded once at startup.

Later this can read from configs/settings.yaml instead of hardcoded
defaults, but keeping it as a plain dataclass for now keeps Phase 1 simple.
"""

from dataclasses import dataclass


@dataclass
class Settings:
    ollama_host: str = "http://localhost:11434"
    model_name: str = "qwen3:4b-instruct"
    stream: bool = True
    app_title: str = "RIKO"
    window_width: int = 480
    window_height: int = 720


settings = Settings()
