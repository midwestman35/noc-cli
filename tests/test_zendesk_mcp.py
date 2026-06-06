import httpx
import pytest

from noc_cli.mcp.zendesk_server import fetch_ticket, _map_error
from noc_cli.models import Ticket
from noc_cli.zendesk import ZendeskError


class _FakeClient:
    def __init__(self, ticket=None, exc=None):
        self._ticket = ticket
        self._exc = exc

    def get_ticket(self, ticket_id):
        if self._exc:
            raise self._exc
        return self._ticket


def _ticket(**kw):
    base = dict(
        id=761, subject="911 at 37.7749, -122.4194", description="caller addr 123 Main Street",
        requester_org="City PD", requester_email="caller@example.com", requester_id=99,
        assignee_id=1, assignee_email="noc@carbyne.com", status="open", priority="high",
        tags=["apex"], created_at="2026-06-01T00:00:00Z", updated_at="2026-06-02T00:00:00Z",
        comments=[],
    )
    base.update(kw)
    return Ticket.model_validate(base)


def test_fetch_ticket_redacts_freetext_and_excludes_email_identifiers():
    out = fetch_ticket(_FakeClient(ticket=_ticket()), 761)
    assert out["id"] == 761
    assert out["status"] == "open"
    assert "<COORDS>" in out["subject"]      # coords scrubbed
    assert "<ADDR>" in out["description"]     # address scrubbed
    assert "requester_email" not in out       # raw email identifier excluded
    assert "assignee_email" not in out
    assert "requester_id" not in out


def test_fetch_ticket_maps_404_to_structured_error():
    req = httpx.Request("GET", "https://x.zendesk.com/api/v2/tickets/9.json")
    resp = httpx.Response(404, request=req)
    exc = httpx.HTTPStatusError("not found", request=req, response=resp)
    out = fetch_ticket(_FakeClient(exc=exc), 9)
    assert out == {"error": "not found", "kind": "not_found"}


def test_map_error_classifies_zendesk_auth():
    out = _map_error(ZendeskError("Zendesk auth failed - check token"))
    assert out["kind"] == "auth"


from noc_cli.mcp.zendesk_server import fetch_comments
from noc_cli.models import Comment


class _FakeCommentsClient:
    def __init__(self, comments=None, exc=None):
        self._comments = comments or []
        self._exc = exc

    def get_comments(self, ticket_id):
        if self._exc:
            raise self._exc
        return self._comments


def _comment(**kw):
    base = dict(id=1, author_id=42, public=True, body="callback 37.7749, -122.4194",
                created_at="2026-06-01T00:00:00Z", attachments=[])
    base.update(kw)
    return Comment.model_validate(base)


def test_fetch_comments_redacts_body_and_excludes_author_id():
    out = fetch_comments(_FakeCommentsClient(comments=[_comment()]), 761)
    assert out["comments"][0]["public"] is True
    assert "<COORDS>" in out["comments"][0]["body"]
    assert "author_id" not in out["comments"][0]
    assert out["count"] == 1


from noc_cli.mcp.zendesk_server import search_tickets, SEARCH_CAP


class _FakeSearchClient:
    def __init__(self, tickets):
        self._tickets = tickets

    def search(self, query):
        return self._tickets


def test_search_caps_results_and_redacts_subjects():
    tickets = [_ticket(id=i, subject="37.7749, -122.4194") for i in range(SEARCH_CAP + 5)]
    out = search_tickets(_FakeSearchClient(tickets), "apex")
    assert out["count"] == SEARCH_CAP
    assert len(out["results"]) == SEARCH_CAP
    assert "<COORDS>" in out["results"][0]["subject"]
    assert set(out["results"][0].keys()) == {"id", "subject", "status", "tags"}
