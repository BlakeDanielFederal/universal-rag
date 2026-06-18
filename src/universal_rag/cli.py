"""Thin ops CLI:  `urag <command>`.

urag projects                       list configured projects
urag db-init                        create tables (dev; Alembic for real migrations)
urag sync <project> [--source ID]   run ingestion
urag query <project> "<text>"       hybrid retrieval
urag migrate [revision]             run Alembic migrations (default head)
urag serve-api                      run FastAPI (uvicorn)
urag serve-mcp                      run FastMCP server
urag serve-scheduler                run the incremental-sync scheduler
"""

from __future__ import annotations

import typer
from rich import print as rprint

app = typer.Typer(
    help="Universal RAG — multi-provider RAG for business comms.", no_args_is_help=True
)


def _load_app_config():
    """Load config.yaml, falling back to the committed example with a warning."""
    from universal_rag.config import default_config_path, get_settings
    from universal_rag.config.schema import load_config

    try:
        path = default_config_path()
    except FileNotFoundError as exc:
        rprint(f"[red]{exc}.[/red]")
        raise typer.Exit(code=1) from exc
    if str(path) != get_settings().config_path:
        rprint(
            f"[yellow]{get_settings().config_path} not found — using {path.name}. "
            f"Copy it to config.yaml to customize.[/yellow]"
        )
    return load_config(path)


@app.command()
def projects() -> None:
    """List configured project workspaces."""
    cfg = _load_app_config()
    for p in cfg.projects:
        rprint(f"[bold]{p.id}[/bold]  {p.name} — {len(p.sources)} source(s)")


@app.command("db-init")
def db_init() -> None:
    """Create/upgrade the schema by running Alembic migrations to head."""
    from universal_rag.db.migrate import upgrade

    upgrade("head")
    rprint("[green]Schema is at head.[/green]")


@app.command()
def migrate(
    revision: str = typer.Argument("head", help="Target revision (default: head)"),
) -> None:
    """Run Alembic migrations to a revision (default head)."""
    from universal_rag.db.migrate import upgrade

    upgrade(revision)
    rprint(f"[green]Migrated to {revision}.[/green]")


@app.command()
def sync(
    project: str,
    source: str | None = typer.Option(None, help="Limit to one source id"),
    prune: bool | None = typer.Option(
        None,
        "--prune/--no-prune",
        help="Override the per-source prune setting (delete items removed at the source)",
    ),
) -> None:
    """Run ingestion for a project (optionally a single source)."""
    from universal_rag.ingestion import run_sync

    results = run_sync(project, source, config=_load_app_config(), prune=prune)
    exit_code = 0
    for r in results:
        if r.status == "ok":
            rprint(
                f"[green]✓[/green] {r.source_id}: "
                f"{r.documents} doc(s), {r.chunks} chunk(s), {r.skipped} unchanged, "
                f"{r.deleted} deleted"
            )
        else:
            exit_code = 1
            rprint(f"[red]✗ {r.source_id}: {r.error}[/red]")
    raise typer.Exit(code=exit_code)


@app.command()
def query(
    project: str,
    text: str,
    k: int = typer.Option(8, help="Number of results to show"),
    source: str | None = typer.Option(None, help="Limit to one source id"),
) -> None:
    """Inspect hybrid retrieval results for a query."""
    from universal_rag.retrieval import HybridRetriever

    retriever = HybridRetriever(top_k=k)
    hits = retriever.search(text, project, source_ids=[source] if source else None)
    if not hits:
        rprint("[yellow]No matches (is the project ingested?).[/yellow]")
        return
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h.content.split())[:160]
        title = h.title or "(untitled)"
        rprint(f"[bold]{i}. {title}[/bold]  [dim]{h.source_id} · rrf={h.score:.4f}[/dim]")
        rprint(f"   {snippet}")
        if h.url:
            rprint(f"   [blue]{h.url}[/blue]")


@app.command("serve-api")
def serve_api(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run("universal_rag.api.app:app", host=host, port=port, reload=True)


@app.command("serve-mcp")
def serve_mcp() -> None:
    """Run the FastMCP server (stdio)."""
    from universal_rag.mcp.server import mcp

    mcp.run()


@app.command("serve-scheduler")
def serve_scheduler() -> None:
    """Run the incremental-sync scheduler (blocks; cron cadence from config)."""
    from universal_rag.scheduler import SyncScheduler

    try:
        SyncScheduler(_load_app_config()).start()
    except (KeyboardInterrupt, SystemExit):
        rprint("[yellow]Scheduler stopped.[/yellow]")


if __name__ == "__main__":
    app()
