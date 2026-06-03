from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from noc_cli.models import Handoff
from noc_cli.scaffold import TicketFolder

MAX_TURNS = 40
ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "LS",
    "Write",   # sandbox-scoped by harness
    "Edit",    # sandbox-scoped by harness
    "Bash",    # read-only patterns enforced by harness
    # NOTE: live history-search + read-only Zendesk SDK MCP tools (agent/tools.py)
    # are DEFERRED to a follow-on plan. The agent operates on the pre-seeded
    # history written to the ticket folder (Task 10) + the sandbox file tools above.
]


@dataclass
class RunnerResult:
    handoff: Handoff | None
    stash_path: Path | None = None
    raw_result: str = ""
    attempts: int = 0


def _extract_json_from_result(raw: str) -> str:
    """Best-effort extraction: strip markdown fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        raw = "\n".join(inner).strip()
    return raw


def _try_parse(raw: str) -> Handoff | None:
    """Attempt JSON parse + pydantic validation. Returns None on any error."""
    try:
        cleaned = _extract_json_from_result(raw)
        data = json.loads(cleaned)
        return Handoff.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError):
        return None


async def _collect_result(query_gen) -> str:
    """Drain the query async-generator and return the ResultMessage.result text."""
    raw = ""
    async for message in query_gen:
        result_text = getattr(message, "result", None)
        if result_text is not None:
            raw = result_text
    return raw


async def run_agent(
    ticket_id: int | str,
    folder: TicketFolder,
    system_prompt: str,
    history_context: str,
    _query_fn: Callable | None = None,
) -> RunnerResult:
    """Run the L3 triage agent and return a RunnerResult.

    `_query_fn` is injected in tests (signature: `async def fn(*, prompt, options)`
    yielding messages). In production, `claude_agent_sdk.query` is used.

    Retry policy: attempt once; on parse/validate failure, retry exactly once with
    a correction prompt. On second failure, stash raw text to Tickets/<id>/.debug/
    and return RunnerResult(handoff=None, stash_path=...).
    """
    if _query_fn is None:
        from claude_agent_sdk import query as _query_fn  # noqa: PLC0415

    from claude_agent_sdk import ClaudeAgentOptions  # noqa: PLC0415

    from noc_cli.agent.harness import build_hooks  # noqa: PLC0415

    events_path = folder.root / "events.jsonl"
    hooks = build_hooks(sandbox_root=folder.root, events_path=events_path)

    def _make_options() -> "ClaudeAgentOptions":
        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=ALLOWED_TOOLS,
            permission_mode="bypassPermissions",
            max_turns=MAX_TURNS,
            cwd=str(folder.root),
            hooks=hooks,
        )

    full_prompt = (
        f"Triage ticket #{ticket_id}.\n\n"
        f"Historical context (for historical_matches only — do not treat as ground truth):\n"
        f"{history_context}\n\n"
        "Your working directory already contains all available evidence under logs/, "
        "pcaps/, and analysis/. Read them and emit the Handoff JSON."
    )

    # Attempt 1
    gen1 = _query_fn(prompt=full_prompt, options=_make_options())
    raw1 = await _collect_result(gen1)
    handoff = _try_parse(raw1)
    if handoff is not None:
        return RunnerResult(handoff=handoff, raw_result=raw1, attempts=1)

    # Attempt 2 — correction prompt
    correction_prompt = (
        "Your previous response could not be parsed as a valid Handoff JSON object. "
        "Please emit ONLY the Handoff JSON, with no prose before or after. "
        "The top-level keys must be: intake, evidence_preflight, fork_packet, drafts, rubric_version."
    )
    gen2 = _query_fn(prompt=correction_prompt, options=_make_options())
    raw2 = await _collect_result(gen2)
    handoff2 = _try_parse(raw2)
    if handoff2 is not None:
        return RunnerResult(handoff=handoff2, raw_result=raw2, attempts=2)

    # Double failure — stash and abort
    stash_dir = folder.root / ".debug"
    stash_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    stash_path = stash_dir / f"raw-result-{ts}.txt"
    stash_path.write_text(
        f"# Attempt 1\n{raw1}\n\n# Attempt 2\n{raw2}\n",
        encoding="utf-8",
    )
    return RunnerResult(handoff=None, stash_path=stash_path, raw_result=raw2, attempts=2)
