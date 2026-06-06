import asyncio
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from noc_cli.cli import _run_investigate, app
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
    for name in (
        "INTAKE.md",
        "EVIDENCE_PREFLIGHT.md",
        "FORK_PACKET.md",
        "DRAFTS.md",
        "STATE.md",
    ):
        assert (ticket_dir / name).exists(), f"{name} not rendered"


def test_investigate_fixture_fork_invariant(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])
    state = (tmp_path / "18432" / "STATE.md").read_text()
    assert any(
        f'fork: "{letter}"' in state or f"fork: {letter}" in state for letter in "ABCD"
    )


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


def test_redact_skips_binary_attachment(tmp_path, monkeypatch):
    """Binary files (e.g. PDFs) copied into logs/ must not be mangled by the redact pass."""
    _base_env(monkeypatch, tmp_path)

    # A plausible PDF: ASCII header + null bytes + high bytes (would corrupt under errors="replace")
    binary = b"%PDF-1.4\x00\x01\x02\x03\xff\xfe\xfd\x00binary content here"
    fake_pdf = tmp_path / "report.pdf"
    fake_pdf.write_bytes(binary)

    result = runner.invoke(
        app, ["investigate", "18432", "--no-agent", "--file", str(fake_pdf)]
    )
    assert result.exit_code == 0, result.output

    written = (tmp_path / "18432" / "logs" / "report.pdf").read_bytes()
    assert written == binary, "redact pass must not mangle binary files"


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


def test_investigate_invalid_suspect_exits_2(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["investigate", "18432", "--no-agent", "--suspect", "bogus"]
    )
    assert result.exit_code == 2, result.output


def test_investigate_valid_suspect_no_agent_ok(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["investigate", "18432", "--no-agent", "--suspect", "low-audio"]
    )
    assert result.exit_code == 0, result.output


def test_investigate_no_agent_skips_interactive_seed_prompt(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_resolve_seed(suspect, *, interactive, prompt_fn, echo_fn):
        calls["interactive"] = interactive
        calls["suspect"] = suspect
        return ""

    async def fake_run_investigate(**kwargs):
        calls["initial_hypothesis"] = kwargs["initial_hypothesis"]

    monkeypatch.setattr("noc_cli.seed.resolve_seed", fake_resolve_seed)
    monkeypatch.setattr("noc_cli.cli._run_investigate", fake_run_investigate)

    result = runner.invoke(app, ["investigate", "18432", "--no-agent"])

    assert result.exit_code == 0, result.output
    assert calls == {"interactive": False, "suspect": None, "initial_hypothesis": ""}


def test_investigate_fixture_skips_interactive_seed_prompt(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_resolve_seed(suspect, *, interactive, prompt_fn, echo_fn):
        calls["interactive"] = interactive
        return ""

    async def fake_run_investigate(**kwargs):
        calls["initial_hypothesis"] = kwargs["initial_hypothesis"]

    monkeypatch.setattr("noc_cli.seed.resolve_seed", fake_resolve_seed)
    monkeypatch.setattr("noc_cli.cli._run_investigate", fake_run_investigate)

    result = runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])

    assert result.exit_code == 0, result.output
    assert calls == {"interactive": False, "initial_hypothesis": ""}


def _load_good_handoff():
    import json

    from noc_cli.models import Handoff

    return Handoff.model_validate(
        json.loads((FIXTURES / "handoff_good.json").read_text())
    )


