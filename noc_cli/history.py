from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noc_cli.memory import MemoryStore
    from noc_cli.zendesk import ZendeskClient

# The six symptom-specific tags used for history search.
# [unclassified] is excluded (too broad to produce useful history).
# [vendor] is excluded (it is a routing exclusion tag, never a symptom tag).
HISTORY_SEARCH_TAGS: tuple[str, ...] = (
    "[apex]",
    "[low audio]",
    "[dropped calls]",
    "[No ANI]",
    "[No ALI]",
    "[event history]",
)

_TAG_TO_ZD_KEYWORD: dict[str, str] = {
    "[apex]": "apex",
    "[low audio]": "low_audio",
    "[dropped calls]": "dropped_calls",
    "[No ANI]": "no_ani",
    "[No ALI]": "no_ali",
    "[event history]": "event_history",
}


@dataclass
class HistoryCandidate:
    ticket_id: str
    subject: str
    source: str  # "zendesk" | "memory"
    relevance_hint: str = ""
    tags: list[str] = field(default_factory=list)


def seed_history(
    symptom_tag: str,
    zendesk_client: ZendeskClient,
    memory_store: MemoryStore,
    limit: int = 20,
) -> list[HistoryCandidate]:
    """Return a merged, deduplicated candidate pool for the agent's historical_matches.

    Sources:
    1. Live Zendesk search on the symptom tag keyword (approved tags only;
       [vendor] is never searched). ZendeskClient.search() returns list[Ticket]
       pydantic models — read attributes via getattr, never dict .get().
    2. Local FTS5 memory search on the symptom tag string.

    Deduplication is by ticket_id (string). Zendesk results take precedence
    (they carry the latest subject and tags).
    """
    seen: dict[str, HistoryCandidate] = {}

    # 1. Zendesk live search (skip if tag not in approved set)
    if symptom_tag in HISTORY_SEARCH_TAGS:
        keyword = _TAG_TO_ZD_KEYWORD.get(symptom_tag, "")
        if keyword:
            try:
                zd_results = zendesk_client.search(keyword) or []
            except Exception:
                zd_results = []
            for ticket in zd_results[:limit]:
                tid = str(getattr(ticket, "id", "") or "")
                if tid:
                    seen[tid] = HistoryCandidate(
                        ticket_id=tid,
                        subject=getattr(ticket, "subject", "") or "",
                        source="zendesk",
                        tags=list(getattr(ticket, "tags", []) or []),
                    )

    # 2. Local memory FTS5 search
    from noc_cli.memory import search as memory_search

    mem_results = memory_search(memory_store, symptom_tag.strip("[]"), limit=limit)
    for rec in mem_results:
        tid = rec.ticket_id
        if tid not in seen:
            seen[tid] = HistoryCandidate(
                ticket_id=tid,
                subject=rec.one_line_fingerprint,
                source="memory",
                relevance_hint=rec.summary[:120],
                tags=[rec.symptom_tag],
            )

    return list(seen.values())[:limit]
