"""Outline -> artifact renderers. Each is a pure function of the Outline.

markdown : email/memo prose with a citations footer (jinja2 template)
pptx     : python-pptx deck (title + one slide per section, sources slide)
docx     : python-docx memo (headings, paragraphs, references section)
"""

from __future__ import annotations

from universal_rag.generation.outline import Outline


def render_markdown(outline: Outline) -> str:
    raise NotImplementedError("render_markdown not yet implemented")


def render_pptx(outline: Outline, out_path: str) -> str:
    raise NotImplementedError("render_pptx not yet implemented")


def render_docx(outline: Outline, out_path: str) -> str:
    raise NotImplementedError("render_docx not yet implemented")
