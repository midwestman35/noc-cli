from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from noc_cli.store import connect


@dataclass
class InvestigationRecord:
    ticket_id: str
    symptom_tag: str
    fork_letter: str
    confidence: str
    one_line_fingerprint: str
    summary: str
    related_zendesk: list[int] = field(default_factory=list)
    rubric_version: str = ""
    investigated_at: str = ""

    def __post_init__(self) -> None:
        if not self.investigated_at:
            self.investigated_at = datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryStore:
    db_path: Path
    memory_md_path: Path

    def init(self) -> None:
        """Create the FTS5 virtual table if it does not exist. Idempotent."""
        conn = connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS investigations USING fts5(
                        ticket_id,
                        symptom_tag,
                        fork_letter,
                        confidence,
                        one_line_fingerprint,
                        summary,
                        related_zendesk,
                        rubric_version,
                        investigated_at,
                        tokenize='porter unicode61'
                    )
                    """
                )
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        return connect(self.db_path)


def append_investigation(store: MemoryStore, record: InvestigationRecord) -> None:
    """Insert a new investigation into FTS5 and append a row to MEMORY.md."""
    conn = store._conn()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO investigations(
                    ticket_id, symptom_tag, fork_letter, confidence,
                    one_line_fingerprint, summary, related_zendesk,
                    rubric_version, investigated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.ticket_id,
                    record.symptom_tag,
                    record.fork_letter,
                    record.confidence,
                    record.one_line_fingerprint,
                    record.summary,
                    json.dumps(record.related_zendesk),
                    record.rubric_version,
                    record.investigated_at,
                ),
            )
    finally:
        conn.close()
    _append_memory_md(store.memory_md_path, record)


def search(store: MemoryStore, query: str, limit: int = 10) -> list[InvestigationRecord]:
    """FTS5 full-text search over investigations. Returns ranked results."""
    if not query or not query.strip():
        return []
    conn = store._conn()
    try:
        rows = conn.execute(
            """
            SELECT ticket_id, symptom_tag, fork_letter, confidence,
                   one_line_fingerprint, summary, related_zendesk,
                   rubric_version, investigated_at
            FROM investigations
            WHERE investigations MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table not yet initialised or FTS query error — return empty.
        return []
    finally:
        conn.close()

    results = []
    for row in rows:
        results.append(
            InvestigationRecord(
                ticket_id=row["ticket_id"],
                symptom_tag=row["symptom_tag"],
                fork_letter=row["fork_letter"],
                confidence=row["confidence"],
                one_line_fingerprint=row["one_line_fingerprint"],
                summary=row["summary"],
                related_zendesk=json.loads(row["related_zendesk"] or "[]"),
                rubric_version=row["rubric_version"],
                investigated_at=row["investigated_at"],
            )
        )
    return results


def _append_memory_md(md_path: Path, record: InvestigationRecord) -> None:
    header_needed = not md_path.exists() or md_path.stat().st_size == 0
    with md_path.open("a", encoding="utf-8") as f:
        if header_needed:
            f.write("# NOC Investigation Memory\n\n")
        f.write(
            f"## Ticket {record.ticket_id} — {record.one_line_fingerprint}\n"
            f"- **Tag:** {record.symptom_tag}  **Fork:** {record.fork_letter}"
            f"  **Confidence:** {record.confidence}\n"
            f"- **At:** {record.investigated_at}\n"
            f"- {record.summary}\n\n"
        )