def _patch_live_investigate_deps(monkeypatch, captured, handoff=None):
    from noc_cli.models import Comment, Ticket

    handoff = handoff or _load_good_handoff()

    class FakeZD:
        def __init__(self, cfg):
            pass

        def get_ticket(self, ticket_id):
            return Ticket(
                id=ticket_id,
                subject="Audio report",
                description="Caller reports low audio",
                status="open",
                tags=["low-audio"],
            )

        def get_comments(self, ticket_id):
            return [Comment(id=1, public=True, body="low audio on one call")]

        def search(self, query):
            return []

    def fake_seed_history(symptom_tag, *, zendesk_client, memory_store):
        captured.setdefault("history_tags", []).append(symptom_tag)
        return []

    def fake_build_system_prompt(rubric_text):
        captured["rubric_text"] = rubric_text
        return "SYSTEM_PROMPT"

    async def fake_run_agent(**kwargs):
        captured["run_agent"] = kwargs
        return SimpleNamespace(
            handoff=handoff,
            transcript=[
                SimpleNamespace(
                    kind="tool", tool_name="Read", tool_args="runbooks/low-audio.md"
                )
            ],
            stash_path=None,
        )

    def fake_consulted_runbook_slugs(transcript):
        captured["consulted_transcript"] = transcript
        return ["low-audio"]

    def fake_validation_warnings(handoff_arg, consulted, *, folder):
        captured["validation"] = (handoff_arg, consulted, folder)
        return ["soft warning"]

    def fake_render_handoff(handoff_arg, folder, owner="", **kwargs):
        captured["render_handoff"] = (handoff_arg, folder, owner, kwargs)

    def fake_render_reasoning(transcript, handoff_arg, folder):
        captured.setdefault("render_reasoning", []).append(
            (transcript, handoff_arg, folder)
        )

    async def fake_select_runbook(ticket_text, hypothesis, **kwargs):
        from noc_cli.grounding import RunbookSelection

        return RunbookSelection(slug=None, confidence="low", rationale="stub")

    monkeypatch.setattr("noc_cli.grounding.select_runbook", fake_select_runbook)
    monkeypatch.setattr("noc_cli.zendesk.ZendeskClient", FakeZD)
    monkeypatch.setattr("noc_cli.history.seed_history", fake_seed_history)
    monkeypatch.setattr(
        "noc_cli.rubric.load_rubric",
        lambda: SimpleNamespace(
            text="RUBRIC_FULL_SENTINEL", core="RUBRIC_CORE_SENTINEL"
        ),
    )
    monkeypatch.setattr(
        "noc_cli.agent.prompt.build_system_prompt", fake_build_system_prompt
    )
    monkeypatch.setattr("noc_cli.agent.runner.run_agent", fake_run_agent)

    import noc_cli.render as render

    monkeypatch.setattr(
        render, "consulted_runbook_slugs", fake_consulted_runbook_slugs, raising=False
    )
    monkeypatch.setattr(
        render, "validation_warnings", fake_validation_warnings, raising=False
    )
    monkeypatch.setattr(render, "render_handoff", fake_render_handoff)
    monkeypatch.setattr(
        render, "render_reasoning", fake_render_reasoning, raising=False
    )


def test_run_investigate_wires_seed_prompt_history_agent_and_render(
    monkeypatch, tmp_path
):
    _base_env(monkeypatch, tmp_path)
    captured: dict[str, object] = {}
    _patch_live_investigate_deps(monkeypatch, captured)

    asyncio.run(
        _run_investigate(
            ticket_id=18432,
            extra_files=[],
            pastes=[],
            force=False,
            fixture=None,
            no_agent=False,
            initial_hypothesis="[low audio]",
            verbose=False,
        )
    )

    assert captured["history_tags"] == ["[low audio]"]
    assert captured["rubric_text"] == "RUBRIC_CORE_SENTINEL"
    assert captured["run_agent"]["system_prompt"] == "SYSTEM_PROMPT"
    assert captured["run_agent"]["initial_hypothesis"] == "[low audio]"
    assert captured["render_handoff"][3] == {
        "consulted_runbooks": ["low-audio"],
        "validator_warnings": ["soft warning"],
    }
    assert captured["render_reasoning"][0][1] is not None


def test_run_investigate_persists_seed_into_rendered_handoff(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    captured: dict[str, object] = {}
    handoff = _load_good_handoff()
    handoff = handoff.model_copy(
        deep=True,
        update={"intake": handoff.intake.model_copy(update={"initial_hypothesis": ""})},
    )
    _patch_live_investigate_deps(monkeypatch, captured, handoff=handoff)

    asyncio.run(
        _run_investigate(
            ticket_id=18432,
            extra_files=[],
            pastes=[],
            force=False,
            fixture=None,
            no_agent=False,
            initial_hypothesis="[low audio]",
            verbose=False,
        )
    )

    rendered_handoff = captured["render_handoff"][0]
    assert rendered_handoff.intake.initial_hypothesis == "[low audio]"


def test_run_investigate_history_seed_falls_back_to_unclassified(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    captured: dict[str, object] = {}
    _patch_live_investigate_deps(monkeypatch, captured)

    asyncio.run(
        _run_investigate(
            ticket_id=18432,
            extra_files=[],
            pastes=[],
            force=False,
            fixture=None,
            no_agent=False,
            initial_hypothesis="",
            verbose=False,
        )
    )

    assert captured["history_tags"] == ["[unclassified]"]


def test_run_investigate_renders_reasoning_on_agent_parse_failure(
    monkeypatch, tmp_path
):
    _base_env(monkeypatch, tmp_path)
    captured: dict[str, object] = {}
    _patch_live_investigate_deps(monkeypatch, captured)

    async def fake_failed_run_agent(**kwargs):
        captured["run_agent"] = kwargs
        return SimpleNamespace(
            handoff=None,
            transcript=[SimpleNamespace(kind="reasoning", text="parse failed")],
            stash_path=tmp_path / "raw.txt",
        )

    monkeypatch.setattr("noc_cli.agent.runner.run_agent", fake_failed_run_agent)

    result = runner.invoke(
        app,
        ["investigate", "18432"],
    )

    assert result.exit_code == 1, result.output
    assert captured["render_reasoning"][0][1] is None
