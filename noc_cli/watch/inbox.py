from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from rich.text import Text

from noc_cli.models import Ticket


# Ordered phase checklist for the inline `investigate` panel. Each entry is
# (substring, label): the substring is matched against a `mark_done` line from
# `cli.py` (see PhaseTracker.mark_done output); the label is shown in the TUI.
# Kept here (not in watch_app) so `detect_phase` stays a pure, unit-testable
# function with no Textual import. "complete" is special-cased (see below).
INVESTIGATE_PHASES: list[tuple[str, str]] = [
    ("Scaffold ready", "Scaffold ready"),
    ("fetched", "Ticket fetched"),
    ("Evidence gathered", "Evidence gathered"),
    ("PII redacted", "PII redacted"),
    ("History seeded", "History seeded"),
    ("Agent completed", "Agent completed"),
    ("Report rendered", "Report rendered"),
    ("complete", "Complete"),
]


def detect_phase(line: str) -> str | None:
    """Return the phase label a `mark_done` line completes, or None.

    Substring match against `cli.py`'s `mark_done` strings. "complete" is the
    final phase and is only matched when the line also names a ticket
    ("Ticket #…"), so the word "complete" appearing mid-stream elsewhere does
    not prematurely tick the last box.
    """
    for substring, label in INVESTIGATE_PHASES:
        if substring == "complete":
            if "complete" in line and "Ticket #" in line:
                return label
        elif substring in line:
            return label
    return None


def resolve_display_tz(pref: str | None) -> tzinfo | None:
    """Map a timezone preference to a tzinfo for display.

    - "local" (or blank) → ``None``, which makes ``astimezone(None)`` use the
      operating system's local zone — i.e. the agent's own computer time.
    - "utc" → UTC.
    - any IANA name (e.g. "America/New_York") → that zone, when the platform's
      tz database is available; otherwise we fall back to local rather than
      crash. (Arbitrary IANA names on Windows need the `tzdata` package.)
    """
    p = (pref or "local").strip()
    low = p.lower()
    if low in ("", "local"):
        return None
    if low == "utc":
        return timezone.utc
    try:
        return ZoneInfo(p)
    except Exception:
        return None


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


def render_activity(ticket: Ticket, *, tz: str = "local") -> str:
    # Ticket fields + the "investigate me" hint only. Comments are rendered
    # separately by `render_comments` so they can be color-coded by author role.
    lines = [
        f"Ticket: ZD-{ticket.id}",
        f"Subject: {_display(ticket.subject)}",
        f"Requester: {_display(ticket.requester_email)}",
        f"Organization: {_display(ticket.requester_org)}",
        f"Status: {_display(ticket.status)}",
        "",
        "Not yet investigated — press [i] to investigate.",
    ]
    return "\n".join(lines)


# Comment author roles → Rich styles, Zendesk-style:
#   internal note  → tan-yellow highlight (a private NOC note)
#   customer       → blue (the requester wrote it)
#   agent          → white/default (our public reply)
_COMMENT_STYLES = {
    "internal": "black on rgb(181,137,0)",
    "customer": "blue",
    "agent": "white",
}


def _comment_role(comment, requester_id: int | None) -> str:
    if not comment.public:
        return "internal"
    if requester_id is not None and comment.author_id == requester_id:
        return "customer"
    return "agent"


def render_comments(ticket: Ticket, *, tz: str = "local") -> Text:
    """Color-coded comment thread, oldest at top → newest at bottom.

    Returns a Rich ``Text`` so the pane can show author-role colors; the caller
    keeps ``.plain`` for the y-copy contract.
    """
    display_tz = resolve_display_tz(tz)
    text = Text()
    text.append("Latest comments:\n", style="bold")

    comments = sorted(
        ticket.comments,
        key=lambda comment: _as_utc(comment.created_at),
    )
    if not comments:
        text.append("  (none)")
        return text

    for index, comment in enumerate(comments):
        if index:
            text.append("\n")
        role = _comment_role(comment, ticket.requester_id)
        style = _COMMENT_STYLES[role]
        author = (
            f"author #{comment.author_id}"
            if comment.author_id is not None
            else "author unknown"
        )
        header = f"[{role}] {_format_timestamp(comment.created_at, display_tz)} {author}:"
        body = _truncate_block(comment.body)
        text.append(f"{header}\n{body}\n", style=style)

    return text


def render_ticket_header(
    *, ticket_id: int, subject: str | None, status: str | None
) -> Text:
    """Permanent right-pane header: ``ZD-<id> · <subject> · <status>``.

    Worked / disk-only rows pass ``subject=None`` (no live subject to show).
    """
    text = Text()
    text.append(f"ZD-{ticket_id}", style="bold")
    if subject:
        text.append(" · ", style="dim")
        text.append(subject.strip(), style="bold")
    if status:
        text.append(" · ", style="dim")
        text.append(status.strip(), style=_status_style(status))
    return text


# Status → Rich style, matching the queue list so the header is scannable by
# color. Kept here (not imported from watch_app) so this module stays free of
# any Textual dependency.
_STATUS_STYLES = {
    "new": "blue",
    "open": "cyan",
    "pending": "yellow",
    "hold": "magenta",
    "on-hold": "magenta",
    "solved": "green",
    "closed": "green",
}


def _status_style(status: str | None) -> str:
    return _STATUS_STYLES.get((status or "").strip().lower(), "white")


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


def _format_timestamp(dt: datetime | None, tz: tzinfo | None = None) -> str:
    if dt is None:
        return "unknown time"
    # tz=None → system local time (the agent's own computer). 12-hour clock with
    # AM/PM and no leading zero on the hour, e.g. "2026-06-04 5:15 AM".
    local = _as_utc(dt).astimezone(tz)
    hour = local.strftime("%I").lstrip("0") or "12"
    return local.strftime(f"%Y-%m-%d {hour}:%M %p")


def _truncate_block(body: str, *, max_len: int = 500) -> str:
    """Lightly trim a comment body for the pane, preserving its line breaks.

    Unlike the old one-line collapse, this keeps the multi-line shape so the
    Zendesk-style thread reads naturally; it only caps very long bodies.
    """
    text = (body or "").strip()
    if not text:
        return "(none)"
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."
