from __future__ import annotations

import json
import os
import sys
from typing import Any

from noc_cli.config import Config
from noc_cli.redact import redact_value
from noc_cli.zendesk import ZendeskClient, ZendeskError

SEARCH_CAP = 25
READ_TOOL_NAMES = ("get_ticket", "get_comments", "search")


def _map_error(exc: Exception) -> dict[str, Any]:
    """Map an exception to a structured, agent-friendly error dict (never raise)."""
    import httpx  # noqa: PLC0415

    if isinstance(exc, ZendeskError):
        kind = "auth" if "auth" in str(exc).lower() else "transient"
        return {"error": str(exc), "kind": kind}
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return {"error": "not found", "kind": "not_found"}
        if code in (401, 403):
            return {"error": "auth failed", "kind": "auth"}
        return {"error": f"http {code}", "kind": "transient"}
    return {"error": str(exc) or exc.__class__.__name__, "kind": "transient"}


def _log_residual(tool: str, ref: object, n: int) -> None:
    if n:
        print(f"[zendesk-mcp] {tool} {ref}: redacted {n} PII span(s)", file=sys.stderr)


def fetch_ticket(client: ZendeskClient, ticket_id: int) -> dict[str, Any]:
    try:
        t = client.get_ticket(int(ticket_id))
    except Exception as exc:  # noqa: BLE001 — boundary: never raise into the turn
        return _map_error(exc)
    payload = {
        "id": t.id,
        "subject": t.subject,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "tags": list(t.tags or []),
        "requester_org": t.requester_org,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }
    redacted, n = redact_value(payload)
    _log_residual("get_ticket", ticket_id, n)
    return redacted


def fetch_comments(client: ZendeskClient, ticket_id: int) -> dict[str, Any]:
    try:
        comments = client.get_comments(int(ticket_id))
    except Exception as exc:  # noqa: BLE001
        return _map_error(exc)
    items = [
        {
            "id": c.id,
            "public": c.public,
            "created_at": c.created_at,
            "body": c.body,
        }
        for c in comments
    ]
    redacted, n = redact_value({"comments": items, "count": len(items)})
    _log_residual("get_comments", ticket_id, n)
    return redacted


def _build_client() -> ZendeskClient:
    config = Config(
        zendesk_subdomain=os.environ.get("ZENDESK_SUBDOMAIN", ""),
        zendesk_email=os.environ.get("ZENDESK_EMAIL", ""),
        zendesk_api_token=os.environ.get("ZENDESK_API_TOKEN", ""),
    )
    return ZendeskClient(config)
