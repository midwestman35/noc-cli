from pathlib import Path

from typer.testing import CliRunner

from noc_cli.cli import app
from noc_cli.models import APPROVED_SYMPTOM_TAGS

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@b.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "tok")
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOC_HOME", str(tmp_path))  # isolate the SQLite DB


def test_investigate_no_agent_dry_path(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["investigate", "18432", "--no-agent"])
    assert result.exit_code == 0, result.output
    assert (
        "dry" in result.output.lower()
        or "no-agent" in result.output.lower()
        or "scaffold" in result.output.lower()
    )


def test_investigate_fixture_produces_five_files(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])
    assert result.exit_code == 0, result.output
    ticket_dir = tmp_path / "18432"
    for name in ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md"):
        assert (ticket_dir / name).exists(), f"{name} not rendered"


def test_investigate_fixture_fork_invariant(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])
    state = (tmp_path / "18432" / "STATE.md").read_text()
    assert any(f'fork: "{letter}"' in state or f"fork: {letter}" in state for letter in "ABCD")


def test_investigate_fixture_symptom_tag_invariant(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])
    state = (tmp_path / "18432" / "STATE.md").read_text()
    assert any(tag in state for tag in APPROVED_SYMPTOM_TAGS)


def test_investigate_soft_lock_exits_2(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NOC_OWNER", "bob@axon.com")
    ticket_dir = tmp_path / "18432"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "STATE.md").write_text('---\nowner: "alice@axon.com"\n---\n')
    result = runner.invoke(app, ["investigate", "18432", "--no-agent"])
    assert result.exit_code == 2


def test_investigate_force_overrides_soft_lock(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NOC_OWNER", "bob@axon.com")
    ticket_dir = tmp_path / "18432"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "STATE.md").write_text('---\nowner: "alice@axon.com"\n---\n')
    result = runner.invoke(app, ["investigate", "18432", "--no-agent", "--force"])
    assert result.exit_code == 0, result.output


def test_investigate_fixture_works_without_zendesk_creds(tmp_path, monkeypatch):
    # Offline fixture replay must NOT require Zendesk credentials configured.
    monkeypatch.delenv("ZENDESK_SUBDOMAIN", raising=False)
    monkeypatch.delenv("ZENDESK_EMAIL", raising=False)
    monkeypatch.delenv("ZENDESK_API_TOKEN", raising=False)
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    result = runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "18432" / "INTAKE.md").exists()
