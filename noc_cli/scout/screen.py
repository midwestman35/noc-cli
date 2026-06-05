from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from noc_cli.scout.models import Candidate, ScreenReport
from noc_cli.scout.profiles import SCREEN, build_options

SCREEN_SYSTEM_PROMPT = (
    "You are a NOC triage-readiness pre-screener. Given one stale ticket and "
    "the runbooks staged under runbooks/, decide whether a runbook plausibly "
    "covers this ticket and what evidence is missing for a human investigation. "
    "Do not investigate, fetch ticket bodies, draft customer comments, update "
    "status, or authorize closure. Scout only identifies candidates needing "
    "review."
)

_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    fenced = _JSON_FENCE_RE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1].strip()
    return raw


async def _final_result(query_gen) -> str:
    raw = ""
    async for message in query_gen:
        text = getattr(message, "result", None)
        if text is not None:
            raw = text
    return raw


def _parse(raw: str, ticket_id: int) -> ScreenReport | None:
    try:
        data = json.loads(_extract_json(raw))
        if isinstance(data, dict):
            data.setdefault("ticket_id", ticket_id)
        return ScreenReport.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError):
        return None


async def screen_ticket(
    candidate: Candidate,
    *,
    runbooks_dir: Path,
    query_fn: Callable,
    options_factory: Callable | None = None,
    options_cls=None,
) -> ScreenReport | None:
    """Run one read-only screen and parse its ScreenReport."""
    if options_factory is None:
        from noc_cli.agent.harness import build_hooks  # noqa: PLC0415

        workspace = runbooks_dir.parent
        hooks = build_hooks(
            sandbox_root=workspace,
            events_path=workspace / "events.jsonl",
            restrict_read_tools=True,
        )
        options_factory = lambda: build_options(
            SCREEN,
            system_prompt=SCREEN_SYSTEM_PROMPT,
            cwd=workspace,
            hooks=hooks,
            options_cls=options_cls,
        )

    prompt = (
        f"Ticket #{candidate.ticket_id}: {candidate.subject!r} "
        f"(status={candidate.status}, stale {candidate.staleness_days}d).\n\n"
        "Read runbooks under runbooks/. Emit ONLY this JSON object:\n"
        '{"ticket_id": <int>, "runbook_id": "<slug or empty>", '
        '"runbook_match_confidence": <0.0-1.0>, "triage_ready": <true|false>, '
        '"missing_evidence": ["<needed for human triage>"], '
        '"one_line": "<=120 char summary"}'
    )
    raw = await _final_result(query_fn(prompt=prompt, options=options_factory()))
    return _parse(raw, candidate.ticket_id)


async def screen_candidates(
    candidates: list[Candidate],
    *,
    runbooks_dir: Path,
    query_fn: Callable,
    concurrency: int = 3,
    options_factory: Callable | None = None,
) -> list[ScreenReport]:
    """Screen candidates with bounded fan-out; drop parse failures."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(candidate: Candidate) -> ScreenReport | None:
        async with sem:
            return await screen_ticket(
                candidate,
                runbooks_dir=runbooks_dir,
                query_fn=query_fn,
                options_factory=options_factory,
            )

    results = await asyncio.gather(*[_one(candidate) for candidate in candidates])
    return [report for report in results if report is not None]
