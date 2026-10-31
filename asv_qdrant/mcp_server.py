"""Read-only MCP server over the asv-engineering index (stdio).

Two tools: search past engineering decisions, and report index freshness.
It never writes to Qdrant or the ASV. Results are pointers: open the ASV file
at the given path before acting on a hit.
"""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .cli import client_for
from .index import search, status

Kind = Literal["design-note", "portfolio", "ticket", "reply", "fyi", "blocker", "claim", "message"]

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

server = MCPServer(
    "asv-engineering-memory",
    instructions=(
        "Search the Agentic Satellite Vault's engineering notes, tickets and replies by meaning. "
        "Use it before boarding a ticket or answering 'have we decided or tried this before?'. "
        "Each hit is a pointer to an ASV path and git blob sha, not the answer: open the file "
        "before acting on it. The ASV is the source of truth."
    ),
)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = client_for(local=False)
    return _client


@server.tool(annotations=READ_ONLY)
def search_asv_engineering(
    query: str,
    k: int = 5,
    kind: list[Kind] | None = None,
    sender: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """Find ASV engineering notes, tickets and replies relevant to a question.

    Args:
        query: plain-language question, e.g. "what blocked the runner install last time".
        k: number of hits, 1 to 20.
        kind: optional filter, e.g. ["design-note"] for decisions or ["ticket", "reply"] for work history.
        sender: optional sender slug, e.g. "lingxi-engineer-grok-bot".
        since: optional YYYY-MM-DD; only notes dated on or after it.

    Returns hits with score, path, title, kind, sender, date, source_sha and a short excerpt.
    """
    return search(_get_client(), query, max(1, min(k, 20)), kind, sender, since)


@server.tool(annotations=READ_ONLY)
def asv_index_status() -> dict:
    """Point count and the ASV commits the index was built from, to judge freshness."""
    return status(_get_client())


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
