# Watch Two-Pane Inbox Viewer + `config` Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `noc-cli watch` into a display-only two-pane inbox (segmented left list of recently-worked + live-queue tickets; right pane renders the selected ticket's fork-packet summary, the five canonical files, or live activity) and add a `config` command group for quick single-field edits.

**Architecture:** Pure-logic modules first (`config.set_config_value`, a `STATE.md` disk scanner, segment/summary builders), then the Textual TUI is rewritten to consume them. Investigations still run out-of-band via `investigate`; the inbox only reads `Tickets/<id>/` from disk and the live Zendesk poll. No agent execution, no chat (see `docs/interactive-feat.md`).

**Tech Stack:** Python 3.10+, Typer (CLI), Textual (TUI), pydantic v2 (models), pytest + `pytest.mark.anyio` (Textual `Pilot`), `python-dotenv`.

**Spec:** `docs/superpowers/specs/2026-06-04-watch-inbox-viewer-design.md`

**Conventions observed:** flat `tests/test_<module>.py`; CLI tested via `typer.testing.CliRunner`; TUI tested via `app.run_test(size=(120,40))` + `Pilot`; STATE.md hand-parsed (no `pyyaml` dependency).

---

## Phase 1 — `config` command group

### Task 1: `config.set_config_value` + key validation

**Files:**
- Modify: `noc_cli/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
import pytest

from noc_cli.config import set_config_value, valid_config_keys


def test_set_config_value_updates_one_key_preserving_others(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "ZENDESK_SUBDOMAIN=carbyne\n"
        "ZENDESK_EMAIL=envelazquez@axon.com\n"
        "NOC_WATCH_ASSIGNEE=envelazquez@axon.com\n"
    )
    set_config_value("watch_assignee", "enriquev@carbyne.com")
    text = (tmp_path / ".env").read_text()
    assert "NOC_WATCH_ASSIGNEE=enriquev@carbyne.com" in text
    assert "ZENDESK_SUBDOMAIN=carbyne" in text          # untouched
    assert "ZENDESK_EMAIL=envelazquez@axon.com" in text  # untouched


def test_set_config_value_creates_env_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    set_config_value("watch_assignee", "enriquev@carbyne.com")
    assert "NOC_WATCH_ASSIGNEE=enriquev@carbyne.com" in (tmp_path / ".env").read_text()


def test_set_config_value_rejects_unknown_key(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    with pytest.raises(KeyError):
        set_config_value("not_a_field", "x")


def test_valid_config_keys_lists_field_names():
    keys = valid_config_keys()
    assert "watch_assignee" in keys
    assert "zendesk_email" in keys
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -k "set_config_value or valid_config_keys" -v`
Expected: FAIL — `ImportError: cannot import name 'set_config_value'`.

- [ ] **Step 3: Implement in `noc_cli/config.py`**

Add at the end of `noc_cli/config.py`:

```python
def valid_config_keys() -> list[str]:
    """The editable Config field names (e.g. 'watch_assignee')."""
    return list(_FIELD_ENV.keys())


def set_config_value(key: str, value: str) -> None:
    """Upsert a single field into the data-dir `.env`, preserving every other key.

    Reads the file values only (not process-env overrides), updates the one
    `key`, and rewrites with the same serialiser `setup` uses. Raises KeyError
    for an unknown field name.
    """
    if key not in _FIELD_ENV:
        raise KeyError(
            f"unknown config key {key!r}; valid keys: {', '.join(valid_config_keys())}"
        )
    path = config_path()
    current = dict(dotenv_values(path)) if path.exists() else {}
    current[_FIELD_ENV[key]] = value
    # Re-key into the canonical insertion order so output is stable.
    ordered = {env_key: current[env_key] for env_key in _FIELD_ENV.values() if env_key in current}
    from noc_cli.setup import build_env_lines  # local import avoids a cycle

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_env_lines(ordered))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (all, including the three pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/config.py tests/test_config.py
git commit -m "feat(config): set_config_value single-field .env upsert"
```

---

### Task 2: `config` CLI sub-app (`set` / `get` / `list` / `path`)

**Files:**
- Modify: `noc_cli/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_config_set_writes_single_field(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("ZENDESK_SUBDOMAIN=carbyne\n")
    result = runner.invoke(app, ["config", "set", "watch_assignee", "enriquev@carbyne.com"])
    assert result.exit_code == 0, result.output
    text = (tmp_path / ".env").read_text()
    assert "NOC_WATCH_ASSIGNEE=enriquev@carbyne.com" in text
    assert "ZENDESK_SUBDOMAIN=carbyne" in text


def test_config_set_rejects_unknown_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    result = runner.invoke(app, ["config", "set", "bogus", "x"])
    assert result.exit_code != 0
    assert "bogus" in result.output


def test_config_get_prints_value(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("NOC_WATCH_ASSIGNEE=enriquev@carbyne.com\n")
    result = runner.invoke(app, ["config", "get", "watch_assignee"])
    assert result.exit_code == 0
    assert "enriquev@carbyne.com" in result.output


def test_config_list_masks_token(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "ZENDESK_API_TOKEN=supersecrettoken\nNOC_OWNER=alice\n"
    )
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    assert "supersecrettoken" not in result.output   # masked
    assert "alice" in result.output


def test_config_path_prints_env_location(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert ".env" in result.output


def test_help_lists_config_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "config" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k config -v`
Expected: FAIL — config command does not exist (non-zero exit / "No such command").

- [ ] **Step 3: Implement the `config` sub-app in `noc_cli/cli.py`**

Add this block in `noc_cli/cli.py` after the `watch` command (before `if __name__ == "__main__":`):

