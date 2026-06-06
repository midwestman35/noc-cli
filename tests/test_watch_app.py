from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from noc_cli import store
from noc_cli.config import Config
from noc_cli.models import Comment, Ticket
from noc_cli.watch.notify import NoOpNotifier
from noc_cli.watch.state import WatchState
from noc_cli.zendesk import ZendeskError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_config(tickets_root) -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="agent@x.com",
        zendesk_api_token="tok",
        tickets_root=tickets_root,
        owner="enrique",
        watch_view="555",
        watch_assignee="agent@x.com",
    )


def _ticket(
    tid: int,
    *,
    status: str = "open",
    subject: str | None = None,
    updated_at: datetime | None = None,
    requester_id: int | None = 777,
    assignee_id: int | None = 42,
) -> Ticket:
    return Ticket(
        id=tid,
        subject=subject or f"Ticket {tid}",
        status=status,
        assignee_id=assignee_id,
        requester_email="requester@example.com",
        requester_org="City PSAP",
        requester_id=requester_id,
        updated_at=updated_at or datetime.now(tz=timezone.utc),
    )


def _comment(body: str, *, public: bool = True) -> Comment:
    return Comment(
        id=10,
        author_id=777,
        public=public,
        body=body,
        created_at=datetime.now(tz=timezone.utc),
    )


def _write_state(
    tickets_root,
    tid: int,
    *,
    fork: str = "B",
    confidence: str = "High",
    status: str = "pending",
    owner: str = "enrique",
    mtime: datetime | None = None,
):
    folder = tickets_root / str(tid)
    folder.mkdir(parents=True)
    (folder / "STATE.md").write_text(
        f"""---
ticket_id: {tid}
fork: "{fork}"
symptom_tag: "[dropped calls]"
confidence: "{confidence}"
rubric_version: "2026-05-13"
status: "{status}"
owner: "{owner}"
quoted_rubric_row: "customer LAN, switch, or SDWAN"
related:
  zendesk: [{tid}]
  jira: ["REP-123"]
---

# Ticket {tid}
"""
    )
    (folder / "INTAKE.md").write_text(f"# Intake for {tid}\n")
    stamp = (mtime or datetime.now(tz=timezone.utc)).timestamp()
    os.utime(folder / "STATE.md", (stamp, stamp))
    return folder


@pytest.fixture()
def db_conn(tmp_path):
    c = store.connect(tmp_path / "noc.db")
    yield c
    c.close()


class _FakeClient:
    def __init__(self, batches=None, comments=None, assignee_id=42):
        self._batches = list(batches or [[]])
        self._comments = comments or {}
        self._assignee_id = assignee_id

    def find_user_id(self, email):
        # The watcher resolves the configured assignee email to a user id; the
        # fixture tickets default to id 42 so they survive the "my queue" filter.
        return self._assignee_id

    def view_tickets(self, view_id):
        batch = self._batches.pop(0) if len(self._batches) > 1 else self._batches[0]
        if isinstance(batch, Exception):
            raise batch
        return [ticket.model_copy(deep=True) for ticket in batch]

    def get_comments(self, ticket_id):
        return [
            comment.model_copy(deep=True)
            for comment in self._comments.get(ticket_id, [])
        ]


def _make_app(cfg, client, ws):
    from noc_cli.tui.watch_app import WatchApp

    return WatchApp(
        config=cfg,
        client=client,
        watch_state=ws,
        notifier=NoOpNotifier(),
        poll_interval=9999,
    )


async def _poll(app, pilot):
    app.action_poll_now()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _text(widget) -> str:
    """Plain text of a Static, whether it was updated with a str or Rich Text."""
    content = widget.content
    return content.plain if hasattr(content, "plain") else str(content)


async def test_app_mounts_with_two_pane_viewer_widgets(db_conn, tmp_path):
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)):
        assert app.query_one("#ticket-list") is not None
        assert app.query_one("#detail") is not None
        assert app.query_one("#banner") is not None


