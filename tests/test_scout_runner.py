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
    assert report.candidates_screened == 1
    assert report.reports_parsed == 1
    assert report.dropped == 0


def test_run_scout_counts_dropped_screens(tmp_path):
    tickets = [
        Ticket(id=1, subject="a", status="open", priority="high",
               updated_at=NOW - timedelta(days=8)),
        Ticket(id=2, subject="b", status="open", priority="high",
               updated_at=NOW - timedelta(days=9)),
    ]

    def query_fn(*, prompt, options):
        if prompt.startswith("Triability pre-screens"):
            out = '{"ranked": [{"ticket_id": 1, "rank": 1, "rationale": "ok"}]}'
        elif "Ticket #2" in prompt:
            out = "not json at all"  # this screen is dropped
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

    report = _run(
        run_scout(
            client=_FakeClient(tickets),
            view_id="6490757606044",
            workspace=tmp_path,
            now=NOW,
            query_fn=query_fn,
            screen_options_factory=lambda: None,
            synth_options_factory=lambda: None,
        )
    )

    assert report.candidates_screened == 2
    assert report.reports_parsed == 1
    assert report.dropped == 1


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


def test_run_scout_default_factories_build_restricted_hooks(tmp_path, monkeypatch):
    """Production path: runner wires sandbox-confined hooks when factories are omitted."""
    tickets = [
        Ticket(
            id=1,
            subject="audio",
            status="open",
            priority="high",
            updated_at=NOW - timedelta(days=8),
        ),
    ]
    hook_builds: list[dict] = []
    real_build_hooks = None

    def recording_build_hooks(sandbox_root, events_path, *, restrict_read_tools=False):
        hook_builds.append(
            {
                "sandbox_root": sandbox_root,
                "events_path": events_path,
                "restrict_read_tools": restrict_read_tools,
            }
        )
        return real_build_hooks(
            sandbox_root,
            events_path,
            restrict_read_tools=restrict_read_tools,
        )

    import noc_cli.agent.harness as harness

    real_build_hooks = harness.build_hooks
    monkeypatch.setattr(harness, "build_hooks", recording_build_hooks)

    report = _run(
        run_scout(
            client=_FakeClient(tickets),
            view_id="6490757606044",
            workspace=tmp_path,
            now=NOW,
            query_fn=_fake_query,
        )
    )

    assert [r.ticket_id for r in report.ranked] == [1]
    assert len(hook_builds) == 1
    assert hook_builds[0]["sandbox_root"] == tmp_path
    assert hook_builds[0]["events_path"] == tmp_path / "events.jsonl"
    assert hook_builds[0]["restrict_read_tools"] is True
