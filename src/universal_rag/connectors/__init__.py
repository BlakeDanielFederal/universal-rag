"""Source connectors and a registry to resolve provider -> Connector class."""

from __future__ import annotations

from universal_rag.config.schema import SourceConfig
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor

# Registry is populated lazily to avoid importing provider SDKs we don't use.
_REGISTRY: dict[str, str] = {
    "confluence": "universal_rag.connectors.confluence:ConfluenceConnector",
    "jira": "universal_rag.connectors.jira:JiraConnector",
    "github": "universal_rag.connectors.github:GitHubConnector",
    "sharepoint": "universal_rag.connectors.sharepoint:SharePointConnector",
    "local": "universal_rag.connectors.local:LocalDirectoryConnector",
}


def get_connector(source: SourceConfig) -> Connector:
    """Instantiate the connector for a configured source."""
    import importlib

    if source.provider not in _REGISTRY:
        raise ValueError(f"No connector registered for provider '{source.provider}'")
    module_path, cls_name = _REGISTRY[source.provider].split(":")
    cls = getattr(importlib.import_module(module_path), cls_name)
    return cls(source)


__all__ = ["Connector", "SourceDocument", "SyncCursor", "get_connector"]
