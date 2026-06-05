from __future__ import annotations

import httpx

from noc_cli.config import Config
from noc_cli.zendesk import build_auth_header


class ZendeskWriteError(RuntimeError):
    pass


class ZendeskWriter:
    """The only Zendesk-mutating surface in noc-cli."""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        if not (
            config.zendesk_subdomain
            and config.zendesk_email
            and config.zendesk_api_token
        ):
            raise ZendeskWriteError(
                "Zendesk is not configured. Run `noc-cli setup` first."
            )
        self._base_url = config.zendesk_base_url
        self._auth_header = build_auth_header(config)
        self._client = client or httpx.Client(timeout=30.0)

    def assign_ticket(self, ticket_id: int, assignee_id: int) -> None:
        """Assign a ticket to one user. No other write behavior belongs here."""
        resp = self._client.put(
            f"{self._base_url}/tickets/{ticket_id}.json",
            json={"ticket": {"assignee_id": assignee_id}},
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code == 401:
            raise ZendeskWriteError(
                "Zendesk auth failed on assign - check ZENDESK_EMAIL and "
                "ZENDESK_API_TOKEN."
            )
        resp.raise_for_status()
