from __future__ import annotations

from pathlib import Path

_REPORT_FILES = (
    "INTAKE.md",
    "EVIDENCE_PREFLIGHT.md",
    "FORK_PACKET.md",
    "DRAFTS.md",
    "STATE.md",
)


def load_report_files(folder: Path) -> dict[str, str]:
    """Load existing canonical report files from a ticket folder.

    Returns a dict mapping filename -> text content for each file that exists.
    Returns empty dict if folder does not exist.
    """
    if not folder.is_dir():
        return {}
    result: dict[str, str] = {}
    for name in _REPORT_FILES:
        path = folder / name
        if path.exists():
            result[name] = path.read_text(encoding="utf-8")
    return result


class ReportViewport:
    """Textual app that displays the five canonical report files.

    Usage (production):
        app = ReportViewport(folder=Path("Tickets/18432"))
        app.run()

    The Textual import is deferred so the module can be imported in
    environments where the display is unavailable (tests, CI).
    """

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def run(self) -> None:
        try:
            from textual.app import App, ComposeResult
            from textual.widgets import Markdown, TabbedContent, TabPane
        except ImportError as exc:
            raise RuntimeError(
                "textual is required for the report viewport. Run: uv add textual"
            ) from exc

        files = load_report_files(self._folder)
        if not files:
            raise FileNotFoundError(f"No report files found in {self._folder}")

        class _ViewportApp(App):
            CSS = "TabbedContent { height: 1fr; }"

            def compose(self) -> ComposeResult:
                with TabbedContent():
                    for name, text in files.items():
                        with TabPane(name, id=name.replace(".", "_")):
                            yield Markdown(text)

        _ViewportApp().run()
