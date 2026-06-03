import pytest

from noc_cli.models import Ticket
from noc_cli.watch.poller import poll_view
from noc_cli.zendesk import ZendeskError


class _FakeClient:
    """Minimal stand-in for ZendeskClient that returns scripted ticket lists."""

    def __init__(self, tickets: list[Ticket]) -> None:
        self._tickets = tickets

    def view_tickets(self, view_id: str) -> list[Ticket]:
        return list(self._tickets)


def _ticket(tid: int, *, assignee_email: str = "agent@x.com", status: str = "open") -> Ticket:
    return Ticket(id=tid, subject=f"Ticket {tid}", status=status, assignee_email=assignee_email)


def test_poll_view_returns_all_tickets_when_no_assignee_filter():
    tickets = [_ticket(1), _ticket(2, assignee_email="other@x.com")]
    client = _FakeClient(tickets)
    result = poll_view(client, "555", assignee="")
    assert [t.id for t in result] == [1, 2]


def test_poll_view_filters_to_matching_assignee():
    tickets = [
        _ticket(1, assignee_email="agent@x.com"),
        _ticket(2, assignee_email="other@x.com"),
        _ticket(3, assignee_email="AGENT@X.COM"),  # case-insensitive
    ]
    client = _FakeClient(tickets)
    result = poll_view(client, "555", assignee="agent@x.com")
    assert [t.id for t in result] == [1, 3]


def test_poll_view_returns_empty_list_when_view_is_empty():
    client = _FakeClient([])
    result = poll_view(client, "555", assignee="agent@x.com")
    assert result == []


def test_poll_view_returns_empty_list_when_no_ticket_matches_assignee():
    tickets = [_ticket(1, assignee_email="someone@x.com")]
    client = _FakeClient(tickets)
    result = poll_view(client, "555", assignee="nobody@x.com")
    assert result == []


def test_poll_view_propagates_zendesk_error():
    class _ErrorClient:
        def view_tickets(self, view_id: str) -> list[Ticket]:
            raise ZendeskError("network timeout")

    with pytest.raises(ZendeskError, match="network timeout"):
        poll_view(_ErrorClient(), "555", assignee="")
