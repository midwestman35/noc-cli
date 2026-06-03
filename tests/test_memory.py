import pytest

from noc_cli.memory import (
    InvestigationRecord,
    MemoryStore,
    append_investigation,
    search,
)


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "noc.db"
    s = MemoryStore(db_path=db, memory_md_path=tmp_path / "MEMORY.md")
    s.init()
    return s


def test_append_and_search_basic(store):
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="18432",
            symptom_tag="[apex]",
            fork_letter="B",
            confidence="Medium",
            one_line_fingerprint="Aurora / apex / Network Error / 06:30 UTC",
            summary="Multi-station error; site LAN issue; Fork B; linked master 41675.",
            related_zendesk=[41675],
            rubric_version="2026-05-13",
        ),
    )
    results = search(store, "Aurora network error")
    assert len(results) >= 1
    assert results[0].ticket_id == "18432"


def test_search_returns_empty_on_no_match(store):
    results = search(store, "zzz_no_such_term_xyz")
    assert results == []


def test_append_writes_memory_md(store, tmp_path):
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="99001",
            symptom_tag="[No ANI]",
            fork_letter="A",
            confidence="High",
            one_line_fingerprint="Test / No ANI / carrier",
            summary="Carrier INVITE had no ANI; Fork A engineering.",
            related_zendesk=[],
            rubric_version="2026-05-13",
        ),
    )
    md = (tmp_path / "MEMORY.md").read_text()
    assert "99001" in md
    assert "[No ANI]" in md


def test_init_is_idempotent(tmp_path):
    db = tmp_path / "noc.db"
    md = tmp_path / "MEMORY.md"
    s = MemoryStore(db_path=db, memory_md_path=md)
    s.init()
    s.init()  # second call must not raise


def test_search_matches_symptom_tag(store):
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="55001",
            symptom_tag="[low audio]",
            fork_letter="A",
            confidence="High",
            one_line_fingerprint="Site X / low audio",
            summary="RTP absent; SDP relay issue; Fork A.",
            related_zendesk=[],
            rubric_version="2026-05-13",
        ),
    )
    results = search(store, "low audio RTP")
    assert any(r.ticket_id == "55001" for r in results)


def test_search_empty_and_whitespace_return_empty(store):
    assert search(store, "") == []
    assert search(store, "   \t\n ") == []


def test_search_before_init_swallows_operational_error(tmp_path):
    # No table yet -> sqlite3.OperationalError -> swallowed -> []
    s = MemoryStore(db_path=tmp_path / "noc.db", memory_md_path=tmp_path / "MEMORY.md")
    assert search(s, "anything") == []


def test_investigated_at_auto_set_and_preserved():
    auto = InvestigationRecord(
        ticket_id="1", symptom_tag="[apex]", fork_letter="A", confidence="High",
        one_line_fingerprint="f", summary="s",
    )
    assert auto.investigated_at  # auto-populated, non-empty
    assert "T" in auto.investigated_at and auto.investigated_at.endswith("+00:00")
    supplied = InvestigationRecord(
        ticket_id="1", symptom_tag="[apex]", fork_letter="A", confidence="High",
        one_line_fingerprint="f", summary="s",
        investigated_at="2020-01-01T00:00:00+00:00",
    )
    assert supplied.investigated_at == "2020-01-01T00:00:00+00:00"


def test_related_zendesk_round_trips(store):
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="77001", symptom_tag="[apex]", fork_letter="B", confidence="Medium",
            one_line_fingerprint="round trip test apex",
            summary="related zendesk round trip", related_zendesk=[41675, 99234],
            rubric_version="2026-05-13",
        ),
    )
    results = search(store, "round trip")
    match = next(r for r in results if r.ticket_id == "77001")
    assert match.related_zendesk == [41675, 99234]
