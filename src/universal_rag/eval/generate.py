"""Synthetic golden-set generation: for sampled chunks, an LLM writes a question
answerable from that chunk + an ideal answer. The seed chunk is the ground-truth
relevant id. Output is meant for human spot-check before use.
"""

from __future__ import annotations

import json
import re

from universal_rag.eval.dataset import GoldenItem

_JSON = re.compile(r"\{.*\}", re.DOTALL)


def generate_golden(
    project_id: str, n: int, *, model: str, host: str | None = None
) -> list[GoldenItem]:
    import ollama
    from sqlalchemy import func, select

    from universal_rag.config import get_settings
    from universal_rag.db.models import Chunk, Document
    from universal_rag.db.session import get_session

    client = ollama.Client(host=host or get_settings().ollama_host)
    with get_session() as session:
        rows = session.execute(
            select(Document.external_id, Chunk.content)
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.project_id == project_id)
            .order_by(func.random())
            .limit(n)
        ).all()

    items: list[GoldenItem] = []
    for ext_id, content in rows:
        prompt = (
            "From the passage below, write ONE specific question a colleague might ask whose "
            "answer is found ONLY in this passage, plus a concise ideal answer. Reply ONLY with "
            'JSON {"question": "...", "answer": "..."}.\n\nPASSAGE:\n' + content[:1500]
        )
        try:
            resp = client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2},
            )
            match = _JSON.search(resp["message"]["content"])
            if match is None:
                continue
            data = json.loads(match.group(0))
            question = str(data.get("question", "")).strip()
            answer = str(data.get("answer", "")).strip()
            if question:
                items.append(
                    GoldenItem(
                        query=question,
                        project_id=project_id,
                        relevant_doc_ids=[ext_id],
                        ideal_answer=answer,
                    )
                )
        except Exception:
            continue
    return items
