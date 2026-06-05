from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from noc_cli.scout.models import ScoutReport
from noc_cli.scout.rank import rank_candidates
from noc_cli.scout.screen import screen_candidates
from noc_cli.scout.synthesize import synthesize
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
        from noc_cli.scout.hooks import build_scout_options  # noqa: PLC0415

        default_screen, default_synth = build_scout_options(workspace)
        screen_options_factory = screen_options_factory or default_screen
        synth_options_factory = synth_options_factory or default_synth

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
