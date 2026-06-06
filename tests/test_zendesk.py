import base64

import httpx
import pytest

from noc_cli.config import Config
from noc_cli.models import Ticket
from noc_cli.zendesk import ZendeskClient, ZendeskError


def make_config() -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="agent@x.com",
        zendesk_api_token="tok123",
    )


@pytest.fixture
def http():
    with httpx.Client() as client:
        yield client


def test_missing_config_raises():
    with pytest.raises(ZendeskError):
        ZendeskClient(Config())


def test_get_ticket_uses_token_auth_and_parses(httpx_mock, http):
    httpx_mock.add_response(
        url="https://carbyne.zendesk.com/api/v2/tickets/18432.json",
        json={
            "ticket": {
                "id": 18432,
                "subject": "No ANI",
                "status": "open",
                "tags": ["apex"],
            }
        },
    )
    client = ZendeskClient(make_config(), client=http)
    ticket = client.get_ticket(18432)
    assert isinstance(ticket, Ticket)
    assert ticket.id == 18432
    request = httpx_mock.get_request()
    expected = "Basic " + base64.b64encode(b"agent@x.com/token:tok123").decode()
    assert request.headers["Authorization"] == expected


def test_auth_failure_raises_friendly_error(httpx_mock, http):
    httpx_mock.add_response(status_code=401)
    client = ZendeskClient(make_config(), client=http)
    with pytest.raises(ZendeskError, match="auth failed"):
        client.get_ticket(1)


def test_search_filters_to_tickets(httpx_mock, http):
    httpx_mock.add_response(
        url="https://carbyne.zendesk.com/api/v2/search.json?query=foo",
        json={
            "results": [
                {"id": 1, "result_type": "ticket", "subject": "a"},
                {"id": 2, "result_type": "user"},
            ]
        },
    )
    client = ZendeskClient(make_config(), client=http)
    results = client.search("foo")
    assert [t.id for t in results] == [1]


def test_view_tickets_parses_list_including_assignee_id(httpx_mock, http):
    httpx_mock.add_response(
        url="https://carbyne.zendesk.com/api/v2/views/555/tickets.json",
        json={"tickets": [{"id": 7, "status": "pending", "assignee_id": 4242}]},
    )
    client = ZendeskClient(make_config(), client=http)
    tickets = client.view_tickets(555)
    assert tickets[0].status == "pending"
    assert tickets[0].assignee_id == 4242


def test_find_user_id_resolves_email_to_id(httpx_mock, http):
    httpx_mock.add_response(
        json={"users": [{"id": 555, "email": "agent@x.com"}]},
    )
    client = ZendeskClient(make_config(), client=http)
    assert client.find_user_id("agent@x.com") == 555
    request = httpx_mock.get_request()
    assert "users/search.json" in str(request.url)


def test_find_user_id_returns_none_when_no_exact_match(httpx_mock, http):
    # Zendesk search can return near-matches; we only accept an exact email.
    httpx_mock.add_response(json={"users": [{"id": 1, "email": "agent2@x.com"}]})
    client = ZendeskClient(make_config(), client=http)
    assert client.find_user_id("agent@x.com") is None


def test_find_user_id_returns_none_for_blank_email_without_calling_api(http):
    client = ZendeskClient(make_config(), client=http)
    assert client.find_user_id("") is None


def test_get_comments_parses_list(httpx_mock, http):
    httpx_mock.add_response(
        url="https://carbyne.zendesk.com/api/v2/tickets/9/comments.json",
        json={"comments": [{"id": 1, "body": "hi", "public": True}]},
    )
    client = ZendeskClient(make_config(), client=http)
    comments = client.get_comments(9)
    assert comments[0].body == "hi"


def test_download_attachment_returns_bytes_and_sends_auth(httpx_mock, http):
    """download_attachment fetches the URL with the existing auth header."""
    httpx_mock.add_response(
        url="https://cdn.zendesk.example/attachments/kamailio.log",
        content=b"SIP line from Zendesk",
    )
    client = ZendeskClient(make_config(), client=http)
    data = client.download_attachment(
        "https://cdn.zendesk.example/attachments/kamailio.log"
    )
    assert data == b"SIP line from Zendesk"
    request = httpx_mock.get_request()
    assert "Authorization" in request.headers


def test_download_attachment_follows_zendesk_cdn_redirect(httpx_mock, http):
    """Zendesk attachment URLs 302-redirect to a pre-signed CDN URL; the download
    must follow the redirect rather than raising on the 3xx response."""
    src = "https://carbyne.zendesk.com/attachments/token/abc/?name=export.txt"
    cdn = "https://p28.zdusercontent.com/attachment/2140499/abc?token=signed"
    httpx_mock.add_response(url=src, status_code=302, headers={"Location": cdn})
    httpx_mock.add_response(url=cdn, content=b"export log bytes")

    client = ZendeskClient(make_config(), client=http)
    data = client.download_attachment(src)
    assert data == b"export log bytes"
