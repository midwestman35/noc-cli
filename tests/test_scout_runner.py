import asyncio
from datetime import datetime, timedelta, timezone

from noc_cli.models import Ticket
from noc_cli.scout.runner import run_scout

NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    def __init__(self, tickets):
        self._tickets = tickets

    def view_tickets(self, view_id):
        assert view_id == "6490757606044"
        return self._tickets


def _fake_query(*, prompt, options):
    if prompt.startswith("Triability pre-screens"):
        out = '{"ranked": [{"ticket_id": 1, "rank": 1, "rationale": "match"}]}'
    else:
        out = (
            '{"ticket_id": 1, "runbook_id": "low-audio", '
            '"runbook_match_confidence": 0.8, "triage_ready": true, '
            '"missing_evidence": [], "one_line": "ok"}'
        )

    async def gen():
        class FakeResult:
            result = out

        yield FakeResult()

    return gen()


def test_run_scout_full_pipeline(tmp_path):
    tickets = [
        Ticket(
            id=1,
            subject="audio",
            status="open",
            priority="high",
            updated_at=NOW - timedelta(days=8),
        ),
        Ticket(
            id=2,
            subject="assigned",
            status="open",
            assignee_id=99,
            updated_at=NOW - timedelta(days=9),
        ),
    ]

    report = _run(
        run_scout(
            client=_FakeClient(tickets),
            view_id="6490757606044",
            workspace=tmp_path,
            now=NOW,
            query_fn=_fake_query,
            screen_options_factory=lambda: None,
            synth_options_factory=lambda: None,
        )
    )

    assert [r.ticket_id for r in report.ranked] == [1]
    assert report.generated_at == NOW


def test_run_scout_empty_candidates_skips_agent(tmp_path):
    report = _run(
        run_scout(
            client=_FakeClient([]),
            view_id="6490757606044",
            workspace=tmp_path,
            now=NOW,
            query_fn=_fake_query,
            screen_options_factory=lambda: None,
            synth_options_factory=lambda: None,
        )
    )

    assert report.ranked == []
