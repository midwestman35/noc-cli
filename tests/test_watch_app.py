from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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
        return [comment.model_copy(deep=True) for comment in self._comments.get(ticket_id, [])]


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


async def test_app_mounts_with_two_pane_viewer_widgets(db_conn, tmp_path):
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)):
        assert app.query_one("#ticket-list") is not None
        assert app.query_one("#detail") is not None
        assert app.query_one("#banner") is not None


async def test_poll_populates_worked_and_live_queue_segments(db_conn, tmp_path):
    from noc_cli.tui.watch_app import TicketList

    _write_state(tmp_path, 101, status="solved", owner="enrique")
    _write_state(tmp_path, 303, fork="A", confidence="Medium", status="open", owner="maya")
    client = _FakeClient([[_ticket(202, status="open"), _ticket(303, status="pending")]])
    app = _make_app(_make_config(tmp_path), client, WatchState(db_conn))

    async with app.run_test(size=(140, 40)) as pilot:
        await _poll(app, pilot)
        ticket_list = app.query_one("#ticket-list", TicketList)
        banner_text = app.query_one("#banner").content

    text = ticket_list.rendered_text
    assert "Recently worked (3d)" in text
    assert "My queue" in text
    assert "✓ #101" in text
    assert "○ #202" in text
    assert "✓ #303" in text
    # Worked row shows the disk summary's status + owner.
    assert "solved" in text
    assert "enrique" in text
    # Live queue rows now show the ticket subject and live status.
    assert "Ticket 202" in text
    assert "open" in text
    assert "Ticket 303" in text
    assert "pending" in text
    assert "maya" in text
    assert "3 tickets" in banner_text


async def test_poll_error_keeps_disk_backed_recently_worked_and_notification(db_conn, tmp_path):
    from noc_cli.tui.watch_app import TicketList

    _write_state(tmp_path, 404)
    client = _FakeClient([ZendeskError("network down")])
    app = _make_app(_make_config(tmp_path), client, WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        ticket_list = app.query_one("#ticket-list", TicketList)
        notification_text = app.query_one("#notification").content

    assert "✓ #404" in ticket_list.rendered_text
    assert "Poll error: network down" in notification_text


async def test_refresh_preserves_cursor_by_ticket_id_when_live_order_changes(db_conn, tmp_path):
    from noc_cli.tui.watch_app import TicketList

    first = [_ticket(1), _ticket(2)]
    second = [_ticket(2), _ticket(1)]
    app = _make_app(_make_config(tmp_path), _FakeClient([first, second]), WatchState(db_conn))

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
    _write_state(tmp_path, 505, fork="C", confidence="Low", status="solved", owner="enrique")
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)

    assert "Ticket: ZD-505" in app.current_detail_text
    assert "Fork: C" in app.current_detail_text
    assert "Owner: enrique" in app.current_detail_text


async def test_detail_shows_activity_for_queue_row_with_comment_body(db_conn, tmp_path):
    ticket = _ticket(606, subject="Low audio report", status="open")
    comments = {606: [_comment("Customer says audio is still low.")]}
    app = _make_app(_make_config(tmp_path), _FakeClient([[ticket]], comments), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)

    assert "Ticket: ZD-606" in app.current_detail_text
    assert "press [i]" in app.current_detail_text
    assert "Customer says audio is still low." in app.current_detail_text


async def test_tab_cycles_to_file_and_escape_returns_to_summary(db_conn, tmp_path):
    _write_state(tmp_path, 707)
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        await pilot.press("tab")
        await pilot.pause()
        assert "# Intake for 707" in app.current_detail_text

        await pilot.press("escape")
        await pilot.pause()

    assert "Ticket: ZD-707" in app.current_detail_text


async def test_shift_tab_cycles_back_from_file_to_summary(db_conn, tmp_path):
    _write_state(tmp_path, 717)
    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        await pilot.press("tab")
        await pilot.pause()
        assert "# Intake for 717" in app.current_detail_text

        await pilot.press("shift+tab")
        await pilot.pause()

    assert "Ticket: ZD-717" in app.current_detail_text
    assert "# Intake for 717" not in app.current_detail_text


async def test_enter_focuses_detail_pane(db_conn, tmp_path):
    ticket = _ticket(727)
    app = _make_app(_make_config(tmp_path), _FakeClient([[ticket]]), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        detail = app.query_one("#detail")
        assert app.focused is detail


async def test_r_key_refreshes_live_queue(db_conn, tmp_path):
    from noc_cli.tui.watch_app import TicketList

    first = [_ticket(737)]
    second = [_ticket(738)]
    app = _make_app(_make_config(tmp_path), _FakeClient([first, second]), WatchState(db_conn))

    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        ticket_list = app.query_one("#ticket-list", TicketList)

    assert [row.ticket_id for row in ticket_list.rows] == [738]


async def test_i_launches_investigate_for_selected_ticket(db_conn, tmp_path):
    ticket = _ticket(808)
    app = _make_app(_make_config(tmp_path), _FakeClient([[ticket]]), WatchState(db_conn))

    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            await pilot.press("i")
            await pilot.pause()

    assert mock_popen.call_count == 1
    cmd = mock_popen.call_args[0][0]
    assert "investigate" in cmd
    assert "808" in cmd


async def test_o_opens_zendesk_ticket_url(db_conn, tmp_path):
    ticket = _ticket(909)
    app = _make_app(_make_config(tmp_path), _FakeClient([[ticket]]), WatchState(db_conn))

    with patch("webbrowser.open") as mock_open:
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            await pilot.press("o")
            await pilot.pause()

    mock_open.assert_called_once_with("https://carbyne.zendesk.com/agent/tickets/909")


async def test_y_copies_current_summary_or_activity(db_conn, tmp_path):
    ticket = _ticket(1001, subject="No ANI")
    app = _make_app(_make_config(tmp_path), _FakeClient([[ticket]]), WatchState(db_conn))

    from noc_cli.tui.watch_app import WatchApp

    with patch.object(WatchApp, "copy_to_clipboard", autospec=True) as mock_copy:
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            await pilot.press("y")
            await pilot.pause()

    assert mock_copy.call_count == 1
    assert mock_copy.call_args[0][0] is app
    assert "Ticket: ZD-1001" in mock_copy.call_args[0][1]
