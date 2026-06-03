from __future__ import annotations

import typer

from noc_cli import __version__, branding

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


def _coming_soon(name: str, arriving_in: str) -> None:
    typer.secho(
        f"🚧 noc-cli {name} is not built yet — arriving in {arriving_in}.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=0)


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
    _coming_soon("setup", "the setup & doctor plan")


@app.command()
def doctor() -> None:
    """Health-check credentials, paths, the Claude Code engine, and notifications."""
    _coming_soon("doctor", "the setup & doctor plan")


@app.command()
def investigate(ticket: str) -> None:
    """Run the L3 agent investigation on a Zendesk ticket (id or URL)."""
    _coming_soon("investigate", "the investigate plan")


@app.command()
def watch() -> None:
    """Watch a Zendesk queue and notify on status changes to your tickets."""
    _coming_soon("watch", "the watch plan")


if __name__ == "__main__":
    app()
