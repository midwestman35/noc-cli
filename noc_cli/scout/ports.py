"""Typed boundaries Scout depends on.

These document the contracts at Scout's injection seams so the pipeline can be
statically checked and tested with lightweight fakes. ``ZendeskClient`` already
satisfies ``TicketReader`` structurally — no nominal inheritance needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from noc_cli.models import Ticket


class TicketReader(Protocol):
    """The read-only Zendesk surface Scout needs: list a view, fetch one ticket."""

    def view_tickets(self, view_id: str) -> list[Ticket]: ...

    def get_ticket(self, ticket_id: int) -> Ticket: ...


# An agent query function: called as ``query_fn(prompt=..., options=...)`` and
# returning an async stream of SDK messages. Mirrors ``claude_agent_sdk.query``.
QueryFn = Callable[..., AsyncIterator[Any]]
