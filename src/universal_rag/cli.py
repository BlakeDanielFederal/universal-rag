"""Thin ops CLI:  `urag <command>`.

urag projects                       list configured projects
urag db-init                        create tables (dev; Alembic for real migrations)
urag sync <project> [--source ID]   run ingestion
urag query <project> "<text>"       inspect retrieval
urag generate <project> "<prompt>" --type email|memo|presentation
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
    from pathlib import Path

    from universal_rag.config import get_settings
    from universal_rag.config.schema import load_config

    path = Path(get_settings().config_path)
    if not path.exists():
        example = path.with_name("config.example.yaml")
        if example.exists():
            rprint(f"[yellow]{path} not found — using {example.name}. "
                   f"Copy it to {path.name} to customize.[/yellow]")
            path = example
        else:
            rprint(f"[red]No config found at {path} (and no example beside it).[/red]")
            raise typer.Exit(code=1)
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
    raise typer.Exit(code=_todo("sync"))


@app.command()
def query(project: str, text: str) -> None:
    """Inspect hybrid retrieval results for a query."""
    raise typer.Exit(code=_todo("query"))


@app.command()
def generate(
    project: str,
    prompt: str,
    type: str = typer.Option("email", help="email | memo | presentation"),
) -> None:
    """Generate a grounded draft artifact."""
    raise typer.Exit(code=_todo("generate"))


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


def _todo(name: str) -> int:
    rprint(f"[yellow]'{name}' is scaffolded but not implemented yet.[/yellow]")
    return 1


if __name__ == "__main__":
    app()
