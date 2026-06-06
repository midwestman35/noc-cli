from __future__ import annotations

import os
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Input, Static

from noc_cli import __version__
from noc_cli.config import Config
from noc_cli.tui.chat import ChatSession, build_sdk_client_factory
from noc_cli.tui.command import KNOWN_COMMANDS, ParsedCommand, parse_input
from noc_cli.models import Comment, Ticket
from noc_cli.rubric import load_rubric
from noc_cli.watch.diff import ChangeEvent, ChangeKind, _iso, _latest_public_comment, diff_tickets
from noc_cli.watch.disk_scan import scan_investigations
from noc_cli.watch.inbox import (
    INVESTIGATE_PHASES,
    InboxRow,
    InboxSummary,
    build_segments,
    detect_phase,
    humanize_when,
    render_activity,
    render_comments,
    render_summary,
    render_ticket_header,
    resolve_display_tz,
)
from noc_cli.watch.notify import Notifier
from noc_cli.watch.poller import poll_view
from noc_cli.watch.state import TicketSnapshot, WatchState

_BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

_DETAIL_MODES = [
    "Summary",
    "Chat",
    "INTAKE.md",
    "EVIDENCE_PREFLIGHT.md",
    "FORK_PACKET.md",
    "DRAFTS.md",
    "STATE.md",
]

# Per-file taglines shown under the permanent header so each tab names not just
# the file but what question it answers.
_TAB_TAGLINES = {
    "Chat": "about this ticket",
    "INTAKE.md": "What are we looking at?",
    "EVIDENCE_PREFLIGHT.md": "Do we have proof?",
    "FORK_PACKET.md": "The decision",
    "DRAFTS.md": "Ready-to-paste comms",
    "STATE.md": "Persisted state + soft-lock",
}

_CSS = """
Screen { layout: vertical; layers: base overlay; }
#splash {
    layer: overlay;
    width: 100%;
    height: 100%;
    content-align: center middle;
    text-align: center;
    background: $surface;
    color: $text;
}
#banner {
    height: 1;
    background: $surface-darken-1;
    color: $text;
    text-style: bold;
    padding: 0 1;
}
#notification {
    height: 1;
    background: $surface;
    color: $text-muted;
    padding: 0 1;
}
#notification.active { color: $warning; }
#body {
    height: 1fr;
    layout: horizontal;
}
#ticket-list {
    width: 45%;
    height: 1fr;
    border: solid $accent;
    padding: 0 1;
}
#ticket-list:focus { border: heavy $accent; }
#detail-pane {
    width: 55%;
    height: 1fr;
    layout: vertical;
    border: solid $accent;
    padding: 0 1;
}
#detail-pane:focus-within { border: heavy $accent; }
#detail-header {
    height: auto;
    text-style: bold;
}
#detail-tablabel {
    height: 1;
    color: $text-muted;
}
#detail {
    width: 1fr;
    height: 1fr;
}
#detail-content {
    width: 1fr;
    height: auto;
}
#command {
    height: 3;
    border: round $accent;
}
"""


def _display(value: object | None) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


# Status → Rich style, so the queue is scannable by color at a glance.
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


class ShowBanner(Message):
    """Posted when a change event should display the in-TUI notification line."""

    def __init__(self, event: ChangeEvent) -> None:
        super().__init__()
        self.event = event


class PollComplete(Message):
    def __init__(
        self,
        tickets: list[Ticket] | None,
        events: list[ChangeEvent],
        seeds: dict[int, TicketSnapshot],
        snapshots: dict[int, TicketSnapshot],
        error: str | None = None,
    ) -> None:
        super().__init__()
        self.tickets = tickets
        self.events = events
        self.seeds = seeds
        self.snapshots = snapshots
        self.error = error


