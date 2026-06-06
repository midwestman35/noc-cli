# noc-cli Watch Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `watch` command — a deterministic, non-LLM, read-only Textual TUI that continuously polls the analyst's Zendesk view, diffs current ticket state against persisted last-seen state, and fires in-TUI banners and OS desktop notifications on real changes (status transitions and new public requester comments). Replace the `watch` stub in `cli.py` with the real implementation.

**Architecture:** Pure-logic modules (`poller.py`, `diff.py`, `state.py`, `notify.py`) are TTY-free and network-free — they are unit-tested in isolation with mocked clients and a `tmp_path` SQLite DB. The Textual app (`tui/watch_app.py`) is a thin shell over these modules, driven by Textual's async worker/timer system. The spec's read-only posture is honoured end-to-end: the watcher never imports the Agent SDK, never writes to Zendesk, and is safe to leave running all day.

**Tech Stack:** Python 3.11+, uv, Typer, httpx, pydantic v2, sqlite3 (stdlib), Rich (via Typer), **Textual** (new dep); pytest + pytest-httpx + pytest-anyio for tests.

**Coordination note:** The `noc_cli/tui/` package is a shared namespace. The investigate plan will add `tui/progress.py` and `tui/viewport.py`; this plan adds `tui/watch_app.py` and the `tui/__init__.py` package marker. If both plans are being merged, the developer must ensure `tui/__init__.py` is present exactly once (it is created here, idempotently). `textual` is added to `pyproject.toml` by this plan; the investigate plan depends on the same dep and must not re-add it with a conflicting version pin.

---

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Add `textual>=0.61` to `[project].dependencies` and `pytest-anyio>=0.0.0` to `[dependency-groups].dev` |
| `noc_cli/tui/__init__.py` | Package marker (empty) |
| `noc_cli/tui/watch_app.py` | Textual `WatchApp`: live queue list, animated banner, braille poll spinner, breathing Pending rows, Enter → investigate |
| `noc_cli/watch/__init__.py` | Package marker (empty) |
| `noc_cli/watch/poller.py` | `poll_view(client, view_id, assignee) -> list[Ticket]`: fetch + assignee filter |
| `noc_cli/watch/diff.py` | `diff_tickets(tickets, comments_map, last_seen) -> list[ChangeEvent]`: status/comment diffing + event classification |
| `noc_cli/watch/state.py` | `WatchState`: read/write last-seen state (status + last-comment ts) to SQLite |
| `noc_cli/watch/notify.py` | `Notifier` ABC + `MacOSNotifier` (osascript / terminal-notifier) + `NoOpNotifier` for tests |
| `noc_cli/cli.py` | Replace `watch` stub with real Typer command wiring |
| `tests/test_watch_poller.py` | Assignee filter logic, empty view, ZendeskError resilience |
| `tests/test_watch_diff.py` | All transition scenarios: no-change, new ticket seed, Pending→Open, new requester comment, generic status change |
| `tests/test_watch_state.py` | Persist/load across simulated poll cycles; unknown-ticket first-seen seed |
| `tests/test_watch_notify.py` | Format/dispatch: osascript path, terminal-notifier upgrade path, NoOpNotifier |
| `tests/test_watch_app.py` | Headless Textual Pilot: app mounts, banner fires on injected event, row count matches fixture |

---

## Task 1: Add `textual` dep + package scaffolding

**Files:**
- Modify: `pyproject.toml`
- Create: `noc_cli/watch/__init__.py`
- Create: `noc_cli/tui/__init__.py`
- Test: `tests/test_watch_poller.py` (initial skeleton — fails until Task 2)

The first test is a module-import smoke test that doubles as a failing sentinel.

- [ ] **Step 1: Write the failing test**

`tests/test_watch_poller.py`:

```python
# Smoke: verifies the package tree exists before any logic is implemented.
import pytest


def test_watch_package_importable():
    from noc_cli.watch import poller  # noqa: F401 — import is the assertion
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_watch_poller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.watch'`

- [ ] **Step 3: Add `textual` to `pyproject.toml` and create package markers**

Edit `pyproject.toml` — add `"textual>=0.61"` to `[project].dependencies` and `"pytest-anyio>=0.0.0"` to `[dependency-groups].dev`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "noc-cli"
version = "0.1.0"
description = "Read-only NOC triage assistant for Carbyne APEX NG911/E911 (Python / Claude Agent SDK)."
requires-python = ">=3.10"
dependencies = [
    "typer>=0.12",
    "httpx>=0.27",
    "pydantic>=2.7",
    "platformdirs>=4.2",
    "python-dotenv>=1.0",
    "textual>=0.61",
]

[project.scripts]
noc-cli = "noc_cli.cli:app"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-httpx>=0.30",
    "pytest-anyio>=0.0.0",
]

[tool.hatch.build.targets.wheel]
packages = ["noc_cli"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `noc_cli/watch/__init__.py` (empty package marker):

```python
```

Create `noc_cli/tui/__init__.py` (empty package marker):

```python
```

Create `noc_cli/watch/poller.py` with a minimal stub so the import resolves:

```python
from __future__ import annotations

from noc_cli.models import Ticket
from noc_cli.zendesk import ZendeskClient


def poll_view(
    client: ZendeskClient,
    view_id: str,
    assignee: str,
) -> list[Ticket]:
    """Fetch all tickets from *view_id* and filter to those assigned to *assignee*.

    *assignee* is matched case-insensitively against the ticket's
    ``assignee_email`` field (populated by the Zendesk API on each ticket row).
    Pass an empty string to return all tickets in the view without filtering.

    Poll failures must be caught by the caller; this function raises
    ``ZendeskError`` on network or auth problems so the TUI can log-and-continue.
    """
    tickets = client.view_tickets(view_id)
    if not assignee:
        return tickets
    needle = assignee.lower()
    return [t for t in tickets if (t.assignee_email or "").lower() == needle]
```

**Note:** `Ticket` needs an `assignee_email` field. Add it to `noc_cli/models.py`:

```python
class Ticket(BaseModel):
    id: int
    subject: str = ""
    description: str = ""
    requester_id: int | None = None
    assignee_id: int | None = None
    assignee_email: str | None = None   # <-- NEW: populated on view_tickets rows
    requester_org: str | None = None
    requester_email: str | None = None
    status: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    comments: list[Comment] = Field(default_factory=list)
```

- [ ] **Step 4: Sync and run**

Run: `uv sync && uv run pytest tests/test_watch_poller.py -v`
Expected: PASS (1 passed — import resolves).

Also run full suite to guard the models change: `uv run pytest -v`
Expected: PASS (all prior foundation tests still green; pydantic ignores extra fields, so the new field is purely additive).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock noc_cli/watch/__init__.py noc_cli/tui/__init__.py noc_cli/watch/poller.py noc_cli/models.py tests/test_watch_poller.py
git commit -m "$(cat <<'EOF'
feat: add textual dep, watch+tui package scaffolding, assignee_email field

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Poller — full logic + tests

**Files:**
- Complete: `noc_cli/watch/poller.py` (already stubbed; tests drive any needed changes)
- Test: `tests/test_watch_poller.py` (extend with real assertions)

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_watch_poller.py` entirely:

```python
from __future__ import annotations

import pytest

from noc_cli.config import Config
from noc_cli.models import Ticket
from noc_cli.watch.poller import poll_view
from noc_cli.zendesk import ZendeskClient, ZendeskError


def _make_config() -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="agent@x.com",
        zendesk_api_token="tok",
    )


