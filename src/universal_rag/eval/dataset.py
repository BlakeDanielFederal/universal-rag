"""Golden-set schema + JSONL I/O.

One line per query: the query, its project, the ground-truth relevant chunk ids,
and an ideal answer (used by the optional judge metric).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class GoldenItem:
    query: str
    project_id: str
    # Relevance is recorded at the DOCUMENT level (source external_ids) so a golden
    # set survives re-chunking / re-embedding / re-index — chunk ids are not stable.
    relevant_doc_ids: list[str] = field(default_factory=list)
    ideal_answer: str = ""
    source_ids: list[str] | None = None


def load_golden(path: str | Path) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            items.append(GoldenItem(**json.loads(line)))
    return items


def save_golden(items: list[GoldenItem], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(asdict(it)) for it in items) + "\n")
