"""Typed schema for `config/config.yaml` (projects -> sources mapping).

Loaded and validated with Pydantic. `${ENV_VAR}` placeholders in string values
are expanded from the environment at load time so secrets stay out of YAML.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

Provider = Literal["confluence", "jira", "github"]

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ChunkConfig(BaseModel):
    max_tokens: int = 512
    overlap_tokens: int = 64


class SyncConfig(BaseModel):
    strategy: Literal["incremental", "manual"] = "incremental"
    schedule: str = "0 * * * *"


class Defaults(BaseModel):
    embedding_model: str = "nomic-embed-text"
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)


class SourceConfig(BaseModel):
    id: str
    provider: Provider
    # Provider-specific keys (spaces / projects / repos / include / ...) are kept
    # loose here and validated by each connector. Keeps the schema open for new
    # providers without churn.
    options: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectConfig(BaseModel):
    id: str
    name: str
    description: str = ""
    sources: list[SourceConfig] = Field(default_factory=list)


class AppConfig(BaseModel):
    defaults: Defaults = Field(default_factory=Defaults)
    projects: list[ProjectConfig] = Field(default_factory=list)

    def project(self, project_id: str) -> ProjectConfig | None:
        return next((p for p in self.projects if p.id == project_id), None)


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_config(path: str | Path) -> AppConfig:
    """Read, env-expand, and validate the YAML project config."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return AppConfig.model_validate(_expand_env(raw))
