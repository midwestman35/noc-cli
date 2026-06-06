# Live Read-Only Agent Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the L3 triage agent four live, read-only tools — `mcp__zendesk__{get_ticket,get_comments,search}` (external stdio FastMCP server) and `mcp__history__search_history` (in-process SDK server) — that it can call during an investigation, supplementing the seeded snapshot.

**Architecture:** An external stdio MCP server (`noc_cli/mcp/zendesk_server.py`, built on `FastMCP`) wraps the existing read-only `ZendeskClient`; it is the **trust boundary** — every output is field-allowlisted and PII-redacted before crossing the wire. An in-process SDK MCP server (`noc_cli/agent/tools.py`) wraps `seed_history`. `runner.py` builds both via `build_mcp_servers(config, memory_store)` and passes them to `ClaudeAgentOptions`. Seeding is unchanged (purely additive).

**Tech Stack:** Python 3.10+, `uv`, `claude-agent-sdk` 0.2.88 (`create_sdk_mcp_server`, `@tool`, `ClaudeAgentOptions.mcp_servers`), `mcp` / `mcp.server.fastmcp.FastMCP`, pytest + pytest-httpx + anyio.

**Spec:** `docs/superpowers/specs/2026-06-05-live-readonly-agent-tools-design.md`

---

## Branch & environment notes

