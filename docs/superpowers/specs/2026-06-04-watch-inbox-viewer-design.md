# Watch Two-Pane Inbox Viewer + `config` Command — Design Spec

- **Date:** 2026-06-04
- **Status:** Approved in brainstorm; pending implementation plan.
- **Scope of this session:** *Display-only* two-pane inbox for the `watch` command,
  plus a companion `config` command group for quick single-field edits.
- **Explicitly deferred:** all interactive/agent-driven parity (triage-from-inbox,
  chat, revise, retry) → see `docs/interactive-feat.md`.

---

## 1. Goal

Bring triage-cli's inbox **display** parity to `noc-cli watch`: a two-pane TUI where
the left pane shows the engineer's tickets (recently-worked + live queue) and the
right pane renders the selected ticket's fork packet / investigation results.
Investigations continue to run **out-of-band** via `investigate`; the inbox only
*reads and renders* what is already on disk.

Secondary: a `config` command group so a misconfigured single field (e.g. the
dual-domain `watch_assignee`) can be changed without re-running the whole `setup`
wizard.

## 2. Background / current state

- `watch` today is a **single-pane** `DataTable` (`tui/watch_app.py`) with a
  *transient* change-banner. `Enter` does `subprocess.Popen([... "investigate", id])`
  — fire-and-forget; results never return to the TUI.
- `investigate` writes five canonical files per ticket to `Tickets/<id>/`
  (`INTAKE.md`, `EVIDENCE_PREFLIGHT.md`, `FORK_PACKET.md`, `DRAFTS.md`, `STATE.md`)
  via `render.py::render_handoff`. `STATE.md` carries YAML frontmatter
  (`fork`, `symptom_tag`, `confidence`, `rubric_version`, `status`, `owner`,
  `related.zendesk`, `related.jira`) and a body line `> {quoted_rubric_row}`.
- `poll_view(client, view, assignee)` fetches a Zendesk view and filters by
  `assignee_email` (single, lowercased, exact).
- `setup.py::run_setup` re-prompts **all 8 fields** on every run (defaults
  pre-filled); there is no targeted single-field edit.
- This maps almost 1:1 onto triage-cli's disk-discovery inbox. The only data the
  screenshot's summary shows that noc-cli does **not** persist today: `master` and
  `cluster` (present on the `ForkPacket` model but dropped by `render.py`).

## 3. Scope

**In:** two-pane segmented viewer; right-pane summary synthesis + file cycling;
queue-ticket activity view; persistent banner + folded change notifications; disk
scanner + `STATE.md` parser; a small `render.py` frontmatter extension; the `config`
command group.

**Out (→ `interactive-feat.md`):** running the agent from the inbox, phase gauges,
chat, revise/retry, auto-triage on poll, match-any multi-email assignee.

---

## 4. Feature A — Two-pane inbox viewer

### 4.1 Command & layout

`watch` evolves in place (keeps `--view / --assignee / --interval`). Three vertical
zones:

