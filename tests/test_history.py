from unittest.mock import MagicMock

from noc_cli.history import HISTORY_SEARCH_TAGS, seed_history
from noc_cli.memory import InvestigationRecord, MemoryStore, append_investigation
from noc_cli.models import Ticket


def test_vendor_tag_not_in_search_tags():
    assert "[vendor]" not in HISTORY_SEARCH_TAGS
    assert "[unclassified]" not in HISTORY_SEARCH_TAGS


def test_all_six_symptom_tags_present():
    expected = {
        "[apex]",
        "[low audio]",
        "[dropped calls]",
        "[No ANI]",
        "[No ALI]",
        "[event history]",
    }
    assert expected == set(HISTORY_SEARCH_TAGS)


def test_seed_history_returns_memory_results(tmp_path):
    store = MemoryStore(
        db_path=tmp_path / "noc.db", memory_md_path=tmp_path / "MEMORY.md"
    )
    store.init()
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="88001",
            symptom_tag="[apex]",
            fork_letter="B",
            confidence="Medium",
            one_line_fingerprint="Site Y / apex / station error",
            summary="Multi-station dropped; site LAN.",
            related_zendesk=[],
            rubric_version="2026-05-13",
        ),
    )
    mock_zendesk = MagicMock()
    mock_zendesk.search.return_value = []

    candidates = seed_history(
        symptom_tag="[apex]",
        zendesk_client=mock_zendesk,
        memory_store=store,
        limit=10,
    )
    assert any(c.ticket_id == "88001" for c in candidates)


def test_seed_history_merges_zendesk_results(tmp_path):
    store = MemoryStore(
        db_path=tmp_path / "noc.db", memory_md_path=tmp_path / "MEMORY.md"
    )
    store.init()

    mock_zendesk = MagicMock()
    # Production returns Ticket models, not dicts.
    mock_zendesk.search.return_value = [
        Ticket(id=41675, subject="Cobb site network error", tags=["apex"]),
    ]

    candidates = seed_history(
        symptom_tag="[apex]",
        zendesk_client=mock_zendesk,
        memory_store=store,
        limit=10,
    )
    match = next(c for c in candidates if c.ticket_id == "41675")
    assert match.subject == "Cobb site network error"
    assert match.source == "zendesk"


def test_seed_history_deduplicates(tmp_path):
    """Same ticket_id from both sources should appear only once."""
    store = MemoryStore(
        db_path=tmp_path / "noc.db", memory_md_path=tmp_path / "MEMORY.md"
    )
    store.init()
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="41675",
            symptom_tag="[apex]",
            fork_letter="B",
            confidence="Medium",
            one_line_fingerprint="Cobb / apex",
            summary="Site LAN.",
            related_zendesk=[],
            rubric_version="2026-05-13",
        ),
    )
    mock_zendesk = MagicMock()
    mock_zendesk.search.return_value = [
        Ticket(id=41675, subject="Cobb site network error", tags=["apex"]),
    ]
    candidates = seed_history(
        symptom_tag="[apex]",
        zendesk_client=mock_zendesk,
        memory_store=store,
        limit=10,
    )
    ids = [c.ticket_id for c in candidates]
    assert ids.count("41675") == 1