async def test_poll_populates_worked_and_live_queue_segments(db_conn, tmp_path):
    from noc_cli.tui.watch_app import TicketList

    _write_state(tmp_path, 101, status="solved", owner="enrique")
    _write_state(
        tmp_path, 303, fork="A", confidence="Medium", status="open", owner="maya"
    )
    client = _FakeClient(
        [[_ticket(202, status="open"), _ticket(303, status="pending")]]
    )
    app = _make_app(_make_config(tmp_path), client, WatchState(db_conn))

    async with app.run_test(size=(140, 40)) as pilot:
        await _poll(app, pilot)
        ticket_list = app.query_one("#ticket-list", TicketList)
        banner_text = app.query_one("#banner").content

    text = ticket_list.rendered_text
    assert "Recently worked (3d)" in text
    assert "My queue" in text
    # Triage icons and ticket numbers (now separate columns from the subject).
    assert "✓" in text and "#101" in text
    assert "○" in text and "#202" in text
    assert "#303" in text
    # Statuses are shown for both worked (from STATE.md) and live rows.
    assert "solved" in text  # worked #101
    assert "open" in text  # live #202
    assert "pending" in text  # live #303
    # Subjects (the agent-friendly name) are shown for live queue rows.
    assert "Ticket 202" in text
    assert "Ticket 303" in text
    assert "3 tickets" in banner_text


async def test_poll_error_keeps_disk_backed_recently_worked_and_notification(
    db_conn, tmp_path
):
    from noc_cli.tui.watch_app import TicketList

    _write_state(tmp_path, 404)
    client = _FakeClient([ZendeskError("network down")])
    app = _make_app(_make_config(tmp_path), client, WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        ticket_list = app.query_one("#ticket-list", TicketList)
        notification_text = app.query_one("#notification").content

    assert "#404" in ticket_list.rendered_text
    assert "Poll error: network down" in notification_text


async def test_refresh_preserves_cursor_by_ticket_id_when_live_order_changes(
    db_conn, tmp_path
):
    from noc_cli.tui.watch_app import TicketList

    first = [_ticket(1), _ticket(2)]
    second = [_ticket(2), _ticket(1)]
    app = _make_app(
        _make_config(tmp_path), _FakeClient([first, second]), WatchState(db_conn)
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app.action_cursor_down()
        await pilot.pause()
        assert app.selected_row is not None
        assert app.selected_row.ticket_id == 2

        await _poll(app, pilot)
        ticket_list = app.query_one("#ticket-list", TicketList)
        assert app.selected_row is not None
        assert app.selected_row.ticket_id == 2
        assert ticket_list.cursor_index == 0


async def test_detail_shows_summary_for_worked_row(db_conn, tmp_path):
    _write_state(
        tmp_path, 505, fork="C", confidence="Low", status="solved", owner="enrique"
    )
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)

    assert "Ticket: ZD-505" in app.current_detail_text
    assert "Fork: C" in app.current_detail_text
    assert "Owner: enrique" in app.current_detail_text


async def test_detail_shows_activity_for_queue_row_with_comment_body(db_conn, tmp_path):
    ticket = _ticket(606, subject="Low audio report", status="open")
    comments = {606: [_comment("Customer says audio is still low.")]}
    app = _make_app(
        _make_config(tmp_path), _FakeClient([[ticket]], comments), WatchState(db_conn)
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)

    assert "Ticket: ZD-606" in app.current_detail_text
    assert "press [i]" in app.current_detail_text
    assert "Customer says audio is still low." in app.current_detail_text


async def test_summary_after_triage_keeps_header_and_comments(db_conn, tmp_path):
    # A triaged queue row carries both a STATE.md summary and a live ticket, so
    # the Summary tab must show the triage outcome AND keep the live comments,
    # with the permanent header naming the ticket.
    _write_state(tmp_path, 880, fork="B", status="pending")
    ticket = _ticket(880, subject="Dropped calls at PSAP", status="pending")
    comments = {880: [_comment("Customer says calls still dropping.")]}
    app = _make_app(
        _make_config(tmp_path), _FakeClient([[ticket]], comments), WatchState(db_conn)
    )

    async with app.run_test(size=(140, 40)) as pilot:
        await _poll(app, pilot)
        detail = app.current_detail_text
        header = _text(app.query_one("#detail-header"))

    assert "Fork: B" in detail  # triage summary preserved
    assert "Customer says calls still dropping." in detail  # live comment kept
    assert "ZD-880" in header
    assert "Dropped calls at PSAP" in header
    assert "pending" in header


async def test_tablabel_describes_current_file_tab(db_conn, tmp_path):
    _write_state(tmp_path, 707)
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        assert "Summary" in _text(app.query_one("#detail-tablabel"))
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        label = _text(app.query_one("#detail-tablabel"))

    assert "INTAKE.md" in label
    assert "What are we looking at?" in label


async def test_new_comment_appears_after_second_poll(db_conn, tmp_path):
    ticket = _ticket(990, subject="No ALI", status="open")
    first = _comment("First comment.")
    second = _comment("Second comment arrived.")

    class GrowingClient(_FakeClient):
        def __init__(self):
            super().__init__([[ticket]])
            self.poll_round = 0

        def get_comments(self, ticket_id):
            batch = [first, second] if self.poll_round >= 1 else [first]
            return [c.model_copy(deep=True) for c in batch]

    client = GrowingClient()
    app = _make_app(_make_config(tmp_path), client, WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        assert "First comment." in app.current_detail_text
        assert "Second comment arrived." not in app.current_detail_text

        client.poll_round = 1
        await _poll(app, pilot)
        assert "Second comment arrived." in app.current_detail_text


async def test_tab_cycles_to_file(db_conn, tmp_path):
    _write_state(tmp_path, 707)
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert "# Intake for 707" in app.current_detail_text


async def test_shift_tab_cycles_back_from_file_to_summary(db_conn, tmp_path):
    _write_state(tmp_path, 717)
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert "# Intake for 717" in app.current_detail_text

        await pilot.press("shift+tab")
        await pilot.pause()
        await pilot.press("shift+tab")
        await pilot.pause()

    assert "Ticket: ZD-717" in app.current_detail_text
    assert "# Intake for 717" not in app.current_detail_text


async def test_investigate_runs_in_process_and_streams_phases(db_conn, tmp_path):
    from textual.widgets import Input

    captured = {}

    async def fake_run_investigation(*, ticket_id, on_line=None, **kw):
        captured["ticket_id"] = ticket_id
        on_line("Scaffold ready: /x")
        on_line("Evidence gathered")
        return tmp_path / str(ticket_id)

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(808)]]), WatchState(db_conn)
    )
    with patch(
        "noc_cli.investigate.run_investigation", side_effect=fake_run_investigation
    ):
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            box = app.query_one("#command", Input)
            box.value = "/investigate"
            await box.action_submit()
            for _ in range(30):
                await pilot.pause()
                if app._investigating_id is None:
                    break
    assert captured["ticket_id"] == 808
    assert app._investigating_id is None


