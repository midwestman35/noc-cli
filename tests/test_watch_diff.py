from datetime import datetime

from noc_cli.models import Comment, Ticket
from noc_cli.watch.diff import ChangeKind, diff_tickets
from noc_cli.watch.state import TicketSnapshot


def _ticket(tid: int, *, status: str = "open") -> Ticket:
    return Ticket(id=tid, subject=f"Ticket {tid}", status=status)


def _comment(
    cid: int,
    *,
    author_id: int = 999,
    public: bool = True,
    created_at: str = "2026-06-01T10:00:00Z",
) -> Comment:
    return Comment(
        id=cid,
        author_id=author_id,
        public=public,
        body="hello",
        created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
    )


def _snap(status: str = "open", last_comment_at: str = "") -> TicketSnapshot:
    return TicketSnapshot(status=status, last_comment_at=last_comment_at)


def test_no_change_when_status_and_comments_are_identical():
    tickets = [_ticket(1, status="open")]
    comments_map = {1: [_comment(10, created_at="2026-06-01T10:00:00Z")]}
    last_seen = {1: _snap(status="open", last_comment_at="2026-06-01T10:00:00Z")}
    assert diff_tickets(tickets, comments_map, last_seen) == []


def test_no_change_when_new_comment_is_private():
    tickets = [_ticket(1, status="open")]
    comments_map = {1: [_comment(10, public=False, created_at="2026-06-02T10:00:00Z")]}
    last_seen = {1: _snap(status="open", last_comment_at="2026-06-01T08:00:00Z")}
    assert diff_tickets(tickets, comments_map, last_seen) == []


def test_no_change_when_new_comment_is_agent_not_requester():
    comments_map = {1: [_comment(10, author_id=777, public=True, created_at="2026-06-02T10:00:00Z")]}
    last_seen = {1: _snap(status="open", last_comment_at="2026-06-01T08:00:00Z")}
    t = Ticket(id=1, subject="T1", status="open", requester_id=999)
    assert diff_tickets([t], comments_map, last_seen) == []


def test_first_seen_ticket_produces_no_event_and_returns_seed_snapshot():
    tickets = [_ticket(7, status="open")]
    comments_map = {7: [_comment(20, created_at="2026-06-01T09:00:00Z")]}
    last_seen: dict[int, TicketSnapshot] = {}
    assert diff_tickets(tickets, comments_map, last_seen) == []


def test_generic_status_change_emits_status_changed_event():
    tickets = [_ticket(1, status="solved")]
    comments_map = {1: [_comment(10, created_at="2026-06-01T10:00:00Z")]}
    last_seen = {1: _snap(status="open", last_comment_at="2026-06-01T10:00:00Z")}
    events = diff_tickets(tickets, comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.ticket_id == 1
    assert evt.kind == ChangeKind.STATUS_CHANGED
    assert evt.old_status == "open"
    assert evt.new_status == "solved"
    assert not evt.customer_replied


def test_pending_to_open_is_flagged_as_customer_replied():
    t = Ticket(id=2, subject="T2", status="open", requester_id=999)
    comments_map = {2: [_comment(30, author_id=999, public=True, created_at="2026-06-02T11:00:00Z")]}
    last_seen = {2: _snap(status="pending", last_comment_at="2026-06-01T08:00:00Z")}
    events = diff_tickets([t], comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == ChangeKind.STATUS_CHANGED
    assert evt.old_status == "pending"
    assert evt.new_status == "open"
    assert evt.customer_replied is True


def test_pending_to_open_without_requester_comment_not_flagged_customer_replied():
    t = Ticket(id=3, subject="T3", status="open", requester_id=999)
    comments_map = {3: [_comment(40, author_id=777, public=True, created_at="2026-06-02T11:00:00Z")]}
    last_seen = {3: _snap(status="pending", last_comment_at="2026-06-01T08:00:00Z")}
    events = diff_tickets([t], comments_map, last_seen)
    assert len(events) == 1
    assert events[0].kind == ChangeKind.STATUS_CHANGED
    assert events[0].customer_replied is False


def test_new_public_requester_comment_emits_new_comment_event():
    t = Ticket(id=5, subject="T5", status="open", requester_id=999)
    comments_map = {5: [
        _comment(50, author_id=999, public=True, created_at="2026-06-01T08:00:00Z"),
        _comment(51, author_id=999, public=True, created_at="2026-06-02T12:00:00Z"),
    ]}
    last_seen = {5: _snap(status="open", last_comment_at="2026-06-01T08:00:00Z")}
    events = diff_tickets([t], comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == ChangeKind.NEW_REQUESTER_COMMENT
    assert evt.ticket_id == 5
    assert evt.customer_replied is True


def test_both_status_change_and_new_comment_emits_one_event_with_both_flags():
    t = Ticket(id=6, subject="T6", status="open", requester_id=999)
    comments_map = {6: [_comment(60, author_id=999, public=True, created_at="2026-06-02T14:00:00Z")]}
    last_seen = {6: _snap(status="pending", last_comment_at="2026-06-01T09:00:00Z")}
    events = diff_tickets([t], comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == ChangeKind.STATUS_CHANGED
    assert evt.customer_replied is True


def test_ticket_with_no_comments_does_not_raise():
    tickets = [_ticket(8, status="open")]
    comments_map: dict[int, list[Comment]] = {8: []}
    last_seen = {8: _snap(status="open", last_comment_at="")}
    assert diff_tickets(tickets, comments_map, last_seen) == []


def test_returns_seed_snapshots_for_caller_to_persist():
    tickets = [_ticket(9, status="open")]
    comments_map: dict[int, list[Comment]] = {9: []}
    last_seen: dict[int, TicketSnapshot] = {}
    events, seeds = diff_tickets(tickets, comments_map, last_seen, return_seeds=True)
    assert events == []
    assert 9 in seeds
    assert seeds[9].status == "open"
