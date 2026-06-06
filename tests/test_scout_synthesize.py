import asyncio
from datetime import datetime, timezone

from noc_cli.scout.models import ScreenReport
from noc_cli.scout.synthesize import synthesize

NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _fake_query(result_json: str):
    async def fake_query(*, prompt, options):
        class FakeResult:
            result = result_json

        yield FakeResult()

    return fake_query


REPORTS = [
    ScreenReport(ticket_id=1, runbook_id="low-audio", runbook_match_confidence=0.4),
    ScreenReport(ticket_id=2, runbook_id="no-ani", runbook_match_confidence=0.9),
]


def test_synthesize_parses_ranked_report():
    out = (
        '{"ranked": [{"ticket_id": 2, "rank": 1, "rationale": "strong match"}, '
        '{"ticket_id": 1, "rank": 2, "rationale": "weak"}]}'
    )

    report = _run(
        synthesize(
            REPORTS, query_fn=_fake_query(out), now=NOW, options_factory=lambda: None
        )
    )

    assert [r.ticket_id for r in report.ranked] == [2, 1]
    assert report.ranked[0].rank == 1
    assert report.generated_at == NOW


def test_synthesize_falls_back_to_confidence_order_on_garbage():
    report = _run(
        synthesize(
            REPORTS,
            query_fn=_fake_query("garbage"),
            now=NOW,
            options_factory=lambda: None,
        )
    )

    assert [r.ticket_id for r in report.ranked] == [2, 1]
    assert report.ranked[0].rank == 1
    assert report.ranked[0].runbook_id == "no-ani"


def test_synthesize_empty_reports_returns_empty():
    report = _run(
        synthesize(
            [], query_fn=_fake_query("{}"), now=NOW, options_factory=lambda: None
        )
    )

    assert report.ranked == []


import json  # noqa: E402


def test_synthesize_logs_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))  # redirect data_dir() to tmp

    async def fake_query(*, prompt, options):
        class R:
            result = (
                '{"ranked":[{"ticket_id":1,"rank":1,"rationale":"x",'
                '"runbook_id":"low-audio","runbook_match_confidence":0.4,'
                '"missing_evidence":[]}]}'
            )
            usage = {"input_tokens": 1, "cache_read_input_tokens": 0}
            total_cost_usd = 0.0
            num_turns = 1
            session_id = "s"

        yield R()

    report = _run(
        synthesize(
            REPORTS,
            query_fn=fake_query,
            now=NOW,
            options_factory=lambda: None,
        )
    )

    assert report.ranked
    usage_log = tmp_path / "usage.jsonl"
    assert usage_log.exists()
    line = json.loads(usage_log.read_text().splitlines()[-1])
    assert line["surface"] == "scout_synth"
    assert line["model"] == "claude-opus-4-8"