async def test_investigate_is_single_flight(db_conn, tmp_path):
    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(818)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._investigating_id = 818  # simulate in-flight
        app.action_investigate()
        await pilot.pause()
        assert "already running" in app.query_one("#notification").content


async def test_new_update_badge_lifecycle(db_conn, tmp_path):
    from noc_cli.tui.watch_app import TicketList

    # Batch 1: #700 open and #701 open. Batch 2: #700 flips to pending (a change)
    # while the cursor sits on #701.
    first = [_ticket(701, status="open"), _ticket(700, status="open")]
    second = [_ticket(701, status="open"), _ticket(700, status="pending")]
    comments = {700: [_comment("Customer replied.")]}
    client = _FakeClient([first, second], comments)
    app = _make_app(_make_config(tmp_path), client, WatchState(db_conn))

    async with app.run_test(size=(140, 40)) as pilot:
        await _poll(app, pilot)
        # Park the cursor on #701 so #700's change gets flagged.
        ticket_list = app.query_one("#ticket-list", TicketList)
        if app.selected_row.ticket_id != 701:
            app.action_cursor_down()
            await pilot.pause()
        assert app.selected_row.ticket_id == 701

        await _poll(app, pilot)
        assert 700 in app._unread_ids
        assert "!" in ticket_list.rendered_text

        # Arrowing onto #700 clears its badge.
        target = next(i for i, r in enumerate(ticket_list.rows) if r.ticket_id == 700)
        while ticket_list.cursor_index != target:
            if ticket_list.cursor_index < target:
                app.action_cursor_down()
            else:
                app.action_cursor_up()
            await pilot.pause()
        assert 700 not in app._unread_ids


