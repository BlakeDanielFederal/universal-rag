"""Configuration: env-backed secrets (Settings) + YAML project/source config."""

from __future__ import annotations

from pathlib import Path

from universal_rag.config.schema import AppConfig, load_config
from universal_rag.config.settings import Settings, get_settings


def default_config_path() -> Path:
    """Resolve the active config file: the configured path, else the committed
    `config.example.yaml` beside it. Raises FileNotFoundError if neither exists."""
    path = Path(get_settings().config_path)
    if path.exists():
        return path
    example = path.with_name("config.example.yaml")
    if example.exists():
        return example
    raise FileNotFoundError(f"No config found at {path} (and no example beside it)")


def load_app_config() -> AppConfig:
    """Load the active project config (with example fallback)."""
    return load_config(default_config_path())


__all__ = ["Settings", "default_config_path", "get_settings", "load_app_config"]
