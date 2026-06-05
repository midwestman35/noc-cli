"""noc-cli command-line interface — thin Typer shims over the logic modules."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Optional

import typer

from noc_cli import __version__, branding
from noc_cli.config import config_path, load_config, set_config_value, valid_config_keys
from noc_cli.doctor import print_report, run_checks
from noc_cli.setup import run_setup

app = typer.Typer(
    name="noc-cli",
    help="Read-only NOC triage assistant for Carbyne APEX NG911/E911.",
    no_args_is_help=True,
)
config_app = typer.Typer(
    help="Read and edit noc-cli configuration.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


def _valid_config_key_text() -> str:
    return ", ".join(valid_config_keys())


def _validate_config_key(key: str) -> None:
    if key not in valid_config_keys():
        typer.secho(
            f"Unknown config key {key!r}. Valid keys: {_valid_config_key_text()}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


def _mask_config_value(key: str, value: object) -> str:
    if key == "zendesk_api_token" and value:
        return "********"
    return str(value)


def _version_callback(value: bool) -> None:
    if value:
        branding.render_banner()
        typer.echo(f"noc-cli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Read-only NOC triage assistant."""


@app.command()
def setup() -> None:
    """Interactive first-run onboarding (Zendesk creds, paths, watch, notifications)."""
    branding.render_banner()
    typer.echo("")

    existing = load_config()

    def _prompt(label: str, default: str = "", hide_input: bool = False) -> str:
        return typer.prompt(label, default=default, hide_input=hide_input)

    run_setup(
        config_path=config_path(),
        existing=existing,
        prompt_fn=_prompt,
    )

    # Best-effort Claude engine probe (non-fatal).
    if shutil.which("claude") is None:
        typer.secho(
            "Warning: `claude` not found on PATH. Install Claude Code before using `investigate`.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho("Claude Code engine found.", fg=typer.colors.GREEN)

    typer.echo("")
    typer.secho(
        "Configuration written. Run `noc-cli doctor` to verify all checks pass.",
        fg=typer.colors.CYAN,
    )


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config field name to set."),
    value: str = typer.Argument(..., help="Value to persist."),
) -> None:
    """Persist one config value to the data-dir `.env` file."""
    try:
        set_config_value(key, value)
    except KeyError as exc:
        message = exc.args[0] if exc.args else str(exc)
        typer.secho(message, fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{key}={_mask_config_value(key, value)}")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config field name to read."),
) -> None:
    """Print one effective config value."""
    _validate_config_key(key)
    value = getattr(load_config(), key)
    typer.echo(f"{key}={_mask_config_value(key, value)}")


@config_app.command("list")
def config_list() -> None:
    """Print all effective config values."""
    cfg = load_config()
    for key in valid_config_keys():
        typer.echo(f"{key}={_mask_config_value(key, getattr(cfg, key))}")


@config_app.command("path")
def config_path_command() -> None:
    """Print the config file path."""
    typer.echo(config_path())


@app.command()
def doctor(
    online: bool = typer.Option(
        False,
        "--online",
        help="Perform a live Zendesk auth probe (requires network).",
    ),
) -> None:
    """Health-check credentials, paths, the Claude Code engine, and notifications."""
    config = load_config()

    # Build the live-auth factory only for --online. The ZendeskClient import
    # (which pulls in httpx) stays inside this branch so offline `doctor` runs
    # — the common case, including CI — never pay the httpx import cost.
    zd_factory = None
    if online:
        from noc_cli.zendesk import ZendeskClient

        zd_factory = lambda cfg: ZendeskClient(cfg)  # noqa: E731

    results = run_checks(config, zd_factory=zd_factory)
    exit_code = print_report(results)
    raise typer.Exit(code=exit_code)


