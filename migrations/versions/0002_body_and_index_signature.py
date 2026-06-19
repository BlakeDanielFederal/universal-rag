"""store raw document body + index signature (embedding_model / chunk_scheme)

Revision ID: 0002_index_sig
Revises: 0001_initial
Create Date: 2026-06-19

Adds:
  documents.body            — raw extracted text (offline re-chunk / contextualize /
                              late-chunk / embedding swaps without re-fetch)
  documents.embedding_model — signature its chunks were built with (re-index trigger)
  documents.chunk_scheme
  chunks.embedding_model     — per-chunk provenance
  chunks.chunk_scheme

Existing rows default to '' → they're treated as stale (signature mismatch) and
re-embedded on the next sync, a controlled re-index rather than silent drift.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_index_sig"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY = sa.text("''")


def upgrade() -> None:
    op.add_column("documents", sa.Column("body", sa.Text(), nullable=False, server_default=_EMPTY))
    op.add_column(
        "documents",
        sa.Column("embedding_model", sa.String(), nullable=False, server_default=_EMPTY),
    )
    op.add_column(
        "documents", sa.Column("chunk_scheme", sa.String(), nullable=False, server_default=_EMPTY)
    )
    op.add_column(
        "chunks", sa.Column("embedding_model", sa.String(), nullable=False, server_default=_EMPTY)
    )
    op.add_column(
        "chunks", sa.Column("chunk_scheme", sa.String(), nullable=False, server_default=_EMPTY)
    )


def downgrade() -> None:
    op.drop_column("chunks", "chunk_scheme")
    op.drop_column("chunks", "embedding_model")
    op.drop_column("documents", "chunk_scheme")
    op.drop_column("documents", "embedding_model")
    op.drop_column("documents", "body")
