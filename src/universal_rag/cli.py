"""Thin ops CLI:  `urag <command>`.

urag projects                       list configured projects
urag db-init                        create tables (dev; Alembic for real migrations)
urag sync <project> [--source ID]   run ingestion
urag query <project> "<text>"       hybrid retrieval
urag serve-api                      run FastAPI (uvicorn)
urag serve-mcp                      run FastMCP server
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
    """Create all tables (dev convenience; use Alembic for migrations)."""
    from universal_rag.db.models import Base
    from universal_rag.db.session import get_engine

    Base.metadata.create_all(get_engine())
    rprint("[green]Tables created.[/green]")


@app.command()
def sync(
    project: str, source: str | None = typer.Option(None, help="Limit to one source id")
) -> None:
    """Run ingestion for a project (optionally a single source)."""
    from universal_rag.ingestion import run_sync

    results = run_sync(project, source, config=_load_app_config())
    exit_code = 0
    for r in results:
        if r.status == "ok":
            rprint(
                f"[green]✓[/green] {r.source_id}: "
                f"{r.documents} doc(s), {r.chunks} chunk(s), {r.skipped} unchanged"
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


if __name__ == "__main__":
    app()
