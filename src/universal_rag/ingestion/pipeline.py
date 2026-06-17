"""Per-source incremental ingestion orchestration."""

from __future__ import annotations

from universal_rag.config.schema import ProjectConfig, SourceConfig


class IngestionPipeline:
    """Chunk + embed + upsert documents for a single source."""

    def run(self, project: ProjectConfig, source: SourceConfig) -> dict[str, int]:
        """Returns counts: {documents, chunks, skipped}. TODO(impl)."""
        raise NotImplementedError("IngestionPipeline.run not yet implemented")


def sync_source(project_id: str, source_id: str) -> dict[str, int]:
    """Entry point used by the CLI / API / scheduler to sync one source."""
    raise NotImplementedError("sync_source not yet implemented")
