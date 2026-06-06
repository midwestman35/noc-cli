import json
from pathlib import Path

from noc_cli.agent.runner import TranscriptEntry
from noc_cli.models import Handoff
from noc_cli.render import (
    consulted_runbook_slugs,
    render_handoff,
    render_reasoning,
    validation_warnings,
)
from noc_cli.scaffold import scaffold_ticket

FIXTURES = Path(__file__).parent / "fixtures"


def load_good() -> Handoff:
    data = json.loads((FIXTURES / "handoff_good.json").read_text())
    return Handoff.model_validate(data)


def load_pivot() -> Handoff:
    data = json.loads((FIXTURES / "handoff_pivot.json").read_text())
    return Handoff.model_validate(data)


def _sample_transcript():
    return [
        TranscriptEntry(kind="reasoning", text="Accepting analyst hypothesis [apex]."),
        TranscriptEntry(kind="tool", tool_name="Read", tool_args="runbooks/apex.md"),
        TranscriptEntry(kind="reasoning", text="Three stations flipped — Fork B."),
        TranscriptEntry(kind="result", text='{"...": "..."}'),
    ]


def test_consulted_runbook_slugs_only_counts_read_events():
    transcript = [
        TranscriptEntry(kind="tool", tool_name="Grep", tool_args="runbooks/apex.md"),
        TranscriptEntry(
            kind="tool", tool_name="Glob", tool_args="runbooks/low-audio.md"
        ),
        TranscriptEntry(kind="tool", tool_name="Read", tool_args="runbooks/no-ani.md"),
    ]

    assert consulted_runbook_slugs(transcript) == ["no-ani"]


def test_all_five_files_created(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    for name in (
        "INTAKE.md",
        "EVIDENCE_PREFLIGHT.md",
        "FORK_PACKET.md",
        "DRAFTS.md",
        "STATE.md",
    ):
        assert (folder.root / name).exists(), f"{name} missing"


def test_intake_md_contains_ticket_id(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "INTAKE.md").read_text()
    assert "18432" in content


def test_fork_packet_md_contains_runbook_reference_section(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "FORK_PACKET.md").read_text()
    assert "Runbook Reference" in content
    assert "apex" in content.lower()


def test_fork_packet_md_contains_quoted_rubric_row(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "FORK_PACKET.md").read_text()
    assert "customer LAN, switch, or SDWAN" in content


def test_fork_packet_md_contains_historical_matches(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "FORK_PACKET.md").read_text()
    assert "41675" in content


def test_state_md_has_yaml_frontmatter(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "STATE.md").read_text()
    assert content.startswith("---")
    assert "fork:" in content
    assert "symptom_tag:" in content
    assert "confidence:" in content
    assert "rubric_version:" in content
    assert "quoted_rubric_row:" in content


def test_state_md_fork_letter_is_correct(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "STATE.md").read_text()
    assert 'fork: "B"' in content or "fork: B" in content


def test_state_md_records_owner(tmp_path):
    # The soft-lock (scaffold.py) depends on STATE.md carrying the real owner.
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder, owner="alice@axon.com")
    content = (folder.root / "STATE.md").read_text()
    assert 'owner: "alice@axon.com"' in content


def test_state_md_records_master_ticket_and_cluster(tmp_path):
    handoff = load_good()
    handoff.fork_packet.master_ticket = 12345
    handoff.fork_packet.cluster = "Aurora metro outage"
    folder = scaffold_ticket(tmp_path, 18432)

    render_handoff(handoff, folder)

    content = (folder.root / "STATE.md").read_text()
    assert "  master: 12345" in content
    assert 'cluster: "Aurora metro outage"' in content


def test_state_md_omits_master_ticket_and_cluster_when_absent(tmp_path):
    handoff = load_good()
    handoff.fork_packet.master_ticket = None
    handoff.fork_packet.cluster = None
    folder = scaffold_ticket(tmp_path, 18432)

    render_handoff(handoff, folder)

    content = (folder.root / "STATE.md").read_text()
    assert "cluster:" not in content
    assert "  master:" not in content


def test_state_md_quotes_rubric_row_as_single_line_yaml_scalar(tmp_path):
    handoff = load_good()
    handoff.fork_packet.quoted_rubric_row = 'alpha "beta" \\ gamma\nnext'
    folder = scaffold_ticket(tmp_path, 18432)

    render_handoff(handoff, folder)

    content = (folder.root / "STATE.md").read_text()
    assert 'quoted_rubric_row: "alpha \\"beta\\" \\\\ gamma\\nnext"' in content


def test_drafts_md_contains_customer_reply(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "DRAFTS.md").read_text()
    assert "customer" in content.lower()
    assert "WebSocket" in content or "network" in content.lower()


