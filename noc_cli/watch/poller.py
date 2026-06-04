from __future__ import annotations

from noc_cli.models import Ticket
from noc_cli.zendesk import ZendeskClient


def poll_view(
    client: ZendeskClient,
    view_id: str,
    assignee_id: int | None,
) -> list[Ticket]:
    """Fetch all tickets from *view_id* and filter to those assigned to *assignee_id*.

    The Zendesk view-tickets endpoint returns each ticket's ``assignee_id`` (a
    numeric user id), not the assignee email, so the watcher matches on that.
    Pass ``None`` to return all tickets in the view without filtering — the
    caller resolves the email to an id (see ``ZendeskClient.find_user_id``) and
    passes ``None`` when it cannot, so an unresolved assignee shows the whole
    view rather than nothing. ``ZendeskError`` propagates so the TUI can
    log-and-continue.
    """
    tickets = client.view_tickets(view_id)
    if assignee_id is None:
        return tickets
    return [t for t in tickets if t.assignee_id == assignee_id]
