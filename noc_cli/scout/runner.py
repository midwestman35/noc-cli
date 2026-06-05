from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from noc_cli.scout.models import ScoutReport
from noc_cli.scout.profiles import SCREEN, SYNTHESIS, build_options
from noc_cli.scout.rank import rank_candidates
from noc_cli.scout.screen import SCREEN_SYSTEM_PROMPT, screen_candidates
from noc_cli.scout.synthesize import SYNTHESIS_SYSTEM_PROMPT, synthesize
from noc_cli.scout.workspace import materialize_workspace


async def run_scout(
    *,
    client,
    view_id: str,
    workspace: Path,
    now: datetime,
    query_fn: Callable,
    top_k: int = 8,
    concurrency: int = 3,
    min_staleness_days: int = 7,
    screen_options_factory: Callable | None = None,
    synth_options_factory: Callable | None = None,
) -> ScoutReport:
    """Run the read-only Scout pipeline: rank, screen, synthesize."""
    tickets = client.view_tickets(view_id)
    candidates = rank_candidates(
        tickets, now=now, top_k=top_k, min_staleness_days=min_staleness_days
    )
    if not candidates:
        return ScoutReport(generated_at=now, ranked=[])

    runbooks_dir = materialize_workspace(workspace)

    if screen_options_factory is None or synth_options_factory is None:
        from noc_cli.agent.harness import build_hooks  # noqa: PLC0415

        hooks = build_hooks(
            sandbox_root=workspace,
            events_path=workspace / "events.jsonl",
            restrict_read_tools=True,
        )
        if screen_options_factory is None:
            screen_options_factory = lambda: build_options(
                SCREEN, system_prompt=SCREEN_SYSTEM_PROMPT, cwd=workspace, hooks=hooks
            )
        if synth_options_factory is None:
            synth_options_factory = lambda: build_options(
                SYNTHESIS,
                system_prompt=SYNTHESIS_SYSTEM_PROMPT,
                cwd=workspace,
                hooks=hooks,
            )

    reports = await screen_candidates(
        candidates,
        runbooks_dir=runbooks_dir,
        query_fn=query_fn,
        concurrency=concurrency,
        options_factory=screen_options_factory,
    )
    return await synthesize(
        reports, query_fn=query_fn, now=now, options_factory=synth_options_factory
    )
