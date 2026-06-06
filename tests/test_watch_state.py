from __future__ import annotations

import pytest

from noc_cli import store
from noc_cli.watch.state import TicketSnapshot, WatchState


@pytest.fixture()
def conn(tmp_path):
    db = tmp_path / "noc.db"
    c = store.connect(db)
    yield c
    c.close()


def test_load_returns_empty_dict_when_table_is_fresh(conn):
    ws = WatchState(conn)
    assert ws.load_all() == {}


def test_save_and_load_roundtrips_a_snapshot(conn):
    ws = WatchState(conn)
    snap = TicketSnapshot(status="open", last_comment_at="2026-06-01T10:00:00Z")
    ws.save(ticket_id=101, snapshot=snap)
    loaded = ws.load_all()
    assert loaded[101] == snap


def test_update_overwrites_previous_snapshot(conn):
    ws = WatchState(conn)
    ws.save(101, TicketSnapshot(status="open", last_comment_at="2026-06-01T10:00:00Z"))
    ws.save(
        101, TicketSnapshot(status="pending", last_comment_at="2026-06-02T09:00:00Z")
    )
    loaded = ws.load_all()
    assert loaded[101].status == "pending"
    assert loaded[101].last_comment_at == "2026-06-02T09:00:00Z"


def test_multiple_tickets_are_stored_independently(conn):
    ws = WatchState(conn)
    ws.save(1, TicketSnapshot(status="open", last_comment_at="2026-06-01T08:00:00Z"))
    ws.save(2, TicketSnapshot(status="pending", last_comment_at="2026-06-01T09:00:00Z"))
    loaded = ws.load_all()
    assert loaded[1].status == "open"
    assert loaded[2].status == "pending"


def test_state_persists_across_reconnect(tmp_path):
    db = tmp_path / "noc.db"
    conn1 = store.connect(db)
    ws1 = WatchState(conn1)
    ws1.save(
        42, TicketSnapshot(status="solved", last_comment_at="2026-06-03T00:00:00Z")
    )
    conn1.close()

    conn2 = store.connect(db)
    ws2 = WatchState(conn2)
    loaded = ws2.load_all()
    conn2.close()
    assert loaded[42].status == "solved"


def test_seed_inserts_if_absent_and_is_idempotent(conn):
    ws = WatchState(conn)
    snap = TicketSnapshot(status="open", last_comment_at="2026-06-01T00:00:00Z")
    ws.seed_if_absent(99, snap)
    ws.seed_if_absent(
        99, TicketSnapshot(status="pending", last_comment_at="2026-06-02T00:00:00Z")
    )
    loaded = ws.load_all()
    # The first seed wins; the second call is a no-op.
    assert loaded[99].status == "open"
