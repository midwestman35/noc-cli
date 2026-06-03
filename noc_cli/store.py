from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a configured SQLite connection, creating the parent directory if needed.

    Consumers (memory in the investigate plan, watch-state in the watch plan)
    own their own `CREATE TABLE IF NOT EXISTS` DDL; this primitive only
    guarantees a consistently configured connection at the right path.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