```python
config_app = typer.Typer(name="config", help="View or edit individual config fields.", no_args_is_help=True)
app.add_typer(config_app)


def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:2] + "…" + "*" * 6 if len(value) > 2 else "***"


@config_app.command("set")
def config_set(key: str = typer.Argument(...), value: str = typer.Argument(...)) -> None:
    """Set a single config field, e.g. `config set watch_assignee a@b.com`."""
    from noc_cli.config import set_config_value, valid_config_keys

    try:
        set_config_value(key, value)
    except KeyError:
        typer.secho(
            f"Unknown key {key!r}. Valid keys: {', '.join(valid_config_keys())}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.secho(f"Set {key} = {value}", fg=typer.colors.GREEN)


@config_app.command("get")
def config_get(key: str = typer.Argument(...)) -> None:
    """Print one config field's effective value (token masked)."""
    from noc_cli.config import load_config, valid_config_keys

    if key not in valid_config_keys():
        typer.secho(f"Unknown key {key!r}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    cfg = load_config()
    raw = str(getattr(cfg, key, ""))
    typer.echo(_mask(raw) if key == "zendesk_api_token" else raw)


@config_app.command("list")
def config_list() -> None:
    """Print all config fields and effective values (token masked)."""
    from noc_cli.config import load_config, valid_config_keys

    cfg = load_config()
    for key in valid_config_keys():
        raw = str(getattr(cfg, key, ""))
        shown = _mask(raw) if key == "zendesk_api_token" else raw
        typer.echo(f"{key} = {shown}")


@config_app.command("path")
def config_path_cmd() -> None:
    """Print the .env file location."""
    typer.echo(str(config_path()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k config -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/cli.py tests/test_cli.py
git commit -m "feat(config): add `config set/get/list/path` command group"
```

---

## Phase 2 — pure data layer

### Task 3: extend `render.py` STATE.md frontmatter

**Files:**
- Modify: `noc_cli/render.py` (`_render_state`)
- Test: `tests/test_render.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render.py`:

```python
def test_state_md_includes_quoted_rubric_row_in_frontmatter(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "STATE.md").read_text()
    head = content.split("---", 2)[1]  # the frontmatter block
    assert "quoted_rubric_row:" in head


def test_state_md_includes_master_and_cluster_when_present(tmp_path):
    h = load_good()
    h.fork_packet.master_ticket = 12345
    h.fork_packet.cluster = "jeffcom-network-error"
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(h, folder)
    content = (folder.root / "STATE.md").read_text()
    assert "master: 12345" in content
    assert "jeffcom-network-error" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_render.py -k "quoted_rubric_row_in_frontmatter or master_and_cluster" -v`
Expected: FAIL — frontmatter lacks `quoted_rubric_row:` / `master:` / cluster.

- [ ] **Step 3: Implement in `noc_cli/render.py`**

Add this helper above `_render_state`:

```python
def _yaml_q(text: str) -> str:
    """Escape a string for a double-quoted single-line YAML scalar."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
```

Then in `_render_state`, replace the `lines = [ ... ]` frontmatter block so it reads (note the three new lines — `quoted_rubric_row`, optional `cluster`, and `master` under `related:`):

```python
    lines = [
        "---",
        f"ticket_id: {intake.ticket_id}",
        f'fork: "{fp.fork_letter.value}"',
        f'symptom_tag: "{fp.symptom_tag}"',
        f'confidence: "{fp.confidence.value}"',
        f'rubric_version: "{handoff.rubric_version}"',
        'status: "open"',
        f'owner: "{owner}"',
        f'quoted_rubric_row: "{_yaml_q(fp.quoted_rubric_row)}"',
    ]
    if fp.cluster:
        lines.append(f'cluster: "{_yaml_q(fp.cluster)}"')
    lines += [
        "related:",
        f"  zendesk: {json.dumps(fp.related_zendesk)}",
        f"  jira: {json.dumps(fp.related_jira)}",
    ]
    if fp.master_ticket is not None:
        lines.append(f"  master: {fp.master_ticket}")
    lines += [
        "---",
        "",
        f"# Ticket {intake.ticket_id} — {intake.one_line_fingerprint}",
        "",
        f"**Fork:** {fp.fork_letter.value} | **Tag:** {fp.symptom_tag} | "
        f"**Confidence:** {fp.confidence.value}",
        "",
        f"> {fp.quoted_rubric_row}" if fp.quoted_rubric_row else "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_render.py -v`
Expected: PASS (all, including pre-existing frontmatter tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/render.py tests/test_render.py
git commit -m "feat(render): persist quoted_rubric_row, master, cluster in STATE.md frontmatter"
```

---

### Task 4: `inbox.py` — models + `build_segments` + `humanize_when`

**Files:**
- Create: `noc_cli/watch/inbox.py`
- Test: `tests/test_watch_inbox.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watch_inbox.py`:

```python
from datetime import datetime, timedelta, timezone

from noc_cli.models import Ticket
from noc_cli.watch.inbox import InboxSummary, build_segments, humanize_when


def _summary(tid: int, *, days_ago: float, fork: str = "A") -> InboxSummary:
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    return InboxSummary(
        ticket_id=tid,
        fork=fork,
        confidence="High",
        status="open",
        investigated_at=now - timedelta(days=days_ago),
    )


def _ticket(tid: int) -> Ticket:
    return Ticket(id=tid, subject=f"T{tid}", status="open")


NOW = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)


def test_worked_segment_keeps_only_last_3_days():
    disk = [_summary(1, days_ago=1), _summary(2, days_ago=5)]
    worked, queue = build_segments(disk, [], now=NOW)
    ids = [r.ticket_id for r in worked]
    assert ids == [1]            # #2 is older than 3 days


def test_worked_segment_excludes_tickets_in_live_queue():
    disk = [_summary(1, days_ago=1)]
    live = [_ticket(1)]
    worked, queue = build_segments(disk, live, now=NOW)
    assert worked == []                       # #1 is live → not in "worked"
    assert [r.ticket_id for r in queue] == [1]
    assert queue[0].triaged is True           # badged from disk
    assert queue[0].summary is not None


