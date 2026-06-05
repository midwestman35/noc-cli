from __future__ import annotations

import base64

import httpx

from noc_cli.config import Config
from noc_cli.models import Comment, Ticket


class ZendeskError(RuntimeError):
    pass


def build_auth_header(config: Config) -> str:
    """Zendesk token auth header: ``Basic base64("<email>/token:<api_token>")``."""
    raw = f"{config.zendesk_email}/token:{config.zendesk_api_token}".encode()
    return "Basic " + base64.b64encode(raw).decode()


class ZendeskClient:
    """Read-only Zendesk API v2 client. Performs no writes, ever."""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        if not (config.zendesk_subdomain and config.zendesk_email and config.zendesk_api_token):
            raise ZendeskError(
                "Zendesk is not configured. Run `noc-cli setup` to set "
                "ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, and ZENDESK_API_TOKEN."
            )
        self._base_url = config.zendesk_base_url
        self._auth_header = build_auth_header(config)
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

    def find_user_id(self, email: str) -> int | None:
        """Resolve a user email to its Zendesk user id (read-only).

        Used by the watcher to filter a view down to one assignee. The view
        tickets endpoint returns ``assignee_id`` (a number), not the email, so
        we map the configured/own email to an id once and compare on that.
        Returns ``None`` when the email matches no user, so the caller can fall
        back to showing the whole view rather than an empty list.
        """
        email = (email or "").strip()
        if not email:
            return None
        data = self._get("/users/search.json", params={"query": email})
        needle = email.lower()
        for user in data.get("users", []):
            if (user.get("email") or "").lower() == needle:
                return user.get("id")
        return None

    def download_attachment(self, url: str) -> bytes:
        """Fetch an attachment by its content URL (read-only).

        Zendesk attachment URLs 302-redirect to a pre-signed CDN host
        (zdusercontent.com), so we must follow redirects. httpx strips the
        Authorization header on the cross-origin hop automatically — the CDN
        URL carries its own signed token, so that is correct.
        """
        resp = self._client.get(
            url,
            headers={"Authorization": self._auth_header},
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content
