from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Static

from noc_cli.config import Config
from noc_cli.models import Comment, Ticket
from noc_cli.rubric import load_rubric
from noc_cli.watch.diff import ChangeEvent, ChangeKind, _iso, _latest_public_comment, diff_tickets
from noc_cli.watch.disk_scan import scan_investigations
from noc_cli.watch.inbox import (
    InboxRow,
    InboxSummary,
    build_segments,
    humanize_when,
    render_activity,
    render_summary,
)
from noc_cli.watch.notify import Notifier
from noc_cli.watch.poller import poll_view
from noc_cli.watch.state import TicketSnapshot, WatchState

_BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_DETAIL_MODES = [
    "Summary",
    "INTAKE.md",
    "EVIDENCE_PREFLIGHT.md",
    "FORK_PACKET.md",
    "DRAFTS.md",
    "STATE.md",
]

_CSS = """
Screen { layout: vertical; }
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
#detail {
    width: 55%;
    height: 1fr;
    border: solid $accent;
    padding: 0 1;
}
#detail:focus { border: heavy $accent; }
#detail-content {
    width: 1fr;
    height: auto;
}
"""


def _display(value: object | None) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


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
    ) -> bool:
        previous_id = self.selected_ticket_id
        if preferred_ticket_id is None:
            preferred_ticket_id = previous_id

        self._rows = [*worked, *queue]
        self._worked_count = len(worked)
        self._queue_count = len(queue)
        self._now = now

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

    def _render_rows(self) -> None:
        lines = [
            "Recently worked (3d)",
            *self._segment_lines(0, self._worked_count),
            "",
            "My queue",
            *self._segment_lines(self._worked_count, self._queue_count),
        ]
        self.rendered_text = "\n".join(lines)
        self.update(self.rendered_text)

    def _segment_lines(self, start: int, count: int) -> list[str]:
        if count == 0:
            return ["  (none)"]
        return [
            self._format_row(index, row)
            for index, row in enumerate(self._rows[start : start + count], start=start)
        ]

    def _format_row(self, index: int, row: InboxRow) -> str:
        selector = ">" if index == self.cursor_index else " "
        triage = "✓" if row.triaged else "○"
        fork = "in queue"
        confidence = "—"
        owner = "—"
        status = "—"

        if row.summary is not None:
            fork = _display(row.summary.fork)
            confidence = _display(row.summary.confidence)
            owner = _display(row.summary.owner)
            status = _display(row.summary.status)
        if row.ticket is not None:
            owner = _display(row.summary.owner if row.summary else row.ticket.assignee_email)
            status = _display(row.ticket.status)

        when = humanize_when(row.when, self._now)
        return (
            f"{selector} {triage} #{row.ticket_id:<7} "
            f"{fork:<8} {when:<8} {confidence:<12} {owner} / {status}"
        )


class DetailPane(VerticalScroll, can_focus=True):
    pass


class WatchApp(App[None]):
    """Two-pane Zendesk queue watcher TUI (display-only; no Agent SDK)."""

    CSS = _CSS
    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("k", "cursor_up", "Up", show=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("j", "cursor_down", "Down", show=True),
        Binding("enter", "focus_detail", "Detail", show=True),
        Binding("i", "investigate", "Investigate", show=True),
        Binding("tab", "next_detail_file", "Next file", show=True, priority=True),
        Binding("shift+tab", "previous_detail_file", "Prev file", show=True, priority=True),
        Binding("escape", "summary", "Summary", show=True, priority=True),
        Binding("r", "poll_now", "Refresh", show=True),
        Binding("y", "copy_current", "Copy", show=True),
        Binding("o", "open_ticket", "Open", show=True),
        Binding("q", "quit", "Quit", show=True),
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
            with DetailPane(id="detail"):
                yield Static("", id="detail-content", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._update_banner()
        self._refresh_detail()
        self.query_one("#ticket-list", TicketList).focus()
        if self._poll_interval < 9999:
            self.set_interval(self._poll_interval, self.action_poll_now)
            self.action_poll_now()
        self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._polling:
            self._spinner_frame = (self._spinner_frame + 1) % len(_BRAILLE)
            self._update_banner()

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
            "noc-cli watch",
            "my tickets",
            f"{self._row_count} {label}",
            f"last poll {self._last_poll}",
        ]
        if self._polling:
            parts.append(f"{_BRAILLE[self._spinner_frame]} polling...")
        banner.update(" · ".join(parts))

    @work(exclusive=True, thread=True)
    def _run_poll(self, last_seen: dict[int, TicketSnapshot]) -> None:
        from noc_cli.zendesk import ZendeskError

        try:
            tickets = poll_view(self._client, self._config.watch_view, self._config.watch_assignee)
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
        self._polling = False
        self._last_poll = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")

        if message.error:
            self._set_notification(f"Poll error: {message.error[:120]}", sticky=True)
            self._rebuild_rows(self._current_tickets)
            return

        for tid, snap in message.seeds.items():
            self._watch_state.seed_if_absent(tid, snap)
        for tid, snap in message.snapshots.items():
            self._watch_state.save(tid, snap)
        for evt in message.events:
            self._notifier.notify(evt)
            self.post_message(ShowBanner(event=evt))

        self._current_tickets = message.tickets or []
        self._rebuild_rows(self._current_tickets)

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
        )
        self._row_count = ticket_list.row_count
        self._update_banner()
        self._refresh_detail(reset_mode=selection_changed)

    def _refresh_detail(self, *, reset_mode: bool = False) -> None:
        if reset_mode:
            self._detail_index = 0

        row = self.selected_row
        if row is None:
            self._set_detail_text("No tickets to display.")
            return

        if row.summary is not None:
            if self._detail_index == 0:
                text = render_summary(
                    row.summary,
                    shipped_version=self._shipped_rubric_version,
                )
            else:
                text = self._read_detail_file(row.summary, _DETAIL_MODES[self._detail_index])
            self._set_detail_text(text)
            return

        if row.ticket is not None:
            self._detail_index = 0
            self._set_detail_text(render_activity(row.ticket))
            return

        self._detail_index = 0
        self._set_detail_text(f"Ticket: ZD-{row.ticket_id}\n\nNo live activity available.")

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

    def _set_detail_text(self, text: str) -> None:
        self._current_detail_text = text
        try:
            self.query_one("#detail-content", Static).update(text)
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
            self._refresh_detail()

    def action_cursor_down(self) -> None:
        moved = self.query_one("#ticket-list", TicketList).move_down()
        if moved:
            self._detail_index = 0
            self._refresh_detail()

    def action_focus_detail(self) -> None:
        self.query_one("#detail", DetailPane).focus()

    def action_investigate(self) -> None:
        row = self.selected_row
        if row is None:
            return
        subprocess.Popen(
            [sys.executable, "-m", "noc_cli.cli", "investigate", str(row.ticket_id)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def action_next_detail_file(self) -> None:
        row = self.selected_row
        if row is None or row.summary is None:
            return
        self._detail_index = (self._detail_index + 1) % len(_DETAIL_MODES)
        self._refresh_detail()

    def action_previous_detail_file(self) -> None:
        row = self.selected_row
        if row is None or row.summary is None:
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