1. **Top — persistent banner** (replaces today's `Header` + transient `Label`):
   `noc-cli watch · my tickets · N tickets · last poll HH:MM[ · ⠋ polling…]`, with a
   **notification line** beneath that briefly shows change events.
2. **Body — horizontal split ~45 / 55:** left ticket list, right summary.
3. **Footer — keybindings.**

### 4.2 Left pane — segmented (hybrid)

Two labeled segments, rendered top-to-bottom in one pane. No scrolling; both are
bounded. Columns mirror the screenshot: selection/status glyph, `Ticket`, `Fork`,
`When`, `Conf`, `Owner / Status`.

- **▸ Recently worked (last 3 days):** every `Tickets/<id>/STATE.md` whose **file
  mtime** is within 3 days *and whose ticket id is not in the live queue*, newest
  first. Glyph `✓`; shows fork letter + confidence; `When` = time since mtime.
- **▸ My queue (live):** the configured assignee's tickets from the live poll. If a
  `STATE.md` exists on disk (any age) → `✓` + fork/confidence; else `○` + `in queue`.
  `When` = time since the ticket's `updated_at` (so customer/vendor/internal activity
  recency is visible — a deliberate, minor improvement over the reference's `—`).

**Dedup precedence (approved):** a ticket present in both lists appears **once**,
under **My queue** (it's active), carrying its `✓`/fork badge. "Recently worked" is
therefore disk(3d) **minus** live-queue ids.

**Cursor** moves across both segments as one logical list; segment headers are
non-selectable rows.

**Banner count** `N` = total distinct rows across both segments.

> "Assignee" is mechanically `NOC_WATCH_ASSIGNEE`, matched by **email** on view rows.
> Display names are not used for matching.

### 4.3 Right pane — selected ticket

Two cases, chosen by whether the selected ticket has a `STATE.md`:

**(a) Investigated → synthesized Summary** (default `detail_mode = Summary`),
mirroring the screenshot, in this order:

```
Ticket: ZD-<id>
Fork: <A> · Confidence: <high> · Status: <open>
Owner: <owner>

Quoted rubric row:
  "<quoted_rubric_row>"
  rubric_version on STATE.md: <state_version>
  shipped rubric_version:     <shipped_version>

Related:
  Zendesk: #..., #...
  Jira:    REP-..., (none)
  Master:  #... / (none)
  Cluster: <cluster> / (none)
```

- Shipped version = `rubric.load_rubric().version` (parsed from
  `noc_cli/data/fork-rubric.md`). If it differs from the `STATE.md` value, prepend a
  ⚠ mismatch line. (Note: triage-cli also shows "validator soft-warnings" here;
  noc-cli does **not** persist those today, so they are intentionally omitted.)
- `Tab` / `Shift+Tab` cycle: `Summary → INTAKE → EVIDENCE_PREFLIGHT → FORK_PACKET →
  DRAFTS → STATE` (raw markdown of each file, read on demand). `Esc` returns to
  `Summary`.

**(b) Queue ticket (no `STATE.md`) → live activity view:** subject, requester/org,
status, and the **latest comments** (author + `public`/`internal` marker +
timestamp), so customer/vendor replies and internal notes are readable in-pane.
Reuses the comments the poller already fetches.

### 4.4 Top banner & notifications

`diff_tickets` change detection is unchanged: a new requester comment / status
change shows a **transient line** in the banner zone *and* fires the desktop
notifier (`build_notifier`). This preserves the watcher's alerting value, now folded
into the persistent banner rather than a disappearing overlay.

### 4.5 Data layer

- **`watch/disk_scan.py` (new):** `scan_investigations(tickets_root, *, window_days=3,
  now=...) -> list[InboxSummary]`. Walk numeric subdirs with a `STATE.md`; `stat`
  the mtime; parse; return summaries (caller applies the window for the
  "recently worked" segment but the parser itself does not filter, so queue badging
  can use any-age results).
- **`STATE.md` parser** (in `disk_scan.py`): read frontmatter scalars + the
  `related:` block + the body `> {quoted_rubric_row}` fallback. Tolerant of legacy
  files missing the newly-added fields (§4.6). Unparseable file → skipped (logged),
  never crashes the scan.
- **Refresh:** on each poll tick and on `r`, re-run disk-scan + `poll_view`, rebuild
  segments, **preserve the cursor** (by ticket id). A ticket investigated out-of-band
  surfaces as `✓` within one cycle (or immediately on `r`).

### 4.6 `render.py` frontmatter extension (approved)

Extend `_render_state` so future investigations persist the summary fields that are
currently dropped. Added to `STATE.md` frontmatter:

- top-level: `quoted_rubric_row` (YAML-escaped, single line), `cluster`
- under `related:`: `master`

This changes the *persisted output* of `investigate` but **not the agent run** — it
serialises more of the already-produced `Handoff`. Legacy tickets degrade
gracefully: `quoted_rubric_row` falls back to the existing body line; `master` /
`cluster` render as `(none)`.

### 4.7 Keybindings (viewer subset)

`↑/k ↓/j` move · `enter` focus/scroll right pane · `i` investigate (launches the
detached `investigate`, today's bridge) · `tab` / `shift+tab` cycle files · `esc`
back to Summary / unfocus · `r` refresh now · `y` copy summary to clipboard ·
`o` open ticket in Zendesk · `q` quit. *(Reference's `a` chat is deferred.)*

---

## 5. Feature B — `config` command group

### 5.1 Commands

- `noc-cli config set <key> <value>` — set one field (e.g.
  `config set watch_assignee enriquev@carbyne.com`).
- `noc-cli config get <key>` — print one value (token masked).
- `noc-cli config list` — print all fields + values (token masked).
- `noc-cli config path` — print the `.env` location.

`<key>` accepts the Config **field name** (`watch_assignee`, `zendesk_email`, …);
unknown keys are rejected with the valid list.

### 5.2 `.env` upsert behavior

New `config.py::set_config_value(key, value)`:

1. `dotenv_values(config_path())` → current file values (file only, **not** merged
   with process env, so overrides are never baked in).
2. Map `key` → env key via `_FIELD_ENV`; validate; update the one entry.
3. Rewrite the file via the existing `build_env_lines` serialiser (preserves quoting
   rules + every other field).

The full `setup` wizard is unchanged for first-run onboarding.

### 5.3 Assignee filter stays single-value

`poll_view` is unchanged (single exact email match). Dual-domain is handled by
changing the one value on demand via `config set`. (See §7 Future.)

---

## 6. Module layout

| Module | Change | Responsibility |
|--------|--------|----------------|
| `noc_cli/watch/disk_scan.py` | **new** | scan `Tickets/`, parse `STATE.md` → `InboxSummary`, mtime/window helpers |
| `noc_cli/watch/inbox.py` | **new** | `InboxSummary` / `InboxRow` models + `build_segments(...)` (pure) |
| `noc_cli/tui/watch_app.py` | **rewrite** | two-pane segmented layout; focus/detail-mode reactives; summary synthesis; file cycling; queue activity view |
| `noc_cli/render.py` | **extend** | `_render_state` frontmatter: `quoted_rubric_row`, `cluster`, `related.master` |
| `noc_cli/config.py` | **extend** | `set_config_value` (+ helpers) |
| `noc_cli/cli.py` | **extend** | `config` Typer sub-app (`set/get/list/path`) |

## 7. Data models (sketch)

```python
@dataclass
class InboxSummary:           # parsed from STATE.md (+ body fallback)
    ticket_id: int
    fork: str | None
    confidence: str | None
    status: str | None
    owner: str | None
    symptom_tag: str | None
    rubric_version: str | None
    quoted_rubric_row: str | None
    related_zendesk: list[int]
    related_jira: list[str]
    master: int | None
    cluster: str | None
    investigated_at: datetime         # from STATE.md mtime
    folder: Path

@dataclass
class InboxRow:
    ticket_id: int
    segment: Literal["worked", "queue"]
    triaged: bool
    summary: InboxSummary | None      # present iff a STATE.md exists
    ticket: Ticket | None             # live data for queue rows
    when: datetime | None
```

`build_segments(disk, live, live_state, *, now, window_days=3) -> tuple[list[InboxRow],
list[InboxRow]]` — pure; applies the 3-day window to `disk`, removes live-queue ids
from "worked", badges queue rows from `live_state`.

## 8. Error handling

- Disk scan: unparseable / missing `STATE.md` skipped; missing `tickets_root` → empty
  "recently worked".
- Poll error: existing behavior (error in banner, keep last good list).
- Right pane: missing file → `(not generated)`.
- `config set` unknown key → non-zero exit + valid-key list; never partial-writes.

## 9. Testing strategy

- **Pure logic (pytest):** `set_config_value` upsert preserves siblings + rejects
  unknown keys; `STATE.md` parse incl. **legacy files** without the new fields (body
  fallback); 3-day mtime window; `build_segments` dedup precedence + queue badging;
  `render.py` round-trips through the new parser.
- **TUI (Textual `Pilot`):** two-pane layout; segment headers; `✓`/`○` glyphs;
  summary synthesis incl. rubric mismatch ⚠; `Tab` file cycling; queue activity view.
- Existing `poller` / `diff` / `notify` tests stand.

## 10. Future / out of scope

- **Match-any multi-email assignee** (dual-domain): make `watch_assignee` a
  comma-separated list and `poll_view` match-any. Deferred — single-value + on-demand
  `config set` for now.
- All interactive agent features → `docs/interactive-feat.md`.

## Appendix — field mapping (summary ↔ source)

| Summary field | Source |
|---------------|--------|
| Ticket / Fork / Confidence / Status / Owner | `STATE.md` frontmatter |
| Quoted rubric row | `STATE.md` frontmatter (new) → body `>` fallback |
| rubric_version (state vs shipped) | `STATE.md` frontmatter vs `rubric.load_rubric().version` |
| Related: Zendesk / Jira | `STATE.md` `related.zendesk` / `related.jira` |
| Related: Master / Cluster | `STATE.md` `related.master` (new) / top-level `cluster` (new) |
| When (worked) | `STATE.md` mtime |
| When (queue) | ticket `updated_at` |
| Activity (queue) | live `get_comments` |
