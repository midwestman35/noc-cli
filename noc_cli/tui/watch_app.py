from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, Static

from noc_cli.config import Config
from noc_cli.models import Comment, Ticket
from noc_cli.watch.diff import ChangeEvent, ChangeKind, _iso, _latest_public_comment, diff_tickets
from noc_cli.watch.notify import Notifier
from noc_cli.watch.poller import poll_view
from noc_cli.watch.state import TicketSnapshot, WatchState

_BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

_CSS = """
Screen { layout: vertical; }
#queue-table { height: 1fr; border: solid $accent; }
#change-banner {
    height: 3; background: $warning 20%; color: $warning;
    text-style: bold; content-align: center middle; display: none;
}
#change-banner.visible { display: block; }
#status-bar { height: 1; background: $surface-darken-1; color: $text-muted; padding: 0 1; }
"""


class ShowBanner(Message):
    """Posted when a change event should display the in-TUI banner."""

    def __init__(self, event: ChangeEvent) -> None:
        super().__init__()
        self.event = event


class PollComplete(Message):
    def __init__(self, tickets, events, seeds, snapshots, error=None) -> None:
        super().__init__()
        self.tickets = tickets
        self.events = events
        self.seeds = seeds
        self.snapshots = snapshots
        self.error = error


class WatchApp(App[None]):
    """Live Zendesk queue watcher TUI (read-only; no Agent SDK)."""

    CSS = _CSS
    BINDINGS = [
        Binding("p", "poll_now", "Poll now", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    _spinner_frame: reactive[int] = reactive(0)
    _polling: reactive[bool] = reactive(False)
    _last_poll: reactive[str] = reactive("never")

    def __init__(self, *, config: Config, client, watch_state: WatchState,
                 notifier: Notifier, poll_interval: int = 60) -> None:
        super().__init__()
        self._config = config
        self._client = client
        self._watch_state = watch_state
        self._notifier = notifier
        self._poll_interval = poll_interval
        self._current_tickets: list[Ticket] = []
        self._banner_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("", id="change-banner")
        yield DataTable(id="queue-table", cursor_type="row")
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.add_columns("ID", "Subject", "Status", "Updated")
        if self._poll_interval < 9999:
            self.set_interval(self._poll_interval, self.action_poll_now)
            self.action_poll_now()
        self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._polling:
            self._spinner_frame = (self._spinner_frame + 1) % len(_BRAILLE)
            self._update_status_bar()

    def _update_status_bar(self) -> None:
        try:
            bar = self.query_one("#status-bar", Static)
        except NoMatches:
            return
        if self._polling:
            bar.update(f" {_BRAILLE[self._spinner_frame]} Polling…")
        else:
            bar.update(f" Last poll: {self._last_poll}  |  Interval: {self._poll_interval}s  |  [p] poll now  [q] quit")

    @work(exclusive=True, thread=True)
    def _run_poll(self, last_seen: dict[int, TicketSnapshot]) -> None:
        from noc_cli.zendesk import ZendeskError

        try:
            tickets = poll_view(self._client, self._config.watch_view, self._config.watch_assignee)
            comments_map: dict[int, list[Comment]] = {}
            for ticket in tickets:
                try:
                    comments_map[ticket.id] = self._client.get_comments(ticket.id)
                except ZendeskError:
                    comments_map[ticket.id] = []
            events, seeds = diff_tickets(tickets, comments_map, last_seen, return_seeds=True)
            # Compute current snapshots in the worker so the main thread makes
            # no network call. last_comment_at = latest PUBLIC comment timestamp.
            snapshots: dict[int, TicketSnapshot] = {}
            for ticket in tickets:
                latest = _latest_public_comment(comments_map.get(ticket.id, []))
                snapshots[ticket.id] = TicketSnapshot(
                    status=ticket.status,
                    last_comment_at=_iso(latest.created_at if latest else None),
                )
            self.post_message(PollComplete(tickets, events, seeds, snapshots))
        except ZendeskError as exc:
            self.post_message(PollComplete([], [], {}, {}, error=str(exc)))

    def action_poll_now(self) -> None:
        self._polling = True
        self._update_status_bar()
        last_seen = self._watch_state.load_all()  # DB read on the main thread
        self._run_poll(last_seen)

    def on_poll_complete(self, message: PollComplete) -> None:
        self._polling = False
        self._last_poll = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")
        self._update_status_bar()
        if message.error:
            self._set_status_error(message.error)
            return
        # All DB writes happen here, on the main thread (the thread that opened
        # the connection) — no cross-thread sqlite access.
        for tid, snap in message.seeds.items():
            self._watch_state.seed_if_absent(tid, snap)
        for tid, snap in message.snapshots.items():
            self._watch_state.save(tid, snap)
        for evt in message.events:
            self._notifier.notify(evt)
            self.post_message(ShowBanner(event=evt))
        self._current_tickets = message.tickets
        self._rebuild_table(message.tickets)

    def _rebuild_table(self, tickets: list[Ticket]) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        for ticket in tickets:
            updated = ticket.updated_at.strftime("%Y-%m-%d %H:%M") if ticket.updated_at else "—"
            table.add_row(str(ticket.id), ticket.subject[:60], ticket.status.upper(), updated)

    def _set_status_error(self, error: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(f" [red]Poll error:[/red] {error[:80]}")
        except NoMatches:
            pass

    def on_show_banner(self, message: ShowBanner) -> None:
        evt = message.event
        if evt.customer_replied and evt.old_status == "pending":
            text = f" Customer replied on #{evt.ticket_id}: {evt.ticket_subject} "
        elif evt.kind == ChangeKind.STATUS_CHANGED:
            text = f" #{evt.ticket_id} status: {evt.old_status} → {evt.new_status} "
        else:
            text = f" New comment on #{evt.ticket_id}: {evt.ticket_subject} "
        try:
            banner = self.query_one("#change-banner", Label)
            banner.update(text)
            banner.display = True
            banner.add_class("visible")
        except NoMatches:
            return
        if self._banner_timer is not None:
            self._banner_timer.stop()
        self._banner_timer = self.set_timer(4.0, self._hide_banner)

    def _hide_banner(self) -> None:
        try:
            banner = self.query_one("#change-banner", Label)
            banner.display = False
            banner.remove_class("visible")
        except NoMatches:
            pass

    def on_data_table_row_selected(self, message: DataTable.RowSelected) -> None:
        """Enter on a focused DataTable row → launch investigate for that ticket."""
        self._launch_investigate(message.cursor_row)

    def _launch_investigate(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self._current_tickets):
            return
        ticket = self._current_tickets[row_index]
        subprocess.Popen([sys.executable, "-m", "noc_cli.cli", "investigate", str(ticket.id)])
