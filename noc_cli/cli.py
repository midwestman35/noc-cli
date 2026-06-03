"""noc-cli command-line interface — thin Typer shims over the logic modules."""
from __future__ import annotations

import shutil

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
def investigate(ticket: str) -> None:
    """Run the L3 agent investigation on a Zendesk ticket (id or URL)."""
    typer.secho(
        "noc-cli investigate is not built yet — arriving in the investigate plan.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=0)


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