def test_queue_row_untriaged_when_no_disk_summary():
    worked, queue = build_segments([], [_ticket(9)], now=NOW)
    assert queue[0].triaged is False
    assert queue[0].summary is None


def test_worked_segment_sorted_newest_first():
    disk = [_summary(1, days_ago=2), _summary(2, days_ago=0.5)]
    worked, _ = build_segments(disk, [], now=NOW)
    assert [r.ticket_id for r in worked] == [2, 1]


def test_humanize_when():
    assert humanize_when(None, NOW) == "—"
    assert humanize_when(NOW - timedelta(seconds=10), NOW) == "just now"
    assert humanize_when(NOW - timedelta(minutes=5), NOW) == "5m ago"
    assert humanize_when(NOW - timedelta(hours=17), NOW) == "17h ago"
    assert humanize_when(NOW - timedelta(days=3), NOW) == "3d ago"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_watch_inbox.py -v`
Expected: FAIL — `ModuleNotFoundError: noc_cli.watch.inbox`.

- [ ] **Step 3: Implement `noc_cli/watch/inbox.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from noc_cli.models import Ticket


@dataclass
class InboxSummary:
    """One investigated ticket, parsed from its STATE.md."""

    ticket_id: int
    fork: Optional[str] = None
    confidence: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None
    symptom_tag: Optional[str] = None
    rubric_version: Optional[str] = None
    quoted_rubric_row: Optional[str] = None
    related_zendesk: list[int] = field(default_factory=list)
    related_jira: list[str] = field(default_factory=list)
    master: Optional[int] = None
    cluster: Optional[str] = None
    investigated_at: Optional[datetime] = None
    folder: Optional[Path] = None


@dataclass
class InboxRow:
    """One rendered left-pane row."""

    ticket_id: int
    segment: Literal["worked", "queue"]
    triaged: bool
    summary: Optional[InboxSummary] = None
    ticket: Optional[Ticket] = None
    when: Optional[datetime] = None


def build_segments(
    disk: list[InboxSummary],
    live: list[Ticket],
    *,
    now: datetime,
    window_days: int = 3,
) -> tuple[list[InboxRow], list[InboxRow]]:
    """Return (worked_rows, queue_rows).

    queue_rows: one per live ticket (input order), badged triaged + summary when a
      disk summary of any age exists. worked_rows: disk summaries within
      `window_days`, whose id is NOT in the live queue, newest first.
    """
    disk_by_id = {s.ticket_id: s for s in disk}
    live_ids = {t.id for t in live}

    queue_rows: list[InboxRow] = []
    for t in live:
        summ = disk_by_id.get(t.id)
        queue_rows.append(
            InboxRow(
                ticket_id=t.id,
                segment="queue",
                triaged=summ is not None,
                summary=summ,
                ticket=t,
                when=t.updated_at,
            )
        )

    cutoff = now - timedelta(days=window_days)
    worked = [
        s
        for s in disk
        if s.ticket_id not in live_ids
        and s.investigated_at is not None
        and s.investigated_at >= cutoff
    ]
    worked.sort(key=lambda s: s.investigated_at, reverse=True)
    worked_rows = [
        InboxRow(
            ticket_id=s.ticket_id,
            segment="worked",
            triaged=True,
            summary=s,
            when=s.investigated_at,
        )
        for s in worked
    ]
    return worked_rows, queue_rows


def humanize_when(dt: Optional[datetime], now: datetime) -> str:
    """Relative time like '17h ago' / '3d ago'. None -> '—'."""
    if dt is None:
        return "—"
    secs = (now - dt).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_watch_inbox.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/watch/inbox.py tests/test_watch_inbox.py
git commit -m "feat(watch): inbox models + build_segments + humanize_when"
```

---

### Task 5: `inbox.py` — `render_summary` + `render_activity`

**Files:**
- Modify: `noc_cli/watch/inbox.py`
- Test: `tests/test_watch_inbox.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_inbox.py`:

```python
from datetime import datetime as _dt

from noc_cli.models import Comment
from noc_cli.watch.inbox import render_activity, render_summary


def test_render_summary_has_all_sections():
    s = InboxSummary(
        ticket_id=44999, fork="A", confidence="high", status="open",
        owner="enriquev", quoted_rubric_row="RTP absent in PCAP",
        rubric_version="2026-05-13", related_zendesk=[44999, 32549],
        master=None, cluster=None,
    )
    out = render_summary(s, shipped_version="2026-05-13")
    assert "Ticket: ZD-44999" in out
    assert "Fork: A · Confidence: high · Status: open" in out
    assert "Owner: enriquev" in out
    assert "RTP absent in PCAP" in out
    assert "Zendesk: #44999, #32549" in out
    assert "Master:  (none)" in out
    assert "⚠" not in out                         # versions match


def test_render_summary_flags_version_mismatch():
    s = InboxSummary(ticket_id=1, rubric_version="2026-04-30")
    out = render_summary(s, shipped_version="2026-05-13")
    assert "⚠" in out
    assert "2026-04-30" in out and "2026-05-13" in out


def test_render_activity_lists_recent_comments():
    t = Ticket(id=7, subject="No ANI", status="open", requester_email="c@site.com")
    t.comments = [
        Comment(id=1, public=True, body="customer reply", created_at=_dt(2026, 6, 4, 9, 0, tzinfo=timezone.utc)),
        Comment(id=2, public=False, body="internal note", created_at=_dt(2026, 6, 4, 10, 0, tzinfo=timezone.utc)),
    ]
    out = render_activity(t)
    assert "ZD-7" in out
    assert "No ANI" in out
    assert "[internal]" in out
    assert "[public]" in out
    assert "press [i]" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_watch_inbox.py -k "render_summary or render_activity" -v`
Expected: FAIL — `ImportError: cannot import name 'render_summary'`.

- [ ] **Step 3: Implement in `noc_cli/watch/inbox.py`**

Add these imports at the top (extend the existing import lines):

```python
from datetime import datetime, timedelta, timezone   # add timezone

