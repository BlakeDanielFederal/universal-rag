"""Generation service: ties retrieval + LLM into an Outline, then renders it."""

from __future__ import annotations

from universal_rag.generation.outline import ArtifactType, Outline


class GenerationService:
    def build_outline(
        self,
        project_id: str,
        prompt: str,
        artifact_type: ArtifactType,
        *,
        source_ids: list[str] | None = None,
    ) -> Outline:
        """Retrieve project context and have the LLM produce a cited Outline.

        TODO(impl): HybridRetriever.search -> assemble grounded context ->
        get_provider().generate(...) with a schema-constrained prompt -> Outline.
        """
        raise NotImplementedError("GenerationService.build_outline not yet implemented")

    def render(self, outline: Outline, out_path: str | None = None) -> bytes | str:
        """Dispatch to the renderer for outline.artifact_type."""
        raise NotImplementedError("GenerationService.render not yet implemented")