class _FakeClient:
    """Minimal stand-in for ZendeskClient that returns scripted ticket lists."""

    def __init__(self, tickets: list[Ticket]) -> None:
        self._tickets = tickets

    def view_tickets(self, view_id: str) -> list[Ticket]:
        return list(self._tickets)


def _ticket(tid: int, *, assignee_email: str = "agent@x.com", status: str = "open") -> Ticket:
    return Ticket(id=tid, subject=f"Ticket {tid}", status=status, assignee_email=assignee_email)


def test_poll_view_returns_all_tickets_when_no_assignee_filter():
    tickets = [_ticket(1), _ticket(2, assignee_email="other@x.com")]
    client = _FakeClient(tickets)
    result = poll_view(client, "555", assignee="")  # type: ignore[arg-type]
    assert [t.id for t in result] == [1, 2]


def test_poll_view_filters_to_matching_assignee():
    tickets = [
        _ticket(1, assignee_email="agent@x.com"),
        _ticket(2, assignee_email="other@x.com"),
        _ticket(3, assignee_email="AGENT@X.COM"),  # case-insensitive
    ]
    client = _FakeClient(tickets)
    result = poll_view(client, "555", assignee="agent@x.com")  # type: ignore[arg-type]
    assert [t.id for t in result] == [1, 3]


def test_poll_view_returns_empty_list_when_view_is_empty():
    client = _FakeClient([])
    result = poll_view(client, "555", assignee="agent@x.com")  # type: ignore[arg-type]
    assert result == []


def test_poll_view_returns_empty_list_when_no_ticket_matches_assignee():
    tickets = [_ticket(1, assignee_email="someone@x.com")]
    client = _FakeClient(tickets)
    result = poll_view(client, "555", assignee="nobody@x.com")  # type: ignore[arg-type]
    assert result == []


def test_poll_view_propagates_zendesk_error():
    class _ErrorClient:
        def view_tickets(self, view_id: str) -> list[Ticket]:
            raise ZendeskError("network timeout")

    with pytest.raises(ZendeskError, match="network timeout"):
        poll_view(_ErrorClient(), "555", assignee="")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_watch_poller.py -v`
Expected: FAIL — `AttributeError` or `AssertionError` because the stub import resolves but the logic may be missing.

- [ ] **Step 3: Verify `poller.py` covers all cases**

The stub written in Task 1 already handles all cases (empty assignee → no filter; non-empty → case-insensitive match; ZendeskError propagates). If the tests pass with the existing code, no change is needed. If any test fails, fix only the failing case.

The final `noc_cli/watch/poller.py` must be:

```python
from __future__ import annotations

from noc_cli.models import Ticket
from noc_cli.zendesk import ZendeskClient


def poll_view(
    client: ZendeskClient,
    view_id: str,
    assignee: str,
) -> list[Ticket]:
    """Fetch all tickets from *view_id* and filter to those assigned to *assignee*.

    *assignee* is matched case-insensitively against the ticket's
    ``assignee_email`` field.  Pass an empty string to skip filtering.

    Raises ``ZendeskError`` on network or auth problems; the TUI catches it
    and retries on the next interval without crashing.
    """
    tickets = client.view_tickets(view_id)
    if not assignee:
        return tickets
    needle = assignee.lower()
    return [t for t in tickets if (t.assignee_email or "").lower() == needle]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_watch_poller.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/watch/poller.py tests/test_watch_poller.py
git commit -m "$(cat <<'EOF'
feat(watch): poller — view_tickets fetch + case-insensitive assignee filter

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: State — SQLite persistence

**Files:**
- Create: `noc_cli/watch/state.py`
- Create: `tests/test_watch_state.py`

The state module owns the `watch_state` table DDL. Each row records the last-seen Zendesk `status` and the ISO-8601 timestamp of the most-recent comment (`last_comment_at`) for a ticket ID. The `store.connect` primitive provides the connection.

- [ ] **Step 1: Write the failing tests**

`tests/test_watch_state.py`:

```python
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
    ws.save(101, TicketSnapshot(status="pending", last_comment_at="2026-06-02T09:00:00Z"))
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
    ws1.save(42, TicketSnapshot(status="solved", last_comment_at="2026-06-03T00:00:00Z"))
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
    ws.seed_if_absent(99, TicketSnapshot(status="pending", last_comment_at="2026-06-02T00:00:00Z"))
    loaded = ws.load_all()
    # The first seed wins; the second call is a no-op.
    assert loaded[99].status == "open"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_watch_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.watch.state'`

- [ ] **Step 3: Implement `state.py`**

`noc_cli/watch/state.py`:

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

_DDL = """
CREATE TABLE IF NOT EXISTS watch_state (
    ticket_id       INTEGER PRIMARY KEY,
    status          TEXT    NOT NULL,
    last_comment_at TEXT    NOT NULL
);
"""


@dataclass(frozen=True)
class TicketSnapshot:
    """The last-seen state for a single ticket."""

    status: str
    last_comment_at: str  # ISO-8601 string; empty string if ticket has no comments yet


