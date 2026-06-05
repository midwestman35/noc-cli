"""Scout command orchestration.

Holds the logic behind the ``noc scout`` command so ``cli.py`` stays a thin
parse-and-dispatch layer. These functions are the public Scout command API;
tests drive them here rather than reaching into ``cli`` internals.
"""

from __future__ import annotations

from datetime import datetime, timezone

import typer

from noc_cli.scout.models import ScoutReport


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def make_zendesk_client(cfg):
    from noc_cli.zendesk import ZendeskClient  # noqa: PLC0415

    return ZendeskClient(cfg)


def make_writer(cfg):
    from noc_cli.scout.writer import ZendeskWriter  # noqa: PLC0415

    return ZendeskWriter(cfg)


def resolve_owner_id(cfg) -> int | None:
    return make_zendesk_client(cfg).find_user_id(cfg.watch_assignee or cfg.owner)


def invoke_investigate(ticket_id: int) -> None:
    """Hand a claimed ticket to the existing investigate flow.

    Imports lazily to avoid a ``cli`` <-> ``scout.commands`` import cycle (the
    proper fix is splitting investigate out of ``cli.py`` — backlog #11)."""
    import asyncio  # noqa: PLC0415

    from noc_cli.cli import _run_investigate  # noqa: PLC0415

    asyncio.run(
        _run_investigate(
            ticket_id=ticket_id,
            extra_files=[],
            pastes=[],
            force=False,
            fixture=None,
            no_agent=False,
            initial_hypothesis="",
            verbose=False,
        )
    )


def run_scout_report(cfg, *, top_k: int, min_staleness_days: int) -> ScoutReport:
    """Build a read-only client and run the Scout pipeline in a temp workspace."""
    import asyncio  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from claude_agent_sdk import query  # noqa: PLC0415

    from noc_cli.scout.runner import run_scout  # noqa: PLC0415

    client = make_zendesk_client(cfg)

    async def _go():
        with tempfile.TemporaryDirectory(prefix="noc-scout-") as tmp:
            return await run_scout(
                client=client,
                view_id=cfg.scout_view,
                workspace=Path(tmp),
                now=now_utc(),
                query_fn=query,
                top_k=top_k,
                min_staleness_days=min_staleness_days,
            )

    return asyncio.run(_go())


def preflight_current_ticket(
    cfg, *, ticket_id: int, min_staleness_days: int
) -> str | None:
    """Re-read the ticket now and return why it is ineligible to take, or None."""
    from noc_cli.scout.rank import take_ineligible_reason  # noqa: PLC0415

    ticket = make_zendesk_client(cfg).get_ticket(ticket_id)
    return take_ineligible_reason(
        ticket, now=now_utc(), min_staleness_days=min_staleness_days
    )


def _abort(reason: str) -> None:
    typer.secho(reason, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


def run_scout_list(cfg, *, top_k: int, min_staleness_days: int) -> None:
    """List candidates needing review."""
    from noc_cli.scout.render import render_scout_report  # noqa: PLC0415

    report = run_scout_report(
        cfg, top_k=top_k, min_staleness_days=min_staleness_days
    )
    typer.echo(render_scout_report(report))


def take_ticket(cfg, *, ticket_id: int, min_staleness_days: int, yes: bool) -> None:
    """Propose-then-confirm claim: preflight, confirm, re-preflight, assign, investigate."""
    reason = preflight_current_ticket(
        cfg, ticket_id=ticket_id, min_staleness_days=min_staleness_days
    )
    if reason is not None:
        _abort(reason)

    owner_id = resolve_owner_id(cfg)
    if owner_id is None:
        typer.secho(
            f"Could not resolve a Zendesk user id for "
            f"{cfg.watch_assignee or cfg.owner!r}. Set NOC_WATCH_ASSIGNEE / "
            "NOC_OWNER to your Zendesk email.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if not yes:
        typer.confirm(
            f"Assign ticket #{ticket_id} to yourself and start investigating?",
            abort=True,
        )

    # Re-read after the human confirms: the ticket may have changed state while
    # the prompt was open (TOCTOU). Backlog #15 keeps this double fetch on purpose.
    reason = preflight_current_ticket(
        cfg, ticket_id=ticket_id, min_staleness_days=min_staleness_days
    )
    if reason is not None:
        _abort(reason)

    make_writer(cfg).assign_ticket(ticket_id, owner_id)
    typer.secho(
        f"Assigned #{ticket_id} to you. Investigating...", fg=typer.colors.GREEN
    )
    invoke_investigate(ticket_id)
