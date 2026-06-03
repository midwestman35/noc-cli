from __future__ import annotations

import typer

from noc_cli import __version__

app = typer.Typer(
    name="noc-cli",
    help="Read-only NOC triage assistant for Carbyne APEX NG911/E911.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
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


if __name__ == "__main__":
    app()