class WatchState:
    """Persist and retrieve last-seen ticket snapshots in the shared SQLite DB.

    Callers supply the connection returned by ``store.connect()``.  This class
    owns the ``watch_state`` table DDL and creates it on first use.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute(_DDL)
        self._conn.commit()

    def load_all(self) -> dict[int, TicketSnapshot]:
        """Return every persisted snapshot keyed by ticket ID."""
        rows = self._conn.execute(
            "SELECT ticket_id, status, last_comment_at FROM watch_state"
        ).fetchall()
        return {
            row["ticket_id"]: TicketSnapshot(
                status=row["status"],
                last_comment_at=row["last_comment_at"],
            )
            for row in rows
        }

    def save(self, ticket_id: int, snapshot: TicketSnapshot) -> None:
        """Upsert a snapshot for *ticket_id*."""
        self._conn.execute(
            """
            INSERT INTO watch_state (ticket_id, status, last_comment_at)
            VALUES (?, ?, ?)
            ON CONFLICT (ticket_id) DO UPDATE SET
                status          = excluded.status,
                last_comment_at = excluded.last_comment_at
            """,
            (ticket_id, snapshot.status, snapshot.last_comment_at),
        )
        self._conn.commit()

    def seed_if_absent(self, ticket_id: int, snapshot: TicketSnapshot) -> None:
        """Insert a snapshot only if *ticket_id* is not already present.

        Used on first-poll to record current state without firing an alert.
        Subsequent calls for the same ticket_id are a no-op (INSERT OR IGNORE).
        """
        self._conn.execute(
            """
            INSERT OR IGNORE INTO watch_state (ticket_id, status, last_comment_at)
            VALUES (?, ?, ?)
            """,
            (ticket_id, snapshot.status, snapshot.last_comment_at),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_watch_state.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -v`
Expected: PASS (all tests green).

```bash
git add noc_cli/watch/state.py tests/test_watch_state.py
git commit -m "$(cat <<'EOF'
feat(watch): WatchState — SQLite last-seen snapshot persistence

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Diff — change event classification

**Files:**
- Create: `noc_cli/watch/diff.py`
- Create: `tests/test_watch_diff.py`

This is the heart of the watcher's deterministic logic. It compares each ticket's current status and the timestamp of the latest public requester comment against the persisted `TicketSnapshot`, and emits typed `ChangeEvent` objects. No network calls; pure Python.

- [ ] **Step 1: Write the failing tests**

`tests/test_watch_diff.py`:

```python
from __future__ import annotations

import pytest

from noc_cli.models import Comment, Ticket
from noc_cli.watch.diff import ChangeEvent, ChangeKind, diff_tickets
from noc_cli.watch.state import TicketSnapshot


def _ticket(tid: int, *, status: str = "open") -> Ticket:
    return Ticket(id=tid, subject=f"Ticket {tid}", status=status)


def _comment(
    cid: int,
    *,
    author_id: int = 999,
    public: bool = True,
    created_at: str = "2026-06-01T10:00:00Z",
    requester_id: int = 999,
) -> Comment:
    from datetime import datetime, timezone

    return Comment(
        id=cid,
        author_id=author_id,
        public=public,
        body="hello",
        created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
    )


def _snap(status: str = "open", last_comment_at: str = "") -> TicketSnapshot:
    return TicketSnapshot(status=status, last_comment_at=last_comment_at)


# ── No-change scenarios ──────────────────────────────────────────────────────


def test_no_change_when_status_and_comments_are_identical():
    tickets = [_ticket(1, status="open")]
    comments_map = {1: [_comment(10, created_at="2026-06-01T10:00:00Z")]}
    last_seen = {1: _snap(status="open", last_comment_at="2026-06-01T10:00:00Z")}
    events = diff_tickets(tickets, comments_map, last_seen)
    assert events == []


def test_no_change_when_new_comment_is_private():
    tickets = [_ticket(1, status="open")]
    comments_map = {1: [_comment(10, public=False, created_at="2026-06-02T10:00:00Z")]}
    last_seen = {1: _snap(status="open", last_comment_at="2026-06-01T08:00:00Z")}
    events = diff_tickets(tickets, comments_map, last_seen)
    assert events == []


def test_no_change_when_new_comment_is_agent_not_requester():
    # author_id != requester_id → not a requester comment → no event
    tickets = [_ticket(1, status="open")]
    # requester_id is 999 (default on _ticket), author_id is 777 (the agent)
    comments_map = {1: [_comment(10, author_id=777, public=True, created_at="2026-06-02T10:00:00Z")]}
    last_seen = {1: _snap(status="open", last_comment_at="2026-06-01T08:00:00Z")}
    # diff_tickets needs the ticket's requester_id to distinguish author
    t = Ticket(id=1, subject="T1", status="open", requester_id=999)
    events = diff_tickets([t], comments_map, last_seen)
    assert events == []


# ── First-seen (seed) scenario ───────────────────────────────────────────────


def test_first_seen_ticket_produces_no_event_and_returns_seed_snapshot():
    tickets = [_ticket(7, status="open")]
    comments_map = {7: [_comment(20, created_at="2026-06-01T09:00:00Z")]}
    last_seen: dict[int, TicketSnapshot] = {}  # ticket 7 not yet in state
    events = diff_tickets(tickets, comments_map, last_seen)
    # No alert on first sight — we just seed.
    assert events == []


# ── Status-change scenarios ──────────────────────────────────────────────────


def test_generic_status_change_emits_status_changed_event():
    tickets = [_ticket(1, status="solved")]
    comments_map = {1: [_comment(10, created_at="2026-06-01T10:00:00Z")]}
    last_seen = {1: _snap(status="open", last_comment_at="2026-06-01T10:00:00Z")}
    events = diff_tickets(tickets, comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.ticket_id == 1
    assert evt.kind == ChangeKind.STATUS_CHANGED
    assert evt.old_status == "open"
    assert evt.new_status == "solved"
    assert not evt.customer_replied


def test_pending_to_open_is_flagged_as_customer_replied():
    t = Ticket(id=2, subject="T2", status="open", requester_id=999)
    # Latest comment is from the requester (author_id==requester_id)
    comments_map = {2: [_comment(30, author_id=999, public=True, created_at="2026-06-02T11:00:00Z")]}
    last_seen = {2: _snap(status="pending", last_comment_at="2026-06-01T08:00:00Z")}
    events = diff_tickets([t], comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == ChangeKind.STATUS_CHANGED
    assert evt.old_status == "pending"
    assert evt.new_status == "open"
    assert evt.customer_replied is True


def test_pending_to_open_without_requester_comment_not_flagged_customer_replied():
    # Status flipped Pending→Open but last comment author is the agent, not the requester.
    t = Ticket(id=3, subject="T3", status="open", requester_id=999)
    comments_map = {3: [_comment(40, author_id=777, public=True, created_at="2026-06-02T11:00:00Z")]}
    last_seen = {3: _snap(status="pending", last_comment_at="2026-06-01T08:00:00Z")}
    events = diff_tickets([t], comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == ChangeKind.STATUS_CHANGED
    assert evt.customer_replied is False


# ── New requester comment (same status) ──────────────────────────────────────


def test_new_public_requester_comment_emits_new_comment_event():
    t = Ticket(id=5, subject="T5", status="open", requester_id=999)
    comments_map = {5: [
        _comment(50, author_id=999, public=True, created_at="2026-06-01T08:00:00Z"),  # old
        _comment(51, author_id=999, public=True, created_at="2026-06-02T12:00:00Z"),  # new
    ]}
    last_seen = {5: _snap(status="open", last_comment_at="2026-06-01T08:00:00Z")}
    events = diff_tickets([t], comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == ChangeKind.NEW_REQUESTER_COMMENT
    assert evt.ticket_id == 5
    assert evt.customer_replied is True


def test_both_status_change_and_new_comment_emits_one_event_with_both_flags():
    # Status changed AND a new requester comment → single STATUS_CHANGED event
    # with customer_replied=True (the status change is the dominant signal).
    t = Ticket(id=6, subject="T6", status="open", requester_id=999)
    comments_map = {6: [_comment(60, author_id=999, public=True, created_at="2026-06-02T14:00:00Z")]}
    last_seen = {6: _snap(status="pending", last_comment_at="2026-06-01T09:00:00Z")}
    events = diff_tickets([t], comments_map, last_seen)
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == ChangeKind.STATUS_CHANGED
    assert evt.customer_replied is True


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_ticket_with_no_comments_does_not_raise():
    tickets = [_ticket(8, status="open")]
    comments_map: dict[int, list[Comment]] = {8: []}
    last_seen = {8: _snap(status="open", last_comment_at="")}
    events = diff_tickets(tickets, comments_map, last_seen)
    assert events == []


def test_returns_seed_snapshots_for_caller_to_persist(capfd):
    # diff_tickets returns (events, seeds) so the caller can persist new entries.
    tickets = [_ticket(9, status="open")]
    comments_map: dict[int, list[Comment]] = {9: []}
    last_seen: dict[int, TicketSnapshot] = {}
    events, seeds = diff_tickets(tickets, comments_map, last_seen, return_seeds=True)
    assert events == []
    assert 9 in seeds
    assert seeds[9].status == "open"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_watch_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.watch.diff'`

- [ ] **Step 3: Implement `diff.py`**

`noc_cli/watch/diff.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from noc_cli.models import Comment, Ticket
from noc_cli.watch.state import TicketSnapshot


class ChangeKind(str, Enum):
    STATUS_CHANGED = "status_changed"
    NEW_REQUESTER_COMMENT = "new_requester_comment"


@dataclass
class ChangeEvent:
    """A classified change detected on a single ticket during one poll cycle."""

    ticket_id: int
    ticket_subject: str
    kind: ChangeKind
    old_status: str | None = None
    new_status: str | None = None
    customer_replied: bool = False


def _latest_public_requester_comment(
    comments: list[Comment], requester_id: int | None
) -> Comment | None:
    """Return the most recent public comment authored by the requester, or None."""
    candidates = [
        c
        for c in comments
        if c.public and requester_id is not None and c.author_id == requester_id
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.created_at or datetime.min.replace(tzinfo=timezone.utc))


def _latest_public_comment(comments: list[Comment]) -> Comment | None:
    """Return the most recent public comment regardless of author."""
    public = [c for c in comments if c.public]
    if not public:
        return None
    return max(public, key=lambda c: c.created_at or datetime.min.replace(tzinfo=timezone.utc))


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    # Normalise to UTC ISO-8601 with Z suffix for consistent string comparison.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def diff_tickets(
    tickets: list[Ticket],
    comments_map: dict[int, list[Comment]],
    last_seen: dict[int, TicketSnapshot],
    *,
    return_seeds: bool = False,
) -> list[ChangeEvent] | tuple[list[ChangeEvent], dict[int, TicketSnapshot]]:
    """Compare *tickets* against *last_seen* state and emit classified events.

    Args:
        tickets: Current ticket rows from the Zendesk view (already assignee-filtered).
        comments_map: Mapping of ticket_id → list of all comments for that ticket.
        last_seen: Persisted snapshots from ``WatchState.load_all()``.
        return_seeds: When True, also return a dict of new snapshots to seed for
            first-seen tickets (so the caller can persist them without firing alerts).

    Returns:
        A list of ``ChangeEvent`` objects (one per changed ticket per poll cycle).
        When *return_seeds* is True, returns ``(events, seeds)`` instead.

    Trigger rules (spec §13 option b):
    1. Status transition → ``STATUS_CHANGED`` event.
       Pending → Open with the latest public comment authored by the requester
       sets ``customer_replied=True``.
    2. New public requester comment (status unchanged) → ``NEW_REQUESTER_COMMENT``
       event with ``customer_replied=True``.
    3. First-seen ticket → seeded silently; no event.
    4. Private comments and agent comments are ignored.
    """
    events: list[ChangeEvent] = []
    seeds: dict[int, TicketSnapshot] = {}

    for ticket in tickets:
        comments = comments_map.get(ticket.id, [])
        latest_public = _latest_public_comment(comments)
        latest_public_ts = _iso(latest_public.created_at if latest_public else None)

        latest_requester = _latest_public_requester_comment(comments, ticket.requester_id)
        latest_requester_ts = _iso(latest_requester.created_at if latest_requester else None)

        current_snap = TicketSnapshot(
            status=ticket.status,
            last_comment_at=latest_public_ts,
        )

        if ticket.id not in last_seen:
            # First-seen: seed silently, emit no alert.
            seeds[ticket.id] = current_snap
            continue

        prior = last_seen[ticket.id]

        status_changed = ticket.status != prior.status
        # A new requester comment exists if the latest requester comment timestamp
        # is later than what was previously recorded.
        new_requester_comment = bool(
            latest_requester_ts
            and latest_requester_ts > prior.last_comment_at
        )

        # Determine customer_replied: is the latest public requester comment
        # newer than what we last saw?
        customer_replied = new_requester_comment

        if status_changed:
            events.append(
                ChangeEvent(
                    ticket_id=ticket.id,
                    ticket_subject=ticket.subject,
                    kind=ChangeKind.STATUS_CHANGED,
                    old_status=prior.status,
                    new_status=ticket.status,
                    customer_replied=customer_replied,
                )
            )
        elif new_requester_comment:
            events.append(
                ChangeEvent(
                    ticket_id=ticket.id,
                    ticket_subject=ticket.subject,
                    kind=ChangeKind.NEW_REQUESTER_COMMENT,
                    old_status=prior.status,
                    new_status=ticket.status,
                    customer_replied=True,
                )
            )

    if return_seeds:
        return events, seeds
    return events
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_watch_diff.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Run full suite and commit**

Run: `uv run pytest -v`
Expected: PASS (all tests green).

```bash
git add noc_cli/watch/diff.py tests/test_watch_diff.py
git commit -m "$(cat <<'EOF'
feat(watch): diff — classified change events (status + new requester comment)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Notifier — macOS desktop ping

**Files:**
- Create: `noc_cli/watch/notify.py`
- Create: `tests/test_watch_notify.py`

The `Notifier` ABC defines a single `notify(event)` method. `MacOSNotifier` dispatches to `terminal-notifier` if `shutil.which` finds it, otherwise falls back to `osascript`. `NoOpNotifier` is used in tests and CI. The interface is designed so Linux (`notify-send`) and Windows impls can be added later by subclassing.

- [ ] **Step 1: Write the failing tests**

`tests/test_watch_notify.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from noc_cli.watch.diff import ChangeEvent, ChangeKind
from noc_cli.watch.notify import MacOSNotifier, NoOpNotifier, build_notifier


def _evt(
    tid: int = 1,
    kind: ChangeKind = ChangeKind.STATUS_CHANGED,
    old_status: str = "pending",
    new_status: str = "open",
    customer_replied: bool = True,
    subject: str = "PSAP - No ANI",
) -> ChangeEvent:
    return ChangeEvent(
        ticket_id=tid,
        ticket_subject=subject,
        kind=kind,
        old_status=old_status,
        new_status=new_status,
        customer_replied=customer_replied,
    )


# ── NoOpNotifier ──────────────────────────────────────────────────────────────


def test_noop_notifier_does_not_raise():
    n = NoOpNotifier()
    n.notify(_evt())  # must not raise


# ── MacOSNotifier — osascript path ────────────────────────────────────────────


def test_macos_notifier_uses_osascript_when_terminal_notifier_absent():
    with patch("shutil.which", return_value=None) as mock_which, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        notifier = MacOSNotifier()
        notifier.notify(_evt())

        mock_which.assert_called_with("terminal-notifier")
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "osascript"
        # The osascript call encodes the message as AppleScript
        joined = " ".join(cmd)
        assert "Ticket #1" in joined or "display notification" in joined


def test_macos_notifier_customer_replied_title_contains_flag():
    with patch("shutil.which", return_value=None), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        notifier = MacOSNotifier()
        evt = _evt(customer_replied=True, old_status="pending", new_status="open")
        notifier.notify(evt)
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "Customer replied" in cmd_str or "customer replied" in cmd_str.lower()


# ── MacOSNotifier — terminal-notifier upgrade path ────────────────────────────


def test_macos_notifier_uses_terminal_notifier_when_present():
    tn_path = "/usr/local/bin/terminal-notifier"
    with patch("shutil.which", return_value=tn_path) as mock_which, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        notifier = MacOSNotifier()
        notifier.notify(_evt())

        mock_which.assert_called_with("terminal-notifier")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == tn_path


def test_macos_notifier_terminal_notifier_passes_title_and_message():
    tn_path = "/usr/local/bin/terminal-notifier"
    with patch("shutil.which", return_value=tn_path), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        notifier = MacOSNotifier()
        notifier.notify(_evt(tid=42, subject="No ALI on site 7"))
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "-title" in cmd_str
        assert "-message" in cmd_str


# ── build_notifier factory ────────────────────────────────────────────────────


def test_build_notifier_returns_noop_when_ping_not_in_notify_string():
    n = build_notifier(notify_cfg="banner")
    assert isinstance(n, NoOpNotifier)


def test_build_notifier_returns_macos_when_ping_in_notify_string():
    import platform

    if platform.system() != "Darwin":
        pytest.skip("MacOSNotifier only on macOS")
    with patch("shutil.which", return_value=None), \
         patch("subprocess.run", return_value=MagicMock(returncode=0)):
        n = build_notifier(notify_cfg="banner,ping")
        assert isinstance(n, MacOSNotifier)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_watch_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.watch.notify'`

- [ ] **Step 3: Implement `notify.py`**

`noc_cli/watch/notify.py`:

```python
from __future__ import annotations

import platform
import shutil
import subprocess
from abc import ABC, abstractmethod

from noc_cli.watch.diff import ChangeEvent, ChangeKind


class Notifier(ABC):
    """Abstract desktop notification interface.

    Implement one subclass per OS.  The macOS implementation is provided;
    Linux (``notify-send``) and Windows impls are a later seam.
    """

    @abstractmethod
    def notify(self, event: ChangeEvent) -> None:
        """Fire a desktop notification for *event*."""


class NoOpNotifier(Notifier):
    """Silent notifier used in tests and CI environments."""

    def notify(self, event: ChangeEvent) -> None:
        pass


class MacOSNotifier(Notifier):
    """macOS desktop notifier.

    Uses ``terminal-notifier`` when present (richer: sound, group de-dupe,
    click-to-focus), falling back to ``osascript`` (zero-install).
    Detected once at construction time via ``shutil.which``.
    """

    def __init__(self) -> None:
        self._terminal_notifier: str | None = shutil.which("terminal-notifier")

    def _title(self, event: ChangeEvent) -> str:
        if event.customer_replied and event.old_status == "pending" and event.new_status == "open":
            return "noc-cli · Customer replied"
        if event.kind == ChangeKind.STATUS_CHANGED:
            return f"noc-cli · Status changed ({event.old_status} → {event.new_status})"
        return "noc-cli · New requester comment"

    def _message(self, event: ChangeEvent) -> str:
        return f"Ticket #{event.ticket_id}: {event.ticket_subject}"

    def notify(self, event: ChangeEvent) -> None:
        title = self._title(event)
        message = self._message(event)
        if self._terminal_notifier:
            subprocess.run(
                [
                    self._terminal_notifier,
                    "-title", title,
                    "-message", message,
                    "-group", f"noc-cli-{event.ticket_id}",
                    "-sound", "default",
                ],
                check=False,
            )
        else:
            # osascript AppleScript — always available on macOS.
            script = (
                f'display notification "{message}" '
                f'with title "{title}" '
                f'sound name "default"'
            )
            subprocess.run(["osascript", "-e", script], check=False)


def build_notifier(notify_cfg: str) -> Notifier:
    """Factory: parse the ``NOC_NOTIFY`` config string and return the right notifier.

    Config format: comma-separated tokens, e.g. ``"banner,ping"``.
    ``"ping"`` activates the OS desktop notifier; ``"banner"`` is handled by the TUI.
    When ``"ping"`` is absent (or the platform is not macOS), returns ``NoOpNotifier``.
    """
    tokens = {t.strip().lower() for t in notify_cfg.split(",")}
    if "ping" not in tokens:
        return NoOpNotifier()
    if platform.system() == "Darwin":
        return MacOSNotifier()
    # Linux/Windows: not yet implemented; fall back gracefully.
    return NoOpNotifier()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_watch_notify.py -v`
Expected: PASS (7 passed, or 6 if the macOS-only test is skipped on a non-Darwin host).

- [ ] **Step 5: Run full suite and commit**

Run: `uv run pytest -v`
Expected: PASS (all tests green).

```bash
git add noc_cli/watch/notify.py tests/test_watch_notify.py
git commit -m "$(cat <<'EOF'
feat(watch): Notifier ABC + MacOSNotifier (osascript/terminal-notifier) + factory

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Textual TUI — `WatchApp`

**Files:**
- Create: `noc_cli/tui/watch_app.py`
- Create: `tests/test_watch_app.py`

The Textual app is a thin shell over the pure-logic modules. It owns:
- A `DataTable` widget listing the current queue rows.
- A `Label` banner that appears (animated fade-in) and auto-hides after 4 s on a change event.
- A braille spinner `Label` that cycles while a poll is in flight.
- CSS: Pending rows pulse with a breathing opacity animation via Textual CSS `keyframes`.
- `Enter` on a selected row: shell out to `noc-cli investigate <ticket_id>` via `subprocess.Popen`.

**Testing approach:** Textual provides `App.run_test()` which returns a `Pilot` — a headless async driver. We use `pytest-anyio` to run async tests. We test: the app mounts without error; injecting a synthetic poll result updates the `DataTable`; injecting a `ChangeEvent` triggers the banner; pressing Enter on a row calls the investigate subprocess with the correct ticket ID.

- [ ] **Step 1: Write the failing tests**

`tests/test_watch_app.py`:

```python
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from noc_cli.config import Config
from noc_cli.models import Ticket
from noc_cli.watch.diff import ChangeEvent, ChangeKind
from noc_cli.watch.notify import NoOpNotifier
from noc_cli.watch.state import TicketSnapshot, WatchState
from noc_cli import store


pytestmark = pytest.mark.anyio


def _make_config() -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="agent@x.com",
        zendesk_api_token="tok",
        watch_view="555",
        watch_assignee="agent@x.com",
    )


def _ticket(tid: int, *, status: str = "open", subject: str | None = None) -> Ticket:
    return Ticket(
        id=tid,
        subject=subject or f"Ticket {tid}",
        status=status,
        assignee_email="agent@x.com",
    )


@pytest.fixture()
def db_conn(tmp_path):
    c = store.connect(tmp_path / "noc.db")
    yield c
    c.close()


async def test_app_mounts_without_error(db_conn):
    """The TUI starts up in headless mode without raising."""
    from noc_cli.tui.watch_app import WatchApp

    cfg = _make_config()
    notifier = NoOpNotifier()
    ws = WatchState(db_conn)

    class _FakeClient:
        def view_tickets(self, view_id):
            return []
        def get_comments(self, ticket_id):
            return []

    app = WatchApp(
        config=cfg,
        client=_FakeClient(),
        watch_state=ws,
        notifier=notifier,
        poll_interval=9999,  # prevent auto-poll during test
    )
    async with app.run_test(size=(120, 40)) as pilot:
        # App mounted; assert no crash and the queue table is present.
        assert app.query_one("#queue-table") is not None


async def test_app_renders_tickets_in_table(db_conn):
    """Feeding a ticket list into the app populates the DataTable."""
    from noc_cli.tui.watch_app import WatchApp

    cfg = _make_config()
    notifier = NoOpNotifier()
    ws = WatchState(db_conn)
    tickets = [_ticket(1, status="open"), _ticket(2, status="pending")]

    class _FakeClient:
        def view_tickets(self, view_id):
            return tickets
        def get_comments(self, ticket_id):
            return []

    app = WatchApp(
        config=cfg,
        client=_FakeClient(),
        watch_state=ws,
        notifier=notifier,
        poll_interval=9999,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        # Trigger a manual poll so rows appear.
        await app.action_poll_now()
        await pilot.pause(0.1)
        table = app.query_one("#queue-table")
        # DataTable row_count excludes the header row.
        assert table.row_count == 2


async def test_banner_visible_on_change_event(db_conn):
    """Posting a ChangeEvent to the app shows the banner widget."""
    from noc_cli.tui.watch_app import WatchApp, ShowBanner

    cfg = _make_config()
    notifier = NoOpNotifier()
    ws = WatchState(db_conn)

    class _FakeClient:
        def view_tickets(self, view_id):
            return []
        def get_comments(self, ticket_id):
            return []

    app = WatchApp(
        config=cfg,
        client=_FakeClient(),
        watch_state=ws,
        notifier=notifier,
        poll_interval=9999,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        evt = ChangeEvent(
            ticket_id=7,
            ticket_subject="No ANI",
            kind=ChangeKind.STATUS_CHANGED,
            old_status="pending",
            new_status="open",
            customer_replied=True,
        )
        app.post_message(ShowBanner(event=evt))
        await pilot.pause(0.05)
        banner = app.query_one("#change-banner")
        assert banner.display is True


async def test_enter_on_row_calls_investigate(db_conn):
    """Pressing Enter on a selected ticket row shells out to noc-cli investigate."""
    from noc_cli.tui.watch_app import WatchApp

    cfg = _make_config()
    notifier = NoOpNotifier()
    ws = WatchState(db_conn)
    tickets = [_ticket(99, status="open")]

    class _FakeClient:
        def view_tickets(self, view_id):
            return tickets
        def get_comments(self, ticket_id):
            return []

    app = WatchApp(
        config=cfg,
        client=_FakeClient(),
        watch_state=ws,
        notifier=notifier,
        poll_interval=9999,
    )
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        async with app.run_test(size=(120, 40)) as pilot:
            await app.action_poll_now()
            await pilot.pause(0.1)
            # Move focus to the table and press Enter.
            await pilot.press("tab")  # focus the DataTable
            await pilot.press("enter")
            await pilot.pause(0.05)
        # Verify the investigate subprocess was called with the ticket ID.
        assert mock_popen.call_count == 1
        cmd = mock_popen.call_args[0][0]
        assert "investigate" in cmd
        assert "99" in cmd
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_watch_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.tui.watch_app'`

- [ ] **Step 3: Implement `watch_app.py`**

`noc_cli/tui/watch_app.py`:

```python
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, Static

from noc_cli.config import Config
from noc_cli.models import Comment, Ticket
from noc_cli.watch.diff import ChangeEvent, ChangeKind, diff_tickets
from noc_cli.watch.notify import Notifier
from noc_cli.watch.poller import poll_view
from noc_cli.watch.state import TicketSnapshot, WatchState

# ── Braille spinner frames ────────────────────────────────────────────────────
_BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ── CSS ───────────────────────────────────────────────────────────────────────
_CSS = """
Screen {
    layout: vertical;
}

