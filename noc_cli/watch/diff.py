from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from noc_cli.models import Comment, Ticket
from noc_cli.watch.state import TicketSnapshot


class ChangeKind(str, Enum):
    STATUS_CHANGED = "status_changed"
    NEW_REQUESTER_COMMENT = "new_requester_comment"


@dataclass
class ChangeEvent:
    """A classified change detected on a single ticket during one poll cycle."""

    ticket_id: int
    ticket_subject: str
    kind: ChangeKind
    old_status: str | None = None
    new_status: str | None = None
    customer_replied: bool = False


def _latest_public_requester_comment(
    comments: list[Comment], requester_id: int | None
) -> Comment | None:
    """Return the most recent public comment authored by the requester, or None."""
    candidates = [
        c
        for c in comments
        if c.public and requester_id is not None and c.author_id == requester_id
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: c.created_at or datetime.min.replace(tzinfo=timezone.utc),
    )


def _latest_public_comment(comments: list[Comment]) -> Comment | None:
    """Return the most recent public comment regardless of author."""
    public = [c for c in comments if c.public]
    if not public:
        return None
    return max(
        public, key=lambda c: c.created_at or datetime.min.replace(tzinfo=timezone.utc)
    )


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def diff_tickets(
    tickets: list[Ticket],
    comments_map: dict[int, list[Comment]],
    last_seen: dict[int, TicketSnapshot],
    *,
    return_seeds: bool = False,
) -> list[ChangeEvent] | tuple[list[ChangeEvent], dict[int, TicketSnapshot]]:
    """Compare *tickets* against *last_seen* and emit classified events.

    Trigger rules (spec §13 option b):
    1. Status transition → STATUS_CHANGED. Pending→Open with the latest public
       comment authored by the requester sets customer_replied=True.
    2. New public requester comment (status unchanged) → NEW_REQUESTER_COMMENT
       with customer_replied=True.
    3. First-seen ticket → seeded silently; no event.
    4. Private comments and agent comments are ignored.

    When return_seeds is True, returns (events, seeds) so the caller can persist
    first-seen snapshots without firing alerts.
    """
    events: list[ChangeEvent] = []
    seeds: dict[int, TicketSnapshot] = {}

    for ticket in tickets:
        comments = comments_map.get(ticket.id, [])
        latest_public = _latest_public_comment(comments)
        latest_public_ts = _iso(latest_public.created_at if latest_public else None)

        latest_requester = _latest_public_requester_comment(
            comments, ticket.requester_id
        )
        latest_requester_ts = _iso(
            latest_requester.created_at if latest_requester else None
        )

        current_snap = TicketSnapshot(
            status=ticket.status, last_comment_at=latest_public_ts
        )

        if ticket.id not in last_seen:
            seeds[ticket.id] = current_snap
            continue

        prior = last_seen[ticket.id]
        status_changed = ticket.status != prior.status
        new_requester_comment = bool(
            latest_requester_ts and latest_requester_ts > prior.last_comment_at
        )
        customer_replied = new_requester_comment

        if status_changed:
            events.append(
                ChangeEvent(
                    ticket_id=ticket.id,
                    ticket_subject=ticket.subject,
                    kind=ChangeKind.STATUS_CHANGED,
                    old_status=prior.status,
                    new_status=ticket.status,
                    customer_replied=customer_replied,
                )
            )
        elif new_requester_comment:
            events.append(
                ChangeEvent(
                    ticket_id=ticket.id,
                    ticket_subject=ticket.subject,
                    kind=ChangeKind.NEW_REQUESTER_COMMENT,
                    old_status=prior.status,
                    new_status=ticket.status,
                    customer_replied=True,
                )
            )

    if return_seeds:
        return events, seeds
    return events
