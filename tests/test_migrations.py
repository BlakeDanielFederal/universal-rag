"""Migration sanity checks that don't touch the database."""

from __future__ import annotations

from alembic.script import ScriptDirectory

from universal_rag.db.migrate import alembic_config


def test_single_head_revision() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, f"expected one head, got {heads}"


def test_initial_revision_is_reversible() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    head = script.get_heads()[0]
    module = script.get_revision(head).module
    # Both directions are defined (no accidental one-way migration).
    assert callable(module.upgrade)
    assert callable(module.downgrade)
