from __future__ import annotations

from datetime import datetime, timezone

from noc_cli.models import Ticket
from noc_cli.scout.models import Candidate

_PRIORITY_WEIGHT = {"urgent": 4.0, "high": 3.0, "normal": 2.0, "low": 1.0}
_ACTIVE_STATUSES = {"new", "open", "pending", "hold"}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def staleness_days(ticket: Ticket, *, now: datetime) -> int | None:
    """Return non-negative whole stale days, or None when updated_at is absent."""
    updated = _as_utc(ticket.updated_at)
    if updated is None:
        return None
    now_utc = _as_utc(now)
    if now_utc is None:
        return None
    return max((now_utc - updated).days, 0)


def is_active_status(status: str) -> bool:
    return not status or status.lower() in _ACTIVE_STATUSES


def take_ineligible_reason(
    ticket: Ticket, *, now: datetime, min_staleness_days: int
) -> str | None:
    """Why *ticket* is ineligible to surface/take, or None if eligible.

    The single source of truth for the Scout ruleset — shared by
    ``rank_candidates`` (list filter) and the ``--take`` preflight, so the list
    and the claim path can never disagree about what counts as a candidate.
    """
    if ticket.assignee_id is not None:
        return f"Ticket #{ticket.id} is already assigned."
    if not is_active_status(ticket.status):
        return f"Ticket #{ticket.id} is {ticket.status or 'inactive'}."
    stale_days = staleness_days(ticket, now=now)
    if stale_days is None:
        return f"Ticket #{ticket.id} has no updated_at timestamp."
    if stale_days < min_staleness_days:
        return (
            f"Ticket #{ticket.id} is no longer stale enough "
            f"({stale_days}d < {min_staleness_days}d)."
        )
    return None


def rank_candidates(
    tickets: list[Ticket],
    *,
    now: datetime,
    top_k: int = 8,
    min_staleness_days: int = 7,
) -> list[Candidate]:
    """Rank unassigned, active, stale tickets by staleness times priority.

    Pure metadata arithmetic: no agent, no network, no writes.
    """
    candidates: list[Candidate] = []
    for ticket in tickets:
        if take_ineligible_reason(
            ticket, now=now, min_staleness_days=min_staleness_days
        ):
            continue
        stale_days = staleness_days(ticket, now=now)  # not None past the guard
        weight = _PRIORITY_WEIGHT.get((ticket.priority or "").lower(), 1.0)
        candidates.append(
            Candidate(
                ticket_id=ticket.id,
                subject=ticket.subject,
                status=ticket.status,
                priority=ticket.priority,
                staleness_days=stale_days,
                score=stale_days * weight,
            )
        )

    candidates.sort(key=lambda c: (c.score, c.staleness_days), reverse=True)
    return candidates[:top_k]
