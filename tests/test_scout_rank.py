from datetime import datetime, timedelta, timezone

from noc_cli.models import Ticket
from noc_cli.scout.rank import rank_candidates

NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _ticket(tid, *, days_stale, priority="normal", status="open", assignee=None):
    return Ticket(
        id=tid,
        subject=f"ticket {tid}",
        status=status,
        priority=priority,
        assignee_id=assignee,
        updated_at=NOW - timedelta(days=days_stale),
    )


def test_ranks_by_staleness_times_priority():
    tickets = [
        _ticket(1, days_stale=2, priority="urgent"),
        _ticket(2, days_stale=10, priority="low"),
        _ticket(3, days_stale=3, priority="normal"),
    ]

    ranked = rank_candidates(tickets, now=NOW, top_k=5, min_staleness_days=0)

    assert [c.ticket_id for c in ranked] == [2, 1, 3]
    assert ranked[0].score == 10.0
    assert ranked[0].staleness_days == 10


def test_drops_assigned_inactive_undated_and_recent_tickets():
    tickets = [
        _ticket(1, days_stale=9, assignee=999),
        _ticket(2, days_stale=9, status="solved"),
        _ticket(3, days_stale=9, status="closed"),
        Ticket(id=4, subject="no date", status="open"),
        _ticket(5, days_stale=2),
        _ticket(6, days_stale=9),
    ]

    ranked = rank_candidates(tickets, now=NOW, top_k=5, min_staleness_days=7)

    assert [c.ticket_id for c in ranked] == [6]


def test_top_k_truncates():
    tickets = [_ticket(i, days_stale=i) for i in range(1, 11)]

    ranked = rank_candidates(tickets, now=NOW, top_k=3, min_staleness_days=0)

    assert len(ranked) == 3
    assert [c.ticket_id for c in ranked] == [10, 9, 8]
