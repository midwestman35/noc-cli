from unittest.mock import MagicMock, patch

import pytest

from noc_cli import store
from noc_cli.config import Config
from noc_cli.models import Ticket
from noc_cli.watch.diff import ChangeEvent, ChangeKind
from noc_cli.watch.notify import NoOpNotifier
from noc_cli.watch.state import WatchState

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_config() -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="agent@x.com",
        zendesk_api_token="tok",
        watch_view="555",
        watch_assignee="agent@x.com",
    )


def _ticket(tid: int, *, status: str = "open", subject: str | None = None) -> Ticket:
    return Ticket(id=tid, subject=subject or f"Ticket {tid}", status=status, assignee_email="agent@x.com")


@pytest.fixture()
def db_conn(tmp_path):
    c = store.connect(tmp_path / "noc.db")
    yield c
    c.close()


class _FakeClient:
    def __init__(self, tickets=None):
        self._tickets = tickets or []

    def view_tickets(self, view_id):
        return list(self._tickets)

    def get_comments(self, ticket_id):
        return []


def _make_app(cfg, client, ws):
    from noc_cli.tui.watch_app import WatchApp

    return WatchApp(config=cfg, client=client, watch_state=ws, notifier=NoOpNotifier(), poll_interval=9999)


async def test_app_mounts_without_error(db_conn):
    app = _make_app(_make_config(), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)):
        assert app.query_one("#queue-table") is not None


async def test_app_renders_tickets_in_table(db_conn):
    from textual.widgets import DataTable

    client = _FakeClient([_ticket(1, status="open"), _ticket(2, status="pending")])
    app = _make_app(_make_config(), client, WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_poll_now()
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#queue-table", DataTable)
        assert table.row_count == 2


async def test_banner_visible_on_change_event(db_conn):
    from noc_cli.tui.watch_app import ShowBanner

    app = _make_app(_make_config(), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        evt = ChangeEvent(
            ticket_id=7, ticket_subject="No ANI", kind=ChangeKind.STATUS_CHANGED,
            old_status="pending", new_status="open", customer_replied=True,
        )
        app.post_message(ShowBanner(event=evt))
        await pilot.pause()
        banner = app.query_one("#change-banner")
        assert banner.display is True


async def test_enter_on_row_calls_investigate(db_conn):
    from textual.widgets import DataTable

    client = _FakeClient([_ticket(99, status="open")])
    app = _make_app(_make_config(), client, WatchState(db_conn))
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_poll_now()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#queue-table", DataTable)
            table.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
    assert mock_popen.call_count == 1
    cmd = mock_popen.call_args[0][0]
    assert "investigate" in cmd
    assert "99" in cmd
