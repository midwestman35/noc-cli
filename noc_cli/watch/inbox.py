from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from noc_cli.models import Ticket


@dataclass
class InboxSummary:
    ticket_id: int
    fork: str | None = None
    confidence: str | None = None
    status: str | None = None
    owner: str | None = None
    symptom_tag: str | None = None
    rubric_version: str | None = None
    quoted_rubric_row: str | None = None
    related_zendesk: list[int] = field(default_factory=list)
    related_jira: list[str] = field(default_factory=list)
    master: int | None = None
    cluster: str | None = None
    investigated_at: datetime | None = None
    folder: Path | None = None


@dataclass
class InboxRow:
    ticket_id: int
    segment: Literal["worked", "queue"]
    triaged: bool
    summary: InboxSummary | None = None
    ticket: Ticket | None = None
    when: datetime | None = None
    subject: str | None = None  # live ticket subject; None for disk-only worked rows


def build_segments(
    disk: list[InboxSummary],
    live: list[Ticket],
    *,
    now: datetime,
    window_days: int = 3,
) -> tuple[list[InboxRow], list[InboxRow]]:
    """Build the worked and queue segments for the inbox view.

    Live queue rows keep Zendesk's input order. Disk summaries are used for
    queue badging regardless of age, while the worked segment is constrained to
    recent disk investigations and excludes live queue ticket IDs.
    """
    now_utc = _as_utc(now)
    cutoff = now_utc - timedelta(days=window_days)
    live_ids = {ticket.id for ticket in live}
    disk_by_id = {summary.ticket_id: summary for summary in disk}

    worked = [
        InboxRow(
            ticket_id=summary.ticket_id,
            segment="worked",
            triaged=True,
            summary=summary,
            ticket=None,
            when=summary.investigated_at,
        )
        for summary in disk
        if summary.ticket_id not in live_ids
        and summary.investigated_at is not None
        and _as_utc(summary.investigated_at) >= cutoff
    ]
    worked.sort(key=lambda row: _as_utc(row.when), reverse=True)

    queue = []
    for ticket in live:
        summary = disk_by_id.get(ticket.id)
        queue.append(
            InboxRow(
                ticket_id=ticket.id,
                segment="queue",
                triaged=summary is not None,
                summary=summary,
                ticket=ticket,
                when=ticket.updated_at,
                subject=ticket.subject or None,
            )
        )

    return worked, queue


def humanize_when(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "—"

    seconds = max(0, int((_as_utc(now) - _as_utc(dt)).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 60 * 60:
        return f"{seconds // 60}m ago"
    if seconds < 24 * 60 * 60:
        return f"{seconds // (60 * 60)}h ago"
    return f"{seconds // (24 * 60 * 60)}d ago"


def render_summary(summary: InboxSummary, *, shipped_version: str | None) -> str:
    state_version = _display(summary.rubric_version)
    shipped = _display(shipped_version)
    lines: list[str] = []

    if summary.rubric_version != shipped_version:
        lines.append(f"⚠ Rubric version mismatch: state={state_version}, shipped={shipped}")
        lines.append("")

    lines.extend(
        [
            f"Ticket: ZD-{summary.ticket_id}",
            (
                f"Fork: {_display(summary.fork)} · "
                f"Confidence: {_display(summary.confidence)} · "
                f"Status: {_display(summary.status)}"
            ),
            f"Owner: {_display(summary.owner)}",
            "",
            "Quoted rubric row:",
            (
                f'  "{summary.quoted_rubric_row}"'
                if summary.quoted_rubric_row
                else "  (none)"
            ),
            f"  rubric_version on STATE.md: {state_version}",
            f"  shipped rubric_version:     {shipped}",
            "",
            "Related:",
            f"  Zendesk: {_format_zendesk(summary.related_zendesk)}",
            f"  Jira:    {_format_jira(summary.related_jira)}",
            f"  Master:  {_format_master(summary.master)}",
            f"  Cluster: {_display(summary.cluster)}",
        ]
    )
    return "\n".join(lines)


def render_activity(ticket: Ticket) -> str:
    lines = [
        f"Ticket: ZD-{ticket.id}",
        f"Subject: {_display(ticket.subject)}",
        f"Requester: {_display(ticket.requester_email)}",
        f"Organization: {_display(ticket.requester_org)}",
        f"Status: {_display(ticket.status)}",
        "",
        "Not yet investigated — press [i] to investigate.",
        "",
        "Latest comments:",
    ]

    comments = sorted(
        ticket.comments,
        key=lambda comment: _as_utc(comment.created_at),
        reverse=True,
    )
    if not comments:
        lines.append("  (none)")
        return "\n".join(lines)

    for comment in comments:
        visibility = "public" if comment.public else "internal"
        author = f"author #{comment.author_id}" if comment.author_id is not None else "author unknown"
        lines.append(
            f"  [{visibility}] {_format_timestamp(comment.created_at)} "
            f"{author}: {_truncate_one_line(comment.body)}"
        )

    return "\n".join(lines)


def _as_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _display(value: object | None) -> str:
    if value is None or value == "":
        return "(none)"
    return str(value)


def _format_zendesk(ticket_ids: list[int]) -> str:
    if not ticket_ids:
        return "(none)"
    return ", ".join(f"#{ticket_id}" for ticket_id in ticket_ids)


def _format_jira(keys: list[str]) -> str:
    if not keys:
        return "(none)"
    return ", ".join(keys)


def _format_master(master: int | None) -> str:
    if master is None:
        return "(none)"
    return f"#{master}"


def _format_timestamp(dt: datetime | None) -> str:
    if dt is None:
        return "unknown time"
    return _as_utc(dt).strftime("%Y-%m-%d %H:%M UTC")


def _truncate_one_line(body: str, *, max_len: int = 96) -> str:
    one_line = " ".join(body.split())
    if not one_line:
        return "(none)"
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3].rstrip() + "..."
