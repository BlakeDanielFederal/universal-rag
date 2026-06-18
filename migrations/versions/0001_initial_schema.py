"""initial schema (projects/sources/documents/chunks/sync_state + pgvector)

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-18

Frozen snapshot of the schema. Mirrors universal_rag.db.models exactly, including
the pgvector HNSW index and the GIN functional FTS index. Future changes must add
new revisions rather than editing this one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBED_DIM = 768  # nomic-embed-text; keep in sync with db.models.EMBED_DIM


def upgrade() -> None:
    # pgvector for embeddings; pg_trgm available for future trigram use.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.String(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("doc_metadata", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_doc_source_external"),
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index("ix_documents_source_id", "documents", ["source_id"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=False),
        sa.Column("chunk_metadata", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_project_id", "chunks", ["project_id"])
    op.create_index("ix_chunks_source_id", "chunks", ["source_id"])
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    # Functional GIN index (expression indexes aren't expressible via create_index).
    op.execute(
        "CREATE INDEX ix_chunks_content_fts ON chunks USING gin (to_tsvector('english', content))"
    )

    op.create_table(
        "sync_state",
        sa.Column(
            "source_id",
            sa.String(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cursor", sa.String(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sync_state")
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_fts")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.drop_index("ix_chunks_source_id", table_name="chunks")
    op.drop_index("ix_chunks_project_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_documents_source_id", table_name="documents")
    op.drop_index("ix_documents_project_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("sources")
    op.drop_table("projects")
