import json
from pathlib import Path

from noc_cli.models import Handoff
from noc_cli.render import render_handoff
from noc_cli.scaffold import scaffold_ticket

FIXTURES = Path(__file__).parent / "fixtures"


def load_good() -> Handoff:
    data = json.loads((FIXTURES / "handoff_good.json").read_text())
    return Handoff.model_validate(data)


def test_all_five_files_created(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    for name in ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md"):
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
    for name in ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md"):
        assert not (folder.root / name).exists()
    render_handoff(load_good(), folder)
    for name in ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md"):
        assert (folder.root / name).exists()
