from __future__ import annotations

import os
from datetime import datetime, timezone

from noc_cli.watch.disk_scan import parse_state_md, scan_investigations


FULL_STATE = """---
ticket_id: 44999
fork: "B"
symptom_tag: "[dropped calls]"
confidence: "High"
rubric_version: "2026-05-13"
status: "pending"
owner: "enrique"
quoted_rubric_row: "RTP absent in PCAP"
cluster: "Aurora metro outage"
related:
  zendesk: [44999, 32549]
  jira: ["REP-123", "REP-456"]
  master: 12345
---

# Ticket 44999
"""


def test_parse_state_md_reads_full_frontmatter_related_block_mtime_and_folder(tmp_path):
    folder = tmp_path / "44999"
    folder.mkdir()
    state = folder / "STATE.md"
    state.write_text(FULL_STATE)
    mtime = datetime(2026, 6, 4, 15, 30, tzinfo=timezone.utc).timestamp()
    os.utime(state, (mtime, mtime))

    summary = parse_state_md(state)

    assert summary is not None
    assert summary.ticket_id == 44999
    assert summary.fork == "B"
    assert summary.confidence == "High"
    assert summary.status == "pending"
    assert summary.owner == "enrique"
    assert summary.symptom_tag == "[dropped calls]"
    assert summary.rubric_version == "2026-05-13"
    assert summary.quoted_rubric_row == "RTP absent in PCAP"
    assert summary.related_zendesk == [44999, 32549]
    assert summary.related_jira == ["REP-123", "REP-456"]
    assert summary.master == 12345
    assert summary.cluster == "Aurora metro outage"
    assert summary.investigated_at == datetime(2026, 6, 4, 15, 30, tzinfo=timezone.utc)
    assert summary.folder == folder


def test_parse_state_md_uses_first_body_quote_as_legacy_quoted_rubric_row(tmp_path):
    folder = tmp_path / "41675"
    folder.mkdir()
    state = folder / "STATE.md"
    state.write_text(
        """---
ticket_id: 41675
fork: C
confidence: Low
status: solved
related:
  zendesk: []
  jira: []
---

Intro text.
> customer LAN, switch, or SDWAN
> later quote ignored
"""
    )

    summary = parse_state_md(state)

    assert summary is not None
    assert summary.ticket_id == 41675
    assert summary.quoted_rubric_row == "customer LAN, switch, or SDWAN"
    assert summary.related_zendesk == []
    assert summary.related_jira == []
    assert summary.master is None


def test_parse_state_md_returns_none_for_missing_no_frontmatter_and_bad_scalars(tmp_path):
    missing = tmp_path / "missing" / "STATE.md"
    assert parse_state_md(missing) is None

    no_frontmatter = tmp_path / "no-frontmatter.md"
    no_frontmatter.write_text("ticket_id: 44999\n")
    assert parse_state_md(no_frontmatter) is None

    bad_ticket = tmp_path / "bad-ticket" / "STATE.md"
    bad_ticket.parent.mkdir()
    bad_ticket.write_text("---\nticket_id: not-a-number\n---\n")
    assert parse_state_md(bad_ticket) is None

    bad_related = tmp_path / "bad-related" / "STATE.md"
    bad_related.parent.mkdir()
    bad_related.write_text(
        """---
ticket_id: 44999
related:
  zendesk: [44999, nope]
---
"""
    )
    assert parse_state_md(bad_related) is None

    bad_master = tmp_path / "bad-master" / "STATE.md"
    bad_master.parent.mkdir()
    bad_master.write_text(
        """---
ticket_id: 44999
related:
  master: nope
---
"""
    )
    assert parse_state_md(bad_master) is None


def test_scan_investigations_returns_empty_for_missing_root(tmp_path):
    assert scan_investigations(tmp_path / "Tickets") == []


def test_scan_investigations_walks_numeric_direct_subdirs_and_skips_missing_or_bad_files(tmp_path):
    valid = tmp_path / "44999"
    valid.mkdir()
    (valid / "STATE.md").write_text(FULL_STATE)

    non_numeric = tmp_path / "notes"
    non_numeric.mkdir()
    (non_numeric / "STATE.md").write_text(
        FULL_STATE.replace("ticket_id: 44999", "ticket_id: 12345")
    )

    nested_numeric = non_numeric / "12345"
    nested_numeric.mkdir()
    (nested_numeric / "STATE.md").write_text(
        FULL_STATE.replace("ticket_id: 44999", "ticket_id: 12345")
    )

    missing_state = tmp_path / "55555"
    missing_state.mkdir()

    bad = tmp_path / "45000"
    bad.mkdir()
    (bad / "STATE.md").write_text("---\nticket_id: nope\n---\n")

    summaries = scan_investigations(tmp_path)

    assert [summary.ticket_id for summary in summaries] == [44999]


def test_scan_investigations_skips_parser_exceptions(tmp_path, monkeypatch):
    folder = tmp_path / "44999"
    folder.mkdir()
    (folder / "STATE.md").write_text(FULL_STATE)

    def boom(path):
        raise RuntimeError("unexpected parse failure")

    monkeypatch.setattr("noc_cli.watch.disk_scan.parse_state_md", boom)

    assert scan_investigations(tmp_path) == []