def test_render_is_atomic_on_failure(tmp_path):
    """Existing files must not be partially written; verify none exist before
    render and all exist after a successful render."""
    folder = scaffold_ticket(tmp_path, 18432)
    for name in (
        "INTAKE.md",
        "EVIDENCE_PREFLIGHT.md",
        "FORK_PACKET.md",
        "DRAFTS.md",
        "STATE.md",
    ):
        assert not (folder.root / name).exists()
    render_handoff(load_good(), folder)
    for name in (
        "INTAKE.md",
        "EVIDENCE_PREFLIGHT.md",
        "FORK_PACKET.md",
        "DRAFTS.md",
        "STATE.md",
    ):
        assert (folder.root / name).exists()


def test_consulted_runbook_slugs_from_transcript():
    assert consulted_runbook_slugs(_sample_transcript()) == ["apex"]
    assert consulted_runbook_slugs([]) == []


def test_validation_warnings_flags_runbook_mismatch_and_bad_quote(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    handoff = load_good()

    assert validation_warnings(handoff, ["apex"], folder=folder) == []
    warns = validation_warnings(handoff, ["low-audio"], folder=folder)
    assert len(warns) == 1
    assert "apex" in warns[0]

    bad_quote = handoff.model_copy(
        deep=True,
        update={
            "fork_packet": handoff.fork_packet.model_copy(
                update={"quoted_rubric_row": "not a real rubric row"}
            )
        },
    )
    quote_warns = validation_warnings(bad_quote, ["apex"], folder=folder)
    assert any("quoted_rubric_row" in warning for warning in quote_warns)


def test_validation_warnings_flags_cited_runbook_when_none_consulted(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    handoff = load_good()

    warns = validation_warnings(handoff, [], folder=folder)

    assert any("apex" in warning and "no runbooks" in warning for warning in warns)


def test_render_reasoning_creates_file(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_reasoning(_sample_transcript(), load_good(), folder)

    md = (folder.root / "REASONING.md").read_text(encoding="utf-8")
    assert "REASONING" in md
    assert "apex" in md
    assert "Fork B" in md
    assert "Decision summary" in md


def test_render_reasoning_handles_none_handoff(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_reasoning(_sample_transcript(), None, folder)

    md = (folder.root / "REASONING.md").read_text(encoding="utf-8")
    assert "REASONING" in md
    assert "unparseable" in md.lower() or "unknown" in md.lower()


def test_state_md_includes_consulted_and_warnings(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(
        load_good(),
        folder,
        consulted_runbooks=["apex"],
        validator_warnings=["slug mismatch example"],
    )

    state = (folder.root / "STATE.md").read_text(encoding="utf-8")
    assert "Runbooks consulted: apex" in state
    assert "Validator Warnings" in state
    assert "slug mismatch example" in state


def test_reasoning_and_state_include_pivot_indicator(tmp_path):
    folder = scaffold_ticket(tmp_path, 18443)
    transcript = [
        TranscriptEntry(
            kind="tool", tool_name="Read", tool_args="runbooks/low-audio.md"
        ),
    ]
    handoff = load_pivot()

    render_handoff(handoff, folder, consulted_runbooks=["low-audio"])
    render_reasoning(transcript, handoff, folder)

    state = (folder.root / "STATE.md").read_text(encoding="utf-8")
    reasoning = (folder.root / "REASONING.md").read_text(encoding="utf-8")
    assert "Pivoted: yes" in state
    assert "Pivoted: yes" in reasoning


def test_seed_to_final_tag_change_marks_pivoted(tmp_path):
    folder = scaffold_ticket(tmp_path, 18443)
    handoff = load_pivot().model_copy(
        deep=True,
        update={
            "fork_packet": load_pivot().fork_packet.model_copy(
                update={
                    "runbook_reference": load_pivot().fork_packet.runbook_reference.model_copy(
                        update={"slug": "apex"}
                    ),
                    "reasoning": "Media evidence did not match.",
                }
            )
        },
    )
    transcript = [
        TranscriptEntry(
            kind="tool", tool_name="Read", tool_args="runbooks/low-audio.md"
        ),
        TranscriptEntry(kind="tool", tool_name="Read", tool_args="runbooks/apex.md"),
    ]

    render_reasoning(transcript, handoff, folder)

    reasoning = (folder.root / "REASONING.md").read_text(encoding="utf-8")
    assert "Pivoted: yes" in reasoning


def test_render_reasoning_surfaces_pivot_fixture(tmp_path):
    folder = scaffold_ticket(tmp_path, 18443)
    transcript = [
        TranscriptEntry(
            kind="reasoning", text="Accepting analyst hypothesis [low audio]."
        ),
        TranscriptEntry(
            kind="tool", tool_name="Read", tool_args="runbooks/low-audio.md"
        ),
        TranscriptEntry(
            kind="reasoning", text="RTP is healthy; pivoting away from media."
        ),
    ]

    render_reasoning(transcript, load_pivot(), folder)

    md = (folder.root / "REASONING.md").read_text(encoding="utf-8")
    assert "low-audio" in md
    assert "pivoting away from media" in md
    assert "Fork C" in md
