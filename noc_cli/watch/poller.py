from __future__ import annotations

from noc_cli.models import Ticket
from noc_cli.zendesk import ZendeskClient


def poll_view(
    client: ZendeskClient,
    view_id: str,
    assignee: str,
) -> list[Ticket]:
    """Fetch all tickets from *view_id* and filter to those assigned to *assignee*.

    *assignee* is matched case-insensitively against the ticket's
    ``assignee_email`` field. Pass an empty string to return all tickets in the
    view without filtering. ``ZendeskError`` propagates so the TUI can
    log-and-continue.
    """
    tickets = client.view_tickets(view_id)
    if not assignee:
        return tickets
    needle = assignee.lower()
    return [t for t in tickets if (t.assignee_email or "").lower() == needle]
