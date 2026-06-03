from __future__ import annotations

from enum import Enum

from rich.console import Console
from rich.spinner import Spinner
from rich.text import Text


class InvestigatePhase(str, Enum):
    FETCH = "fetch"
    SCAFFOLD = "scaffold"
    GATHER = "gather"
    REDACT = "redact"
    HISTORY = "history"
    AGENT = "agent"
    RENDER = "render"
    DONE = "done"


_PHASE_LABELS: dict[InvestigatePhase, str] = {
    InvestigatePhase.FETCH: "Fetching ticket from Zendesk…",
    InvestigatePhase.SCAFFOLD: "Scaffolding ticket folder…",
    InvestigatePhase.GATHER: "Gathering evidence…",
    InvestigatePhase.REDACT: "Redacting PII…",
    InvestigatePhase.HISTORY: "Seeding history…",
    InvestigatePhase.AGENT: "Running L3 triage agent…",
    InvestigatePhase.RENDER: "Rendering report…",
    InvestigatePhase.DONE: "Done.",
}


class PhaseTracker:
    """Rich-based phase progress tracker. Works headless (Console(record=True))."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._current: InvestigatePhase | None = None

    def set_phase(self, phase: InvestigatePhase) -> None:
        self._current = phase
        label = _PHASE_LABELS.get(phase, phase.value)
        spinner = Spinner("dots", text=Text(f" {label}", style="bold cyan"))
        self._console.print(spinner, end="\r")

    def mark_done(self, message: str) -> None:
        self._console.print(f"[green]✓[/green] {message}")

    def error(self, message: str) -> None:
        self._console.print(f"[red]✗[/red] {message}")
