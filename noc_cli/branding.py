from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

TAGLINE = "Read-only NOC triage · Carbyne APEX NG911/E911"


def render_banner(console: Console | None = None) -> None:
    """Print the noc-cli brand banner. Reused by the TUI plans."""
    console = console or Console()
    console.print(Panel.fit(Text("noc-cli", style="bold cyan"), border_style="cyan"))
    console.print(TAGLINE, style="dim")
