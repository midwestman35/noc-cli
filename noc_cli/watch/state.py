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