- Implement on `feat/live-readonly-agent-tools` (already created from `origin/main` = `6c28b40`). The spec is committed there (`86eefef`).
- **This branch is based on pre-ruff `main`.** Ruff and the format-on-edit / protected-path hooks (PR #7) are NOT present here — do **not** run `ruff` in these tasks; `uv run pytest` is the gate. After PR #7 merges to `main`, rebase this branch onto `main` to inherit ruff + the hooks.
- Follow the codebase conventions: `from __future__ import annotations`, 4-space indent, type hints, injectable side effects (clients passed in for tests), `snake_case`.

## Refinement over the spec (privacy)

The spec says "redact all output via `redact()`." Implementation strengthens this: `redact()` only catches phones/addresses/coords, **not emails**. So each tool returns a **curated field allowlist** (operational fields + free-text), with raw personal identifiers (`requester_email`, `assignee_email`, `requester_id`, `author_id`) **excluded entirely**, and the free-text fields (`subject`, `description`, comment `body`) passed through `redact()`. This makes the "agent never sees raw PII" invariant actually hold.

## File structure

| File | Responsibility |
|---|---|
| `noc_cli/redact.py` *(modify)* | Add `redact_value(value) -> (value, int)` — recursive redaction of str leaves in JSON-like structures. |
| `noc_cli/mcp/__init__.py` *(new, empty)* | New package marker. |
| `noc_cli/mcp/zendesk_server.py` *(new)* | External FastMCP stdio server: `_map_error`, `fetch_ticket`, `fetch_comments`, `search_tickets`, `build_server`, `main`. |
| `noc_cli/agent/tools.py` *(new)* | `run_history_search` + `build_mcp_servers(config, memory_store)`. |
| `noc_cli/agent/runner.py` *(modify)* | Thread `config`/`memory_store`; build `mcp_servers`; extend `allowed_tools`; drop DEFERRED note. |
| `noc_cli/agent/prompt.py` *(modify)* | Add "Live tools" guidance section. |
| `noc_cli/investigate.py` *(modify)* | Pass `config` + `memory_store` into `run_agent`. |
| `pyproject.toml` *(modify)* | Add `mcp>=1.0` dep + console script. |
| `tests/test_redact.py`, `tests/test_zendesk_mcp.py` *(new)*, `tests/test_agent_tools.py` *(new)*, `tests/test_agent_runner.py`, `tests/test_agent_prompt.py` | Tests. |

---

### Task 1: Add `mcp` dependency + console script

**Files:** Modify `pyproject.toml`; regenerate `uv.lock`.

- [ ] **Step 1: Confirm clean tree**

Run: `git status --short`
Expected: empty (the spec is already committed).

- [ ] **Step 2: Add `mcp` to runtime dependencies**

In `pyproject.toml`, add `"mcp>=1.0",` to the `[project].dependencies` list (alongside `claude-agent-sdk`).

- [ ] **Step 3: Add the console script**

In `pyproject.toml`, under `[project.scripts]`, add:
```toml
noc-cli-zendesk-mcp = "noc_cli.mcp.zendesk_server:main"
```

- [ ] **Step 4: Sync and verify**

Run: `uv sync`
Then: `uv run python -c "import mcp; from mcp.server.fastmcp import FastMCP; print('mcp ok')"`
Expected: prints `mcp ok`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(agent): add mcp dependency + zendesk-mcp console script"
```

---

### Task 2: `redact_value` recursive helper

**Files:** Modify `noc_cli/redact.py`; Test `tests/test_redact.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_redact.py`:
```python
from noc_cli.redact import redact_value


def test_redact_value_scrubs_string_leaves_and_counts():
    obj = {"loc": "37.7749, -122.4194", "id": 5, "tags": ["x"]}
    out, total = redact_value(obj)
    assert out["loc"] == "<COORDS>"
    assert out["id"] == 5          # non-str untouched
    assert out["tags"] == ["x"]
    assert total == 1


def test_redact_value_recurses_into_lists_of_dicts():
    obj = {"comments": [{"body": "37.7749, -122.4194"}, {"body": "clean"}]}
    out, total = redact_value(obj)
    assert out["comments"][0]["body"] == "<COORDS>"
    assert out["comments"][1]["body"] == "clean"
    assert total == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_redact.py -k redact_value -v`
Expected: FAIL — `ImportError: cannot import name 'redact_value'`.

- [ ] **Step 3: Implement**

Append to `noc_cli/redact.py`:
```python
def redact_value(value):
    """Recursively redact string leaves in a JSON-like structure.

    Returns ``(redacted_value, total_redactions)``. Dicts and lists are walked;
    non-string leaves pass through untouched. ``total_redactions`` is the sum of
    phone/address/coordinate substitutions across all string leaves.
    """
    if isinstance(value, str):
        red, counts = redact(value)
        return red, counts.phones + counts.addresses + counts.coords
    if isinstance(value, dict):
        out: dict = {}
        total = 0
        for key, val in value.items():
            out[key], n = redact_value(val)
            total += n
        return out, total
    if isinstance(value, list):
        out_list = []
        total = 0
        for item in value:
            red_item, n = redact_value(item)
            out_list.append(red_item)
            total += n
        return out_list, total
    return value, 0
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_redact.py -k redact_value -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/redact.py tests/test_redact.py
git commit -m "feat(redact): add recursive redact_value for structured tool payloads"
```

---

### Task 3: Zendesk server — `_map_error` + `fetch_ticket`

**Files:** Create `noc_cli/mcp/__init__.py` (empty), `noc_cli/mcp/zendesk_server.py`; Test `tests/test_zendesk_mcp.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_zendesk_mcp.py`:
```python
import httpx
import pytest

from noc_cli.mcp.zendesk_server import fetch_ticket, _map_error
from noc_cli.models import Ticket
from noc_cli.zendesk import ZendeskError


class _FakeClient:
    def __init__(self, ticket=None, exc=None):
        self._ticket = ticket
        self._exc = exc

    def get_ticket(self, ticket_id):
        if self._exc:
            raise self._exc
        return self._ticket


def _ticket(**kw):
    base = dict(
        id=761, subject="911 at 37.7749, -122.4194", description="caller addr 123 Main Street",
        requester_org="City PD", requester_email="caller@example.com", requester_id=99,
        assignee_id=1, assignee_email="noc@carbyne.com", status="open", priority="high",
        tags=["apex"], created_at="2026-06-01T00:00:00Z", updated_at="2026-06-02T00:00:00Z",
        comments=[],
    )
    base.update(kw)
    return Ticket.model_validate(base)


def test_fetch_ticket_redacts_freetext_and_excludes_email_identifiers():
    out = fetch_ticket(_FakeClient(ticket=_ticket()), 761)
    assert out["id"] == 761
    assert out["status"] == "open"
    assert "<COORDS>" in out["subject"]      # coords scrubbed
    assert "<ADDR>" in out["description"]     # address scrubbed
    assert "requester_email" not in out       # raw email identifier excluded
    assert "assignee_email" not in out
    assert "requester_id" not in out


def test_fetch_ticket_maps_404_to_structured_error():
    req = httpx.Request("GET", "https://x.zendesk.com/api/v2/tickets/9.json")
    resp = httpx.Response(404, request=req)
    exc = httpx.HTTPStatusError("not found", request=req, response=resp)
    out = fetch_ticket(_FakeClient(exc=exc), 9)
    assert out == {"error": "not found", "kind": "not_found"}


def test_map_error_classifies_zendesk_auth():
    out = _map_error(ZendeskError("Zendesk auth failed - check token"))
    assert out["kind"] == "auth"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_zendesk_mcp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.mcp'`.

- [ ] **Step 3: Implement**

Create empty `noc_cli/mcp/__init__.py`.

Create `noc_cli/mcp/zendesk_server.py`:
```python
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


def _build_client() -> ZendeskClient:
    config = Config(
        zendesk_subdomain=os.environ.get("ZENDESK_SUBDOMAIN", ""),
        zendesk_email=os.environ.get("ZENDESK_EMAIL", ""),
        zendesk_api_token=os.environ.get("ZENDESK_API_TOKEN", ""),
    )
    return ZendeskClient(config)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_zendesk_mcp.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/mcp/__init__.py noc_cli/mcp/zendesk_server.py tests/test_zendesk_mcp.py
git commit -m "feat(mcp): zendesk server fetch_ticket with redaction + error mapping"
```

---

### Task 4: Zendesk server — `fetch_comments`

**Files:** Modify `noc_cli/mcp/zendesk_server.py`; Test `tests/test_zendesk_mcp.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_zendesk_mcp.py`:
```python
from noc_cli.mcp.zendesk_server import fetch_comments
from noc_cli.models import Comment


class _FakeCommentsClient:
    def __init__(self, comments=None, exc=None):
        self._comments = comments or []
        self._exc = exc

    def get_comments(self, ticket_id):
        if self._exc:
            raise self._exc
        return self._comments


def _comment(**kw):
    base = dict(id=1, author_id=42, public=True, body="callback 37.7749, -122.4194",
                created_at="2026-06-01T00:00:00Z", attachments=[])
    base.update(kw)
    return Comment.model_validate(base)


def test_fetch_comments_redacts_body_and_excludes_author_id():
    out = fetch_comments(_FakeCommentsClient(comments=[_comment()]), 761)
    assert out["comments"][0]["public"] is True
    assert "<COORDS>" in out["comments"][0]["body"]
    assert "author_id" not in out["comments"][0]
    assert out["count"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_zendesk_mcp.py -k fetch_comments -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_comments'`.

- [ ] **Step 3: Implement**

Add to `noc_cli/mcp/zendesk_server.py`:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_zendesk_mcp.py -k fetch_comments -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/mcp/zendesk_server.py tests/test_zendesk_mcp.py
git commit -m "feat(mcp): zendesk server fetch_comments (redacted, author_id excluded)"
```

---

### Task 5: Zendesk server — `search_tickets` (capped)

**Files:** Modify `noc_cli/mcp/zendesk_server.py`; Test `tests/test_zendesk_mcp.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_zendesk_mcp.py`:
```python
from noc_cli.mcp.zendesk_server import search_tickets, SEARCH_CAP


class _FakeSearchClient:
    def __init__(self, tickets):
        self._tickets = tickets

    def search(self, query):
        return self._tickets


def test_search_caps_results_and_redacts_subjects():
    tickets = [_ticket(id=i, subject="37.7749, -122.4194") for i in range(SEARCH_CAP + 5)]
    out = search_tickets(_FakeSearchClient(tickets), "apex")
    assert out["count"] == SEARCH_CAP
    assert len(out["results"]) == SEARCH_CAP
    assert "<COORDS>" in out["results"][0]["subject"]
    assert set(out["results"][0].keys()) == {"id", "subject", "status", "tags"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_zendesk_mcp.py -k search -v`
Expected: FAIL — `ImportError: cannot import name 'search_tickets'`.

- [ ] **Step 3: Implement**

Add to `noc_cli/mcp/zendesk_server.py`:
```python
def search_tickets(client: ZendeskClient, query: str, cap: int = SEARCH_CAP) -> dict[str, Any]:
    try:
        tickets = client.search(query)
    except Exception as exc:  # noqa: BLE001
        return _map_error(exc)
    items = [
        {
            "id": t.id,
            "subject": t.subject,
            "status": t.status,
            "tags": list(t.tags or []),
        }
        for t in (tickets or [])[:cap]
    ]
    redacted, n = redact_value({"results": items, "count": len(items)})
    _log_residual("search", query, n)
    return redacted
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_zendesk_mcp.py -k search -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/mcp/zendesk_server.py tests/test_zendesk_mcp.py
git commit -m "feat(mcp): zendesk server search_tickets (capped, redacted)"
```

---

### Task 6: Zendesk server — FastMCP wiring + `main` (exactly 3 read tools)

**Files:** Modify `noc_cli/mcp/zendesk_server.py`; Test `tests/test_zendesk_mcp.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_zendesk_mcp.py`:
```python
import anyio

from noc_cli.mcp.zendesk_server import build_server


def test_server_registers_exactly_the_three_read_tools():
    server = build_server(client=_FakeClient(ticket=_ticket()))
    tools = anyio.run(server.list_tools)
    names = sorted(t.name for t in tools)
    assert names == ["get_comments", "get_ticket", "search"]
    # No write-capable tool names exist.
    assert not any(
        n.startswith(("create", "update", "delete", "add_comment", "close", "set"))
        for n in names
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_zendesk_mcp.py -k server_registers -v`
Expected: FAIL — `ImportError: cannot import name 'build_server'`.

- [ ] **Step 3: Implement**

Add to `noc_cli/mcp/zendesk_server.py`:
```python
def build_server(client: ZendeskClient | None = None):
    """Build the FastMCP server. `client` is injectable for tests."""
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

    zd = client if client is not None else _build_client()
    server = FastMCP("zendesk")

    @server.tool(
        name="get_ticket",
        description="Fetch one Zendesk ticket by id. Read-only; output is PII-redacted. "
        "Use to re-fetch the current ticket if the seeded copy may be stale, or to "
        "inspect a related ticket id you discovered.",
    )
    def get_ticket(ticket_id: int) -> str:
        return json.dumps(fetch_ticket(zd, ticket_id))

    @server.tool(
        name="get_comments",
        description="Fetch the comment thread for a ticket id. Read-only; PII-redacted.",
    )
    def get_comments(ticket_id: int) -> str:
        return json.dumps(fetch_comments(zd, ticket_id))

    @server.tool(
        name="search",
        description="Search Zendesk tickets (Zendesk query syntax). Read-only; PII-redacted; "
        f"capped at {SEARCH_CAP} results.",
    )
    def search(query: str) -> str:
        return json.dumps(search_tickets(zd, query))

    return server


def main() -> None:
    """Console entry / `python -m noc_cli.mcp.zendesk_server` — run stdio transport."""
    build_server().run()  # transport defaults to "stdio"


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_zendesk_mcp.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/mcp/zendesk_server.py tests/test_zendesk_mcp.py
git commit -m "feat(mcp): wire FastMCP zendesk server (3 read tools) + stdio main"
```

---

### Task 7: History tool logic — `run_history_search`

**Files:** Create `noc_cli/agent/tools.py`; Test `tests/test_agent_tools.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_tools.py`:
```python
from noc_cli.agent.tools import run_history_search
from noc_cli.memory import InvestigationRecord, MemoryStore, append_investigation


class _FakeZd:
    def search(self, keyword):
        return []  # no live Zendesk in tests


def test_run_history_search_returns_redacted_candidates(tmp_path):
    store = MemoryStore(db_path=tmp_path / "m.db", memory_md_path=tmp_path / "MEMORY.md")
    store.init()
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="500", symptom_tag="[apex]", fork_letter="A", confidence="High",
            one_line_fingerprint="apex outage at 37.7749, -122.4194",
            summary="apex node down",
        ),
    )
    out, total = run_history_search("[apex]", _FakeZd(), store)
    assert out["count"] >= 1
    subjects = [c["subject"] for c in out["candidates"]]
    assert any("<COORDS>" in s for s in subjects)  # fingerprint coords scrubbed
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.agent.tools'`.

- [ ] **Step 3: Implement**

Create `noc_cli/agent/tools.py`:
```python
from __future__ import annotations

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_agent_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/tools.py tests/test_agent_tools.py
git commit -m "feat(agent): history search tool logic (merged sources, redacted)"
```

---

### Task 8: `build_mcp_servers` (hybrid dict + allowed tool names)

**Files:** Modify `noc_cli/agent/tools.py`; Test `tests/test_agent_tools.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_tools.py`:
```python
from noc_cli.agent.tools import build_mcp_servers
from noc_cli.config import Config


def test_build_mcp_servers_hybrid_shape(tmp_path):
    store = MemoryStore(db_path=tmp_path / "m.db", memory_md_path=tmp_path / "MEMORY.md")
    store.init()
    config = Config(zendesk_subdomain="acme", zendesk_email="a@b.co", zendesk_api_token="tok")
    servers, allowed = build_mcp_servers(config, store, _zendesk_client=_FakeZd())

    # zendesk = external stdio process carrying scoped creds
    zd = servers["zendesk"]
    assert zd["command"]  # python executable
    assert zd["args"] == ["-m", "noc_cli.mcp.zendesk_server"]
    assert set(zd["env"]) == {"ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN"}
    assert zd["env"]["ZENDESK_SUBDOMAIN"] == "acme"

    # history = in-process SDK server (McpSdkServerConfig dict: type == "sdk")
    assert servers["history"]["type"] == "sdk"

    assert allowed == [
        "mcp__zendesk__get_ticket",
        "mcp__zendesk__get_comments",
        "mcp__zendesk__search",
        "mcp__history__search_history",
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent_tools.py -k build_mcp_servers -v`
Expected: FAIL — `ImportError: cannot import name 'build_mcp_servers'`.

- [ ] **Step 3: Implement**

Add to `noc_cli/agent/tools.py`:
```python
import json  # add to existing imports at top
import sys   # add to existing imports at top

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_agent_tools.py -v`
Expected: PASS (both tests). Note: `create_sdk_mcp_server` returns a `McpSdkServerConfig` whose `["type"]` is `"sdk"`.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/tools.py tests/test_agent_tools.py
git commit -m "feat(agent): build_mcp_servers (external zendesk + in-process history)"
```

---

### Task 9: Wire MCP servers into `runner.py`

**Files:** Modify `noc_cli/agent/runner.py`; Test `tests/test_agent_runner.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_runner.py`:
```python
import anyio

from noc_cli.agent.runner import run_agent
from noc_cli.config import Config
from noc_cli.memory import MemoryStore


def test_run_agent_passes_mcp_servers_and_allowed_tools(tmp_path):
    from noc_cli.scaffold import TicketFolder

    folder = TicketFolder(root=tmp_path / "Tickets" / "761")
    folder.root.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(db_path=tmp_path / "m.db", memory_md_path=tmp_path / "MEMORY.md")
    store.init()
    config = Config(zendesk_subdomain="acme", zendesk_email="a@b.co", zendesk_api_token="tok")

    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        if False:
            yield  # make this an async generator that yields nothing

    anyio.run(
        lambda: run_agent(
            ticket_id=761, folder=folder, system_prompt="sys", history_context="",
            config=config, memory_store=store, _query_fn=fake_query,
        )
    )
    opts = captured["options"]
    assert set(opts.mcp_servers) == {"zendesk", "history"}
    for name in (
        "mcp__zendesk__get_ticket", "mcp__zendesk__get_comments",
        "mcp__zendesk__search", "mcp__history__search_history",
    ):
        assert name in opts.allowed_tools
```

(If `TicketFolder` needs other init args, mirror the construction used elsewhere in `tests/test_agent_runner.py`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent_runner.py -k passes_mcp_servers -v`
Expected: FAIL — `run_agent` has no `config`/`memory_store` params (TypeError), or `mcp_servers` empty.

- [ ] **Step 3: Implement**

In `noc_cli/agent/runner.py`:

(a) Replace the `ALLOWED_TOOLS` block's DEFERRED note (lines ~16-27) — drop the comment lines about deferral, keeping the local tools list:
```python
ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "LS",
    "Write",  # sandbox-scoped by harness
    "Edit",  # sandbox-scoped by harness
    "Bash",  # read-only patterns enforced by harness
]
```

(b) Extend `run_agent`'s signature (add `config` and `memory_store` before `_query_fn`):
```python
async def run_agent(
    ticket_id: int | str,
    folder: TicketFolder,
    system_prompt: str,
    history_context: str,
    initial_hypothesis: str = "",
    config=None,
    memory_store=None,
    _query_fn: Callable | None = None,
) -> RunnerResult:
```

(c) After `hooks = build_hooks(...)` (~line 196), build the servers:
```python
    mcp_servers: dict = {}
    extra_tools: list[str] = []
    if config is not None and memory_store is not None:
        from noc_cli.agent.tools import build_mcp_servers  # noqa: PLC0415

        mcp_servers, extra_tools = build_mcp_servers(config, memory_store)
```

(d) Update `_make_options` to pass them:
```python
    def _make_options() -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=ALLOWED_TOOLS + extra_tools,
            permission_mode="bypassPermissions",
            max_turns=MAX_TURNS,
            cwd=str(folder.root),
            hooks=hooks,
            mcp_servers=mcp_servers,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: PASS (new test + all existing runner tests — existing callers omit `config`/`memory_store`, so `mcp_servers` stays `{}` and behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/runner.py tests/test_agent_runner.py
git commit -m "feat(agent): wire live mcp_servers + tool allowlist into run_agent"
```

---

### Task 10: Add the "Live tools" section to the system prompt

**Files:** Modify `noc_cli/agent/prompt.py`; Test `tests/test_agent_prompt.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_prompt.py`:
```python
from noc_cli.agent.prompt import build_system_prompt


def test_system_prompt_documents_live_tools():
    sp = build_system_prompt("## Symptom Class\nx")
    assert "Live tools" in sp
    for token in ("get_ticket", "get_comments", "search", "search_history"):
        assert token in sp
    # reinforces that live output is already redacted
    assert "redacted" in sp.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent_prompt.py -k live_tools -v`
Expected: FAIL — "Live tools" not in prompt.

- [ ] **Step 3: Implement**

In `noc_cli/agent/prompt.py`, insert this block into `_PROMPT_TEMPLATE` immediately before the `# Constraints — READ-ONLY operation` line:
```python
# Live tools (read-only; results are already PII-redacted)
You also have live, read-only tools. Their output is pre-redacted, so treat any
`<PHONE>`/`<ADDR>`/`<COORDS>` markers as expected.
- `get_ticket(ticket_id)` / `get_comments(ticket_id)` — fetch a ticket or its
  comments by id. Use to follow a related/linked ticket you discover, or to
  re-fetch the CURRENT ticket if the seeded copy under logs/ may be stale.
- `search(query)` — find sibling tickets (Zendesk query syntax), capped.
- `search_history(symptom_tag)` — pull prior-investigation matches on demand to
  populate `fork_packet.historical_matches`.
Prefer the seeded evidence files for the incident itself; reach for live tools to
corroborate, follow links, or fill gaps. Never call a Zendesk write endpoint.

```
(Keep the existing `# Constraints — READ-ONLY operation` section after it.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_agent_prompt.py -v`
Expected: PASS (new test + existing prompt tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/prompt.py tests/test_agent_prompt.py
git commit -m "feat(agent): document live read-only tools in the system prompt"
```

---

### Task 11: Thread `config` + `memory_store` through `investigate.py`

**Files:** Modify `noc_cli/investigate.py`; Test `tests/test_investigate_module.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_investigate_module.py` a test that the agent path forwards the new kwargs. Use the existing module's patterns; the key assertion:
```python
def test_investigate_forwards_config_and_memory_store_to_run_agent(monkeypatch, tmp_path):
    import noc_cli.investigate as inv

    captured = {}

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        from noc_cli.agent.runner import RunnerResult
        from noc_cli.models import Handoff
        # minimal valid handoff via the fixture skeleton used elsewhere in tests
        raise inv.InvestigationError("stop after capture")

    monkeypatch.setattr(inv.run_agent if hasattr(inv, "run_agent") else "noc_cli.agent.runner.run_agent", fake_run_agent, raising=False)
    # NOTE: implementer — wire monkeypatch to the actual import site used in run();
    # the assertion below is the contract:
    # assert "config" in captured and "memory_store" in captured
```

**Implementer note:** `run_agent` is imported lazily inside `investigate.py:run()`. Patch it at its definition (`monkeypatch.setattr("noc_cli.agent.runner.run_agent", fake_run_agent)`) and drive the existing `investigate`/`run` entry the other tests use, then assert `captured["config"]` and `captured["memory_store"]` are set. If the existing tests run the agent path via a fixture, model this test on that fixture.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_investigate_module.py -k forwards_config -v`
Expected: FAIL — `config`/`memory_store` not in captured kwargs.

- [ ] **Step 3: Implement**

In `noc_cli/investigate.py`, update the `run_agent(...)` call (~line 155) to pass the in-scope `config` and `mem_store`:
```python
        runner_result = await run_agent(
            ticket_id=ticket_id,
            folder=folder,
            system_prompt=system_prompt,
            history_context=history_context,
            initial_hypothesis=initial_hypothesis,
            config=config,
            memory_store=mem_store,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_investigate_module.py -v`
Expected: PASS (new test + existing). Existing fixture-mode tests are unaffected (they never reach `run_agent`).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/investigate.py tests/test_investigate_module.py
git commit -m "feat(investigate): pass config + memory_store to run_agent for live tools"
```

---

### Task 12: Integration smoke test (optional, marked)

**Files:** Test `tests/test_zendesk_mcp_integration.py` (new).

- [ ] **Step 1: Write the test**

Create `tests/test_zendesk_mcp_integration.py`:
```python
import sys

import anyio
import pytest

pytestmark = pytest.mark.integration


def test_spawned_server_lists_three_read_tools():
    """Spawn the stdio server as a subprocess and list its tools over MCP.

    Never touches live Zendesk (we only list tools; no creds needed to enumerate).
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "noc_cli.mcp.zendesk_server"],
        env={"ZENDESK_SUBDOMAIN": "x", "ZENDESK_EMAIL": "x@x.co", "ZENDESK_API_TOKEN": "x"},
    )

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return sorted(t.name for t in result.tools)

    names = anyio.run(_run)
    assert names == ["get_comments", "get_ticket", "search"]
```

- [ ] **Step 2: Register the marker**

In `pyproject.toml` under `[tool.pytest.ini_options]`, add:
```toml
markers = ["integration: spawns the stdio MCP server subprocess"]
```

- [ ] **Step 3: Run the integration test explicitly**

Run: `uv run pytest tests/test_zendesk_mcp_integration.py -m integration -v`
Expected: PASS — lists `['get_comments', 'get_ticket', 'search']`. (If the MCP stdio client import path differs in the installed `mcp` version, adjust the import per `python -c "import mcp; help(mcp)"`; the contract is: spawn, initialize, list_tools == the 3 names.)

- [ ] **Step 4: Confirm the default suite still excludes it cleanly**

Run: `uv run pytest -q`
Expected: full suite passes; the integration test is collected but only runs under `-m integration` if you set `addopts`/CI to deselect — otherwise it runs and passes too. Keep it in the suite (it's hermetic — no live Zendesk).

- [ ] **Step 5: Commit**

```bash
git add tests/test_zendesk_mcp_integration.py pyproject.toml
git commit -m "test(mcp): integration smoke — spawned stdio server lists 3 read tools"
```

---

## Self-Review

**Spec coverage:**
- Supplement (seeding unchanged) → Tasks 9/11 add tools without touching seeding ✔
- Redact all tool output → `redact_value` (Task 2) used in every tool (Tasks 3/4/5/7); field allowlist excludes raw emails ✔
- 4-tool set (get_ticket/get_comments/search/search_history) → Tasks 3-8 ✔
- Current-ticket live re-fetch → id-parameterized `get_ticket` + prompt guidance (Tasks 6/10) ✔
- Hybrid (external zendesk / in-process history) → Tasks 6/8 ✔
- External server = trust/redaction boundary → redaction inside `fetch_*` (Tasks 3-5) ✔
- Read-only + write-seam (no writes built) → only read tools; Task 6 test asserts no write tool names ✔
- Error handling never raises into the turn → `_map_error` (Task 3), `seed_history` swallows (Task 7) ✔
- Secrets via scoped env, never logged → Task 8 env dict; residual counts to stderr only (Task 3) ✔
- Bounded output (search cap 25) → Task 5 ✔
- Testing without live Zendesk → fakes/pytest-httpx throughout; integration is hermetic list-tools (Task 12) ✔
- Per-run cache → intentionally deferred per spec ("acceptable to defer"); not built ✔

**Placeholder scan:** No TBD/TODO. Task 11's test carries an explicit implementer note (the monkeypatch site depends on the existing test fixture style) rather than a fake assertion — flagged, not hidden. All code blocks are complete.

**Type/name consistency:** `redact_value` returns `(value, int)` and is called that way everywhere. `build_mcp_servers(config, memory_store, *, _zendesk_client=None) -> (dict, list[str])` matches its test and the runner call. `ALLOWED_MCP_TOOLS` names match the prompt tokens, the runner test, and the `build_server` registration (`get_ticket`/`get_comments`/`search`; the `mcp__zendesk__`/`mcp__history__` prefixes are SDK-applied). `run_history_search(symptom_tag, zendesk_client, memory_store, limit)` matches Tasks 7/8.

**Known seam:** the harness already denies `mcp__zendesk__create_/update_/...`; this plan adds only reads, so nothing is denied at runtime. No harness change needed.
