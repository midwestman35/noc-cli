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


def _warn_deprecated(old: str, new: str) -> None:
    typer.secho(
        f"`noc-cli {old}` is deprecated and will be removed in a future release. "
        f"Use {new} instead.",
        fg=typer.colors.YELLOW,
        err=True,
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Read-only NOC triage assistant. Run with no command to open the inbox."""
    if ctx.invoked_subcommand is None:
        _launch_tui()


@app.command()
def setup() -> None:
    """Interactive first-run onboarding (Zendesk creds, paths, TUI, notifications)."""
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


@app.command(hidden=True, deprecated=True)
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
    _warn_deprecated("investigate", "`/investigate` inside the TUI")
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
    # Import the investigation pipeline lazily so the other CLI commands don't pay for it.
    import os

    from rich.console import Console

    from noc_cli.investigate import InvestigationError, run_investigation
    from noc_cli.scaffold import SoftLockConflict

    console = Console()
    cfg = load_config()
    owner = os.environ.get("NOC_OWNER", getattr(cfg, "owner", ""))

    try:
        root = await run_investigation(
            ticket_id=ticket_id,
            config=cfg,
            tickets_root=Path(os.environ.get("NOC_TICKETS_ROOT", str(cfg.tickets_root))),
            owner=owner,
            initial_hypothesis=initial_hypothesis,
            extra_files=extra_files,
            pastes=pastes,
            force=force,
            fixture=fixture,
            no_agent=no_agent,
            verbose=verbose,
            on_line=lambda line: console.print(f"[green]✓[/green] {line}"),
        )
    except InvestigationError as exc:
        # Preserve the CLI's distinct exit codes: a soft-lock conflict is exit 2
        # (caller must --force or hand off), every other pipeline failure is
        # exit 1. run_investigation chains the SoftLockConflict as __cause__.
        cause = exc.__cause__
        if isinstance(cause, SoftLockConflict):
            console.print(f"[red]Soft-lock conflict:[/red] {cause}")
            for field_name, old, new in cause.summary:
                console.print(f"  {field_name}: {old!r} → {new!r}")
            raise typer.Exit(code=2)
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"\n[bold green]Report:[/bold green] {root}")


@app.command(hidden=True, deprecated=True)
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
    _warn_deprecated("scout", "`/scout` inside the TUI")
    import httpx

    from noc_cli.scout import commands
    from noc_cli.scout.writer import ZendeskWriteError
    from noc_cli.zendesk import ZendeskError

    branding.render_banner()
    cfg = load_config()

    try:
        if take is None:
            commands.run_scout_list(
                cfg, top_k=top_k, min_staleness_days=min_staleness_days
            )
        else:
            commands.take_ticket(
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


def _launch_tui(view: str = "", assignee: str = "", interval: int = 60) -> None:
    """Bootstrap and run the WatchApp TUI, with optional view/assignee/interval overrides."""
    from noc_cli import store
    from noc_cli.config import db_path
    from noc_cli.tui.watch_app import WatchApp
    from noc_cli.watch.notify import build_notifier
    from noc_cli.watch.state import WatchState
    from noc_cli.zendesk import ZendeskClient, ZendeskError

    cfg = load_config()
    if view:
        cfg = cfg.model_copy(update={"watch_view": view})
    if assignee:
        cfg = cfg.model_copy(update={"watch_assignee": assignee})
    if not cfg.watch_view:
        typer.secho(
            "Error: no view configured. Run `noc-cli setup`.",
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
        WatchApp(
            config=cfg,
            client=client,
            watch_state=ws,
            notifier=notifier,
            poll_interval=interval,
        ).run()
    finally:
        conn.close()


@app.command(hidden=True, deprecated=True)
def watch(
    view: str = typer.Option("", "--view"),
    assignee: str = typer.Option("", "--assignee"),
    interval: int = typer.Option(60, "--interval", min=1),
) -> None:
    """Deprecated alias — bare `noc-cli` now opens the inbox."""
    _warn_deprecated("watch", "bare `noc-cli`")
    _launch_tui(view=view, assignee=assignee, interval=interval)


if __name__ == "__main__":
    app()
