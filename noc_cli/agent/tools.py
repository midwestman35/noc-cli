from __future__ import annotations

import json
import sys

from noc_cli.history import seed_history
from noc_cli.redact import redact_value

HISTORY_LIMIT = 20


def run_history_search(symptom_tag, zendesk_client, memory_store, limit: int = HISTORY_LIMIT):
    """Merged local-FTS5 + live-Zendesk history search. Returns (redacted_dict, total).

    `seed_history` swallows Zendesk errors internally (returns []), so this never
    raises on a network failure.
    """
    candidates = seed_history(
        symptom_tag,
        zendesk_client=zendesk_client,
        memory_store=memory_store,
        limit=limit,
    )
    items = [
        {
            "ticket_id": c.ticket_id,
            "subject": c.subject,
            "source": c.source,
            "relevance_hint": c.relevance_hint,
            "tags": list(c.tags or []),
        }
        for c in candidates
    ]
    return redact_value({"candidates": items, "count": len(items)})


ALLOWED_MCP_TOOLS = [
    "mcp__zendesk__get_ticket",
    "mcp__zendesk__get_comments",
    "mcp__zendesk__search",
    "mcp__history__search_history",
]


def build_mcp_servers(config, memory_store, *, _zendesk_client=None):
    """Build the hybrid mcp_servers dict + the allowed tool-name list.

    Returns ``(mcp_servers, allowed_tool_names)``. `_zendesk_client` is injectable
    for tests; in production the in-process history server constructs its own
    read-only client.
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool  # noqa: PLC0415

    from noc_cli.zendesk import ZendeskClient  # noqa: PLC0415

    zd = _zendesk_client if _zendesk_client is not None else ZendeskClient(config)

    @tool(
        "search_history",
        "Search prior investigations and Zendesk for tickets matching a symptom "
        "tag (e.g. '[apex]'). Read-only; output is PII-redacted. Use to populate "
        "historical_matches on demand.",
        {"symptom_tag": str},
    )
    async def search_history(args):
        result, _n = run_history_search(args["symptom_tag"], zd, memory_store)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    history_server = create_sdk_mcp_server("history", tools=[search_history])

    mcp_servers = {
        "zendesk": {
            "command": sys.executable,
            "args": ["-m", "noc_cli.mcp.zendesk_server"],
            "env": {
                "ZENDESK_SUBDOMAIN": config.zendesk_subdomain,
                "ZENDESK_EMAIL": config.zendesk_email,
                "ZENDESK_API_TOKEN": config.zendesk_api_token,
            },
        },
        "history": history_server,
    }
    return mcp_servers, list(ALLOWED_MCP_TOOLS)
