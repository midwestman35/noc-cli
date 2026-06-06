from rich.console import Console

from noc_cli.tui.progress import InvestigatePhase, PhaseTracker


def test_phase_tracker_cycles_all_phases():
    console = Console(record=True, width=80)
    tracker = PhaseTracker(console=console)
    for phase in InvestigatePhase:
        tracker.set_phase(phase)
    output = console.export_text()
    # Cycling 8 phases prints a spinner line for each — output must be non-empty.
    assert len(output) > 0


def test_phase_tracker_mark_done_renders():
    console = Console(record=True, width=80)
    tracker = PhaseTracker(console=console)
    tracker.set_phase(InvestigatePhase.FETCH)
    tracker.mark_done("Ticket 18432 fetched")
    output = console.export_text()
    assert "18432" in output


def test_all_investigate_phases_defined():
    phase_names = {p.value for p in InvestigatePhase}
    required = {
        "fetch",
        "scaffold",
        "gather",
        "redact",
        "history",
        "agent",
        "render",
        "done",
    }
    assert required.issubset(phase_names)


def test_report_viewport_loads_files(tmp_path):
    """Data-loading logic of the viewport, without running the Textual app."""
    from noc_cli.tui.viewport import load_report_files

    (tmp_path / "INTAKE.md").write_text("# INTAKE\nticket 18432")
    (tmp_path / "FORK_PACKET.md").write_text("# FORK PACKET\nFork B")
    (tmp_path / "STATE.md").write_text("---\nfork: B\n---")

    files = load_report_files(tmp_path)
    assert "INTAKE.md" in files
    assert "18432" in files["INTAKE.md"]
    assert "FORK_PACKET.md" in files
    assert "Fork B" in files["FORK_PACKET.md"]


def test_report_viewport_missing_folder_returns_empty(tmp_path):
    from noc_cli.tui.viewport import load_report_files

    files = load_report_files(tmp_path / "nonexistent")
    assert files == {}