from noc_cli.models import Comment, Ticket            # add Comment
```

Append these functions:

```python
def render_summary(summary: InboxSummary, *, shipped_version: str) -> str:
    """The right-pane synthesized summary for an investigated ticket."""
    s = summary
    lines: list[str] = []
    if s.rubric_version and shipped_version and s.rubric_version != shipped_version:
        lines += [
            f"⚠ Rubric version mismatch: state={s.rubric_version}, shipped={shipped_version}",
            "",
        ]
    lines += [
        f"Ticket: ZD-{s.ticket_id}",
        f"Fork: {s.fork or '—'} · Confidence: {s.confidence or '—'} · Status: {s.status or '—'}",
        f"Owner: {s.owner or '—'}",
        "",
        "Quoted rubric row:",
        f'  "{s.quoted_rubric_row}"' if s.quoted_rubric_row else "  (none)",
        f"  rubric_version on STATE.md: {s.rubric_version or '(unknown)'}",
        f"  shipped rubric_version:     {shipped_version or '(unknown)'}",
        "",
        "Related:",
        f"  Zendesk: {', '.join(f'#{z}' for z in s.related_zendesk) or '(none)'}",
        f"  Jira:    {', '.join(s.related_jira) or '(none)'}",
        f"  Master:  {('#' + str(s.master)) if s.master else '(none)'}",
        f"  Cluster: {s.cluster or '(none)'}",
    ]
    return "\n".join(lines)