@app.command()
def investigate(
    ticket_id: int = typer.Argument(..., help="Zendesk ticket ID"),
    file: list[Path] = typer.Option(
        [], "--file", "-f", help="Local file(s) to include as evidence (repeatable)"
    ),
    paste: list[str] = typer.Option(
        [], "--paste", help="Inline text as LABEL=TEXT (repeatable)"
    ),
    force: bool = typer.Option(False, "--force", help="Override the STATE.md soft-lock"),
    fixture: Optional[Path] = typer.Option(
        None,
        "--fixture",
        help="Offline replay: directory containing handoff_good.json (skips fetch + agent)",
    ),
    no_agent: bool = typer.Option(
        False, "--no-agent", help="Dry path: scaffold + gather + redact; no LLM"
    ),
    suspect: Optional[str] = typer.Option(
        None,
        "--suspect",
        help="Runbook slug to seed grounding (e.g. low-audio); skips the interactive prompt.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Run the L3 agent investigation on a Zendesk ticket and produce a triage handoff."""
    import sys

    from noc_cli.seed import resolve_seed

    branding.render_banner()
    interactive = sys.stdin.isatty() and fixture is None and not no_agent
    try:
        initial_hypothesis = resolve_seed(
            suspect,
            interactive=interactive,
            prompt_fn=lambda label: typer.prompt(label, default=""),
            echo_fn=typer.echo,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    asyncio.run(
        _run_investigate(
            ticket_id=ticket_id,
            extra_files=list(file),
            pastes=paste,
            force=force,
            fixture=fixture,
            no_agent=no_agent,
            initial_hypothesis=initial_hypothesis,
            verbose=verbose,
        )
    )


def _handoff_with_initial_hypothesis(
    handoff: "Handoff",
    initial_hypothesis: str,
) -> "Handoff":
    if not initial_hypothesis:
        return handoff
    return handoff.model_copy(
        deep=True,
        update={
            "intake": handoff.intake.model_copy(
                update={"initial_hypothesis": initial_hypothesis}
            )
        },
    )


def _now_utc():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _make_zendesk_client(cfg):
    from noc_cli.zendesk import ZendeskClient

    return ZendeskClient(cfg)


def _run_scout_report(cfg, *, top_k: int, min_staleness_days: int):
    """Build a read-only client and run the Scout pipeline."""
    import tempfile

    from claude_agent_sdk import query  # noqa: PLC0415

    from noc_cli.scout.runner import run_scout

    client = _make_zendesk_client(cfg)

    async def _go():
        with tempfile.TemporaryDirectory(prefix="noc-scout-") as tmp:
            return await run_scout(
                client=client,
                view_id=cfg.scout_view,
                workspace=Path(tmp),
                now=_now_utc(),
                query_fn=query,
                top_k=top_k,
                min_staleness_days=min_staleness_days,
            )

    return asyncio.run(_go())


def _make_writer(cfg):
    from noc_cli.scout.acquire import ZendeskWriter

    return ZendeskWriter(cfg)


def _resolve_owner_id(cfg) -> int | None:
    return _make_zendesk_client(cfg).find_user_id(cfg.watch_assignee or cfg.owner)


def _invoke_investigate(ticket_id: int) -> None:
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


def _take_preflight(ticket, *, now, min_staleness_days: int) -> str | None:
    from noc_cli.scout.rank import is_active_status, staleness_days

    if ticket.assignee_id is not None:
        return f"Ticket #{ticket.id} is already assigned."
    if not is_active_status(ticket.status):
        return f"Ticket #{ticket.id} is {ticket.status or 'inactive'}."

    stale_days = staleness_days(ticket, now=now)
    if stale_days is None:
        return f"Ticket #{ticket.id} has no updated_at timestamp."
    if stale_days < min_staleness_days:
        return (
            f"Ticket #{ticket.id} is no longer stale enough "
            f"({stale_days}d < {min_staleness_days}d)."
        )
    return None


def _preflight_current_ticket(cfg, *, ticket_id: int, min_staleness_days: int) -> str | None:
    ticket = _make_zendesk_client(cfg).get_ticket(ticket_id)
    return _take_preflight(
        ticket, now=_now_utc(), min_staleness_days=min_staleness_days
    )


def _abort_take(reason: str) -> None:
    typer.secho(reason, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


def _take_ticket(cfg, *, ticket_id: int, min_staleness_days: int, yes: bool) -> None:
    reason = _preflight_current_ticket(
        cfg, ticket_id=ticket_id, min_staleness_days=min_staleness_days
    )
    if reason is not None:
        _abort_take(reason)

    owner_id = _resolve_owner_id(cfg)
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

    reason = _preflight_current_ticket(
        cfg, ticket_id=ticket_id, min_staleness_days=min_staleness_days
    )
    if reason is not None:
        _abort_take(reason)

    _make_writer(cfg).assign_ticket(ticket_id, owner_id)
    typer.secho(f"Assigned #{ticket_id} to you. Investigating...", fg=typer.colors.GREEN)
    _invoke_investigate(ticket_id)


async def _run_investigate(
    ticket_id: int,
    extra_files: list[Path],
    pastes: list[str],
    force: bool,
    fixture: Optional[Path],
    no_agent: bool,
    initial_hypothesis: str,
    verbose: bool,
) -> None:
    # Lazy imports keep `noc-cli --help` and the other commands from loading the
    # heavy investigate dependency graph (agent SDK, textual, etc.).
    import os

    from rich.console import Console

    from noc_cli.evidence import PasteInput, _looks_like_text, gather_evidence, write_ticket_source
    from noc_cli.memory import InvestigationRecord, MemoryStore, append_investigation
    from noc_cli.scaffold import SoftLockConflict, preflight_soft_lock, scaffold_ticket
    from noc_cli.tui.progress import InvestigatePhase, PhaseTracker

    console = Console()
    tracker = PhaseTracker(console=console)

    cfg = load_config()
    tickets_root = Path(os.environ.get("NOC_TICKETS_ROOT", str(cfg.tickets_root)))
    owner = os.environ.get("NOC_OWNER", getattr(cfg, "owner", ""))

    # ── Scaffold + soft-lock pre-flight ──────────────────────────────────────
    tracker.set_phase(InvestigatePhase.SCAFFOLD)
    folder = scaffold_ticket(tickets_root, ticket_id)
    try:
        preflight_soft_lock(folder, owner=owner, force=force)
    except SoftLockConflict as exc:
        console.print(f"[red]Soft-lock conflict:[/red] {exc}")
        for field_name, old, new in exc.summary:
            console.print(f"  {field_name}: {old!r} → {new!r}")
        raise typer.Exit(code=2)
    tracker.mark_done(f"Scaffold ready: {folder.root}")

    # ── Fetch (skipped in --no-agent and --fixture modes) ────────────────────
    attachments: list[dict] = []
    zd_client = None
    if fixture is None and not no_agent:
        tracker.set_phase(InvestigatePhase.FETCH)
        try:
            from noc_cli.zendesk import ZendeskClient

            zd_client = ZendeskClient(cfg)
            ticket = zd_client.get_ticket(ticket_id)
            comments = zd_client.get_comments(ticket_id)
            # Persist the ticket body + comments as the agent's PRIMARY input.
            # Lands in logs/ so the redact pass below scrubs caller PII from it.
            write_ticket_source(folder, ticket, comments)
            for comment in comments:
                # gather_evidence expects dicts; Comment.attachments are Attachment
                # pydantic models — convert with model_dump().
                for att in getattr(comment, "attachments", []) or []:
                    attachments.append(att.model_dump())
            tracker.mark_done(f"Ticket #{ticket_id} fetched")
        except Exception as exc:
            console.print(f"[yellow]Zendesk fetch failed:[/yellow] {exc}")

    # ── Gather evidence ──────────────────────────────────────────────────────
    tracker.set_phase(InvestigatePhase.GATHER)
    paste_inputs: list[PasteInput] = []
    for p in pastes:
        if "=" in p:
            label, _, text = p.partition("=")
            paste_inputs.append(PasteInput(label=label.strip(), text=text))
        else:
            paste_inputs.append(PasteInput(label="paste", text=p))
    gather_evidence(
        folder=folder,
        zendesk_attachments=attachments,
        extra_files=extra_files,
        pastes=paste_inputs,
        zendesk_client=zd_client,
    )
    tracker.mark_done("Evidence gathered")

    # ── Redact (best-effort; never crash the run on one bad file) ────────────
    tracker.set_phase(InvestigatePhase.REDACT)
    from noc_cli.redact import redact, residual_pii_warning

    for log_file in folder.logs.iterdir():
        if log_file.is_file() and _looks_like_text(log_file.name):
            try:
                text = log_file.read_text(encoding="utf-8", errors="replace")
                redacted, counts = redact(text)
                log_file.write_text(redacted, encoding="utf-8")
                warning = residual_pii_warning(redacted, counts)
                if warning and verbose:
                    console.print(f"[yellow]{log_file.name}:[/yellow] {warning}")
            except Exception:
                pass
    tracker.mark_done("PII redacted")

    # ── --no-agent dry path ──────────────────────────────────────────────────
    if no_agent:
        console.print("[dim]--no-agent: stopping after gather + redact (no LLM run)[/dim]")
        return

    # ── History ──────────────────────────────────────────────────────────────
    tracker.set_phase(InvestigatePhase.HISTORY)
    from noc_cli.config import db_path

    mem_db = Path(os.environ.get("NOC_DB_PATH", str(db_path())))
    mem_md = tickets_root / "MEMORY.md"
    mem_store = MemoryStore(db_path=mem_db, memory_md_path=mem_md)
    mem_store.init()

    history_context = ""
    if fixture is None:
        # Live history seeding only when the agent will actually run. Fixture
        # (offline replay) mode skips Zendesk entirely so the command works
        # without configured credentials.
        from noc_cli.history import seed_history
        from noc_cli.zendesk import ZendeskClient

        zd_for_history = ZendeskClient(cfg)
        symptom_tag = initial_hypothesis or "[unclassified]"  # analyst seed; agent re-steers
        candidates = seed_history(
            symptom_tag, zendesk_client=zd_for_history, memory_store=mem_store
        )
        history_context = "\n".join(
            f"- Ticket #{c.ticket_id}: {c.subject} (source: {c.source})" for c in candidates[:10]
        )
        tracker.mark_done(f"History seeded: {len(candidates)} candidate(s)")
    else:
        tracker.mark_done("History skipped (fixture mode)")

    # ── Agent (or fixture replay) ─────────────────────────────────────────────
    tracker.set_phase(InvestigatePhase.AGENT)
    from noc_cli.models import Handoff
    handoff: Optional[Handoff] = None
    transcript = []
    if fixture is not None:
        import json

        handoff_path = fixture / "handoff_good.json"
        if not handoff_path.exists():
            console.print(f"[red]Fixture {handoff_path} not found[/red]")
            raise typer.Exit(code=1)
        handoff = Handoff.model_validate(json.loads(handoff_path.read_text()))
        tracker.mark_done("Fixture handoff loaded")
    else:
        from noc_cli.agent.prompt import build_system_prompt
        from noc_cli.agent.runner import run_agent
        from noc_cli.rubric import load_rubric

        rubric = load_rubric()
        system_prompt = build_system_prompt(rubric.core)
        runner_result = await run_agent(
            ticket_id=ticket_id,
            folder=folder,
            system_prompt=system_prompt,
            history_context=history_context,
            initial_hypothesis=initial_hypothesis,
        )
        transcript = runner_result.transcript
        handoff = runner_result.handoff
        if handoff is None:
            try:
                from noc_cli.render import render_reasoning

                render_reasoning(transcript, None, folder)
            except Exception:
                pass
            console.print(
                f"[red]Agent failed after 2 attempts. Raw output stashed to:[/red] "
                f"{runner_result.stash_path}"
            )
            raise typer.Exit(code=1)
        tracker.mark_done("Agent completed")

    # ── Render (owner recorded → drives the soft-lock on re-run) ─────────────
    tracker.set_phase(InvestigatePhase.RENDER)
    from noc_cli.render import (
        consulted_runbook_slugs,
        render_handoff,
        render_reasoning,
        validation_warnings,
    )

    handoff = _handoff_with_initial_hypothesis(handoff, initial_hypothesis)
    consulted = consulted_runbook_slugs(transcript)
    warnings = validation_warnings(handoff, consulted, folder=folder)
    render_handoff(
        handoff,
        folder,
        owner=owner,
        consulted_runbooks=consulted,
        validator_warnings=warnings,
    )
    try:
        render_reasoning(transcript, handoff, folder)
    except Exception:
        pass
    tracker.mark_done("Report rendered")

    # ── Memory append ─────────────────────────────────────────────────────────
    append_investigation(
        mem_store,
        InvestigationRecord(
            ticket_id=str(ticket_id),
            symptom_tag=handoff.fork_packet.symptom_tag,
            fork_letter=handoff.fork_packet.fork_letter.value,
            confidence=handoff.fork_packet.confidence.value,
            one_line_fingerprint=handoff.intake.one_line_fingerprint,
            summary=handoff.fork_packet.reasoning[:300],
            related_zendesk=handoff.fork_packet.related_zendesk,
            rubric_version=handoff.rubric_version,
        ),
    )

    tracker.set_phase(InvestigatePhase.DONE)
    tracker.mark_done(f"Ticket #{ticket_id} complete — {folder.root}")
    console.print(f"\n[bold green]Report:[/bold green] {folder.root}")


@app.command()
def scout(
    take: Optional[int] = typer.Option(
        None,
        "--take",
        help="Confirm, assign this ticket id to yourself, then investigate.",
    ),
    top_k: int = typer.Option(8, "--top-k", min=1, help="Candidates to screen."),
    min_staleness_days: int = typer.Option(
        7,
        "--min-staleness-days",
        min=0,
        help="Minimum idle age before Scout considers a ticket stale.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the --take confirmation prompt."
    ),
) -> None:
    """Scan Tier-1 backlog candidates needing review; optionally claim one."""
    import httpx

    from noc_cli.scout.acquire import ZendeskWriteError
    from noc_cli.scout.render import render_scout_report
    from noc_cli.zendesk import ZendeskError

    branding.render_banner()
    cfg = load_config()

    try:
        if take is None:
            report = _run_scout_report(
                cfg, top_k=top_k, min_staleness_days=min_staleness_days
            )
            typer.echo(render_scout_report(report))
            return

        _take_ticket(
            cfg, ticket_id=take, min_staleness_days=min_staleness_days, yes=yes
        )
    except typer.Exit:
        raise
    except ZendeskError as exc:
        typer.secho(f"Zendesk error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except ZendeskWriteError as exc:
        typer.secho(f"Zendesk error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except httpx.HTTPError as exc:
        typer.secho(f"Zendesk request failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.secho(f"Scout failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@app.command()
def watch(
    view: str = typer.Option(
        "",
        "--view",
        help="Zendesk view ID to poll (overrides NOC_WATCH_VIEW config).",
    ),
    assignee: str = typer.Option(
        "",
        "--assignee",
        help="Assignee email to filter (overrides NOC_WATCH_ASSIGNEE config).",
    ),
    interval: int = typer.Option(
        60,
        "--interval",
        min=1,
        help="Poll interval in seconds (default 60).",
    ),
) -> None:
    """Watch a Zendesk view and notify on ticket status changes / new requester comments."""
    from noc_cli import store
    from noc_cli.config import db_path
    from noc_cli.tui.watch_app import WatchApp
    from noc_cli.watch.notify import build_notifier
    from noc_cli.watch.state import WatchState
    from noc_cli.zendesk import ZendeskClient, ZendeskError

    cfg = load_config()

    # CLI flags override config values.
    if view:
        cfg = cfg.model_copy(update={"watch_view": view})
    if assignee:
        cfg = cfg.model_copy(update={"watch_assignee": assignee})

    if not cfg.watch_view:
        typer.secho(
            "Error: no view configured. Pass --view <id> or run `noc-cli setup`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        client = ZendeskClient(cfg)
    except ZendeskError as exc:
        typer.secho(f"Zendesk error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    conn = store.connect(db_path())
    try:
        ws = WatchState(conn)
        notifier = build_notifier(cfg.notify)
        watch_app = WatchApp(
            config=cfg,
            client=client,
            watch_state=ws,
            notifier=notifier,
            poll_interval=interval,
        )
        watch_app.run()
    finally:
        conn.close()


if __name__ == "__main__":
    app()
