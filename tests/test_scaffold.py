"""Smoke tests that the scaffold imports and config schema validates.

These run without Postgres/Ollama so the project is verifiable from day one.
"""

from __future__ import annotations

from pathlib import Path

from universal_rag.config.schema import load_config


def test_package_imports() -> None:
    import universal_rag  # noqa: F401
    from universal_rag.api.app import create_app
    from universal_rag.mcp.server import mcp  # noqa: F401
    from universal_rag.retrieval import HybridRetriever  # noqa: F401

    assert create_app().title == "Universal RAG"


def test_example_config_validates() -> None:
    cfg = load_config(Path(__file__).resolve().parents[1] / "config" / "config.example.yaml")
    apollo = cfg.project("apollo")
    assert apollo is not None
    providers = {s.provider for s in apollo.sources}
    assert providers == {"confluence", "jira", "github", "sharepoint", "local", "website"}
