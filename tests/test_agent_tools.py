from noc_cli.agent.tools import run_history_search
from noc_cli.memory import InvestigationRecord, MemoryStore, append_investigation


class _FakeZd:
    def search(self, keyword):
        return []  # no live Zendesk in tests


def test_run_history_search_returns_redacted_candidates(tmp_path):
    store = MemoryStore(db_path=tmp_path / "m.db", memory_md_path=tmp_path / "MEMORY.md")
    store.init()
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="500", symptom_tag="[apex]", fork_letter="A", confidence="High",
            one_line_fingerprint="apex outage at 37.7749, -122.4194",
            summary="apex node down",
        ),
    )
    out, total = run_history_search("[apex]", _FakeZd(), store)
    assert out["count"] >= 1
    subjects = [c["subject"] for c in out["candidates"]]
    assert any("<COORDS>" in s for s in subjects)  # fingerprint coords scrubbed
