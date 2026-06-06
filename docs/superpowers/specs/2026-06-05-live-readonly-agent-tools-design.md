# Live Read-Only Agent Tools — Design

**Date:** 2026-06-05
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Layer:** Layer 2 — the application's runtime agent (ships to NOC operators). See [[layer1-vs-layer2-tooling]].
**Sub-project:** 1 of 3. Follow-ons (held): #2 deeper runbook grounding, #3 SDK tuning (prompt caching + model selection).

## Goal

Give the L3 triage agent **live, read-only tools** it can call *during* an investigation, instead of operating solely on the static snapshot seeded into the ticket folder. This is the work `noc_cli/agent/runner.py:24-27` explicitly defers ("live history-search + read-only Zendesk SDK MCP tools … are DEFERRED to a follow-on plan").

## Context (current state)

- The runtime agent (`noc_cli/agent/`) investigates one ticket and emits a validated `Handoff` JSON. It is mature: retry/parse handling, transcript stashing, `events.jsonl` logging, runbook+rubric grounding in the system prompt.
- A read-only **sandbox harness** (`agent/harness.py`) already denies destructive bash, out-of-sandbox writes, and — crucially — **Zendesk *write* MCP calls** (`mcp__zendesk__create_/update_/delete_/add_comment/close_/set_`). The write-guard for live tools exists; the tools do not.
- The agent's tools today are local-only: `Read`, `Glob`, `Grep`, `LS`, sandbox-scoped `Write`/`Edit`, read-only-pattern `Bash`.
- Data layer to wrap: `ZendeskClient` (read-only sync httpx — `get_ticket`, `get_comments`, `search`, …) and `seed_history(symptom_tag, zd_client, memory_store)` (merged local FTS5 + live Zendesk tag-search). Both run **once before** the agent today; their results are flattened into the seeded folder + `history_context` string.
- Redaction: `noc_cli/redact.py` `redact(text) -> (redacted, counts)` scrubs phone/address/coordinates. `investigate.py` redacts every evidence file before the agent sees it — the agent never touches raw PII.

## Verified SDK API (claude-agent-sdk 0.2.88, installed)

- `ClaudeAgentOptions.mcp_servers: dict[str, McpServerConfig] | str | Path`.
- External stdio config: `McpStdioServerConfig = {"command": str, "args": NotRequired[list[str]], "env": NotRequired[dict[str,str]]}` (`type:"stdio"` implicit).
- In-process: `create_sdk_mcp_server(name: str, version="1.0.0", tools=[SdkMcpTool]) -> McpSdkServerConfig`.
- Tool: `@tool(name: str, description: str, input_schema: type | dict)` on `async def handler(args) -> dict`.
- Both server kinds coexist in one `mcp_servers` dict. SDK namespaces tools as `mcp__<server>__<tool>`.
- `mcp` package (+ `mcp.server.fastmcp.FastMCP`, with `.tool` decorator and `.run`) is available transitively; promote to a direct dependency.

## Key decisions (all approved during brainstorming)

1. **Supplement, not replace.** Seeding of ticket + evidence files + initial history snapshot is unchanged. Live tools are purely additive. (Evidence files like extracted pcap/log analysis must be seeded regardless — live Zendesk cannot supply them.)
2. **Redact ALL tool output.** Every live tool's text output passes through `redact()` before reaching the agent, preserving the "agent never sees raw PII" invariant. Residual-PII counts are logged for audit (never shown to the agent).
3. **Tool set (4 read-only tools).**
   - `zendesk` server: `get_ticket(ticket_id)`, `get_comments(ticket_id)`, `search(query)`.
   - `history` server: `search_history(symptom_tag)`.
   - Live re-fetch of the *current* ticket is supported (the tools are id-parameterized) — useful when the seeded snapshot may be stale.
4. **Architecture = hybrid.** `zendesk` is an **external stdio MCP server** (FastMCP); `history` is **in-process** (`create_sdk_mcp_server`).
   - *Rationale for external Zendesk:* it is the artifact intended to eventually gain **write** capability after extensive vetting; a standalone, independently-versioned, independently-auditable server is the correct home for future mutations. History only reads local SQLite + a Zendesk search — no write future, no need for process isolation.

## Architecture & file structure

