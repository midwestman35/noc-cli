from pathlib import Path

import pytest

from noc_cli.config import Config

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _cfg(tmp_path) -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="a@x.com",
        zendesk_api_token="tok",
        tickets_root=tmp_path,
        owner="enrique",
        watch_view="555",
    )


async def test_run_investigation_no_agent_emits_phase_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("NOC_TICKETS_ROOT", raising=False)
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