def render_activity(ticket: Ticket) -> str:
    """The right-pane view for a not-yet-investigated queue ticket."""
    lines = [
        f"Ticket: ZD-{ticket.id}",
        f"Subject: {ticket.subject}",
        f"Requester: {ticket.requester_email or '—'}  ({ticket.requester_org or '—'})",
        f"Status: {ticket.status or '—'}",
        "",
        "Not yet investigated — press [i] to investigate.",
        "",
        "Recent activity:",
    ]
    comments = sorted(
        ticket.comments,
        key=lambda c: c.created_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if not comments:
        lines.append("  (no comments)")
    for c in comments[:5]:
        kind = "public" if c.public else "internal"
        ts = c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "—"
        body = (c.body or "").strip().replace("\n", " ")
        if len(body) > 80:
            body = body[:77] + "..."
        lines.append(f"  · [{kind}] {ts} — {body}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_watch_inbox.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/watch/inbox.py tests/test_watch_inbox.py
git commit -m "feat(watch): render_summary + render_activity for the detail pane"
```

---

### Task 6: `disk_scan.py` — `parse_state_md` + `scan_investigations`

**Files:**
- Create: `noc_cli/watch/disk_scan.py`
- Test: `tests/test_watch_disk_scan.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watch_disk_scan.py`:

```python
import os
from datetime import datetime, timezone

from noc_cli.watch.disk_scan import parse_state_md, scan_investigations

_FULL = (
    "---\n"
    "ticket_id: 44999\n"
    'fork: "A"\n'
    'symptom_tag: "[apex]"\n'
    'confidence: "High"\n'
    'rubric_version: "2026-05-13"\n'
    'status: "open"\n'
    'owner: "enriquev"\n'
    'quoted_rubric_row: "RTP absent in PCAP"\n'
    'cluster: "jeffcom-network-error"\n'
    "related:\n"
    "  zendesk: [44999, 32549]\n"
    '  jira: ["REP-1"]\n'
    "  master: 12345\n"
    "---\n"
    "\n"
    "# Ticket 44999 — no audio\n"
    "> RTP absent in PCAP\n"
)

_LEGACY = (
    "---\n"
    "ticket_id: 41675\n"
    'fork: "B"\n'
    'confidence: "Medium"\n'
    'owner: "alice"\n'
    "related:\n"
    "  zendesk: [41675]\n"
    "---\n"
    "\n"
    "> customer LAN, switch, or SDWAN\n"
)


def test_parse_full_state_md(tmp_path):
    p = tmp_path / "44999" / "STATE.md"
    p.parent.mkdir()
    p.write_text(_FULL)
    s = parse_state_md(p)
    assert s.ticket_id == 44999
    assert s.fork == "A"
    assert s.confidence == "High"
    assert s.symptom_tag == "[apex]"
    assert s.quoted_rubric_row == "RTP absent in PCAP"
    assert s.cluster == "jeffcom-network-error"
    assert s.related_zendesk == [44999, 32549]
    assert s.related_jira == ["REP-1"]
    assert s.master == 12345
    assert s.folder == p.parent
    assert s.investigated_at is not None


def test_parse_legacy_state_md_uses_body_for_quoted_row(tmp_path):
    p = tmp_path / "41675" / "STATE.md"
    p.parent.mkdir()
    p.write_text(_LEGACY)
    s = parse_state_md(p)
    assert s.ticket_id == 41675
    assert s.fork == "B"
    assert s.quoted_rubric_row == "customer LAN, switch, or SDWAN"  # body fallback
    assert s.master is None
    assert s.cluster is None


def test_parse_unparseable_returns_none(tmp_path):
    p = tmp_path / "x" / "STATE.md"
    p.parent.mkdir()
    p.write_text("not a state file")
    assert parse_state_md(p) is None


def test_scan_skips_non_numeric_and_missing_state(tmp_path):
    (tmp_path / "44999").mkdir()
    (tmp_path / "44999" / "STATE.md").write_text(_FULL)
    (tmp_path / "notes").mkdir()                       # non-numeric → skip
    (tmp_path / "55555").mkdir()                       # numeric but no STATE.md → skip
    out = scan_investigations(tmp_path)
    assert [s.ticket_id for s in out] == [44999]


def test_scan_missing_root_returns_empty(tmp_path):
    assert scan_investigations(tmp_path / "nope") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_watch_disk_scan.py -v`
Expected: FAIL — `ModuleNotFoundError: noc_cli.watch.disk_scan`.

- [ ] **Step 3: Implement `noc_cli/watch/disk_scan.py`**

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from noc_cli.watch.inbox import InboxSummary

_TOP_SCALARS = {
    "fork", "symptom_tag", "confidence", "rubric_version",
    "status", "owner", "quoted_rubric_row", "cluster",
}


def _unquote(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return v[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    return v


def _split_frontmatter(text: str) -> Optional[tuple[str, str]]:
    """Return (frontmatter, body) or None if no leading `--- ... ---` block."""
    if not text.startswith("---"):
        return None
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return None
    front = rest[:end]
    body = rest[end + 4 :]
    return front, body


def parse_state_md(path: Path) -> Optional[InboxSummary]:
    """Parse a STATE.md into an InboxSummary, or None if it has no frontmatter."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    split = _split_frontmatter(text)
    if split is None:
        return None
    front, body = split

    summ = InboxSummary(ticket_id=0, folder=path.parent)
    in_related = False
    for line in front.splitlines():
        if not line.strip():
            continue
        indented = line[0] in (" ", "\t")
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if not indented:
            in_related = key == "related"
            if key == "ticket_id":
                summ.ticket_id = int(raw or 0)
            elif key in _TOP_SCALARS:
                setattr(summ, key, _unquote(raw))
        elif in_related:
            if key == "zendesk":
                summ.related_zendesk = _as_int_list(raw)
            elif key == "jira":
                summ.related_jira = _as_str_list(raw)
            elif key == "master":
                summ.master = int(raw) if raw.lstrip("-").isdigit() else None

    # ticket id falls back to the folder name (Tickets/<id>/)
    if not summ.ticket_id and path.parent.name.isdigit():
        summ.ticket_id = int(path.parent.name)

    # body fallback: first `> ...` quote becomes quoted_rubric_row if absent
    if not summ.quoted_rubric_row:
        for line in body.splitlines():
            if line.startswith(">"):
                summ.quoted_rubric_row = line[1:].strip()
                break

    summ.investigated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return summ


def _as_int_list(raw: str) -> list[int]:
    try:
        return [int(x) for x in json.loads(raw)]
    except (json.JSONDecodeError, ValueError, TypeError):
        return []


def _as_str_list(raw: str) -> list[str]:
    try:
        return [str(x) for x in json.loads(raw)]
    except (json.JSONDecodeError, ValueError, TypeError):
        return []


def scan_investigations(tickets_root: Path) -> list[InboxSummary]:
    """Scan Tickets/<id>/STATE.md across numeric subdirs. Tolerant of bad files."""
    out: list[InboxSummary] = []
    if not tickets_root.exists():
        return out
    for child in sorted(tickets_root.iterdir()):
        if not child.is_dir() or not child.name.isdigit():
            continue
        state = child / "STATE.md"
        if not state.exists():
            continue
        summ = parse_state_md(state)
        if summ is not None:
            out.append(summ)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_watch_disk_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/watch/disk_scan.py tests/test_watch_disk_scan.py
git commit -m "feat(watch): STATE.md disk scanner + parser (frontmatter + body fallback)"
```

---

## Phase 3 — TUI

### Task 7: rewrite `watch_app.py` as the two-pane inbox

**Files:**
- Rewrite: `noc_cli/tui/watch_app.py`
- Rewrite: `tests/test_watch_app.py`

- [ ] **Step 1: Write the new failing tests (replace the file)**

Replace the entire contents of `tests/test_watch_app.py` with:

```python
import os
from unittest.mock import MagicMock, patch

import pytest

from noc_cli import store
from noc_cli.config import Config
from noc_cli.models import Comment, Ticket
from noc_cli.watch.notify import NoOpNotifier
from noc_cli.watch.state import WatchState

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db_conn(tmp_path):
    c = store.connect(tmp_path / "noc.db")
    yield c
    c.close()


def _config(tmp_path) -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="agent@x.com",
        zendesk_api_token="tok",
        watch_view="555",
        watch_assignee="agent@x.com",
        tickets_root=tmp_path / "Tickets",
    )


def _ticket(tid: int, *, status: str = "open") -> Ticket:
    return Ticket(id=tid, subject=f"Ticket {tid}", status=status, assignee_email="agent@x.com")


class _FakeClient:
    def __init__(self, tickets=None, comments=None):
        self._tickets = tickets or []
        self._comments = comments or {}

    def view_tickets(self, view_id):
        return list(self._tickets)

    def get_comments(self, ticket_id):
        return list(self._comments.get(ticket_id, []))


def _write_state(tmp_path, tid: int) -> None:
    root = tmp_path / "Tickets" / str(tid)
    root.mkdir(parents=True)
    (root / "STATE.md").write_text(
        "---\n"
        f"ticket_id: {tid}\n"
        'fork: "A"\n'
        'confidence: "High"\n'
        'status: "open"\n'
        'owner: "agent"\n'
        'quoted_rubric_row: "RTP absent in PCAP"\n'
        "related:\n"
        "  zendesk: [%d]\n" % tid
        + '  jira: []\n'
        "---\n\n> RTP absent in PCAP\n"
    )


def _make_app(cfg, client, ws):
    from noc_cli.tui.watch_app import WatchApp

    return WatchApp(config=cfg, client=client, watch_state=ws, notifier=NoOpNotifier(), poll_interval=9999)


async def test_app_mounts_with_two_panes(db_conn, tmp_path, monkeypatch):
    monkeypatch.delenv("NOC_TICKETS_ROOT", raising=False)
    app = _make_app(_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)):
        assert app.query_one("#ticket-list") is not None
        assert app.query_one("#detail") is not None
        assert app.query_one("#banner") is not None


async def test_segments_populated_after_poll(db_conn, tmp_path, monkeypatch):
    monkeypatch.delenv("NOC_TICKETS_ROOT", raising=False)
    _write_state(tmp_path, 4000)                       # recently worked, not in queue
    client = _FakeClient([_ticket(1), _ticket(2)])     # live queue
    app = _make_app(_config(tmp_path), client, WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_poll_now()
        await app.workers.wait_for_complete()
        await pilot.pause()
        from noc_cli.tui.watch_app import TicketList

        tl = app.query_one("#ticket-list", TicketList)
        ids = [r.ticket_id for r in tl.rows]
        assert 4000 in ids and 1 in ids and 2 in ids
        rendered = tl.render().plain
        assert "Recently worked" in rendered
        assert "My queue" in rendered
        assert "✓" in rendered            # the worked row
        assert "○" in rendered            # an un-triaged queue row


async def test_detail_shows_summary_for_worked_row(db_conn, tmp_path, monkeypatch):
    monkeypatch.delenv("NOC_TICKETS_ROOT", raising=False)
    _write_state(tmp_path, 4000)
    app = _make_app(_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_poll_now()
        await app.workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import Static

        text = app.query_one("#detail-content", Static).renderable
        assert "ZD-4000" in str(text)
        assert "RTP absent in PCAP" in str(text)


async def test_detail_shows_activity_for_queue_row(db_conn, tmp_path, monkeypatch):
    monkeypatch.delenv("NOC_TICKETS_ROOT", raising=False)
    comments = {1: [Comment(id=1, public=True, body="customer reply")]}
    client = _FakeClient([_ticket(1)], comments=comments)
    app = _make_app(_config(tmp_path), client, WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_poll_now()
        await app.workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import Static

        text = str(app.query_one("#detail-content", Static).renderable)
        assert "press [i]" in text
        assert "customer reply" in text


async def test_tab_cycles_to_a_file(db_conn, tmp_path, monkeypatch):
    monkeypatch.delenv("NOC_TICKETS_ROOT", raising=False)
    _write_state(tmp_path, 4000)
    app = _make_app(_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_poll_now()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("tab")          # summary -> INTAKE.md
        await pilot.pause()
        assert app.detail_mode == "INTAKE.md"


async def test_i_key_launches_investigate(db_conn, tmp_path, monkeypatch):
    monkeypatch.delenv("NOC_TICKETS_ROOT", raising=False)
    client = _FakeClient([_ticket(99)])
    app = _make_app(_config(tmp_path), client, WatchState(db_conn))
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_poll_now()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
    assert mock_popen.call_count == 1
    cmd = mock_popen.call_args[0][0]
    assert "investigate" in cmd and "99" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_watch_app.py -v`
Expected: FAIL — the new widget ids / `TicketList` / `detail_mode` do not exist yet.

- [ ] **Step 3: Rewrite `noc_cli/tui/watch_app.py`**

Replace the entire file with:

```python
from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Static

from noc_cli.config import Config
from noc_cli.models import Ticket
from noc_cli.watch.diff import (
    ChangeEvent,
    ChangeKind,
    _iso,
    _latest_public_comment,
    diff_tickets,
)
from noc_cli.watch.disk_scan import scan_investigations
from noc_cli.watch.inbox import (
    InboxRow,
    build_segments,
    humanize_when,
    render_activity,
    render_summary,
)
from noc_cli.watch.notify import Notifier
from noc_cli.watch.poller import poll_view
from noc_cli.watch.state import TicketSnapshot, WatchState

_BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_FILES = ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md")

_CSS = """
Screen { layout: vertical; }
#banner { height: 1; background: $surface-darken-1; color: $text; padding: 0 1; text-style: bold; }
#notification { height: 1; background: $warning 20%; color: $warning; padding: 0 1; display: none; }
#notification.visible { display: block; }
#body { height: 1fr; }
#ticket-list { width: 45%; border: solid $accent; padding: 0 1; }
#detail { width: 55%; border: solid $accent; padding: 0 1; }
"""


class PollComplete(Message):
    def __init__(self, tickets, events, seeds, snapshots, error=None) -> None:
        super().__init__()
        self.tickets = tickets
        self.events = events
        self.seeds = seeds
        self.snapshots = snapshots
        self.error = error


class TicketList(Static):
    """Left pane: two segments (recently-worked, my-queue) with one cursor."""

    cursor: reactive[int] = reactive(0)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._worked: list[InboxRow] = []
        self._queue: list[InboxRow] = []

    @property
    def rows(self) -> list[InboxRow]:
        return self._worked + self._queue

    def set_rows(self, worked: list[InboxRow], queue: list[InboxRow]) -> None:
        self._worked = list(worked)
        self._queue = list(queue)
        if self.cursor >= len(self.rows):
            self.cursor = max(0, len(self.rows) - 1)
        self.refresh()

    def selected(self) -> InboxRow | None:
        rows = self.rows
        return rows[self.cursor] if 0 <= self.cursor < len(rows) else None

    def move(self, delta: int) -> None:
        n = len(self.rows)
        if n:
            self.cursor = max(0, min(n - 1, self.cursor + delta))

    def watch_cursor(self) -> None:
        self.refresh()

    def render(self) -> Text:
        now = datetime.now(timezone.utc)
        out = Text()
        idx = 0

        def segment(title: str, rows: list[InboxRow]) -> None:
            nonlocal idx
            out.append(f"▸ {title}\n", style="bold cyan")
            if not rows:
                out.append("    (none)\n", style="dim")
            for r in rows:
                here = idx == self.cursor
                glyph = "✓" if r.triaged else "○"
                fork = r.summary.fork if (r.summary and r.summary.fork) else "—"
                conf = r.summary.confidence if (r.summary and r.summary.confidence) else "—"
                status = (
                    (r.summary.status if r.summary and r.summary.status else None)
                    or (r.ticket.status if r.ticket else None)
                    or "in queue"
                )
                when = humanize_when(r.when, now)
                out.append(
                    f"{'◉' if here else ' '} {glyph} #{r.ticket_id:<7} "
                    f"{fork:<4} {when:<8} {conf:<6} {status}\n",
                    style="bold" if here else "",
                )
                idx += 1

        segment("Recently worked (3d)", self._worked)
        out.append("\n")
        segment("My queue", self._queue)
        return out


class WatchApp(App[None]):
    """Display-only two-pane Zendesk inbox (no Agent SDK)."""

    CSS = _CSS
    BINDINGS = [
        Binding("up,k", "move_up", "Up", show=False),
        Binding("down,j", "move_down", "Down", show=False),
        Binding("enter", "focus_detail", "Focus", show=True),
        Binding("i", "investigate", "Investigate", show=True),
        Binding("tab", "cycle_file(1)", "Files", show=True),
        Binding("shift+tab", "cycle_file(-1)", "Files back", show=False),
        Binding("escape", "summary", "Summary", show=True),
        Binding("r", "poll_now", "Refresh", show=True),
        Binding("y", "copy", "Copy", show=True),
        Binding("o", "open", "Open", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    _spinner_frame: reactive[int] = reactive(0)
    _polling: reactive[bool] = reactive(False)
    _last_poll: reactive[str] = reactive("never")
    detail_mode: reactive[str] = reactive("summary")

    def __init__(self, *, config: Config, client, watch_state: WatchState,
                 notifier: Notifier, poll_interval: int = 60) -> None:
        super().__init__()
        self._config = config
        self._client = client
        self._watch_state = watch_state
        self._notifier = notifier
        self._poll_interval = poll_interval
        self._tickets: list[Ticket] = []
        self._notif_timer = None
        try:
            from noc_cli.rubric import load_rubric

            self._shipped_version = load_rubric().version
        except Exception:
            self._shipped_version = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="banner")
        yield Static("", id="notification")
        with Horizontal(id="body"):
            yield TicketList(id="ticket-list")
            with VerticalScroll(id="detail"):
                yield Static("", id="detail-content")
        yield Footer()

    def on_mount(self) -> None:
        self._update_banner()
        if self._poll_interval < 9999:
            self.set_interval(self._poll_interval, self.action_poll_now)
            self.action_poll_now()
        self.set_interval(0.1, self._tick_spinner)

    # ── polling ────────────────────────────────────────────────────────
    @work(exclusive=True, thread=True)
    def _run_poll(self, last_seen: dict[int, TicketSnapshot]) -> None:
        from noc_cli.zendesk import ZendeskError

        try:
            tickets = poll_view(self._client, self._config.watch_view, self._config.watch_assignee)
            comments_map: dict[int, list] = {}
            for ticket in tickets:
                try:
                    comments_map[ticket.id] = self._client.get_comments(ticket.id)
                except ZendeskError:
                    comments_map[ticket.id] = []
                ticket.comments = comments_map[ticket.id]
            events, seeds = diff_tickets(tickets, comments_map, last_seen, return_seeds=True)
            snapshots: dict[int, TicketSnapshot] = {}
            for ticket in tickets:
                latest = _latest_public_comment(comments_map.get(ticket.id, []))
                snapshots[ticket.id] = TicketSnapshot(
                    status=ticket.status,
                    last_comment_at=_iso(latest.created_at if latest else None),
                )
            self.post_message(PollComplete(tickets, events, seeds, snapshots))
        except ZendeskError as exc:
            self.post_message(PollComplete([], [], {}, {}, error=str(exc)))

    def action_poll_now(self) -> None:
        self._polling = True
        self._update_banner()
        last_seen = self._watch_state.load_all()
        self._run_poll(last_seen)

    def on_poll_complete(self, message: PollComplete) -> None:
        self._polling = False
        self._last_poll = datetime.now(tz=timezone.utc).strftime("%H:%M")
        if message.error:
            self._notify_line(f"Poll error: {message.error[:80]}")
            self._update_banner()
            return
        for tid, snap in message.seeds.items():
            self._watch_state.seed_if_absent(tid, snap)
        for tid, snap in message.snapshots.items():
            self._watch_state.save(tid, snap)
        for evt in message.events:
            self._notifier.notify(evt)
            self._notify_line(self._event_text(evt))
        self._tickets = message.tickets
        self._rebuild_segments()
        self._refresh_detail()
        self._update_banner()

    def _rebuild_segments(self) -> None:
        tickets_root = Path(os.environ.get("NOC_TICKETS_ROOT", str(self._config.tickets_root)))
        disk = scan_investigations(tickets_root)
        worked, queue = build_segments(disk, self._tickets, now=datetime.now(timezone.utc))
        self.query_one("#ticket-list", TicketList).set_rows(worked, queue)

    # ── navigation + detail ────────────────────────────────────────────
    def _selected(self) -> InboxRow | None:
        return self.query_one("#ticket-list", TicketList).selected()

    def action_move_up(self) -> None:
        self.query_one("#ticket-list", TicketList).move(-1)
        self.detail_mode = "summary"
        self._refresh_detail()

    def action_move_down(self) -> None:
        self.query_one("#ticket-list", TicketList).move(1)
        self.detail_mode = "summary"
        self._refresh_detail()

    def action_summary(self) -> None:
        self.detail_mode = "summary"
        self._refresh_detail()

    def action_cycle_file(self, delta: int) -> None:
        order = ["summary", *_FILES]
        try:
            idx = order.index(self.detail_mode)
        except ValueError:
            idx = 0
        self.detail_mode = order[(idx + delta) % len(order)]
        self._refresh_detail()

    def action_focus_detail(self) -> None:
        try:
            self.query_one("#detail", VerticalScroll).focus()
        except NoMatches:
            pass

    def _refresh_detail(self) -> None:
        try:
            content = self.query_one("#detail-content", Static)
        except NoMatches:
            return
        row = self._selected()
        if row is None:
            content.update("")
            return
        if self.detail_mode != "summary" and row.summary and row.summary.folder:
            path = row.summary.folder / self.detail_mode
            try:
                content.update(path.read_text(encoding="utf-8"))
            except OSError:
                content.update(f"({self.detail_mode} not generated)")
            return
        if row.triaged and row.summary:
            content.update(render_summary(row.summary, shipped_version=self._shipped_version))
        elif row.ticket is not None:
            content.update(render_activity(row.ticket))
        else:
            content.update("")

    # ── actions ────────────────────────────────────────────────────────
    def action_investigate(self) -> None:
        row = self._selected()
        if row is None:
            return
        subprocess.Popen([sys.executable, "-m", "noc_cli.cli", "investigate", str(row.ticket_id)])
        self._notify_line(f"Launched investigate #{row.ticket_id}")

    def action_open(self) -> None:
        row = self._selected()
        if row is None:
            return
        sub = self._config.zendesk_subdomain
        if sub:
            webbrowser.open(f"https://{sub}.zendesk.com/agent/tickets/{row.ticket_id}")

    def action_copy(self) -> None:
        text = self._current_summary_text()
        if text and hasattr(self, "copy_to_clipboard"):
            self.copy_to_clipboard(text)
            self._notify_line("Summary copied")

    def _current_summary_text(self) -> str:
        row = self._selected()
        if row and row.triaged and row.summary:
            return render_summary(row.summary, shipped_version=self._shipped_version)
        if row and row.ticket is not None:
            return render_activity(row.ticket)
        return ""

    # ── banner / notification ──────────────────────────────────────────
    def _tick_spinner(self) -> None:
        if self._polling:
            self._spinner_frame = (self._spinner_frame + 1) % len(_BRAILLE)
            self._update_banner()

    def _update_banner(self) -> None:
        try:
            banner = self.query_one("#banner", Static)
            n = len(self.query_one("#ticket-list", TicketList).rows)
        except NoMatches:
            return
        scope = self._config.watch_assignee or "all"
        spin = f" · {_BRAILLE[self._spinner_frame]} polling…" if self._polling else ""
        banner.update(f"noc-cli watch · {scope} · {n} tickets · last poll {self._last_poll}{spin}")

    def _event_text(self, evt: ChangeEvent) -> str:
        if evt.customer_replied and evt.old_status == "pending":
            return f"Customer replied on #{evt.ticket_id}: {evt.ticket_subject}"
        if evt.kind == ChangeKind.STATUS_CHANGED:
            return f"#{evt.ticket_id} status: {evt.old_status} → {evt.new_status}"
        return f"New comment on #{evt.ticket_id}: {evt.ticket_subject}"

    def _notify_line(self, text: str) -> None:
        try:
            notif = self.query_one("#notification", Static)
        except NoMatches:
            return
        notif.update(f" {text} ")
        notif.add_class("visible")
        if self._notif_timer is not None:
            self._notif_timer.stop()
        self._notif_timer = self.set_timer(5.0, self._hide_notif)

    def _hide_notif(self) -> None:
        try:
            self.query_one("#notification", Static).remove_class("visible")
        except NoMatches:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_watch_app.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(watch): rewrite watch TUI as two-pane segmented inbox viewer"
```

---

### Task 8: integration — full suite, help surface, manual smoke

**Files:**
- Modify: `tests/test_cli.py` (extend the existing help-surface assertion)

- [ ] **Step 1: Tighten the help-surface test**

In `tests/test_cli.py`, update `test_help_lists_full_command_surface` to include the new command:

```python
def test_help_lists_full_command_surface():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("setup", "doctor", "investigate", "watch", "config"):
        assert command in result.stdout
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`
Expected: PASS — all suites green (existing + new `test_watch_inbox.py`, `test_watch_disk_scan.py`, rewritten `test_watch_app.py`, extended `test_config.py` / `test_cli.py` / `test_render.py`).

- [ ] **Step 3: Manual smoke (no live Zendesk needed)**

Create a fake investigated ticket and run the TUI against an empty/failing poll to confirm the left "Recently worked" segment + right summary render:

```bash
export NOC_TICKETS_ROOT=/tmp/noc-smoke/Tickets
mkdir -p "$NOC_TICKETS_ROOT/44999"
cat > "$NOC_TICKETS_ROOT/44999/STATE.md" <<'EOF'
---
ticket_id: 44999
fork: "A"
confidence: "High"
status: "open"
owner: "enriquev"
quoted_rubric_row: "RTP absent in PCAP"
related:
  zendesk: [44999]
  jira: []
---

> RTP absent in PCAP
EOF
uv run noc-cli config set watch_assignee enriquev@carbyne.com
uv run noc-cli config list
# Then (Ctrl-C to exit): the left pane should show "Recently worked (3d)" with #44999 ✓,
# and selecting it should render the summary (Fork: A · Confidence: High …) on the right.
uv run noc-cli watch --view 555 --interval 9999 || true
```

Expected: `config list` shows `watch_assignee = enriquev@carbyne.com` (token masked); the inbox renders the two-pane layout with #44999 under "Recently worked".

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): assert config in command surface; finalize inbox viewer"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** banner (Task 7) · segmented left pane / dedup / 3-day window (Tasks 4,7) · right-pane summary + file cycle + activity (Tasks 5,7) · disk scan + STATE.md parse w/ legacy fallback (Task 6) · `render.py` frontmatter (Task 3) · keybindings incl. `i` investigate (Task 7) · `config set/get/list/path` (Tasks 1,2) · single-value assignee unchanged (no `poll_view` change) · testing strategy (every task). All spec sections map to a task.
- **Placeholder scan:** none — every code/test step shows complete content.
- **Type/name consistency:** `InboxSummary`/`InboxRow`/`build_segments`/`humanize_when`/`render_summary`/`render_activity` defined in Tasks 4–5 and consumed identically in `disk_scan.py` (Task 6) and `watch_app.py` (Task 7); widget ids (`#ticket-list`, `#detail`, `#detail-content`, `#banner`, `#notification`) and `detail_mode` match across the app and its tests.

## Execution note

`poll_view` is intentionally **unchanged** (single-email assignee match). The dual-domain "match-any" option remains deferred per the spec.