async def test_change_to_selected_row_is_not_flagged(db_conn, tmp_path):
    first = [_ticket(750, status="open")]
    second = [_ticket(750, status="pending")]
    comments = {750: [_comment("Customer replied.")]}
    client = _FakeClient([first, second], comments)
    app = _make_app(_make_config(tmp_path), client, WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        assert app.selected_row.ticket_id == 750
        await _poll(app, pilot)
        # The row the agent is already on must not get a badge.
        assert 750 not in app._unread_ids


async def test_pulse_marks_changed_ticket_then_expires(db_conn, tmp_path):
    first = [_ticket(761, status="open"), _ticket(760, status="open")]
    second = [_ticket(761, status="open"), _ticket(760, status="pending")]
    comments = {760: [_comment("Customer replied.")]}
    client = _FakeClient([first, second], comments)
    app = _make_app(_make_config(tmp_path), client, WatchState(db_conn))

    async with app.run_test(size=(140, 40)) as pilot:
        await _poll(app, pilot)
        from noc_cli.tui.watch_app import TicketList

        ticket_list = app.query_one("#ticket-list", TicketList)
        if app.selected_row.ticket_id != 761:
            app.action_cursor_down()
            await pilot.pause()

        await _poll(app, pilot)
        assert 760 in app._current_pulse_ids()

        # Force the deadline into the past and expire — pulse set goes empty.
        app._pulse_until[760] = 0.0
        app._expire_pulses()
        assert app._current_pulse_ids() == set()


async def test_command_box_is_focused_and_routes_slash_refresh(db_conn, tmp_path):
    from textual.widgets import Input

    first = [_ticket(601)]
    second = [_ticket(602)]
    app = _make_app(
        _make_config(tmp_path), _FakeClient([first, second]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        assert app.focused is box
        box.value = "/refresh"
        await box.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        ids = [r.ticket_id for r in app.query_one("#ticket-list").rows]
    assert ids == [602]
    assert box.value == ""


async def test_slash_open_routes_to_browser(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(909)]]), WatchState(db_conn)
    )
    with patch("webbrowser.open") as mock_open:
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            box = app.query_one("#command", Input)
            box.value = "/open"
            await box.action_submit()
            await pilot.pause()
    mock_open.assert_called_once_with("https://carbyne.zendesk.com/agent/tickets/909")


async def test_help_lists_commands_in_detail(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "/help"
        await box.action_submit()
        await pilot.pause()
    assert "/investigate" in app.current_detail_text


async def test_slash_copy_copies_current_detail(db_conn, tmp_path):
    from textual.widgets import Input

    from noc_cli.tui.watch_app import WatchApp

    app = _make_app(
        _make_config(tmp_path),
        _FakeClient([[_ticket(1001, subject="No ANI")]]),
        WatchState(db_conn),
    )
    with patch.object(WatchApp, "copy_to_clipboard", autospec=True) as mock_copy:
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            box = app.query_one("#command", Input)
            box.value = "/copy"
            await box.action_submit()
            await pilot.pause()
    assert mock_copy.call_count == 1
    assert "Ticket: ZD-1001" in mock_copy.call_args[0][1]


async def test_banner_shows_version(db_conn, tmp_path):
    from noc_cli import __version__

    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        banner = _text(app.query_one("#banner"))
    assert f"v{__version__}" in banner
    assert "my tickets" in banner


async def test_splash_shows_then_dissolves_on_first_poll(db_conn, tmp_path):
    from noc_cli import __version__

    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        splash = app.query("#splash")
        assert len(splash) == 1
        assert f"v{__version__}" in _text(splash.first())
        await _poll(app, pilot)
        assert len(app.query("#splash")) == 0


async def test_tab_reaches_chat_view(db_conn, tmp_path):
    app = _make_app(
        _make_config(tmp_path),
        _FakeClient([[_ticket(700, subject="No ALI")]]),
        WatchState(db_conn),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        await pilot.press("tab")  # Summary -> Chat (works on a NON-triaged row)
        await pilot.pause()
        label = _text(app.query_one("#detail-tablabel"))
        detail = app.current_detail_text
    assert "Chat" in label
    assert "about this ticket" in label.lower()
    assert "700" in detail


async def test_second_chat_turn_blocked_while_one_in_flight(db_conn, tmp_path):
    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(500)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._chatting_id = 500  # simulate a turn already in flight
        app._submit_chat_turn("another question")
        await pilot.pause()
        assert "in progress" in app.query_one("#notification").content.lower()
        # No second chat buffer was created for the rejected turn.
        assert app._chat_lines.get(500) is None or app._chat_lines.get(500) == []


async def test_escape_interrupts_active_chat(db_conn, tmp_path):
    from textual.widgets import Input

    interrupted = {"flag": False}

    class _SlowClient:
        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def query(self, prompt):
            pass

        async def receive_response(self):
            class M:
                result = "partial"

            yield M()

        async def interrupt(self):
            interrupted["flag"] = True

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(500)]]), WatchState(db_conn)
    )
    app._chat_client_factory = lambda folder: lambda: _SlowClient()
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "explain"
        await box.action_submit()
        await pilot.pause()
        # Ensure a live session exists, then interrupt via escape.
        session = app._ensure_chat_session(500)
        await session._ensure_client()
        await pilot.press("escape")
        for _ in range(20):
            await pilot.pause()
            if interrupted["flag"]:
                break
    assert interrupted["flag"] is True


