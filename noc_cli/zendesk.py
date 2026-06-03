from __future__ import annotations

import base64

import httpx

from noc_cli.config import Config
from noc_cli.models import Comment, Ticket


class ZendeskError(RuntimeError):
    pass


class ZendeskClient:
    """Read-only Zendesk API v2 client. Performs no writes, ever."""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        if not (config.zendesk_subdomain and config.zendesk_email and config.zendesk_api_token):
            raise ZendeskError(
                "Zendesk is not configured. Run `noc-cli setup` to set "
                "ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, and ZENDESK_API_TOKEN."
            )
        self._base_url = config.zendesk_base_url
        # Zendesk token auth: "<email>/token:<api_token>". Do not pre-append /token.
        raw = f"{config.zendesk_email}/token:{config.zendesk_api_token}".encode()
        self._auth_header = "Basic " + base64.b64encode(raw).decode()
        self._client = client or httpx.Client(timeout=30.0)

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._client.get(
            f"{self._base_url}{path}",
            params=params,
            headers={"Authorization": self._auth_header},
        )
        if resp.status_code == 401:
            raise ZendeskError(
                "Zendesk auth failed - check ZENDESK_EMAIL and ZENDESK_API_TOKEN."
            )
        resp.raise_for_status()
        return resp.json()

    def get_ticket(self, ticket_id: int) -> Ticket:
        data = self._get(f"/tickets/{ticket_id}.json")
        return Ticket.model_validate(data["ticket"])

    def get_comments(self, ticket_id: int) -> list[Comment]:
        data = self._get(f"/tickets/{ticket_id}/comments.json")
        return [Comment.model_validate(c) for c in data.get("comments", [])]

    def search(self, query: str) -> list[Ticket]:
        data = self._get("/search.json", params={"query": query})
        return [
            Ticket.model_validate(r)
            for r in data.get("results", [])
            if r.get("result_type") == "ticket"
        ]

    def view_tickets(self, view_id: int | str) -> list[Ticket]:
        data = self._get(f"/views/{view_id}/tickets.json")
        return [Ticket.model_validate(t) for t in data.get("tickets", [])]
