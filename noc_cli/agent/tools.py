from __future__ import annotations

from noc_cli.history import seed_history
from noc_cli.redact import redact_value

HISTORY_LIMIT = 20


def run_history_search(symptom_tag, zendesk_client, memory_store, limit: int = HISTORY_LIMIT):
    """Merged local-FTS5 + live-Zendesk history search. Returns (redacted_dict, total).

    `seed_history` swallows Zendesk errors internally (returns []), so this never
    raises on a network failure.
    """
    candidates = seed_history(
        symptom_tag,
        zendesk_client=zendesk_client,
        memory_store=memory_store,
        limit=limit,
    )
    items = [
        {
            "ticket_id": c.ticket_id,
            "subject": c.subject,
            "source": c.source,
            "relevance_hint": c.relevance_hint,
            "tags": list(c.tags or []),
        }
        for c in candidates
    ]
    return redact_value({"candidates": items, "count": len(items)})
