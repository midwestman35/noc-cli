from datetime import datetime, timezone

from noc_cli.scout.models import RankedCandidate, ScoutReport
from noc_cli.scout.render import render_scout_report


def test_render_lists_ranked_tickets_in_order():
    report = ScoutReport(
        generated_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        ranked=[
            RankedCandidate(
                ticket_id=2,
                rank=1,
                rationale="strong",
                runbook_id="no-ani",
                runbook_match_confidence=0.9,
            ),
            RankedCandidate(
                ticket_id=1,
                rank=2,
                rationale="weak",
                runbook_id="low-audio",
                runbook_match_confidence=0.4,
            ),
        ],
    )

    text = render_scout_report(report)

    assert "#2" in text and "#1" in text
    assert text.index("#2") < text.index("#1")
    assert "no-ani" in text
    assert "strong" in text
    assert "close" not in text.lower()
    assert "resolve" not in text.lower()


def test_render_empty_report():
    text = render_scout_report(ScoutReport())

    assert "No stale tickets" in text
