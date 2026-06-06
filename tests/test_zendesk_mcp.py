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
