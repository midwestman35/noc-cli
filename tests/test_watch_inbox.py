from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from noc_cli.models import Comment, Ticket
from noc_cli.watch.inbox import (
    InboxSummary,
    build_segments,
    humanize_when,
    render_activity,
    render_summary,
)


NOW = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)


def _summary(
    ticket_id: int,
    *,
    investigated_at: datetime | None = None,
    folder: Path | None = None,
    fork: str | None = "B",
    confidence: str | None = "High",
    status: str | None = "open",
    owner: str | None = "enrique",
    symptom_tag: str | None = "[dropped calls]",
    rubric_version: str | None = "2026-05-13",
    quoted_rubric_row: str | None = "customer LAN, switch, or SDWAN",
    related_zendesk: list[int] | None = None,
    related_jira: list[str] | None = None,
    master: int | None = None,
    cluster: str | None = None,
) -> InboxSummary:
    return InboxSummary(
        ticket_id=ticket_id,
        fork=fork,
        confidence=confidence,
        status=status,
        owner=owner,
        symptom_tag=symptom_tag,
        rubric_version=rubric_version,
        quoted_rubric_row=quoted_rubric_row,
        related_zendesk=related_zendesk or [],
        related_jira=related_jira or [],
        master=master,
        cluster=cluster,
        investigated_at=investigated_at or NOW,
        folder=folder or Path(f"Tickets/{ticket_id}"),
    )


def _ticket(
    ticket_id: int,
    *,
    updated_at: datetime | None = None,
    subject: str | None = None,
    status: str = "open",
) -> Ticket:
    return Ticket(
        id=ticket_id,
        subject=subject or f"Ticket {ticket_id}",
        status=status,
        requester_email="requester@example.com",
        requester_org="City PSAP",
        updated_at=updated_at,
    )


def test_build_segments_uses_three_day_window_dedups_live_and_sorts_worked_newest_first():
    disk = [
        _summary(101, investigated_at=NOW - timedelta(hours=4)),
        _summary(102, investigated_at=NOW - timedelta(days=4)),
        _summary(103, investigated_at=NOW - timedelta(hours=1)),
        _summary(104, investigated_at=NOW - timedelta(days=3)),
    ]
    live = [_ticket(101), _ticket(200)]

    worked, queue = build_segments(disk, live, now=NOW, window_days=3)

    assert [row.ticket_id for row in worked] == [103, 104]
    assert all(row.segment == "worked" for row in worked)
    assert [row.ticket_id for row in queue] == [101, 200]


def test_build_segments_preserves_live_order_and_badges_queue_from_any_age_disk_summary():
    old_summary = _summary(
        300,
        investigated_at=NOW - timedelta(days=30),
        fork="A",
        confidence="Medium",
    )
    live = [
        _ticket(200, updated_at=NOW - timedelta(minutes=5)),
        _ticket(300, updated_at=NOW - timedelta(hours=2)),
        _ticket(100, updated_at=NOW - timedelta(days=2)),
    ]

    worked, queue = build_segments([old_summary], live, now=NOW)

    assert worked == []
    assert [row.ticket_id for row in queue] == [200, 300, 100]
    assert [row.triaged for row in queue] == [False, True, False]
    assert queue[1].summary is old_summary
    assert queue[1].when == live[1].updated_at


def test_humanize_when_formats_empty_recent_minutes_hours_and_days():
    assert humanize_when(None, NOW) == "—"
    assert humanize_when(NOW - timedelta(seconds=30), NOW) == "just now"
    assert humanize_when(NOW - timedelta(minutes=7, seconds=30), NOW) == "7m ago"
    assert humanize_when(NOW - timedelta(hours=3, minutes=30), NOW) == "3h ago"
    assert humanize_when(NOW - timedelta(days=2, hours=3), NOW) == "2d ago"
    assert humanize_when(NOW + timedelta(minutes=1), NOW) == "just now"


def test_render_summary_includes_design_sections_related_values_and_version_mismatch():
    summary = _summary(
        44999,
        fork="B",
        confidence="High",
        status="pending",
        owner="enrique",
        rubric_version="2026-05-13",
        quoted_rubric_row="RTP absent in PCAP",
        related_zendesk=[44999, 32549],
        related_jira=["REP-123", "REP-456"],
        master=12345,
        cluster="Aurora metro outage",
    )

    rendered = render_summary(summary, shipped_version="2026-06-04")

    assert "Rubric version mismatch: state=2026-05-13, shipped=2026-06-04" in rendered
    assert "Ticket: ZD-44999" in rendered
    assert "Fork: B · Confidence: High · Status: pending" in rendered
    assert "Owner: enrique" in rendered
    assert '  "RTP absent in PCAP"' in rendered
    assert "rubric_version on STATE.md: 2026-05-13" in rendered
    assert "shipped rubric_version:     2026-06-04" in rendered
    assert "Zendesk: #44999, #32549" in rendered
    assert "Jira:    REP-123, REP-456" in rendered
    assert "Master:  #12345" in rendered
    assert "Cluster: Aurora metro outage" in rendered


def test_render_summary_uses_none_fallbacks_without_mismatch_when_versions_match():
    summary = _summary(
        45000,
        fork=None,
        confidence=None,
        status=None,
        owner=None,
        symptom_tag=None,
        rubric_version=None,
        quoted_rubric_row=None,
        related_zendesk=[],
        related_jira=[],
        master=None,
        cluster=None,
    )

    rendered = render_summary(summary, shipped_version=None)

    assert "Rubric version mismatch" not in rendered
    assert "Fork: (none) · Confidence: (none) · Status: (none)" in rendered
    assert "Owner: (none)" in rendered
    assert "Quoted rubric row:\n  (none)" in rendered
    assert "rubric_version on STATE.md: (none)" in rendered
    assert "shipped rubric_version:     (none)" in rendered
    assert "Zendesk: (none)" in rendered
    assert "Jira:    (none)" in rendered
    assert "Master:  (none)" in rendered
    assert "Cluster: (none)" in rendered


def test_render_activity_lists_ticket_fields_and_comments_newest_first_with_markers_and_authors():
    older = Comment(
        id=10,
        author_id=111,
        public=True,
        body="Public customer reply\nwith a second line",
        created_at=datetime(2026, 6, 4, 9, 15, tzinfo=timezone.utc),
    )
    newer = Comment(
        id=11,
        author_id=None,
        public=False,
        body=(
            "Internal note with enough words to demonstrate that the one-line "
            "body is shortened before display to keep the pane readable."
        ),
        created_at=datetime(2026, 6, 4, 10, 30, tzinfo=timezone.utc),
    )
    ticket = _ticket(500, subject="Phone registration trouble", status="pending")
    ticket.comments = [older, newer]

    rendered = render_activity(ticket)

    assert "Ticket: ZD-500" in rendered
    assert "Subject: Phone registration trouble" in rendered
    assert "Requester: requester@example.com" in rendered
    assert "Organization: City PSAP" in rendered
    assert "Status: pending" in rendered
    assert "Not yet investigated — press [i] to investigate." in rendered
    assert rendered.index("[internal]") < rendered.index("[public]")
    assert "[internal] 2026-06-04 10:30 UTC author unknown:" in rendered
    assert "[public] 2026-06-04 09:15 UTC author #111: Public customer reply with a second line" in rendered
    assert "\nwith a second line" not in rendered
    assert "..." in rendered
