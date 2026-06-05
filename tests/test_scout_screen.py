import asyncio

from noc_cli.scout.models import Candidate
from noc_cli.scout.screen import screen_candidates, screen_ticket


def _run(coro):
    return asyncio.run(coro)


def _fake_query(result_json: str):
    async def fake_query(*, prompt, options):
        class FakeResult:
            result = result_json

        yield FakeResult()

    return fake_query


GOOD = (
    '{"ticket_id": 42, "runbook_id": "low-audio", '
    '"runbook_match_confidence": 0.7, "triage_ready": true, '
    '"missing_evidence": ["pcap"], "one_line": "matches low-audio"}'
)


def test_screen_ticket_parses_report(tmp_path):
    candidate = Candidate(ticket_id=42, subject="audio dropouts")

    report = _run(
        screen_ticket(
            candidate,
            runbooks_dir=tmp_path / "runbooks",
            query_fn=_fake_query(GOOD),
            options_factory=lambda: None,
        )
    )

    assert report is not None
    assert report.runbook_id == "low-audio"
    assert report.triage_ready is True


def test_screen_ticket_default_options_confine_read_hooks(tmp_path):
    candidate = Candidate(ticket_id=42, subject="audio dropouts")
    captured = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    report = _run(
        screen_ticket(
            candidate,
            runbooks_dir=tmp_path / "runbooks",
            query_fn=_fake_query(GOOD),
            options_cls=FakeOptions,
        )
    )

    assert report is not None
    assert captured["cwd"] == str(tmp_path)
    assert captured["allowed_tools"] == ["Read", "Glob", "Grep", "LS"]
    assert "hooks" in captured


def test_screen_ticket_returns_none_on_garbage(tmp_path):
    candidate = Candidate(ticket_id=42)

    report = _run(
        screen_ticket(
            candidate,
            runbooks_dir=tmp_path / "runbooks",
            query_fn=_fake_query("not json at all"),
            options_factory=lambda: None,
        )
    )

    assert report is None


def test_screen_candidates_drops_failures_and_bounds_concurrency(tmp_path):
    candidates = [Candidate(ticket_id=i) for i in range(5)]
    live = 0
    peak = 0

    async def fake_query(*, prompt, options):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1

        class FakeResult:
            result = GOOD

        yield FakeResult()

    reports = _run(
        screen_candidates(
            candidates,
            runbooks_dir=tmp_path / "runbooks",
            query_fn=fake_query,
            concurrency=2,
            options_factory=lambda: None,
        )
    )

    assert len(reports) == 5
    assert peak <= 2
