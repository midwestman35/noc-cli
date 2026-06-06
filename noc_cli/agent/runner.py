from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
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
    "Write",  # sandbox-scoped by harness
    "Edit",  # sandbox-scoped by harness
    "Bash",  # read-only patterns enforced by harness
    # NOTE: live history-search + read-only Zendesk SDK MCP tools (agent/tools.py)
    # are DEFERRED to a follow-on plan. The agent operates on the pre-seeded
    # history written to the ticket folder (Task 10) + the sandbox file tools above.
]


@dataclass
class TranscriptEntry:
    """One captured step from the agent's streamed turn."""

    kind: str
    text: str = ""
    tool_name: str = ""
    tool_args: str = ""
    raw_text: str = ""
    raw_tool_args: str = ""


@dataclass
class RunnerResult:
    handoff: Handoff | None
    stash_path: Path | None = None
    raw_result: str = ""
    attempts: int = 0
    transcript: list[TranscriptEntry] = field(default_factory=list)


_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json_from_result(raw: str) -> str:
    """Best-effort extraction of the Handoff JSON from an agent reply.

    The agent is told to emit only JSON, but the active output style can still
    prepend prose / `★ Insight` blocks. Tolerate that, in order:
      1. the contents of the first fenced ```json block anywhere in the text,
      2. else the first '{' … last '}' span,
      3. else the stripped text as-is.
    """
    raw = raw.strip()
    fenced = _JSON_FENCE_RE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1].strip()
    return raw


def _try_parse(raw: str) -> Handoff | None:
    """Attempt JSON parse + pydantic validation. Returns None on any error."""
    try:
        cleaned = _extract_json_from_result(raw)
        data = json.loads(cleaned)
        return Handoff.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError):
        return None


def _summarize_tool_args(tool_input: dict) -> str:
    """Return the path, pattern, or command that makes a tool call inspectable."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("file_path", "path", "pattern", "command", "query"):
        value = tool_input.get(key)
        if value:
            return str(value)[:160]
    return ""


def _raw_tool_args(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    try:
        return json.dumps(tool_input)
    except TypeError:
        return str(tool_input)


def _stash_transcript(
    folder: TicketFolder, transcript: list[TranscriptEntry]
) -> Path | None:
    if not transcript:
        return None
    stash_dir = folder.root / ".debug"
    stash_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    transcript_path = stash_dir / f"transcript-{ts}.jsonl"
    with transcript_path.open("w", encoding="utf-8") as f:
        for entry in transcript:
            f.write(json.dumps(asdict(entry)) + "\n")
    return transcript_path


async def _drain(query_gen) -> tuple[str, list[TranscriptEntry]]:
    """Drain the agent stream and keep both final result and intermediate turns."""
    raw = ""
    transcript: list[TranscriptEntry] = []
    async for message in query_gen:
        result_text = getattr(message, "result", None)
        if result_text is not None:
            raw = result_text
            transcript.append(
                TranscriptEntry(
                    kind="result",
                    text=str(result_text)[:4000],
                    raw_text=str(result_text),
                )
            )
            continue

        content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                stripped = text.strip()
                transcript.append(
                    TranscriptEntry(kind="reasoning", text=stripped, raw_text=stripped)
                )
                continue
            name = getattr(block, "name", None)
            tool_input = getattr(block, "input", {}) or {}
            if name:
                transcript.append(
                    TranscriptEntry(
                        kind="tool",
                        tool_name=str(name),
                        tool_args=_summarize_tool_args(tool_input),
                        raw_tool_args=_raw_tool_args(tool_input),
                    )
                )
                continue
            tool_result = getattr(block, "content", None)
            if tool_result:
                transcript.append(
                    TranscriptEntry(
                        kind="tool_result",
                        text=str(tool_result)[:1000],
                        raw_text=str(tool_result),
                    )
                )
    return raw, transcript


async def run_agent(
    ticket_id: int | str,
    folder: TicketFolder,
    system_prompt: str,
    history_context: str,
    initial_hypothesis: str = "",
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

    if initial_hypothesis:
        hypothesis_line = (
            f"Analyst's initial hypothesis: {initial_hypothesis} — treat as a "
            "starting point, not a verdict; re-steer if evidence does not correlate.\n\n"
        )
    else:
        hypothesis_line = (
            "No analyst hypothesis was provided — infer the symptom from intake.\n\n"
        )

    full_prompt = (
        f"Triage ticket #{ticket_id}.\n\n"
        f"{hypothesis_line}"
        f"Historical context (for historical_matches only — do not treat as ground truth):\n"
        f"{history_context}\n\n"
        "The ticket body and comments are in logs/00-ticket.md. Read it and every "
        "other file under logs/, pcaps/, and analysis/. Ground your investigation in "
        "the matching runbook under runbooks/. If no evidence covers the incident "
        "window, return Fork D and list what is missing — do not fabricate. "
        "Emit only the Handoff JSON."
    )

    # Attempt 1
    gen1 = _query_fn(prompt=full_prompt, options=_make_options())
    raw1, transcript1 = await _drain(gen1)
    handoff = _try_parse(raw1)
    if handoff is not None:
        _stash_transcript(folder, transcript1)
        return RunnerResult(
            handoff=handoff, raw_result=raw1, attempts=1, transcript=transcript1
        )

    # Attempt 2 — correction prompt
    correction_prompt = (
        "Your previous response could not be parsed as a valid Handoff JSON object. "
        "Please emit ONLY the Handoff JSON, with no prose before or after. "
        "The top-level keys must be: intake, evidence_preflight, fork_packet, drafts, rubric_version."
    )
    gen2 = _query_fn(prompt=correction_prompt, options=_make_options())
    raw2, transcript2 = await _drain(gen2)
    handoff2 = _try_parse(raw2)
    combined = transcript1 + transcript2
    if handoff2 is not None:
        _stash_transcript(folder, combined)
        return RunnerResult(
            handoff=handoff2, raw_result=raw2, attempts=2, transcript=combined
        )

    # Double failure — stash and abort
    stash_dir = folder.root / ".debug"
    stash_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    stash_path = stash_dir / f"raw-result-{ts}.txt"
    stash_path.write_text(
        f"# Attempt 1\n{raw1}\n\n# Attempt 2\n{raw2}\n",
        encoding="utf-8",
    )
    _stash_transcript(folder, combined)
    return RunnerResult(
        handoff=None,
        stash_path=stash_path,
        raw_result=raw2,
        attempts=2,
        transcript=combined,
    )
