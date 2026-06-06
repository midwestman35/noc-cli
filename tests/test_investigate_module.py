import pytest

from noc_cli.config import Config

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_investigate_forwards_config_and_memory_store_to_run_agent(
    monkeypatch, tmp_path
):
    """Agent path must forward config + memory_store to run_agent."""
    import noc_cli.agent.runner as runner_mod
    from noc_cli.investigate import InvestigationError, run_investigation

    captured: dict = {}

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        raise InvestigationError("stop after capture")

    monkeypatch.setattr(runner_mod, "run_agent", fake_run_agent)

    async def fake_select_runbook(ticket_text, hypothesis, **kwargs):
        from noc_cli.grounding import RunbookSelection

        return RunbookSelection(slug=None, confidence="low", rationale="stub")

    monkeypatch.setattr("noc_cli.grounding.select_runbook", fake_select_runbook)

    cfg = _cfg(tmp_path)
    with pytest.raises(InvestigationError):
        await run_investigation(
            ticket_id=9999,
            config=cfg,
            tickets_root=tmp_path,
            owner="tester",
            no_agent=False,
        )

    assert "config" in captured, "run_agent was not called or did not receive config"
    assert "memory_store" in captured, (
        "run_agent was not called or did not receive memory_store"
    )
    assert captured["config"] is cfg


def _cfg(tmp_path) -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="a@x.com",
        zendesk_api_token="tok",
        tickets_root=tmp_path,
        owner="enrique",
        watch_view="555",
    )


async def test_run_investigation_no_agent_emits_phase_lines(tmp_path):
    from noc_cli.investigate import run_investigation
    from noc_cli.watch.inbox import detect_phase

    lines: list[str] = []
    root = await run_investigation(
        ticket_id=4242,
        config=_cfg(tmp_path),
        tickets_root=tmp_path,
        owner="enrique",
        no_agent=True,
        on_line=lines.append,
    )
    assert root == tmp_path / "4242"
    detected = {detect_phase(line) for line in lines}
    assert "Scaffold ready" in detected
    assert "Evidence gathered" in detected
    assert "PII redacted" in detected


async def test_run_investigation_soft_lock_raises(tmp_path):
    from noc_cli.investigate import InvestigationError, run_investigation

    # Pre-create the ticket folder + a STATE.md claimed by a different owner.
    folder = tmp_path / "77"
    folder.mkdir(parents=True)
    (folder / "STATE.md").write_text('owner: "someone-else"\n', encoding="utf-8")

    with pytest.raises(InvestigationError):
        await run_investigation(
            ticket_id=77,
            config=_cfg(tmp_path),
            tickets_root=tmp_path,
            owner="me",
            no_agent=True,
        )


async def test_run_investigation_default_on_line_does_not_crash(tmp_path):
    from noc_cli.investigate import run_investigation

    root = await run_investigation(
        ticket_id=88,
        config=_cfg(tmp_path),
        tickets_root=tmp_path,
        owner="me",
        no_agent=True,
    )  # on_line omitted -> _noop default
    assert root == tmp_path / "88"


async def test_investigate_classifies_injects_and_verifies(monkeypatch, tmp_path):
    import noc_cli.agent.runner as runner_mod
    import noc_cli.grounding as grounding_mod
    from noc_cli.grounding import RunbookSelection
    from noc_cli.investigate import run_investigation

    captured: dict = {}
    returned_handoff: list = []  # mutable container to capture the Handoff object

    async def fake_select(ticket_text, hypothesis, **kw):
        return RunbookSelection(slug="low-audio", confidence="high", rationale="r")

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        from noc_cli.agent.runner import RunnerResult
        from noc_cli.models import (
            Confidence,
            ForkLetter,
            ForkPacket,
            Handoff,
            PreflightBlock,
            RunbookReference,
        )
        from noc_cli.models import IntakeBlock as Intake

        fp = ForkPacket(
            fork_letter=ForkLetter.B,
            confidence=Confidence.HIGH,
            symptom_tag="[low audio]",
            quoted_rubric_row="row",
            runbook_reference=RunbookReference(slug="low-audio", section="s"),
        )
        ho = Handoff(
            rubric_version="t",
            intake=Intake(ticket_id=1),
            evidence_preflight=PreflightBlock(),
            fork_packet=fp,
            drafts={},
        )
        returned_handoff.append(ho)
        return RunnerResult(handoff=ho, raw_result="{}", attempts=1)

    monkeypatch.setattr(grounding_mod, "select_runbook", fake_select)
    monkeypatch.setattr(runner_mod, "run_agent", fake_run_agent)

    await run_investigation(
        ticket_id=1001,
        config=_cfg(tmp_path),
        tickets_root=tmp_path,
        owner="tester",
        no_agent=False,
    )

    # (1) run_agent received the selected slug and non-empty runbook text
    assert captured.get("selected_runbook_slug") == "low-audio", (
        f"expected 'low-audio', got {captured.get('selected_runbook_slug')!r}"
    )
    assert captured.get("selected_runbook_text"), (
        "selected_runbook_text should be non-empty (runbook injected)"
    )
    # (2) verify_grounding set grounding_verified on the handoff (True/False/None, not unset)
    assert returned_handoff, "fake_run_agent was never called"
    ho = returned_handoff[0]
    # grounding_verified is a bool|None — confirm it was touched (not the default unset state)
    # verify_grounding always returns (bool|None, str); the field is set unconditionally
    assert "grounding_verified" in ho.fork_packet.model_fields_set or (
        ho.fork_packet.grounding_verified is not None
        or ho.fork_packet.grounding_note != ""
    ), "verify_grounding must set grounding_verified or grounding_note on fork_packet"
