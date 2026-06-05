import httpx
import pytest

from noc_cli.config import Config
from noc_cli.scout.acquire import ZendeskWriteError, ZendeskWriter

CFG = Config(
    zendesk_subdomain="acme",
    zendesk_email="me@acme.com",
    zendesk_api_token="tok",
)


def test_assign_ticket_puts_assignee():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"ticket": {"id": 42, "assignee_id": 7}})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    ZendeskWriter(CFG, client=client).assign_ticket(42, 7)

    assert seen["method"] == "PUT"
    assert seen["url"] == "https://acme.zendesk.com/api/v2/tickets/42.json"
    assert '"assignee_id":7' in seen["body"].replace(" ", "")
    assert seen["auth"].startswith("Basic ")


def test_assign_ticket_raises_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "denied"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(ZendeskWriteError):
        ZendeskWriter(CFG, client=client).assign_ticket(42, 7)
