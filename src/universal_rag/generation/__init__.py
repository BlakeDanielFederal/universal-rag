"""Draft generation: retrieve -> LLM -> structured outline -> rendered artifact.

A retrieval-grounded `Outline` (JSON) is the shared backbone; each renderer
(markdown email/memo, .pptx, .docx) is a pure function of that outline, so all
formats stay consistent and citations are preserved across them.
"""

from universal_rag.generation.outline import Citation, Outline, OutlineSection
from universal_rag.generation.service import GenerationService

__all__ = ["Citation", "GenerationService", "Outline", "OutlineSection"]