async def test_freeform_input_starts_chat_and_renders(db_conn, tmp_path):
    from textual.widgets import Input

    class _FakeChatClient:
        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def query(self, prompt):
            self.p = prompt

        async def receive_response(self):
            class M:
                result = "Held in queue; ALI link timed out."

            yield M()

        async def interrupt(self):
            pass

    app = _make_app(
        _make_config(tmp_path),
        _FakeClient([[_ticket(45747, subject="stuck")]]),
        WatchState(db_conn),
    )
    app._chat_client_factory = lambda folder: lambda: _FakeChatClient()  # inject fake
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "why is this stuck?"
        await box.action_submit()
        for _ in range(30):
            await pilot.pause()
            if "Held in queue" in app.current_detail_text:
                break
    assert "you ❯ why is this stuck?" in app.current_detail_text
    assert "Held in queue" in app.current_detail_text
    assert (tmp_path / "45747" / "CONVERSATION.jsonl").exists()


async def test_slash_file_attaches_evidence(db_conn, tmp_path):
    from textual.widgets import Input

    src = tmp_path / "pcap-excerpt.txt"
    src.write_text("SIP 200 OK\n")
    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(45747)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = f"/file {src}"
        await box.action_submit()
        await pilot.pause()
        notification_text = app.query_one("#notification").content
    assert (tmp_path / "45747" / "logs" / "pcap-excerpt.txt").exists()
    assert "attached" in notification_text.lower()


async def test_slash_paste_attaches_evidence(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(45747)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "/paste siptrace=INVITE sip:911@psap"
        await box.action_submit()
        await pilot.pause()
        notification = app.query_one("#notification").content
    assert (
        tmp_path / "45747" / "logs" / "paste-siptrace.txt"
    ).read_text() == "INVITE sip:911@psap"
    assert "attached" in notification.lower()


async def test_close_all_chat_sessions_disconnects_clients(db_conn, tmp_path):
    from noc_cli.tui.chat import ChatSession

    disconnected = {"flag": False}

    class _C:
        async def connect(self):
            return None

        async def disconnect(self):
            disconnected["flag"] = True

        async def query(self, p):
            pass

        async def receive_response(self):
            class M:
                result = "ok"

            yield M()

        async def interrupt(self):
            pass

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        session = ChatSession(ticket_id=1, folder=tmp_path, client_factory=lambda: _C())
        await (
            session._ensure_client()
        )  # create the (fake) client so close() will disconnect it
        app._chat_sessions[1] = session
        await app._close_all_chat_sessions()
    assert disconnected["flag"] is True
    assert app._chat_sessions == {}
