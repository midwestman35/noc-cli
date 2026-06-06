from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from noc_cli.config import data_dir
from noc_cli.model_profiles import profile_for
from noc_cli.scout.llm_io import extract_json, final_result
from noc_cli.scout.models import RankedCandidate, ScoutReport, ScreenReport
from noc_cli.scout.ports import QueryFn
from noc_cli.scout.profiles import SYNTHESIS, build_options
from noc_cli.usage import log_usage

SYNTHESIS_SYSTEM_PROMPT = (
    "You are the NOC backlog synthesizer. Rank stale ticket pre-screens by "
    "which candidates most deserve a human engineer's next investigation. Weigh "
    "triage_ready, runbook confidence, and missing evidence. This is not closure "
    "authorization, and you must not recommend comments, status changes, or "
    "resolution."
)


def _fallback(reports: list[ScreenReport]) -> list[RankedCandidate]:
    ordered = sorted(
        reports,
        key=lambda r: (r.triage_ready, r.runbook_match_confidence),
        reverse=True,
    )
    return [
        RankedCandidate(
            ticket_id=report.ticket_id,
            rank=i + 1,
            rationale=report.one_line
            or "(synthesis unavailable; ranked by confidence)",
            runbook_id=report.runbook_id,
            runbook_match_confidence=report.runbook_match_confidence,
            missing_evidence=report.missing_evidence,
        )
        for i, report in enumerate(ordered)
    ]


def _parse_ranked(raw: str) -> list[RankedCandidate] | None:
    try:
        data = json.loads(extract_json(raw))
        ranked = [RankedCandidate.model_validate(item) for item in data["ranked"]]
        return ranked or None
    except (json.JSONDecodeError, ValidationError, ValueError, KeyError, TypeError):
        return None


async def synthesize(
    reports: list[ScreenReport],
    *,
    query_fn: QueryFn,
    now: datetime,
    options_factory: Callable | None = None,
    cwd: Path | str = ".",
) -> ScoutReport:
    """Fold screen reports into a ranked ScoutReport with deterministic fallback."""
    if not reports:
        return ScoutReport(generated_at=now, ranked=[])

    if options_factory is None:

        def options_factory():
            return build_options(
                SYNTHESIS, system_prompt=SYNTHESIS_SYSTEM_PROMPT, cwd=cwd
            )

    payload = json.dumps([report.model_dump() for report in reports], indent=2)
    prompt = (
        "Triability pre-screens for stale NOC tickets:\n"
        f"{payload}\n\n"
        "Rank candidates needing review best-first. Emit ONLY this JSON object:\n"
        '{"ranked": [{"ticket_id": <int>, "rank": <1-based int>, '
        '"rationale": "<one sentence>", "runbook_id": "<slug>", '
        '"runbook_match_confidence": <0.0-1.0>, "missing_evidence": ["..."]}]}'
    )
    _profile = profile_for("scout_synth")
    raw = await final_result(
        query_fn(prompt=prompt, options=options_factory()),
        on_result=lambda m: log_usage(
            data_dir() / "usage.jsonl",
            surface="scout_synth",
            profile=_profile,
            result_message=m,
        ),
    )
    ranked = _parse_ranked(raw) or _fallback(reports)
    return ScoutReport(generated_at=now, ranked=ranked)