class TicketList(Static, can_focus=True):
    """Segmented, read-only ticket list with one logical cursor."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("markup", False)
        super().__init__("", *args, **kwargs)
        self._rows: list[InboxRow] = []
        self._worked_count = 0
        self._queue_count = 0
        self._now = datetime.now(tz=timezone.utc)
        self.cursor_index = 0
        self.rendered_text = ""
        # Session-scoped decorations: ticket ids with an unseen update (the `!`
        # badge) and ids currently mid-pulse. Kept in-memory only.
        self._unread_ids: set[int] = set()
        self._pulse_ids: set[int] = set()

    @property
    def rows(self) -> list[InboxRow]:
        return list(self._rows)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    @property
    def selected_row(self) -> InboxRow | None:
        if not self._rows:
            return None
        if self.cursor_index < 0 or self.cursor_index >= len(self._rows):
            return None
        return self._rows[self.cursor_index]

    @property
    def selected_ticket_id(self) -> int | None:
        row = self.selected_row
        return row.ticket_id if row else None

    def set_segments(
        self,
        worked: list[InboxRow],
        queue: list[InboxRow],
        *,
        now: datetime,
        preferred_ticket_id: int | None,
        unread_ids: set[int] | None = None,
        pulse_ids: set[int] | None = None,
    ) -> bool:
        previous_id = self.selected_ticket_id
        if preferred_ticket_id is None:
            preferred_ticket_id = previous_id

        self._rows = [*worked, *queue]
        self._worked_count = len(worked)
        self._queue_count = len(queue)
        self._now = now
        if unread_ids is not None:
            self._unread_ids = set(unread_ids)
        if pulse_ids is not None:
            self._pulse_ids = set(pulse_ids)

        if not self._rows:
            self.cursor_index = 0
        elif preferred_ticket_id is not None and any(
            row.ticket_id == preferred_ticket_id for row in self._rows
        ):
            self.cursor_index = next(
                index
                for index, row in enumerate(self._rows)
                if row.ticket_id == preferred_ticket_id
            )
        else:
            self.cursor_index = min(self.cursor_index, len(self._rows) - 1)

        self._render_rows()
        return previous_id != self.selected_ticket_id

    def update_decorations(
        self, unread_ids: set[int], pulse_ids: set[int]
    ) -> None:
        """Repaint badge/pulse state without re-scanning disk or live tickets.

        Used for pulse expiry and clear-on-select, where the rows themselves are
        unchanged but their `!`/pulse styling needs to refresh.
        """
        self._unread_ids = set(unread_ids)
        self._pulse_ids = set(pulse_ids)
        if self._rows:
            self._render_rows()

    def move_up(self) -> bool:
        if not self._rows or self.cursor_index <= 0:
            return False
        self.cursor_index -= 1
        self._render_rows()
        return True

    def move_down(self) -> bool:
        if not self._rows or self.cursor_index >= len(self._rows) - 1:
            return False
        self.cursor_index += 1
        self._render_rows()
        return True

    # Each row is two logical lines: a title line ("#id subject", which wraps to
    # the pane width) and an indented metadata line (age). The colored status
    # sits just before the ✓/○ icon, padded to a fixed width so every title
    # starts at the same column. The meta line is indented to align under the
    # title, past the
    # "▸ "(2) + "! "(2) + status(_STATUS_W) + "✓ "(2) prefix.
    _STATUS_W = 8
    _META_INDENT = " " * (2 + 2 + _STATUS_W + 2)

    def _render_rows(self) -> None:
        text = Text()
        text.append("Recently worked (3d)\n", style="bold")
        self._append_segment(text, 0, self._worked_count)
        text.append("\n")
        text.append("My queue", style="bold")
        if self._queue_count:
            text.append(f" · {self._queue_count}", style="dim")
        text.append("\n")
        self._append_segment(text, self._worked_count, self._queue_count)
        self.rendered_text = text.plain
        self.update(text)

    def _append_segment(self, text: Text, start: int, count: int) -> None:
        if count == 0:
            text.append("  (none)\n", style="dim")
            return
        for index in range(start, start + count):
            self._append_row(text, index, self._rows[index])
            text.append("\n")

    def _append_row(self, text: Text, index: int, row: InboxRow) -> None:
        selected = index == self.cursor_index
        unread = row.ticket_id in self._unread_ids
        pulsing = row.ticket_id in self._pulse_ids
        icon, icon_style = ("✓", "green") if row.triaged else ("○", "dim")
        status = self._row_status(row)
        age = humanize_when(row.when, self._now)
        subject = (row.subject or "").strip()
        # Ticket number folded into the title so the row reads like a subject
        # line; the standalone #id column is gone. The full subject wraps to the
        # pane width rather than truncating.
        title = f"#{row.ticket_id} {subject}".rstrip()

        line = Text()
        line.append("▸ " if selected else "  ")
        # Persistent "new update" badge — stays until the row is opened. Kept in
        # text.plain (not just styling) so tests can assert on it.
        line.append("! " if unread else "  ", style="bold yellow" if unread else "")
        # Status leads the title (color-coded, padded so titles align).
        line.append(f"{status:<{self._STATUS_W}}", style=_status_style(status))
        line.append(f"{icon} ", style=icon_style)
        line.append(title, style="bold" if selected else "")
        line.append("\n")
        line.append(self._META_INDENT)
        line.append(age, style="dim")
        # Selected styling wins; otherwise a mid-pulse row flashes, and an unread
        # row gets a subtle steady tint so it reads as "needs a look".
        if selected:
            line.stylize("on grey23")
        elif pulsing:
            line.stylize("on yellow")
        elif unread:
            line.stylize("on grey15")
        text.append_text(line)

    def _row_status(self, row: InboxRow) -> str:
        if row.ticket is not None:
            return row.ticket.status or "—"
        if row.summary is not None:
            return _display(row.summary.status)
        return "—"

    def on_resize(self, event) -> None:
        # Reflow the subject column to the new pane width.
        if self._rows:
            self._render_rows()


class DetailPane(VerticalScroll, can_focus=True):
    pass


class WatchApp(App[None]):
    """Two-pane Zendesk queue watcher TUI (display-only; no Agent SDK)."""

    CSS = _CSS
    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("tab", "next_detail_file", "Next view", show=True, priority=True),
        Binding("shift+tab", "previous_detail_file", "Prev view", show=True, priority=True),
        Binding("escape", "interrupt", "Interrupt", show=True, priority=True),
        Binding("pageup", "scroll_detail_up", "Scroll up", show=False, priority=True),
        Binding("pagedown", "scroll_detail_down", "Scroll down", show=False, priority=True),
        Binding("ctrl+c", "quit", "Quit", show=True, priority=True),
    ]

    _spinner_frame: reactive[int] = reactive(0)
    _polling: reactive[bool] = reactive(False)
    _last_poll: reactive[str] = reactive("never")

    def __init__(
        self,
        *,
        config: Config,
        client,
        watch_state: WatchState,
        notifier: Notifier,
        poll_interval: int = 60,
    ) -> None:
        super().__init__()
        self._config = config
        self._client = client
        self._watch_state = watch_state
        self._notifier = notifier
        self._poll_interval = poll_interval
        self._current_tickets: list[Ticket] = []
        self._banner_timer = None
        self._notification_timer = None
        self._detail_index = 0
        self._row_count = 0
        self._current_detail_text = ""
        self._shipped_rubric_version = load_rubric().version
        # "My queue" = tickets assigned to a single user. We resolve the
        # configured assignee email (or, by default, the logged-in user) to a
        # Zendesk user id exactly once, then filter every poll on that id.
        self._assignee_id: int | None = None
        self._assignee_resolved = False
        # Inline investigate state (replaces the old modal). Only one runs at a
        # time; we gate concurrency on `_investigating_id` rather than a worker
        # group so the poll worker can keep refreshing the queue alongside it.
        self._investigating_id: int | None = None
        self._investigate_lines: list[str] = []
        self._investigate_phases: dict[str, bool] = {}
        self._investigate_error: tuple[int, str] | None = None
        # Per-ticket chat: one resumable ChatSession per ticket id, its rendered
        # output buffer, and the id currently mid-turn (gates the spinner).
        self._chat_sessions: dict[int, ChatSession] = {}
        self._chat_lines: dict[int, list[str]] = {}
        self._chatting_id: int | None = None
        # ticket folder -> no-arg client factory. Overridable in tests.
        self._chat_client_factory = build_sdk_client_factory
        # New-update decorations (session-only): ids with an unseen change, and
        # per-id pulse deadlines (monotonic seconds).
        self._unread_ids: set[int] = set()
        self._pulse_until: dict[int, float] = {}
        self._pulse_timer = None
        # Toggles each pulse repaint so the highlight blinks a few times before
        # settling to just the steady `!` badge.
        self._pulse_phase = False
        self._splash_dismissed = False

    @property
    def selected_row(self) -> InboxRow | None:
        try:
            return self.query_one("#ticket-list", TicketList).selected_row
        except NoMatches:
            return None

    @property
    def current_detail_text(self) -> str:
        return self._current_detail_text

    def compose(self) -> ComposeResult:
        yield Static("", id="banner", markup=False)
        yield Static("", id="notification", markup=False)
        with Horizontal(id="body"):
            yield TicketList(id="ticket-list")
            with Vertical(id="detail-pane"):
                # Permanent header + descriptive tab label sit outside the
                # scroll so they stay put on every tab and during investigate.
                yield Static("", id="detail-header", markup=False)
                yield Static("", id="detail-tablabel", markup=False)
                with DetailPane(id="detail"):
                    yield Static("", id="detail-content", markup=False)
        yield Input(placeholder="ask about the selected ticket, or /investigate /scout /help…", id="command")
        yield Footer()
        yield Static(self._splash_text(), id="splash", markup=False)

    def on_mount(self) -> None:
        self._update_banner()
        self._refresh_detail()
        self.query_one("#command", Input).focus()
        if self._poll_interval < 9999:
            self.set_interval(self._poll_interval, self.action_poll_now)
            self.action_poll_now()
        self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        busy = self._polling or self._investigating_id is not None or self._chatting_id is not None
        if busy:
            self._spinner_frame = (self._spinner_frame + 1) % len(_BRAILLE)
            if self._polling:
                self._update_banner()
            if self._selected_is_investigating() or (
                self._chatting_id is not None
                and self.selected_row is not None
                and self.selected_row.ticket_id == self._chatting_id
            ):
                self._refresh_detail()

    def _tickets_root(self) -> Path:
        override = os.environ.get("NOC_TICKETS_ROOT")
        if override:
            return Path(override).expanduser()
        return Path(self._config.tickets_root).expanduser()

    def _scan_disk(self) -> list[InboxSummary]:
        return scan_investigations(self._tickets_root())

    def _update_banner(self) -> None:
        try:
            banner = self.query_one("#banner", Static)
        except NoMatches:
            return

        label = "ticket" if self._row_count == 1 else "tickets"
        parts = [
            f"noc-cli v{__version__}",
            "my tickets",
            f"{self._row_count} {label}",
            f"last poll {self._last_poll}",
        ]
        if self._polling:
            parts.append(f"{_BRAILLE[self._spinner_frame]} polling...")
        banner.update(" · ".join(parts))

    def _splash_text(self) -> str:
        from noc_cli.branding import TAGLINE

        return f"noc-cli\n\n{TAGLINE}\nv{__version__}\n\n⠹ Loading my tickets …"

    def _resolve_assignee_id(self) -> int | None:
        """Resolve the "my queue" assignee to a Zendesk user id, once.

        Defaults to the logged-in user (``zendesk_email``); ``watch_assignee``
        overrides it to watch someone else's queue. On any lookup failure we
        return ``None`` so the poll falls back to the whole view instead of an
        empty list.
        """
        from noc_cli.zendesk import ZendeskError

        if self._assignee_resolved:
            return self._assignee_id
        email = (self._config.watch_assignee or self._config.zendesk_email or "").strip()
        try:
            self._assignee_id = self._client.find_user_id(email) if email else None
        except ZendeskError:
            self._assignee_id = None
        self._assignee_resolved = True
        return self._assignee_id

    @work(exclusive=True, thread=True)
    def _run_poll(self, last_seen: dict[int, TicketSnapshot]) -> None:
        from noc_cli.zendesk import ZendeskError

        try:
            assignee_id = self._resolve_assignee_id()
            tickets = poll_view(self._client, self._config.watch_view, assignee_id)
            comments_map: dict[int, list[Comment]] = {}
            for ticket in tickets:
                try:
                    comments = self._client.get_comments(ticket.id)
                except ZendeskError:
                    comments = []
                ticket.comments = comments
                comments_map[ticket.id] = comments

            events, seeds = diff_tickets(tickets, comments_map, last_seen, return_seeds=True)
            snapshots: dict[int, TicketSnapshot] = {}
            for ticket in tickets:
                latest = _latest_public_comment(comments_map.get(ticket.id, []))
                snapshots[ticket.id] = TicketSnapshot(
                    status=ticket.status,
                    last_comment_at=_iso(latest.created_at if latest else None),
                )
            self.post_message(PollComplete(tickets, events, seeds, snapshots))
        except ZendeskError as exc:
            self.post_message(PollComplete(None, [], {}, {}, error=str(exc)))

    def action_poll_now(self) -> None:
        self._polling = True
        self._update_banner()
        last_seen = self._watch_state.load_all()
        self._run_poll(last_seen)

    def on_poll_complete(self, message: PollComplete) -> None:
        if not self._splash_dismissed:
            self._splash_dismissed = True
            for node in self.query("#splash"):
                node.remove()
        self._polling = False
        self._last_poll = (
            datetime.now(tz=timezone.utc)
            .astimezone(resolve_display_tz(self._config.timezone))
            .strftime("%I:%M:%S %p")
            .lstrip("0")
        )

        if message.error:
            self._set_notification(f"Poll error: {message.error[:120]}", sticky=True)
            self._rebuild_rows(self._current_tickets)
            return

        for tid, snap in message.seeds.items():
            self._watch_state.seed_if_absent(tid, snap)
        for tid, snap in message.snapshots.items():
            self._watch_state.save(tid, snap)
        selected_id = self.query_one("#ticket-list", TicketList).selected_ticket_id
        now_mono = time.monotonic()
        for evt in message.events:
            self._notifier.notify(evt)
            self.post_message(ShowBanner(event=evt))
            self._unread_ids.add(evt.ticket_id)
            self._pulse_until[evt.ticket_id] = now_mono + 2.5
        # Don't flag the row the agent is already looking at.
        if selected_id is not None:
            self._unread_ids.discard(selected_id)

        self._current_tickets = message.tickets or []
        self._rebuild_rows(self._current_tickets)
        self._schedule_pulse_clear()

    def _rebuild_rows(self, live_tickets: list[Ticket]) -> None:
        ticket_list = self.query_one("#ticket-list", TicketList)
        previous_id = ticket_list.selected_ticket_id
        now = datetime.now(tz=timezone.utc)
        worked, queue = build_segments(self._scan_disk(), live_tickets, now=now, window_days=3)
        selection_changed = ticket_list.set_segments(
            worked,
            queue,
            now=now,
            preferred_ticket_id=previous_id,
            unread_ids=self._unread_ids,
            pulse_ids=self._current_pulse_ids(),
        )
        self._row_count = ticket_list.row_count
        self._update_banner()
        self._refresh_detail(reset_mode=selection_changed)

    def _refresh_detail(self, *, reset_mode: bool = False) -> None:
        if reset_mode:
            self._detail_index = 0

        # The permanent header tracks the selected ticket on every path —
        # including mid-investigate and the error state.
        self._update_detail_header()

        # Live investigation takes over the detail pane while it runs; navigate
        # away and the gate falls through to the normal detail, back and the
        # live panel re-renders from the buffer.
        if self._selected_is_investigating():
            self._set_detail_text(self._render_investigate_panel())
            self._update_tablabel()
            return

        row = self.selected_row
        if (
            self._investigate_error is not None
            and row is not None
            and row.ticket_id == self._investigate_error[0]
        ):
            rc, msg = self._investigate_error
            self._set_detail_text(
                f"✗ Investigation of #{row.ticket_id} failed ({msg}).\n\n"
                "Press [i] to retry, or check the terminal for details."
            )
            self._update_tablabel()
            return

        if row is None:
            self._set_detail_text("No tickets to display.")
            self._update_tablabel()
            return

        # Summary tab (index 0): triage outcome when investigated, otherwise the
        # live activity fields — then the color-coded comment thread underneath.
        if self._detail_index == 0:
            text = Text()
            if row.summary is not None:
                text.append(
                    render_summary(
                        row.summary, shipped_version=self._shipped_rubric_version
                    )
                )
            elif row.ticket is not None:
                text.append(render_activity(row.ticket, tz=self._config.timezone))
            else:
                text.append(f"Ticket: ZD-{row.ticket_id}\n\nNo live activity available.")
            # Comments are live: poll_view re-fetches ticket.comments each cycle
            # and _rebuild_rows calls back into here, so a new comment repaints
            # the thread (newest at the bottom) and the header status with no
            # extra wiring.
            if row.ticket is not None:
                text.append("\n\n")
                text.append_text(render_comments(row.ticket, tz=self._config.timezone))
            self._set_detail_text(text)
            self._update_tablabel()
            return

        if _DETAIL_MODES[self._detail_index] == "Chat":
            self._set_detail_text(self._render_chat_panel())
            self._update_tablabel()
            return

        # File tabs (2–6): raw file text, untouched. Only reachable on triaged
        # rows (the tab actions guard on row.summary), but fall back defensively.
        if row.summary is not None:
            self._set_detail_text(
                self._read_detail_file(row.summary, _DETAIL_MODES[self._detail_index])
            )
            self._update_tablabel()
            return

        self._detail_index = 0
        self._refresh_detail()

    def _update_detail_header(self) -> None:
        """Repaint the permanent ``ZD-… · subject · status`` header."""
        try:
            header = self.query_one("#detail-header", Static)
        except NoMatches:
            return

        ticket_id: int | None = None
        subject: str | None = None
        status: str | None = None

        row = self.selected_row
        if row is not None:
            ticket_id = row.ticket_id
            if row.ticket is not None:
                subject = row.ticket.subject or None
                status = row.ticket.status or None
            elif row.summary is not None:
                # Disk-only worked rows have no live subject (row.subject is None).
                subject = row.subject
                status = row.summary.status
        elif self._investigating_id is not None:
            ticket_id = self._investigating_id
            ticket = next(
                (t for t in self._current_tickets if t.id == self._investigating_id),
                None,
            )
            if ticket is not None:
                subject = ticket.subject or None
                status = ticket.status or None

        if ticket_id is None:
            header.update("No ticket selected")
            return
        header.update(
            render_ticket_header(ticket_id=ticket_id, subject=subject, status=status)
        )

    def _update_tablabel(self) -> None:
        """Repaint the descriptive tab label naming the current view + purpose."""
        try:
            label = self.query_one("#detail-tablabel", Static)
        except NoMatches:
            return

        if self._selected_is_investigating():
            label.update("Live investigation")
            return

        row = self.selected_row
        if row is None:
            label.update("")
            return

        if self._detail_index == 0:
            if row.summary is not None:
                label.update("Summary — triage outcome")
            else:
                label.update("Activity — latest from the requester")
            return

        name = _DETAIL_MODES[self._detail_index]
        tagline = _TAB_TAGLINES.get(name, "")
        label.update(f"{name} — {tagline}" if tagline else name)

    def _read_detail_file(self, summary: InboxSummary, filename: str) -> str:
        if summary.folder is None:
            return f"({filename} not generated)"
        path = summary.folder / filename
        try:
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pass
        return f"({filename} not generated)"

    def _set_detail_text(self, content: str | Text) -> None:
        # A Rich Text can carry colors (e.g. the comment thread); we display it
        # styled but keep `.plain` as the copy text so the y-copy contract and
        # substring assertions stay on plain text.
        if isinstance(content, Text):
            self._current_detail_text = content.plain
        else:
            self._current_detail_text = content
        try:
            self.query_one("#detail-content", Static).update(content)
        except NoMatches:
            pass

    def _set_notification(self, text: str, *, sticky: bool = False) -> None:
        try:
            notification = self.query_one("#notification", Static)
        except NoMatches:
            return
        notification.update(text)
        notification.add_class("active")

        if self._notification_timer is not None:
            self._notification_timer.stop()
            self._notification_timer = None
        if not sticky:
            self._notification_timer = self.set_timer(4.0, self._clear_notification)

    def _clear_notification(self) -> None:
        try:
            notification = self.query_one("#notification", Static)
            notification.update("")
            notification.remove_class("active")
        except NoMatches:
            pass

    def on_show_banner(self, message: ShowBanner) -> None:
        evt = message.event
        if evt.customer_replied and evt.old_status == "pending":
            text = f"Customer replied on #{evt.ticket_id}: {evt.ticket_subject}"
        elif evt.kind == ChangeKind.STATUS_CHANGED:
            text = f"#{evt.ticket_id} status: {evt.old_status} -> {evt.new_status}"
        else:
            text = f"New comment on #{evt.ticket_id}: {evt.ticket_subject}"
        self._set_notification(text)

        if self._banner_timer is not None:
            self._banner_timer.stop()
        self._banner_timer = self.set_timer(4.0, self._clear_notification)

    def action_cursor_up(self) -> None:
        moved = self.query_one("#ticket-list", TicketList).move_up()
        if moved:
            self._detail_index = 0
            self._acknowledge_selection()
            self._refresh_detail()

    def action_cursor_down(self) -> None:
        moved = self.query_one("#ticket-list", TicketList).move_down()
        if moved:
            self._detail_index = 0
            self._acknowledge_selection()
            self._refresh_detail()

    def _acknowledge_selection(self) -> None:
        """Clear the `!` badge on the row the cursor just landed on."""
        ticket_list = self.query_one("#ticket-list", TicketList)
        selected_id = ticket_list.selected_ticket_id
        if selected_id is not None and selected_id in self._unread_ids:
            self._unread_ids.discard(selected_id)
            self._repaint_decorations()

    def _repaint_decorations(self) -> None:
        try:
            ticket_list = self.query_one("#ticket-list", TicketList)
        except NoMatches:
            return
        ticket_list.update_decorations(self._unread_ids, self._current_pulse_ids())

    def _current_pulse_ids(self) -> set[int]:
        """Ids whose pulse window has not yet elapsed (phase-independent)."""
        now_mono = time.monotonic()
        return {tid for tid, deadline in self._pulse_until.items() if deadline > now_mono}

    def _schedule_pulse_clear(self) -> None:
        # Tradeoff: the whole list is one Static / one Text, so a pulse is a
        # binary on/off tint toggled by this timer's re-renders — not a smooth
        # per-row fade. A true tween would require converting each row to its own
        # widget (a large rendering refactor), which we've deferred.
        if self._pulse_timer is not None:
            self._pulse_timer.stop()
            self._pulse_timer = None
        if not self._current_pulse_ids():
            return
        # Blink on a short repeating tick so the highlight flashes a few times,
        # then `_expire_pulses` stops it and the steady `!` badge remains.
        self._pulse_timer = self.set_interval(0.4, self._expire_pulses)

    def _expire_pulses(self) -> None:
        live = self._current_pulse_ids()
        # Drop anything past its deadline so the dict doesn't grow unbounded.
        for tid in [tid for tid in self._pulse_until if tid not in live]:
            self._pulse_until.pop(tid, None)
        if not live:
            if self._pulse_timer is not None:
                self._pulse_timer.stop()
                self._pulse_timer = None
            self._pulse_phase = False
            self._repaint_decorations()
            return
        # Toggle the flash: on even ticks show the pulse tint, on odd ticks show
        # only the steady badge — a few blinks before the window elapses.
        self._pulse_phase = not self._pulse_phase
        ticket_list = self.query_one("#ticket-list", TicketList)
        ticket_list.update_decorations(
            self._unread_ids, live if self._pulse_phase else set()
        )

    def action_focus_detail(self) -> None:
        self.query_one("#detail", DetailPane).focus()

    def action_investigate(self) -> None:
        row = self.selected_row
        if row is None:
            return
        if self._investigating_id is not None:
            self._set_notification("An investigation is already running.")
            return
        tid = row.ticket_id
        self._investigating_id = tid
        self._investigate_lines = []
        self._investigate_phases = {label: False for _sub, label in INVESTIGATE_PHASES}
        self._investigate_error = None
        # Looking at it now clears any pending badge.
        self._unread_ids.discard(tid)
        self._refresh_detail()
        self._run_investigate(tid)

    @work(thread=True, exclusive=False)
    def _run_investigate(self, ticket_id: int) -> None:
        """Run the investigate pipeline in-process (thread worker). Streams
        progress lines into the detail pane via _investigate_on_line."""
        import asyncio

        from noc_cli.investigate import InvestigationError, run_investigation

        owner = os.environ.get("NOC_OWNER", getattr(self._config, "owner", ""))

        def emit(line: str) -> None:
            self.app.call_from_thread(self._investigate_on_line, ticket_id, line)

        try:
            asyncio.run(
                run_investigation(
                    ticket_id=ticket_id,
                    config=self._config,
                    tickets_root=self._tickets_root(),
                    owner=owner,
                    on_line=emit,
                )
            )
        except InvestigationError:
            self.app.call_from_thread(self._investigate_finished, ticket_id, 1)
            return
        except Exception as exc:  # pragma: no cover
            self.app.call_from_thread(self._investigate_failed, ticket_id, str(exc))
            return
        self.app.call_from_thread(self._investigate_finished, ticket_id, 0)

    def _investigate_on_line(self, ticket_id: int, line: str) -> None:
        if self._investigating_id != ticket_id:
            return
        self._investigate_lines.append(line)
        label = detect_phase(line)
        if label is not None and label in self._investigate_phases:
            self._investigate_phases[label] = True
        if self._selected_is_investigating():
            self._refresh_detail()

    def _investigate_finished(self, ticket_id: int, rc: int) -> None:
        self._investigating_id = None
        if rc == 0:
            # Refresh flips the row to ✓; cursor-by-id is preserved, so the
            # subsequent detail render shows the freshly written report.
            self.action_poll_now()
        else:
            self._investigate_error = (ticket_id, f"exit {rc}")
            self._refresh_detail()

    def _investigate_failed(self, ticket_id: int, msg: str) -> None:
        self._investigating_id = None
        self._investigate_error = (ticket_id, msg)
        self._refresh_detail()

    def _selected_is_investigating(self) -> bool:
        if self._investigating_id is None:
            return False
        row = self.selected_row
        return row is not None and row.ticket_id == self._investigating_id

    def _render_investigate_panel(self) -> str:
        """Plain-text in-progress panel (preserves the y-copy contract)."""
        frame = _BRAILLE[self._spinner_frame]
        lines = [f"{frame} Investigating #{self._investigating_id} …", ""]
        for _sub, label in INVESTIGATE_PHASES:
            done = self._investigate_phases.get(label, False)
            lines.append(f"  {'✓' if done else '•'} {label}")
        lines.append("")
        lines.append("Output:")
        for raw in self._investigate_lines[-200:]:
            lines.append(f"  {raw}")
        return "\n".join(lines)

    def _render_chat_panel(self) -> str:
        row = self.selected_row
        if row is None:
            return "No ticket selected."
        lines = [f"Chat · ZD-{row.ticket_id}", ""]
        body = self._chat_lines.get(row.ticket_id, [])
        if not body:
            return "\n".join(lines + ["Type a message in the box to start."])
        lines.extend(body[-500:])
        if self._chatting_id == row.ticket_id:
            frame = _BRAILLE[self._spinner_frame]
            lines.append(f"{frame} …")
        return "\n".join(lines)

    def _submit_chat_turn(self, text: str) -> None:
        row = self.selected_row
        if row is None:
            self._set_notification("Select a ticket to chat about.")
            return
        if self._chatting_id is not None:
            self._set_notification("Chat turn in progress — wait for it to finish.")
            return
        ticket_id = row.ticket_id
        self._detail_index = _DETAIL_MODES.index("Chat")
        self._run_chat(ticket_id, text)

    def _ensure_chat_session(self, ticket_id: int) -> ChatSession:
        session = self._chat_sessions.get(ticket_id)
        if session is None:
            from noc_cli.scaffold import scaffold_ticket

            folder = scaffold_ticket(self._tickets_root(), ticket_id)
            session = ChatSession(
                ticket_id=ticket_id,
                folder=folder.root,
                client_factory=self._chat_client_factory(folder.root),
            )
            self._chat_sessions[ticket_id] = session
        return session

    @work(thread=False, exclusive=False)
    async def _run_chat(self, ticket_id: int, text: str) -> None:
        self._chatting_id = ticket_id
        self._refresh_detail()
        session = self._ensure_chat_session(ticket_id)
        try:
            async for line in session.send(text):
                self._chat_on_line(ticket_id, line)
        except Exception as exc:  # surface chat errors in the panel
            self._chat_on_line(ticket_id, f"✗ chat error: {exc}")
        finally:
            self._chatting_id = None
            self._refresh_detail()

    def _chat_on_line(self, ticket_id: int, line: str) -> None:
        self._chat_lines.setdefault(ticket_id, []).append(line)
        row = self.selected_row
        if (
            row is not None
            and row.ticket_id == ticket_id
            and _DETAIL_MODES[self._detail_index] == "Chat"
        ):
            self._refresh_detail()

    def action_next_detail_file(self) -> None:
        row = self.selected_row
        if row is None:
            return
        self._detail_index = (self._detail_index + 1) % len(_DETAIL_MODES)
        self._refresh_detail()

    def action_previous_detail_file(self) -> None:
        row = self.selected_row
        if row is None:
            return
        self._detail_index = (self._detail_index - 1) % len(_DETAIL_MODES)
        self._refresh_detail()

    def action_summary(self) -> None:
        self._detail_index = 0
        self._refresh_detail()
        try:
            self.query_one("#ticket-list", TicketList).focus()
        except NoMatches:
            pass

    def action_copy_current(self) -> None:
        copy = getattr(self, "copy_to_clipboard", None)
        if callable(copy) and self._current_detail_text:
            copy(self._current_detail_text)
            self._set_notification("Copied current detail.")

    def action_open_ticket(self) -> None:
        row = self.selected_row
        if row is None or not self._config.zendesk_subdomain:
            return
        webbrowser.open(
            f"https://{self._config.zendesk_subdomain}.zendesk.com/agent/tickets/{row.ticket_id}"
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command":
            return
        text = event.value
        event.input.value = ""
        parsed = parse_input(text)
        if not parsed.is_command and not parsed.args:
            return
        self._dispatch_command(parsed)

    def _dispatch_command(self, parsed: ParsedCommand) -> None:
        if not parsed.is_command:
            self._submit_chat_turn(parsed.args)
            return
        name = parsed.name
        if name == "refresh":
            self.action_poll_now()
        elif name == "open":
            self.action_open_ticket()
        elif name == "copy":
            self.action_copy_current()
        elif name == "quit":
            self.exit()  # App.exit() is the sync-safe quit (action_quit is a coroutine)
        elif name == "help":
            self._show_help()
        elif name == "investigate":
            self.action_investigate()
        elif name == "doctor":
            self._run_doctor()
        elif name == "scout":
            # PR #6 shipped the engine as the (now hidden/deprecated) CLI command;
            # an in-TUI Scout panel is a future task.
            self._set_notification("Scout runs from the CLI for now: `noc-cli scout`")
        elif name in ("file", "paste", "revise", "retry"):
            self._set_notification(f"/{name} works once chat lands (a later task).")
        else:
            self._set_notification(f"Unknown command /{name}; try /help")

    def action_interrupt(self) -> None:
        target = self._chatting_id
        if target is None:
            target = self.selected_row.ticket_id if self.selected_row else None
        session = self._chat_sessions.get(target) if target is not None else None
        if session is not None:
            self._interrupt_chat(session)
            self._set_notification("Interrupting…")
            return
        # Idle (no chat session to interrupt): clear the box.
        try:
            self.query_one("#command", Input).value = ""
        except NoMatches:
            pass

    @work(thread=False, exclusive=False)
    async def _interrupt_chat(self, session: ChatSession) -> None:
        try:
            await session.interrupt()
        except Exception:
            pass

    def action_scroll_detail_up(self) -> None:
        self.query_one("#detail", DetailPane).scroll_page_up()

    def action_scroll_detail_down(self) -> None:
        self.query_one("#detail", DetailPane).scroll_page_down()

    def _show_help(self) -> None:
        lines = ["Commands:"]
        for cmd, desc in KNOWN_COMMANDS.items():
            lines.append(f"  /{cmd:<12} {desc}")
        lines.append("")
        lines.append("Anything without a leading / is a chat turn about the selected ticket.")
        self._set_detail_text("\n".join(lines))

    def _run_doctor(self) -> None:
        from noc_cli.doctor import run_checks

        results = run_checks(self._config)
        lines = ["Doctor:"]
        for r in results:
            lines.append(self._format_doctor_row(r))
        self._set_detail_text("\n".join(lines))

    def _format_doctor_row(self, r) -> str:
        # CheckResult(ok: bool, label: str, message: str) — mirror print_report's
        # "{icon}  {label}: {message}" layout with a plain-text ✓/✗ icon.
        icon = "✓" if r.ok else "✗"
        return f"  {icon}  {r.label}: {r.message}"