| File | Responsibility |
|---|---|
| `noc_cli/mcp/__init__.py` *(new)* | New package for external MCP server(s). |
| `noc_cli/mcp/zendesk_server.py` *(new)* | External stdio MCP server (FastMCP). Builds `ZendeskClient` from env, exposes 3 read tools, **redacts every output**, maps errors to structured results, emits residual-PII counts to stderr. Console entry `main()` runs stdio transport. |
| `noc_cli/agent/tools.py` *(new)* | In-process `history` server via `create_sdk_mcp_server` + `@tool search_history` (wraps `seed_history`, injected `MemoryStore`+`ZendeskClient`, redacts subjects, logs residual counts to `events.jsonl`). Plus `build_mcp_servers(config, memory_store, events_path) -> (mcp_servers_dict, allowed_tool_names)`. |
| `noc_cli/agent/runner.py` *(modify)* | Call `build_mcp_servers(...)`; pass `mcp_servers=` into `ClaudeAgentOptions`; extend `ALLOWED_TOOLS` with the 4 `mcp__*` names; remove the DEFERRED note. |
| `noc_cli/agent/prompt.py` *(modify)* | Add a "Live tools" section: each tool, *when* to use it (follow a related/linked ticket id; re-fetch the current ticket if the seeded snapshot looks stale; search history on demand), and that outputs are pre-redacted. |
| `pyproject.toml` *(modify)* | Add `mcp` direct dep; add console script `noc-cli-zendesk-mcp = noc_cli.mcp.zendesk_server:main`. |

## Data flow

Seeding unchanged. At agent launch, `runner` builds:
```python
mcp_servers = {
    "zendesk": {
        "command": sys.executable,
        "args": ["-m", "noc_cli.mcp.zendesk_server"],
        "env": {"ZENDESK_SUBDOMAIN": ..., "ZENDESK_EMAIL": ..., "ZENDESK_API_TOKEN": ...},
    },
    "history": create_sdk_mcp_server("history", tools=[search_history]),
}
allowed_tools = ALLOWED_TOOLS + [
    "mcp__zendesk__get_ticket", "mcp__zendesk__get_comments",
    "mcp__zendesk__search", "mcp__history__search_history",
]
```
- `mcp__zendesk__*` → external process → `ZendeskClient` read → **redact inside the server** → JSON over stdio (PII never crosses the wire raw).
- `mcp__history__search_history` → in-process → `seed_history` → redact subjects → return.
- Existing `PostToolUse` harness hook logs each call + snippet to `events.jsonl`; because redaction already happened, the audit log captures *redacted* output.

## Redaction boundary

- **External server is the trust boundary.** Every string field of every tool result is run through `noc_cli.redact.redact` before returning. The server imports `noc_cli.redact` (in-repo, same venv). Residual-PII counts → stderr (captured by the parent for ops).
- **History (in-process)** redacts subjects/hints in the tool handler and logs residual counts directly into the ticket's `events.jsonl`.

## Read-only posture + future write seam (seam only — NOT built here)

The server registers **only read tools** in this sub-project. The structure leaves a clean insertion point for future writes; this spec builds **none**. Future write tools will pass a 3-layer gate:
1. Server-side: behind an explicit off-by-default flag (e.g. `--enable-writes`), added only after vetting.
2. Harness: the existing `mcp__zendesk__create_/update_/delete_/add_comment/close_/set_` deny-guard stays (defense in depth).
3. Agent: read-only system-prompt constraints remain.

## Error handling & config

- Tool handlers **never raise into the turn**. `ZendeskError` / HTTP 404 / auth / timeout → structured `{"error": str, "kind": "not_found" | "auth" | "transient"}`, so the agent adapts (e.g., note missing data → Fork D) instead of crashing.
- **Secrets** flow only via the stdio `env` dict the runner passes (from the loaded `Config`) — scoped to the subprocess, never inherited broadly, never logged.
- **Bounded outputs:** `search` capped at 25 results; `get_comments` returns a structured, redacted list. Optional per-run cache for repeat `get_ticket`/`get_comments` by id (the external process lives for the run) — *in scope as a small dict cache; acceptable to defer if it complicates the first plan.*
- `max_turns` stays 40.

## Testing (no live Zendesk calls — AGENTS.md rule)

- **zendesk_server:** unit-test each tool handler with an injected fake `ZendeskClient` / `pytest-httpx`. Assert: read-only behavior, redaction applied to every output field, error→structured mapping (404/auth/timeout). Assert the server registers exactly the 3 read tool names and **no** write tool names.
- **history tools:** fake `MemoryStore` + fake client. Assert redaction, dedup by ticket_id, limit.
- **runner:** extend existing `_query_fn` injection tests. Assert `_make_options()` carries both servers in `mcp_servers` and the 4 tool names in `allowed_tools`. No real subprocess in unit tests.
- **integration smoke (optional, `@pytest.mark` gated):** spawn the stdio server, list tools, assert the 3 names. Gated so it never reaches live Zendesk.

## Out of scope (future tools — captured, not built)

Dispatched to the Notion "Feature ideas & discussions" dashboard:
- `get_ticket_audits` — status/assignment timeline.
- `list_related_tickets` — Zendesk problem/incident links.
- `get_organization` — org-wide pattern context.
- `get_attachment_text` — **server-side text extraction** (the safe successor to the deliberately-excluded binary `download_attachment`).
- `search_jira` — cross-system correlation for Fork A.
- **Write tools** (add/update comment, set status/tags, create) — behind the 3-layer gate above, after extensive vetting.

## Non-goals

- No change to seeding, the rubric, or the `Handoff` schema.
- No prompt caching / model selection (that is sub-project #3).
- No deeper runbook grounding (sub-project #2).
