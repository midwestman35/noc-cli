from pathlib import Path

import pytest

from noc_cli.config import Config

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_investigate_forwards_config_and_memory_store_to_run_agent(monkeypatch, tmp_path):
    """Agent path must forward config + memory_store to run_agent."""
    import noc_cli.agent.runner as runner_mod
    from noc_cli.investigate import InvestigationError, run_investigation

    captured: dict = {}

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        raise InvestigationError("stop after capture")

    monkeypatch.setattr(runner_mod, "run_agent", fake_run_agent)

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
    assert "memory_store" in captured, "run_agent was not called or did not receive memory_store"
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