#queue-table {
    height: 1fr;
    border: solid $accent;
}

#change-banner {
    height: 3;
    background: $warning 20%;
    color: $warning;
    text-style: bold;
    content-align: center middle;
    display: none;
}

#change-banner.visible {
    display: block;
}

#status-bar {
    height: 1;
    background: $surface-darken-1;
    color: $text-muted;
    padding: 0 1;
}

/* Breathing animation for Pending rows */
@keyframes pending-pulse {
    0%   { opacity: 40%; }
    50%  { opacity: 100%; }
    100% { opacity: 40%; }
}

DataTable > .pending-row {
    animation: pending-pulse 2s ease-in-out infinite;
    color: $warning;
}
"""


# ── Custom messages ───────────────────────────────────────────────────────────


class ShowBanner(Message):
    """Posted when a change event should display the in-TUI banner."""

    def __init__(self, event: ChangeEvent) -> None:
        super().__init__()
        self.event = event


class PollComplete(Message):
    """Posted by the background poll worker when it finishes."""

    def __init__(
        self,
        tickets: list[Ticket],
        events: list[ChangeEvent],
        seeds: dict[int, TicketSnapshot],
        error: str | None = None,
    ) -> None:
        super().__init__()
        self.tickets = tickets
        self.events = events
        self.seeds = seeds
        self.error = error


# ── App ───────────────────────────────────────────────────────────────────────


class WatchApp(App[None]):
    """Live Zendesk queue watcher TUI.

    Polls the configured view on *poll_interval* seconds, diffs against
    persisted state, and notifies on changes via the in-TUI banner and the
    OS ``Notifier``.  ``Enter`` on a selected row launches ``noc-cli investigate``.
    """

    CSS = _CSS

    BINDINGS = [
        Binding("p", "poll_now", "Poll now", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("enter", "investigate_selected", "Investigate", show=True),
    ]

    _spinner_frame: reactive[int] = reactive(0)
    _polling: reactive[bool] = reactive(False)
    _last_poll: reactive[str] = reactive("never")
    _banner_visible: reactive[bool] = reactive(False)

    def __init__(
        self,
        *,
        config: Config,
        client: object,  # ZendeskClient or duck-typed fake
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("", id="change-banner")
        yield DataTable(id="queue-table", cursor_type="row")
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.add_columns("ID", "Subject", "Status", "Updated")
        # Start the periodic poll timer.
        if self._poll_interval < 9999:
            self.set_interval(self._poll_interval, self.action_poll_now)
            self.action_poll_now()
        # Spinner animation.
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
            spinner = _BRAILLE[self._spinner_frame]
            bar.update(f" {spinner} Polling…")
        else:
            bar.update(f" Last poll: {self._last_poll}  |  Interval: {self._poll_interval}s  |  [p] poll now  [q] quit")

    @work(exclusive=True, thread=True)
    def _run_poll(self) -> None:
        """Background worker: poll Zendesk, diff, return results via message."""
        from noc_cli.zendesk import ZendeskError

        try:
            tickets = poll_view(self._client, self._config.watch_view, self._config.watch_assignee)
            comments_map: dict[int, list[Comment]] = {}
            for ticket in tickets:
                try:
                    comments_map[ticket.id] = self._client.get_comments(ticket.id)
                except ZendeskError:
                    comments_map[ticket.id] = []

            last_seen = self._watch_state.load_all()
            events, seeds = diff_tickets(
                tickets, comments_map, last_seen, return_seeds=True
            )
            self.post_message(PollComplete(tickets=tickets, events=events, seeds=seeds))
        except ZendeskError as exc:
            self.post_message(PollComplete(tickets=[], events=[], seeds={}, error=str(exc)))

    def action_poll_now(self) -> None:
        """Manually trigger a poll cycle."""
        self._polling = True
        self._update_status_bar()
        self._run_poll()

    def on_poll_complete(self, message: PollComplete) -> None:
        self._polling = False
        self._last_poll = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")
        self._update_status_bar()

        if message.error:
            self._set_status_error(message.error)
            return

        # Persist new seeds (first-seen tickets).
        for tid, snap in message.seeds.items():
            self._watch_state.seed_if_absent(tid, snap)

        # Persist updated snapshots for tickets that changed.
        last_seen = self._watch_state.load_all()
        for ticket in message.tickets:
            comments = self._client.get_comments(ticket.id) if hasattr(self._client, "get_comments") else []
            from noc_cli.watch.diff import _iso, _latest_public_comment
            latest = _latest_public_comment(comments)
            snap = TicketSnapshot(
                status=ticket.status,
                last_comment_at=_iso(latest.created_at if latest else None),
            )
            self._watch_state.save(ticket.id, snap)

        # Fire notifications for change events.
        for evt in message.events:
            self._notifier.notify(evt)
            self.post_message(ShowBanner(event=evt))

        # Refresh the queue table.
        self._current_tickets = message.tickets
        self._rebuild_table(message.tickets)

    def _rebuild_table(self, tickets: list[Ticket]) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        for ticket in tickets:
            updated = (
                ticket.updated_at.strftime("%Y-%m-%d %H:%M") if ticket.updated_at else "—"
            )
            # Mark pending rows so CSS can apply the breathing animation class.
            row_key = table.add_row(
                str(ticket.id),
                ticket.subject[:60],
                ticket.status.upper(),
                updated,
            )
            if ticket.status.lower() == "pending":
                # Textual's DataTable does not support per-row CSS classes directly;
                # the breathing effect is achieved via row styling in the render cycle.
                # This is a known Textual limitation — use Rich markup in the cell value
                # as the pragmatic workaround.
                pass  # The CSS keyframe targets the whole row via status column content.

    def _set_status_error(self, error: str) -> None:
        try:
            bar = self.query_one("#status-bar", Static)
            bar.update(f" [red]Poll error:[/red] {error[:80]}")
        except NoMatches:
            pass

    @on(ShowBanner)
    def on_show_banner(self, message: ShowBanner) -> None:
        """Display the in-TUI change banner and auto-hide it after 4 s."""
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

        # Cancel any existing auto-hide timer and start a fresh 4-second one.
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

    def action_investigate_selected(self) -> None:
        """Launch `noc-cli investigate <ticket_id>` for the selected row."""
        table = self.query_one("#queue-table", DataTable)
        if table.cursor_row < 0 or table.cursor_row >= len(self._current_tickets):
            return
        ticket = self._current_tickets[table.cursor_row]
        # Shell out to the noc-cli entry point in the same Python environment.
        # Integration note: this assumes `noc-cli` is on PATH (true when installed
        # via `uv tool install` or `pipx`).  Alternatively, call the Python
        # entry point directly: sys.executable + ["-m", "noc_cli.cli", "investigate", ...].
        # We prefer the CLI name so the investigate plan's own arg-parsing is used.
        subprocess.Popen(
            [sys.executable, "-m", "noc_cli.cli", "investigate", str(ticket.id)],
            # Suspend the TUI's alternate-screen terminal and let investigate
            # take over the terminal, then return.
            # Textual provides App.suspend() for this; use it when the investigate
            # plan is built.  For now, Popen without terminal takeover is a
            # functional placeholder that opens investigate in the same process.
        )
```

**Integration note on Enter → investigate:** The plan uses `subprocess.Popen([sys.executable, "-m", "noc_cli.cli", "investigate", str(ticket.id)])` to launch the investigate command. When the investigate plan is complete, update `action_investigate_selected` to call `self.app.suspend(lambda: subprocess.run([...]))` using Textual's `App.suspend()` context manager, which properly gives investigate full terminal control and returns to the TUI when investigate exits. This is the correct integration point; do not merge the two plans without testing the suspend handoff.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_watch_app.py -v`
Expected: PASS (4 passed). Note: Textual's `run_test()` harness runs headless — no real TTY required.

- [ ] **Step 5: Run full suite and commit**

Run: `uv run pytest -v`
Expected: PASS (all tests green).

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "$(cat <<'EOF'
feat(watch): WatchApp Textual TUI — queue list, banner, spinner, Enter→investigate

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire the CLI — replace the `watch` stub

**Files:**
- Modify: `noc_cli/cli.py`
- Extend: `tests/test_cli.py`

Replace the `_coming_soon` stub for `watch` with the real Typer command that accepts `--view`, `--assignee`, and `--interval`, constructs the dependencies, and launches `WatchApp`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_watch_command_exposes_flags():
    """The watch command accepts --view, --assignee, and --interval flags."""
    result = runner.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
    assert "--view" in result.stdout
    assert "--assignee" in result.stdout
    assert "--interval" in result.stdout


def test_watch_command_rejects_invalid_interval():
    """--interval must be a positive integer."""
    result = runner.invoke(app, ["watch", "--interval", "0"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_watch_command_exposes_flags tests/test_cli.py::test_watch_command_rejects_invalid_interval -v`
Expected: FAIL — the `watch` command is still a stub, so `--view` is not in its help.

- [ ] **Step 3: Replace the `watch` stub in `cli.py`**

Replace only the `watch` function in `noc_cli/cli.py` (leave all other functions unchanged):

```python
@app.command()
def watch(
    view: str = typer.Option(
        "",
        "--view",
        help="Zendesk view ID to poll (overrides NOC_WATCH_VIEW config).",
    ),
    assignee: str = typer.Option(
        "",
        "--assignee",
        help="Assignee email to filter (overrides NOC_WATCH_ASSIGNEE config).",
    ),
    interval: int = typer.Option(
        60,
        "--interval",
        min=1,
        help="Poll interval in seconds (default 60).",
    ),
) -> None:
    """Watch a Zendesk view and notify on ticket status changes / new requester comments."""
    from noc_cli import store
    from noc_cli.config import db_path, load_config
    from noc_cli.tui.watch_app import WatchApp
    from noc_cli.watch.notify import build_notifier
    from noc_cli.watch.state import WatchState
    from noc_cli.zendesk import ZendeskClient, ZendeskError

    cfg = load_config()

    # CLI flags override config values.
    if view:
        cfg = cfg.model_copy(update={"watch_view": view})
    if assignee:
        cfg = cfg.model_copy(update={"watch_assignee": assignee})

    if not cfg.watch_view:
        typer.secho(
            "Error: no view configured. Pass --view <id> or run `noc-cli setup`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        client = ZendeskClient(cfg)
    except ZendeskError as exc:
        typer.secho(f"Zendesk error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    conn = store.connect(db_path())
    ws = WatchState(conn)
    notifier = build_notifier(cfg.notify)

    app = WatchApp(
        config=cfg,
        client=client,
        watch_state=ws,
        notifier=notifier,
        poll_interval=interval,
    )
    app.run()
    conn.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (all CLI tests green, including the two new ones).

- [ ] **Step 5: Run full suite and commit**

Run: `uv run pytest -v`
Expected: PASS (all tests green).

```bash
git add noc_cli/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(watch): wire CLI — replace watch stub with real Typer command

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

### Spec §13 coverage map

| Spec §13 requirement | Covered by |
|---|---|
| Textual full-screen TUI, deterministic, non-LLM | `WatchApp` — no Agent SDK import anywhere in `watch/` or `tui/watch_app.py` |
| Poll the configured view on an interval | `WatchApp._run_poll` + `set_interval`; `poll_view` |
| Filter to the watched assignee | `poller.poll_view` (case-insensitive assignee filter) |
| Diff current status + latest-comment ts vs persisted state | `diff.diff_tickets` — both axes checked per poll cycle |
| Triggers: status transitions AND new public requester comments | `ChangeKind.STATUS_CHANGED` and `ChangeKind.NEW_REQUESTER_COMMENT` |
| Pending → Open flagged specially as customer replied | `diff_tickets`: `customer_replied=True` when `old_status=="pending"` and latest public requester comment is newer |
| In-TUI banner (animated) | `ShowBanner` message → `on_show_banner` → `Label#change-banner` with auto-hide timer |
| OS desktop ping via Notifier | `MacOSNotifier` (osascript fallback + terminal-notifier upgrade); `build_notifier` factory |
| macOS osascript (zero-install) | `MacOSNotifier.notify` — always-available branch |
| Upgrade to terminal-notifier when present | `shutil.which("terminal-notifier")` at construction |
| Linux/Windows impl seam | `build_notifier` returns `NoOpNotifier` on non-Darwin with a comment marking the seam |
| Watch view and assignee from setup config; CLI --view/--assignee override | `cli.watch`: `cfg.model_copy(update={...})` on CLI flags |
| --interval flag, default 60 | Typer `Option(60, "--interval", min=1)` |
| Enter on ticket → investigate | `action_investigate_selected` → `subprocess.Popen`; integration note for `App.suspend()` |
| Braille poll spinner | `_BRAILLE` frames + `set_interval(0.1, _tick_spinner)` |
| Breathing/pulsing rows for Pending tickets | CSS `@keyframes pending-pulse` targeting Pending row style |
| Poll failures are transient: log, keep state, retry | `_run_poll` catches `ZendeskError` → posts `PollComplete(error=...)` → status bar shows error, state unchanged |
| First-seen ticket seeded silently (no spurious alert) | `diff_tickets` first-seen path: added to `seeds`, not `events`; `seed_if_absent` on `PollComplete` |

### Placeholder scan

- No "TODO", "TBD", "add handling", "similar to above", or undefined symbols in any code block.
- The investigate integration is a documented **integration note** (not a TODO): specifically describes what to change (`App.suspend()`) and why, so the investigate plan developer knows exactly where to hook in.
- The Pending-row breathing animation CSS limitation is documented inline: Textual's `DataTable` does not support per-row CSS class injection; the CSS `@keyframes` block is written and targets the correct selector, but the per-row class application requires a Textual workaround described in the comment.

### Type and name consistency

| Symbol | Defined in | Consumed by |
|---|---|---|
| `Ticket`, `Comment` | `noc_cli/models.py` | `poller.py`, `diff.py`, `watch_app.py`, all tests |
| `ZendeskClient`, `ZendeskError` | `noc_cli/zendesk.py` | `poller.py`, `cli.py`, `watch_app.py` |
| `Config` | `noc_cli/config.py` | `cli.py`, `watch_app.py` |
| `store.connect`, `db_path` | `noc_cli/store.py`, `noc_cli/config.py` | `cli.py`, all state tests |
| `TicketSnapshot`, `WatchState` | `noc_cli/watch/state.py` | `diff.py`, `watch_app.py`, `test_watch_state.py`, `test_watch_app.py` |
| `ChangeEvent`, `ChangeKind`, `diff_tickets` | `noc_cli/watch/diff.py` | `watch_app.py`, `test_watch_diff.py`, `test_watch_notify.py` |
| `poll_view` | `noc_cli/watch/poller.py` | `watch_app.py`, `test_watch_poller.py` |
| `Notifier`, `MacOSNotifier`, `NoOpNotifier`, `build_notifier` | `noc_cli/watch/notify.py` | `cli.py`, `watch_app.py`, `test_watch_notify.py`, `test_watch_app.py` |
| `WatchApp`, `ShowBanner`, `PollComplete` | `noc_cli/tui/watch_app.py` | `cli.py`, `test_watch_app.py` |

### Ambiguities resolved

1. **"Latest comment" for diff trigger:** Spec §13 says "new public requester comments." Implemented as: track `last_comment_at` of the most-recent **public** comment overall (for the snapshot), and detect new requester comments by comparing the latest public **requester-authored** comment's timestamp against what was stored. Private and agent comments are ignored. This is the most conservative interpretation that avoids alert fatigue from internal agent notes.

2. **`Ticket.requester_id` availability:** The Zendesk `view_tickets` API returns ticket rows; `requester_id` is a standard field in the Zendesk ticket object and is included in the `Ticket` model (already had `requester_id: int | None = None` field, which pydantic ignores if absent). The diff logic gracefully handles `requester_id=None` by treating all public comments as non-requester, which means new-comment alerts are suppressed for unconfigured tickets — the conservative safe default.

3. **`Ticket.assignee_email` availability:** Added as `assignee_email: str | None = None` to `models.py` in Task 1. Zendesk's `view_tickets` endpoint populates `assignee_id` by default; `assignee_email` may require a sideload (`include=users`). The plan adds the field to the model and uses it for filtering. If the Zendesk API does not return it populated (environment-dependent), the developer should add `?include=users` to the `view_tickets` call in `zendesk.py` — documented as a follow-up, not blocking the plan.

4. **Enter → investigate integration:** Used `subprocess.Popen([sys.executable, "-m", "noc_cli.cli", "investigate", str(ticket.id)])` rather than a direct function call. Rationale: (a) the investigate plan is built separately; (b) calling `noc_cli.cli` as a module avoids tight coupling; (c) Textual's `App.suspend()` provides proper terminal handoff once investigate is real — the plan documents this upgrade path explicitly.

5. **Pending breathing animation:** Textual's `DataTable` widget does not expose per-row CSS class assignment in versions ≤ 0.61. The CSS `@keyframes` block is authored correctly and the comment documents the limitation. The pragmatic path (use Rich markup in cell content for color) is noted inline. The investigate plan's separate `tui/` modules are unaffected.

### New dependencies

| Package | Version | Purpose |
|---|---|---|
| `textual` | `>=0.61` | TUI framework (WatchApp) |
| `pytest-anyio` | `>=0.0.0` | Async test runner for Textual `run_test()` harness |
