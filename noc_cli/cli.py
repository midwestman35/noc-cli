"""noc-cli command-line interface — thin Typer shims over the logic modules."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Optional

import typer

from noc_cli import __version__, branding
from noc_cli.config import config_path, load_config
from noc_cli.doctor import print_report, run_checks
from noc_cli.setup import run_setup

app = typer.Typer(
    name="noc-cli",
    help="Read-only NOC triage assistant for Carbyne APEX NG911/E911.",
    no_args_is_help=True,
)


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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Run the L3 agent investigation on a Zendesk ticket and produce a triage handoff."""
    branding.render_banner()
    asyncio.run(
        _run_investigate(
            ticket_id=ticket_id,
            extra_files=list(file),
            pastes=paste,
            force=force,
            fixture=fixture,
            no_agent=no_agent,
            verbose=verbose,
        )
    )


async def _run_investigate(
    ticket_id: int,
    extra_files: list[Path],
    pastes: list[str],
    force: bool,
    fixture: Optional[Path],
    no_agent: bool,
    verbose: bool,
) -> None:
    # Lazy imports keep `noc-cli --help` and the other commands from loading the
    # heavy investigate dependency graph (agent SDK, textual, etc.).
    import os

    from rich.console import Console

    from noc_cli.evidence import PasteInput, gather_evidence
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
    if fixture is None and not no_agent:
        tracker.set_phase(InvestigatePhase.FETCH)
        try:
            from noc_cli.zendesk import ZendeskClient

            zd = ZendeskClient(cfg)
            zd.get_ticket(ticket_id)
            comments = zd.get_comments(ticket_id)
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
    )
    tracker.mark_done("Evidence gathered")

    # ── Redact (best-effort; never crash the run on one bad file) ────────────
    tracker.set_phase(InvestigatePhase.REDACT)
    from noc_cli.redact import redact, residual_pii_warning

    for log_file in folder.logs.iterdir():
        if log_file.is_file():
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
        symptom_tag = "[unclassified]"  # refined by the agent; default for seeding
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
    from noc_cli.render import render_handoff

    handoff: Optional[Handoff] = None
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
        system_prompt = build_system_prompt(rubric.text)
        runner_result = await run_agent(
            ticket_id=ticket_id,
            folder=folder,
            system_prompt=system_prompt,
            history_context=history_context,
        )
        handoff = runner_result.handoff
        if handoff is None:
            console.print(
                f"[red]Agent failed after 2 attempts. Raw output stashed to:[/red] "
                f"{runner_result.stash_path}"
            )
            raise typer.Exit(code=1)
        tracker.mark_done("Agent completed")

    # ── Render (owner recorded → drives the soft-lock on re-run) ─────────────
    tracker.set_phase(InvestigatePhase.RENDER)
    render_handoff(handoff, folder, owner=owner)
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
def watch() -> None:
    """Watch a Zendesk queue and notify on status changes to your tickets."""
    typer.secho(
        "noc-cli watch is not built yet — arriving in the watch plan.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=0)


if __name__ == "__main__":
    app()
