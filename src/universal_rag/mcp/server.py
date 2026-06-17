"""FastMCP server. Lets an MCP client (e.g. Claude) drive the framework:
list projects, sync sources, query context, and generate drafts.

Run:  fastmcp run universal_rag.mcp.server:mcp
"""

from __future__ import annotations

from fastmcp import FastMCP

mcp: FastMCP = FastMCP("universal-rag")


@mcp.tool
def list_projects() -> list[dict[str, str]]:
    """List configured project workspaces."""
    from universal_rag.config import get_settings
    from universal_rag.config.schema import load_config

    cfg = load_config(get_settings().config_path)
    return [{"id": p.id, "name": p.name, "description": p.description} for p in cfg.projects]


# TODO: @mcp.tool sync_project / query_project / generate_draft
