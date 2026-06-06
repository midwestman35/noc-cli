from noc_cli.agent.tools import build_mcp_servers, run_history_search
from noc_cli.config import Config
from noc_cli.memory import InvestigationRecord, MemoryStore, append_investigation


class _FakeZd:
    def search(self, keyword):
        return []  # no live Zendesk in tests


def test_run_history_search_returns_redacted_candidates(tmp_path):
    store = MemoryStore(
        db_path=tmp_path / "m.db", memory_md_path=tmp_path / "MEMORY.md"
    )
    store.init()
    append_investigation(
        store,
        InvestigationRecord(
            ticket_id="500",
            symptom_tag="[apex]",
            fork_letter="A",
            confidence="High",
            one_line_fingerprint="apex outage at 37.7749, -122.4194",
            summary="apex node down",
        ),
    )
    out, total = run_history_search("[apex]", _FakeZd(), store)
    assert out["count"] >= 1
    subjects = [c["subject"] for c in out["candidates"]]
    assert any("<COORDS>" in s for s in subjects)  # fingerprint coords scrubbed


def test_build_mcp_servers_hybrid_shape(tmp_path):
    store = MemoryStore(
        db_path=tmp_path / "m.db", memory_md_path=tmp_path / "MEMORY.md"
    )
    store.init()
    config = Config(
        zendesk_subdomain="acme", zendesk_email="a@b.co", zendesk_api_token="tok"
    )
    servers, allowed = build_mcp_servers(config, store, _zendesk_client=_FakeZd())

    # zendesk = external stdio process carrying scoped creds
    zd = servers["zendesk"]
    assert zd["command"]  # python executable
    assert zd["args"] == ["-m", "noc_cli.mcp.zendesk_server"]
    assert set(zd["env"]) == {"ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN"}
    assert zd["env"]["ZENDESK_SUBDOMAIN"] == "acme"

    # history = in-process SDK server (McpSdkServerConfig dict: type == "sdk")
    assert servers["history"]["type"] == "sdk"

    assert allowed == [
        "mcp__zendesk__get_ticket",
        "mcp__zendesk__get_comments",
        "mcp__zendesk__search",
        "mcp__history__search_history",
    ]
