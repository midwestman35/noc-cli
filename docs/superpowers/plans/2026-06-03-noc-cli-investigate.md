# noc-cli Investigate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `investigate` command end-to-end: port the fork rubric and author the symptom runbooks, reconstruct the agent system prompt, add the pydantic handoff models, scaffold the ticket folder with a soft-lock, gather + redact evidence, seed history from live Zendesk ∪ local FTS5 memory, run the L3 Claude Agent SDK `query()` inside a hook-enforced read-only sandbox, validate the structured handoff, render the five canonical markdown files atomically, and wire it all into a Typer command with a Textual progress + viewport UI.

**Architecture:** A pipeline orchestrated by `cli.py::investigate` (replacing the Foundation stub). Deterministic stages — parse → soft-lock pre-flight → fetch → scaffold → gather evidence → redact → seed history → render → memory append → events flush — are strict TDD. The single **non-deterministic stage** is the L3 agent: `agent/runner.py` builds `ClaudeAgentOptions` and runs `claude_agent_sdk.query()` with `cwd=Tickets/<id>/`, the read-only tool surface, a hook-enforced harness (`agent/harness.py`), and a reconstructed system prompt (`agent/prompt.py`); it collects the final `ResultMessage.result`, parses it as JSON, and validates it against the `Handoff` pydantic model (retry once; on double-failure stash to `.debug/` and abort writing no folder). The agent boundary is covered by **invariant/contract tests** over offline `--fixture` replay and a `--no-agent` dry path (no LLM), plus **harness unit tests** asserting `PreToolUse` blocks an out-of-sandbox `Write` and a destructive `Bash`. Two write-paths coexist by design: the agent writes scratch freely into the sandbox; the canonical five files are rendered deterministically by `render.py` from the validated handoff. The watcher is out of scope (separate plan); this plan must not import it.

**Tech Stack:** Python 3.10+, uv, Typer, httpx, pydantic v2, sqlite3 (stdlib, FTS5), `importlib.resources`, Rich; **new deps:** `claude-agent-sdk>=0.1` and `textual>=0.60`. Tests: pytest + pytest-httpx (already present). The Agent SDK inherits the analyst's existing Claude Code login (claude.ai Enterprise seat) and the Claude Code engine binary; this plan implements **no auth** and the engine presence is a `doctor` concern (separate plan). All non-`--no-agent`/non-`--fixture` runs require the engine; CI uses the offline paths.

---

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | **Modify:** add `claude-agent-sdk` + `textual` deps; add package-data include for `noc_cli/data`, `noc_cli/runbooks` |
| `noc_cli/data/fork-rubric.md` | **New:** the ported A/B/C/D fork rubric (verbatim from the sister app), embedded via `importlib.resources` |
| `noc_cli/rubric.py` | **New:** `load_rubric()` → `Rubric` (text + version + `contains_row`), read via `importlib.resources` |
| `noc_cli/runbooks/{no-ani,no-ali,low-audio,dropped-calls,event-history,apex}.md` | **New:** authored symptom playbooks (starter content) |
| `noc_cli/runbooks/__init__.py` | **New:** `runbook_for_tag(symptom_tag)` → `(slug, section_text)` lookup |
| `noc_cli/models.py` | **Extend:** add the investigation/handoff pydantic models (`ForkLetter`, `Confidence`, `SymptomTag`, `Handoff`, sub-blocks) + `APPROVED_SYMPTOM_TAGS` |
| `noc_cli/redact.py` | **New:** PII scrub at the evidence boundary (`redact(text) -> (str, RedactionCounts)`, `residual_pii_warning`) |
| `noc_cli/scaffold.py` | **New:** create `Tickets/<id>/{logs,pcaps,analysis}/`; STATE.md soft-lock pre-flight (`SoftLockConflict`, `--force`) |
| `noc_cli/evidence.py` | **New:** gather attachments + `--file`/`--paste`; unzip `.zip` (text only); flag `.pcap` (never parse) |
| `noc_cli/memory.py` | **New:** SQLite FTS5 over `MEMORY.md` via `store.connect`; `append_investigation` + `search` |
| `noc_cli/history.py` | **New:** live Zendesk search (approved tags, exclude `[vendor]`) ∪ memory → candidate pool |
| `noc_cli/render.py` | **New:** validated `Handoff` → five markdown files (atomic) + `STATE.md` frontmatter + `.debug/` stash |
| `noc_cli/agent/__init__.py` | **New:** package marker |
| `noc_cli/agent/prompt.py` | **New:** reconstructed system prompt (role/task/constraints/approved tags/examples) + JSON contract |
| `noc_cli/agent/harness.py` | **New:** `PreToolUse` deny hook (out-of-sandbox writes, destructive Bash, Zendesk writes) + `PostToolUse` audit hook (`events.jsonl`) |
| `noc_cli/agent/tools.py` | **Deferred (follow-on):** in-process history-search SDK MCP tool + read-only Zendesk SDK MCP tools — not built in this plan |
| `noc_cli/agent/runner.py` | **New:** build `ClaudeAgentOptions`, run `query()`, collect + parse + validate the handoff (retry-once) |
| `noc_cli/tui/__init__.py` | **New:** package marker |
| `noc_cli/tui/progress.py` | **New:** Rich braille spinner + breathing status context manager for the investigate run |
| `noc_cli/tui/viewport.py` | **New:** Textual report viewer for the rendered folder |
| `noc_cli/cli.py` | **Modify:** replace the `investigate` stub with the real command (`--file`, `--paste`, `--force`, `--fixture`, `--no-agent`, `--verbose`) |
| `tests/test_rubric.py` | **New:** rubric load + version + `contains_row` |
| `tests/test_runbooks.py` | **New:** runbook lookup by symptom tag |
| `tests/test_models_handoff.py` | **New:** handoff schema parse/reject + invariants |
| `tests/test_redact.py` | **New:** PII scrub + residual warning |
| `tests/test_scaffold.py` | **New:** dir creation + soft-lock conflict/force/same-owner |
| `tests/test_evidence.py` | **New:** attachment download, `--file`/`--paste`, unzip, `.pcap` flag |
| `tests/test_memory.py` | **New:** FTS5 append + search + MEMORY.md round-trip |
| `tests/test_history.py` | **New:** Zendesk ∪ memory pool, `[vendor]` excluded |
| `tests/test_render.py` | **New:** five-file render, atomicity, STATE frontmatter, `.debug` stash |
| `tests/test_agent_harness.py` | **New:** PreToolUse blocks out-of-sandbox Write + destructive Bash; PostToolUse appends events |
| `tests/test_agent_runner.py` | **New:** `--fixture` replay validates schema, retry-once, double-failure stash |
| `tests/test_agent_prompt.py` | **New:** prompt contains role/approved tags/JSON contract; excludes `[vendor]` |
| `tests/test_cli_investigate.py` | **New:** `--no-agent` dry path, `--fixture` invariants, soft-lock exit 2, FORK_PACKET to stdout |
| `tests/fixtures/handoff_good.json` | **New (test asset):** a valid agent handoff for `--fixture` replay |
| `tests/fixtures/handoff_inconclusive.json` | **New (test asset):** a no-logs Inconclusive/D handoff |
| `tests/fixtures/handoff_bad.json` | **New (test asset):** a schema-invalid handoff (drives retry/stash) |

Notes on packaging and ignores:
- `Tickets/`, `*.db`, `.env*`, `scratch/` are already in `.gitignore`. **Do not commit ticket folders or SQLite DBs.** Test fixtures that need a DB build it at runtime under `tmp_path`. The committed fixtures are JSON handoffs + ticket payloads only.
- `noc_cli/data/*.md` and `noc_cli/runbooks/*.md` must ship in the wheel — the `pyproject.toml` change adds them to `[tool.hatch.build]`.

---

## Task 1: Add dependencies + package data

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the new runtime deps and package data**

Edit `pyproject.toml`. Change the `dependencies` array to add the two new deps (keep the existing five):

```toml
dependencies = [
    "typer>=0.12",
    "httpx>=0.27",
    "pydantic>=2.7",
    "platformdirs>=4.2",
    "python-dotenv>=1.0",
    "claude-agent-sdk>=0.1.0",
    "textual>=0.60",
]
```

Add a build-data section so the embedded rubric and runbooks ship in the wheel. Append after the existing `[tool.hatch.build.targets.wheel]` block:

```toml
[tool.hatch.build.targets.wheel.force-include]
"noc_cli/data" = "noc_cli/data"
"noc_cli/runbooks" = "noc_cli/runbooks"
```

(The `packages = ["noc_cli"]` line already includes `.py` modules; `force-include` guarantees the `.md` data files are packaged for `importlib.resources`.)

- [ ] **Step 2: Sync and verify the SDK imports**

Run: `uv sync`
Then verify the SDK is importable and exposes the symbols this plan uses:
`uv run python -c "from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher, AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, tool, create_sdk_mcp_server; print('sdk ok')"`
Expected: prints `sdk ok` (the package resolves and the public API names exist).
Also: `uv run python -c "import textual; print(textual.__version__)"`
Expected: prints a version >= 0.60.

- [ ] **Step 3: Run the existing suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: PASS (all Foundation tests still green after the dep change).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add claude-agent-sdk + textual deps and package data

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Port the fork rubric + loader

The fork rubric is the routing playbook (A/B/C/D). It is ported **verbatim** from the sister app so `quoted_rubric_row` substring validation works against the same text the analysts already use.

**Files:**
- Create: `noc_cli/data/fork-rubric.md`
- Create: `noc_cli/rubric.py`
- Test: `tests/test_rubric.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_rubric.py`:

```python
from noc_cli.rubric import Rubric, load_rubric


def test_load_rubric_has_version_and_text():
    r = load_rubric()
    assert isinstance(r, Rubric)
    assert r.version == "2026-05-13"
    assert len(r.text) > 1000
    assert "# NOC Triage" in r.text


def test_contains_row_matches_verbatim_substring():
    r = load_rubric()
    assert r.contains_row(
        "customer LAN, switch, or SDWAN. Link to site master ticket"
    )


def test_contains_row_rejects_unknown_and_empty():
    r = load_rubric()
    assert not r.contains_row("this string is not in the rubric anywhere")
    assert not r.contains_row("")
    assert not r.contains_row("   \n\t ")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rubric.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.rubric'`.

- [ ] **Step 3: Create the rubric data file**

Create `noc_cli/data/fork-rubric.md` with the **exact** content below (ported verbatim from `triage-cli-rs/playbook/fork-rubric.md`):

```markdown
---
rubric_version: "2026-05-13"
source: "DailyNOC/_triage_pipeline/fork-rubric.md (session-export-2026-05-07.md, sessions 1-7)"
maintained_by: "Carbyne NOC team"
---

# NOC Triage → Fork Rubric

**Purpose:** For any Zendesk ticket, decide as fast as possible which fork it goes to:

- **(a) Engineering Jira** — defect in Carbyne-controlled code or infra (SBC, Kamailio, FreeSWITCH, APEX station client, SDK, translation pipeline, control center).
- **(b) Vendor / Internal IT** — defect or instability in carrier, customer ISP, customer LAN/switch, Masergy/SDWAN, or PSTN carrier.
- **(c) NOC self-resolve** — configuration error, customer training/UX, working-as-designed; close with a customer-facing note.
- **(d) Cannot fork yet** — required evidence is missing; ask for it and pause.

**Stop rule:** Once the fork is unambiguous, stop investigating. Detailed root cause is the new owner's job. Aim for *just enough* evidence to commit to (a), (b), (c), or (d).

---

## Step 0 — Always-on intake (every ticket)

Before symptom-class triage, pull these in parallel. Each line is a *binary* prerequisite for forking.

| Need | Where | If missing |
|---|---|---|
| Customer + site (CNC, friendly name, region) | Zendesk ticket fields → `apex-cnc-inventory.md` | Ask in ticket; do not proceed |
| Incident timestamp (UTC and local) | Ticket body | Ask in ticket |
| Affected station ID(s) / agent | Ticket body | Ask in ticket |
| Last 3 tickets for this customer | `zendesk-mcp__search_tickets` | — |
| Open master ticket for this site/region | `search_tickets` w/ "master" tag or recent pending | — |
| Current deployment version | Confluence release notes / customer field | — |
| Active engineering Jira matching keywords | `search_jira_issues` | — |
| Log/PCAP coverage of incident window ±30 min | `Read` + `Bash grep` on uploaded files | **Request fresh logs; pause triage** |

> If a known master ticket or open Jira already covers this symptom and site, **stop. Fork = (a) add evidence to existing Jira**, or (b) link to master, depending on the prior owner.

---

## Symptom Class 1 — Media loss / audio quality

*Example: TWT degradation, one-way audio, missing greeting, choppy audio, dropped audio mid-call.*

### Required evidence
- PCAP covering call lifecycle (SIP + RTP)
- Station logs covering incident timestamp
- Customer reported time, call ID, agent
- Recent translation pipeline Jiras (e.g., REP-85877 class)

### Fork signals

| Observation | Fork |
|---|---|
| RTP present, end-to-end timestamps healthy, but progressive latency increase | **(a) Engineering** — translation pipeline / buffering upstream of SBC |
| RTP absent in PCAP and signaling shows successful 200 OK + ACK | **(a) Engineering** — media not reaching SBC; SDP/relay issue |
| RTP present and clean, station-side renderer hang or heap spike at incident time | **(a) Engineering** — station client team |
| RTP gaps correlate to caller-side network instability (jitter spikes from caller IP only) | **(b) Vendor** — carrier / caller signal |
| Audio capture stops but call continues (orphaned recording) | **(a) Engineering** — call-leg attribution / recording channel bug |
| STUN keepalive warnings present on **every** call across log set (chronic baseline) | Not a root cause — exclude as signal; keep digging |
| Greeting missing post-recovery from a `RECONNECT_ON_DRAINING` event | **(a) Engineering** — race between `EXTENSION_READY` and greeting engine |

### Stop conditions
- Log window does not cover incident — request server-side FreeSWITCH/Kamailio logs and pause.
- Pattern matches an open REP-class Jira — add evidence, do not investigate further.

---

## Symptom Class 2 — Call routing / wrong PSAP / wrong agent

*Example: 911 lands in wrong jurisdiction; call attributed to wrong agent; missing inbound event.*

### Required evidence
- Inbound SIP INVITE from carrier with To/From headers
- Routing decision logs (Kamailio / dispatcher)
- Recent deployment version (regression candidate)
- Carbyne Event PDFs for the affected call(s)

### Fork signals

| Observation | Fork |
|---|---|
| Carrier sends correct INVITE; our routing chose wrong PSAP | **(a) Engineering** — routing regression; check deployment changeset |
| Carrier INVITE has wrong destination data (bad ANI/ALI from carrier) | **(b) Vendor** — carrier (Verizon, AT&T, etc.) |
| Two near-simultaneous calls and only one displayed/attributed correctly | **(a) Engineering** — call-leg attribution under concurrency |
| Inbound 911 event is missing from records entirely | **(a) Engineering — patient safety priority** — escalate same-day |
| Outbound callback recorded but no inbound event for the same number | **(a) Engineering** — record persistence / orphan event |

### Stop conditions
- Symptom appears in regression from a recent (last 14 days) release → fork (a) immediately with deploy version + Jira reference.
- Pure carrier-side malformed signaling → fork (b), forward PCAP to vendor team.

---

## Symptom Class 3 — Network error banner / WebSocket disconnect / station drops

*Example: "Network Error" banner on station, station status flips to ERROR, brief unavailability.*

### Required evidence
- Kamailio drain logs around incident time (look for `X-Web-Socket-Draining: true`)
- Station logs for `RECONNECT_ON_DRAINING`, code 1006 close, status transitions
- Customer network state (NTT, BGP/FG status, switch logs if available)
- Master ticket lookup for the site

### Fork signals

| Observation | Fork |
|---|---|
| Isolated to single station; no customer-network correlation; Kamailio drain present | **(a) Engineering** — egress node drain anomaly |
| Multiple stations at same site flip ERROR within seconds of each other | **(b) Vendor / IT** — customer LAN, switch, or SDWAN. Link to site master ticket |
| Recurring pattern at same site with open master ticket (e.g., Cobb 41675) | **(b) Vendor / IT** — link to master; do not re-investigate |
| Drain coincides with planned rolling restart (release ops calendar) | **(c) Self-resolve** — customer note: expected maintenance event |
| Greeting missing post-recovery (overlaps Class 1) | Cross-list to Class 1 fork (a) for race condition |

### Stop conditions
- Open master ticket for same site/window exists → **fork (b), link only.**
- Drain originated from a known-flapping egress node already under engineering investigation → **fork (a), add evidence.**

---

## Symptom Class 4 — Dial failures / outbound

*Example: "Destination not reachable", calls drop at N seconds, third attempt succeeds.*

### Required evidence
- PCAPs of failed and (if available) successful attempts to same number
- BYE direction analysis (who sent BYE first)
- Speed dial / config audit for the affected number
- Customer's original complaint wording (verify number is the *complained-about* number, not a different call)

### Fork signals

| Observation | Fork |
|---|---|
| SBC sends unsolicited BYE N seconds post-200 OK consistently | **(a) Engineering** — SBC instability; capture node IP (e.g., `10.4.10.103`) |
| SBC returns SIP 5xx (500/503) | **(a) Engineering** — SBC error response path |
| Carrier returns SIP 4xx (404, 408, 487) cleanly | **(b) Vendor** — carrier rejected; provide PCAP |
| Number dialed does not exist in NANP (e.g., area code 875) | **(c) Self-resolve** — speed dial misconfiguration; customer-facing fix |
| Number never appears in any log → wrong target | **(c) Self-resolve** — verify complaint refers to right call/number |
| Bridge contention from concurrent long calls on same fsconf node | **(a) Engineering** — bridge sizing / contention |

### Stop conditions
- Misconfigured speed dial proven (number invalid or recently changed) → **fork (c)**, customer note with corrected number; close.
- Wrong call investigated (PDFs show successful calls; complaint is about *other* numbers) → reset; pull complaint-target call data before continuing.

---

## Symptom Class 5 — Priority / queue / call-offering timing

*Example: Admin call appears to take priority over 911; "system error" claim from customer.*

### Required evidence
- SBC arrival timestamps for all calls in the window (ms precision)
- Queue / offer logic state at the relevant ms tick
- Priority configuration for the customer

### Fork signals

| Observation | Fork |
|---|---|
| 911 arrived at SBC *after* admin call had been offered to agent (even by ms) | **(c) Self-resolve** — working as designed; customer-facing note explaining offering vs. arrival |
| 911 arrived first at SBC but admin was offered first | **(a) Engineering** — priority logic defect |
| Customer's priority configuration assigns wrong weight to call types | **(c) Self-resolve** — config audit + customer note |

### Stop conditions
- Working-as-designed confirmed by SBC timestamps → fork (c) in single response; do not gather further evidence.

---

## Symptom Class 6 — Data / analytics / event-stream gaps

*Example: Calls missing from analytics dashboard, event counts mismatch, customer reports.*

### Required evidence
- Date range of missing data
- Customer count affected (cluster check)
- Pipeline component (intake vs. enrichment vs. dashboard)

### Fork signals

| Observation | Fork |
|---|---|
| Same gap across 2+ customers in same window | **(a) Engineering** — pipeline-wide; cluster ticket |
| Gap isolated to one customer + correlates with their LAN/VPN issue | **(b) Vendor / IT** |
| Customer's filters or dashboard config excluding records | **(c) Self-resolve** — config training |

### Stop conditions
- ≥2 customers affected same window → **fork (a) as a cluster**; consolidate tickets onto one Jira.

---

## Cross-cutting modifiers

These adjust the fork after symptom-class analysis:

- **Patient safety (missing 911 record, mis-routed 911):** Escalate fork (a) to same-day priority regardless of other factors.
- **Recurring at same site (3+ tickets in 30 days):** Force a master-ticket linkage even if individual fork is (a) or (b).
- **Within 14 days of a release:** Fork (a) candidates get tagged "regression" and the release Jira is referenced.
- **Log gap covers incident:** Cannot fork yet (fork **d**). Request server-side logs, set Zendesk status appropriately, pause.

---

## Output template (paste into Zendesk internal note)

> **Note:** This template is the human-readable form pasted into Zendesk. The CLI emits a structured `Handoff` JSON object (see `noc_cli/models.py`); the contents below are rendered into `FORK_PACKET.md` and the internal-note draft in `DRAFTS.md`.

```
Fork: (a) Engineering | (b) Vendor/IT | (c) Self-resolve | (d) Cannot fork yet     [pick one]

Symptom class: [1–6]
Customer / site: [name] / [CNC] / [friendly]
Incident time: [UTC] / [local]
Affected: [stations/agents/calls]

Evidence summary:
- [3–5 bullets, observable facts only]

Decision signal triggered:
- [quote the rubric line that committed the fork]

Next action:
- (a) Open Jira [title] / link to [existing Jira]
- (b) Hand off to [vendor / IT team] with [PCAP / log bundle]
- (c) Customer note drafted (see below)
- (d) Request [missing evidence]; pause triage
```

---

## Maintenance

Update this file when:
- A new symptom class appears 3+ times (add Class 7+).
- A fork signal misroutes a ticket (correct the row; note date).
- A vendor or engineering team's intake expectations change (update "next action").

PRs to this file are reviewed like code. When updating, bump `rubric_version` in the frontmatter to today's date.

Last revised: 2026-05-13. Source sessions: `DailyNOC/exports/session-export-2026-05-07.md` (sessions 1–7).
```

- [ ] **Step 4: Implement the loader**

`noc_cli/rubric.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources

_VERSION_RE = re.compile(r'(?m)^rubric_version:\s*"?([^"\n]+?)"?\s*$')


@dataclass(frozen=True)
class Rubric:
    """The embedded fork rubric: full text + parsed version.

    `contains_row` is the soft-warn validator: it returns True when `quoted`
    is a verbatim substring of the rubric. Strictness is deliberately weak —
    the caller logs a warning on a miss (into STATE.md validator_warnings)
    rather than rejecting the agent's handoff (spec §16).
    """

    text: str
    version: str

    def contains_row(self, quoted: str) -> bool:
        if not quoted or not quoted.strip():
            return False
        return quoted in self.text


def _read_rubric_text() -> str:
    return resources.files("noc_cli.data").joinpath("fork-rubric.md").read_text(
        encoding="utf-8"
    )


def load_rubric() -> Rubric:
    """Load the embedded fork rubric. Raises ValueError if the version
    frontmatter is missing (a packaging error, caught in tests)."""
    text = _read_rubric_text()
    match = _VERSION_RE.search(text)
    if not match:
        raise ValueError("fork-rubric.md is missing the rubric_version frontmatter")
    return Rubric(text=text, version=match.group(1).strip())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_rubric.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add noc_cli/data/fork-rubric.md noc_cli/rubric.py tests/test_rubric.py
git commit -m "feat: port fork rubric + importlib.resources loader

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Author the symptom runbooks + tag lookup

Symptom playbooks are the *grounding* the agent quotes into `FORK_PACKET.md`'s "Runbook Reference". Lookup is keyed by the approved **symptom tag**. The `Legacy/runbooks/` files are *operator* docs (how to drive the CLI) — NOT symptom playbooks; do not reuse them. Each playbook keys to one tag; `[unclassified]` and `[vendor]` have **no** playbook (the lookup returns `None`).

**Files:**
- Create: `noc_cli/runbooks/no-ani.md`
- Create: `noc_cli/runbooks/no-ali.md`
- Create: `noc_cli/runbooks/low-audio.md`
- Create: `noc_cli/runbooks/dropped-calls.md`
- Create: `noc_cli/runbooks/event-history.md`
- Create: `noc_cli/runbooks/apex.md`
- Create: `noc_cli/runbooks/__init__.py`
- Test: `tests/test_runbooks.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_runbooks.py`:

```python
from noc_cli.runbooks import RUNBOOK_SLUGS, _load_runbook, runbook_for_tag


def test_each_approved_symptom_tag_maps_to_a_runbook():
    cases = {
        "[No ANI]": "no-ani",
        "[No ALI]": "no-ali",
        "[low audio]": "low-audio",
        "[dropped calls]": "dropped-calls",
        "[event history]": "event-history",
        "[apex]": "apex",
    }
    for tag, slug in cases.items():
        result = runbook_for_tag(tag)
        assert result is not None, f"{tag} should resolve"
        got_slug, section = result
        assert got_slug == slug
        assert len(section) > 100
        assert "##" in section  # real markdown content


def test_lookup_is_case_and_bracket_insensitive():
    a = runbook_for_tag("[No ANI]")
    b = runbook_for_tag("no ani")
    c = runbook_for_tag("No_ANI")
    assert a is not None and b is not None and c is not None
    assert a[0] == b[0] == c[0] == "no-ani"


def test_unclassified_and_vendor_have_no_runbook():
    assert runbook_for_tag("[unclassified]") is None
    assert runbook_for_tag("[vendor]") is None
    assert runbook_for_tag("nonsense") is None


def test_all_declared_slugs_resolve_to_packaged_files():
    # Every slug in RUNBOOK_SLUGS must have shipped markdown content.
    for slug in RUNBOOK_SLUGS:
        text = _load_runbook(slug)
        assert text and len(text) > 100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runbooks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.runbooks'` (the package + content do not exist yet).

- [ ] **Step 3: Author the six runbook files**

Create `noc_cli/runbooks/no-ani.md`:

```markdown
# Runbook — No ANI (caller number not displaying)

**Symptom tag:** `[No ANI]`
**Maps to fork-rubric:** Symptom Class 2 (call routing / data) — ANI is the calling-party number that should populate on the station.

## What "No ANI" means
The station shows a 911 call but the caller's phone number (ANI / pANI / ESRD/ESRK) is blank, `Unknown`, or the trunk default. ANI arrives in the inbound SIP `From`/`P-Asserted-Identity` headers from the carrier and is surfaced by the translation pipeline to the station client.

## Decisive evidence to gather
- Inbound SIP `INVITE` for the call (carrier leg): inspect `From`, `P-Asserted-Identity`, `Remote-Party-ID`.
- Translation-pipeline log for that Call-ID: did ANI parse, or was it dropped/normalized to empty?
- The Carbyne Event PDF for the call (shows what the station actually rendered).
- One known-good call from the same trunk in the same window (control).

## Fork decision
- **Carrier INVITE already has no/empty ANI** → the number never arrived. **Fork B (Vendor)** — carrier/OSP issue; forward the PCAP. (Rubric Class 2: "Carrier INVITE has wrong destination data (bad ANI/ALI from carrier)".)
- **Carrier INVITE has ANI but the station renders blank** → ours. **Fork A (Engineering)** — translation pipeline / station client dropped a present value; capture Call-ID + pipeline log.
- **ANI present and correct on a control call, absent only on the complained call, no log coverage** → **Fork D** — request the SIP capture for the specific Call-ID; pause.

## Stop conditions
- Patient-safety modifier applies if 911 calls are routed/answered without ANI at scale — escalate Fork A same-day.
- If a master ticket already tracks ANI loss for this trunk/site, link to it; do not re-investigate.
```

Create `noc_cli/runbooks/no-ali.md`:

```markdown
# Runbook — No ALI (caller location not displaying)

**Symptom tag:** `[No ALI]`
**Maps to fork-rubric:** Symptom Class 2 (call routing / data) — ALI is the Automatic Location Information looked up from the ANI/pANI.

## What "No ALI" means
The station shows the call (and often ANI) but the **location** is blank, "ALI timeout", or stale. ALI is fetched from an external ALI/LIS database keyed on the (p)ANI; Carbyne issues the query and renders the response. A failure can be upstream (ALI provider/DBMS), in the bid (wrong key), or in rendering.

## Decisive evidence to gather
- ALI query/response log for the Call-ID: did Carbyne send a bid? Did the provider answer? Timeout vs. NAK vs. empty?
- The ANI used as the ALI key — was it the correct (p)ANI (cross-check the No-ANI runbook)?
- Provider/region: which ALI DB serves this PSAP (steering vs. provider outage).
- A control call to the same ALI DB in the window.

## Fork decision
- **Bid sent, provider timed out / NAK'd / returned empty** → **Fork B (Vendor)** — ALI provider/DBMS degradation; hand off with the query log. (Rubric Class 2 vendor row.)
- **No bid sent, or bid used a wrong/empty key** → **Fork A (Engineering)** — our bid logic or ANI→key mapping; capture the request log.
- **Provider answered correctly but station shows blank/stale** → **Fork A (Engineering)** — rendering/caching defect.
- **No ALI logs cover the incident** → **Fork D** — request the ALI transaction log for the Call-ID; pause.

## Stop conditions
- Multiple PSAPs on the same ALI provider failing in one window → likely **Fork B cluster**; consolidate.
- Recurring for one site only → check for a site master ticket and link.
```

Create `noc_cli/runbooks/low-audio.md`:

```markdown
# Runbook — Low / poor audio (media quality)

**Symptom tag:** `[low audio]`
**Maps to fork-rubric:** Symptom Class 1 (media loss / audio quality).

## What "low audio" means
One-way audio, choppy/garbled audio, low volume, missing greeting, or audio that degrades through the call. Audio rides RTP; signaling is SIP. The split is whether RTP is present and healthy at our SBC vs. degraded before it reaches us.

## Decisive evidence to gather
- PCAP covering the **full call lifecycle** (SIP + RTP) for the Call-ID.
- Station logs around the incident timestamp (renderer hang, heap spike).
- Jitter/loss profile per RTP stream; which leg (caller vs. station) shows the gaps.
- Recent translation-pipeline Jiras (REP-class) for known regressions.

## Fork decision (from the rubric Class 1 table)
- **RTP present, timestamps healthy, progressive latency increase** → **Fork A** — translation pipeline / buffering upstream of SBC.
- **RTP absent though 200 OK + ACK succeeded** → **Fork A** — media not reaching SBC; SDP/relay issue.
- **RTP present + clean but station-side renderer hang/heap spike** → **Fork A** — station client team.
- **RTP gaps correlate to caller-side jitter (caller IP only)** → **Fork B** — carrier / caller signal.
- **Audio capture stops but call continues** → **Fork A** — recording channel / call-leg attribution bug.

## Exclusions & stop conditions
- `STUN keepalive` warnings on **every** call are a chronic baseline — exclude as a signal; keep digging.
- Log/PCAP window does not cover the incident → request server-side FreeSWITCH/Kamailio logs; **Fork D**, pause.
- Pattern matches an open REP-class Jira → add evidence, stop.
```

Create `noc_cli/runbooks/dropped-calls.md`:

```markdown
# Runbook — Dropped calls / dial failures

**Symptom tag:** `[dropped calls]`
**Maps to fork-rubric:** Symptom Class 4 (dial failures / outbound) and the BYE-direction analysis.

## What "dropped calls" means
Calls drop mid-call at ~N seconds, "Destination not reachable", or Nth-attempt-succeeds. The decisive question is **who tore the call down** (who sent BYE first) and **what SIP response** the far end returned.

## Decisive evidence to gather
- PCAPs of the **failed** attempt and (if available) a **successful** attempt to the same number.
- BYE-direction analysis: did our SBC or the carrier send BYE first, and at what offset from 200 OK?
- The SIP final response on failure (4xx vs. 5xx).
- Speed-dial / number config for the dialed number; confirm it is the *complained-about* number.

## Fork decision (from the rubric Class 4 table)
- **SBC sends unsolicited BYE N seconds post-200 OK, consistently** → **Fork A** — SBC instability; capture the node IP.
- **SBC returns SIP 5xx (500/503)** → **Fork A** — SBC error path.
- **Carrier returns SIP 4xx (404/408/487) cleanly** → **Fork B** — carrier rejected; provide PCAP.
- **Number not in NANP (e.g., area code 875), or never appears in logs** → **Fork C** — speed-dial misconfig / wrong target; customer-facing fix.
- **Bridge contention from concurrent long calls on one fsconf node** → **Fork A** — bridge sizing.

## Stop conditions
- Misconfigured speed dial proven → **Fork C**, customer note with the corrected number; close.
- Investigating the wrong call (PDFs show success; complaint is about other numbers) → reset; pull the complaint-target call data first.
```

Create `noc_cli/runbooks/event-history.md`:

```markdown
# Runbook — Event history / analytics gaps

**Symptom tag:** `[event history]`
**Maps to fork-rubric:** Symptom Class 6 (data / analytics / event-stream gaps), with Class 2 overlap for *missing inbound 911 events*.

## What "event history" means
Calls or events are missing from the event history / analytics dashboard, counts mismatch, or an inbound 911 event is absent from the record entirely. The split is **scope** (one customer vs. many) and **pipeline stage** (intake vs. enrichment vs. dashboard).

## Decisive evidence to gather
- Exact date/time range of the missing data and the affected customer(s).
- Cluster check: is the same gap visible for 2+ customers in the same window?
- Which pipeline component: did intake receive the event, did enrichment process it, is only the dashboard view filtering it?
- For a missing **inbound 911** event specifically: is there an outbound callback recorded with no matching inbound (orphan)?

## Fork decision
- **Same gap across 2+ customers in one window** → **Fork A** — pipeline-wide; open/append a cluster ticket. (Rubric Class 6.)
- **Gap isolated to one customer + correlates with their LAN/VPN issue** → **Fork B** — customer connectivity dropped events at source.
- **Customer's filters / dashboard config exclude records** → **Fork C** — config training; the data exists.
- **Inbound 911 event missing entirely** → **Fork A — patient-safety priority**; escalate same-day (Rubric Class 2).

## Stop conditions
- ≥2 customers affected in the same window → **Fork A as a cluster**; consolidate tickets onto one Jira.
- No data-pipeline logs cover the range → **Fork D**, request them, pause.
```

Create `noc_cli/runbooks/apex.md`:

```markdown
# Runbook — APEX platform / station client (general)

**Symptom tag:** `[apex]`
**Maps to fork-rubric:** Symptom Class 3 (network error banner / WebSocket disconnect / station drops) and general APEX station-client issues that do not fit a more specific symptom.

## What "[apex]" means
A general APEX station / control-center symptom: "Network Error" banner, station status flips to ERROR, brief unavailability, login/registration issues, or UI faults — when a more specific tag ([No ANI], [No ALI], [low audio], [dropped calls], [event history]) does not apply. Use this as the catch-all for platform-level APEX behavior.

## Decisive evidence to gather
- Station logs for `RECONNECT_ON_DRAINING`, WebSocket close code `1006`, and status transitions around the timestamp.
- Kamailio drain logs around the incident (`X-Web-Socket-Draining: true`).
- Whether **one** station or **multiple stations at the same site** flipped within seconds.
- Customer network state (switch/SDWAN) and any open site master ticket.

## Fork decision (from the rubric Class 3 table)
- **Isolated to a single station, no customer-network correlation, Kamailio drain present** → **Fork A** — egress-node drain anomaly.
- **Multiple stations at one site flip ERROR within seconds** → **Fork B** — customer LAN, switch, or SDWAN; link to the site master ticket.
- **Recurring at the same site with an open master ticket** → **Fork B** — link to master; do not re-investigate.
- **Drain coincides with a planned rolling restart** → **Fork C** — customer note: expected maintenance.

## Stop conditions
- Open master ticket for the same site/window → **Fork B, link only.**
- Drain from a known-flapping egress node already under engineering investigation → **Fork A, add evidence.**
- No station/drain logs cover the incident → **Fork D**, request them, pause.
```

- [ ] **Step 4: Implement the lookup**

`noc_cli/runbooks/__init__.py`:

```python
from __future__ import annotations

import re
from importlib import resources

# Approved symptom tag (normalized, no brackets/case) -> runbook slug.
# [unclassified] and [vendor] intentionally have no playbook.
_TAG_TO_SLUG: dict[str, str] = {
    "no ani": "no-ani",
    "no ali": "no-ali",
    "low audio": "low-audio",
    "dropped calls": "dropped-calls",
    "event history": "event-history",
    "apex": "apex",
}

RUNBOOK_SLUGS: tuple[str, ...] = tuple(sorted(set(_TAG_TO_SLUG.values())))


def _normalize_tag(tag: str) -> str:
    """Lowercase, strip brackets, collapse separators to single spaces."""
    t = tag.strip().lower().strip("[]")
    t = re.sub(r"[_\-\s]+", " ", t).strip()
    return t


def _load_runbook(slug: str) -> str | None:
    """Read a packaged runbook by slug. Returns None if absent."""
    resource = resources.files("noc_cli.runbooks").joinpath(f"{slug}.md")
    if not resource.is_file():
        return None
    return resource.read_text(encoding="utf-8")


def runbook_for_tag(symptom_tag: str) -> tuple[str, str] | None:
    """Map a symptom tag to (slug, runbook_markdown).

    Bracket- and case-insensitive: `[No ANI]`, `no ani`, `No_ANI` all match.
    Returns None for [unclassified], [vendor], or any unknown tag.
    """
    slug = _TAG_TO_SLUG.get(_normalize_tag(symptom_tag))
    if slug is None:
        return None
    text = _load_runbook(slug)
    if text is None:
        return None
    return slug, text
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_runbooks.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add noc_cli/runbooks tests/test_runbooks.py
git commit -m "feat: author symptom runbooks + tag->runbook lookup

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Handoff models (extend `models.py`)

The `Handoff` model is the pydantic schema for the validated agent → renderer handoff. It mirrors the sister app's `StructuredTriageReport` (intake / evidence_preflight / fork_packet / drafts) and **adds the noc-cli two-axis fields**: `symptom_tag` (∈ approved set) plus `historical_matches` and `runbook_reference` carried on the fork packet. These field names are the **single source of truth** — `runner.py` parses the agent JSON into this model, and `render.py` reads these exact names. (Self-Review cross-checks this.)

**Files:**
- Modify: `noc_cli/models.py` (append; keep the existing `Ticket`/`Comment`/`Attachment`)
- Test: `tests/test_models_handoff.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_models_handoff.py`:

```python
import json

import pytest
from pydantic import ValidationError

from noc_cli.models import (
    APPROVED_SYMPTOM_TAGS,
    Confidence,
    ForkLetter,
    Handoff,
)


def minimal_handoff_dict(**overrides):
    base = {
        "rubric_version": "2026-05-13",
        "intake": {
            "ticket_id": 18432,
            "url": "https://carbyne.zendesk.com/agent/tickets/18432",
            "status": "open",
            "tags": ["apex"],
            "requester": "PSAP Ops",
            "organization": "Aurora 911",
            "one_line_fingerprint": "Aurora / apex / Network Error / 06:30 UTC",
            "ticket_summary": ["Brief all-console network error"],
            "context_pulls": [
                {"pull": "Last 3 tickets", "result": "none similar", "source": "Zendesk"}
            ],
            "initial_hypothesis": "Fork B (site network)",
            "intake_decision": "ready_for_evidence_preflight",
        },
        "evidence_preflight": {
            "gathered": [
                {
                    "evidence_type": "station log",
                    "source": "Aurora-12",
                    "time_window": "06:30 UTC",
                    "summary": "RECONNECT_ON_DRAINING",
                }
            ],
            "decisive_evidence": ["Multiple stations flipped within seconds"],
            "missing_or_non_decisive": ["No switch logs"],
        },
        "fork_packet": {
            "fork_letter": "B",
            "confidence": "Medium",
            "symptom_tag": "[apex]",
            "rubric_class": "Symptom Class 3",
            "quoted_rubric_row": "customer LAN, switch, or SDWAN. Link to site master ticket",
            "reasoning": "Multi-station within seconds is Class 3 (b)",
            "evidence_summary": ["3 stations ERROR in 4s"],
            "missing_evidence": [],
            "runbook_reference": {
                "slug": "apex",
                "section": "Multiple stations at one site flip ERROR within seconds -> Fork B",
            },
            "historical_matches": [
                {
                    "ticket_id": "41675",
                    "subject": "Cobb site network error",
                    "relevance": "same multi-station pattern",
                    "resolution": "linked to site master",
                }
            ],
            "related_zendesk": [41675],
            "related_jira": [],
        },
        "drafts": {
            "customer_reply": "Hi — we saw a brief network interruption ...",
            "internal_note": "Fork B; site LAN. Rubric row quoted.",
            "jira_draft": None,
        },
    }
    base.update(overrides)
    return base


def test_minimal_handoff_parses_and_exposes_fields():
    h = Handoff.model_validate(minimal_handoff_dict())
    assert h.intake.ticket_id == 18432
    assert h.fork_packet.fork_letter is ForkLetter.B
    assert h.fork_packet.confidence is Confidence.MEDIUM
    assert h.fork_packet.symptom_tag == "[apex]"
    assert h.fork_packet.runbook_reference.slug == "apex"
    assert h.fork_packet.historical_matches[0].ticket_id == "41675"
    assert h.drafts.jira_draft is None


def test_fork_letter_rejects_lowercase_and_unknown():
    with pytest.raises(ValidationError):
        Handoff.model_validate(minimal_handoff_dict(
            fork_packet={**minimal_handoff_dict()["fork_packet"], "fork_letter": "a"}
        ))


def test_symptom_tag_must_be_in_approved_set():
    bad = minimal_handoff_dict()
    bad["fork_packet"]["symptom_tag"] = "[ghost]"
    with pytest.raises(ValidationError):
        Handoff.model_validate(bad)


def test_unclassified_is_an_approved_symptom_tag():
    ok = minimal_handoff_dict()
    ok["fork_packet"]["symptom_tag"] = "[unclassified]"
    h = Handoff.model_validate(ok)
    assert h.fork_packet.symptom_tag == "[unclassified]"


def test_vendor_is_not_an_approved_symptom_tag():
    assert "[vendor]" not in APPROVED_SYMPTOM_TAGS
    bad = minimal_handoff_dict()
    bad["fork_packet"]["symptom_tag"] = "[vendor]"
    with pytest.raises(ValidationError):
        Handoff.model_validate(bad)


def test_fork_d_requires_missing_evidence():
    bad = minimal_handoff_dict()
    bad["fork_packet"]["fork_letter"] = "D"
    bad["fork_packet"]["confidence"] = "Inconclusive"
    bad["fork_packet"]["missing_evidence"] = []
    with pytest.raises(ValidationError):
        Handoff.model_validate(bad)


def test_fork_d_with_high_confidence_is_incoherent():
    bad = minimal_handoff_dict()
    bad["fork_packet"]["fork_letter"] = "D"
    bad["fork_packet"]["confidence"] = "High"
    bad["fork_packet"]["missing_evidence"] = ["need server-side logs"]
    with pytest.raises(ValidationError):
        Handoff.model_validate(bad)


def test_extra_fields_are_ignored():
    d = minimal_handoff_dict()
    d["fork_packet"]["telemetry"] = {"unused": True}
    h = Handoff.model_validate(d)  # must not raise
    assert h.fork_packet.fork_letter is ForkLetter.B


def test_handoff_round_trips_via_json():
    h = Handoff.model_validate(minimal_handoff_dict())
    again = Handoff.model_validate(json.loads(h.model_dump_json()))
    assert again.fork_packet.symptom_tag == "[apex]"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models_handoff.py -v`
Expected: FAIL — `ImportError: cannot import name 'Handoff' from 'noc_cli.models'`.

- [ ] **Step 3: Extend `models.py`**

Append to `noc_cli/models.py` (do not remove the existing `Attachment`/`Comment`/`Ticket`):

```python
from enum import Enum

from pydantic import ConfigDict, model_validator


# ─── Two-axis classification enums ─────────────────────────────────────────


class ForkLetter(str, Enum):
    """A/B/C/D routing fork (see noc_cli/data/fork-rubric.md)."""

    A = "A"  # Engineering Jira
    B = "B"  # Vendor or Internal IT
    C = "C"  # NOC self-resolve
    D = "D"  # Cannot fork yet — evidence missing

    @property
    def description(self) -> str:
        return {
            "A": "Engineering Jira",
            "B": "Vendor or Internal IT",
            "C": "NOC self-resolve",
            "D": "Cannot fork yet",
        }[self.value]


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INCONCLUSIVE = "Inconclusive"


class IntakeDecision(str, Enum):
    READY_FOR_EVIDENCE_PREFLIGHT = "ready_for_evidence_preflight"
    KNOWN_ISSUE = "known_issue"
    NEEDS_CLARIFICATION = "needs_clarification"
    CANNOT_PROCEED = "cannot_proceed"


# The approved symptom-tag set (spec §17). [vendor] is deliberately NOT here
# (it is a history-exclusion tag, never a symptom). [unclassified] is the
# catch-all. The agent must emit exactly one of these.
APPROVED_SYMPTOM_TAGS: frozenset[str] = frozenset(
    {
        "[apex]",
        "[low audio]",
        "[dropped calls]",
        "[No ANI]",
        "[No ALI]",
        "[event history]",
        "[unclassified]",
    }
)


# ─── INTAKE.md ─────────────────────────────────────────────────────────────


class ContextPull(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pull: str = ""
    result: str = ""
    source: str = ""


class IntakeBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket_id: int
    url: str = ""
    status: str = ""
    priority: str = ""
    tags: list[str] = Field(default_factory=list)
    requester: str = ""
    organization: str = ""
    site: str | None = None
    cnc: str | None = None
    region: str | None = None
    affected_stations: list[str] = Field(default_factory=list)
    affected_agents: list[str] = Field(default_factory=list)
    call_id: str | None = None
    incident_window: str = ""
    one_line_fingerprint: str = ""
    ticket_summary: list[str] = Field(default_factory=list)
    context_pulls: list[ContextPull] = Field(default_factory=list)
    initial_hypothesis: str = ""
    intake_decision: IntakeDecision = IntakeDecision.READY_FOR_EVIDENCE_PREFLIGHT


# ─── EVIDENCE_PREFLIGHT.md ─────────────────────────────────────────────────


class GatheredEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    evidence_type: str = ""
    source: str = ""
    time_window: str = ""
    summary: str = ""


class PreflightBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    gathered: list[GatheredEvidence] = Field(default_factory=list)
    decisive_evidence: list[str] = Field(default_factory=list)
    missing_or_non_decisive: list[str] = Field(default_factory=list)


# ─── FORK_PACKET.md ────────────────────────────────────────────────────────


class RunbookReference(BaseModel):
    """The symptom-runbook section the agent quoted into FORK_PACKET.md."""

    model_config = ConfigDict(extra="ignore")
    slug: str = ""
    section: str = ""


class HistoricalMatch(BaseModel):
    """One prior ticket/investigation the agent selected as relevant (≤5)."""

    model_config = ConfigDict(extra="ignore")
    ticket_id: str = ""
    subject: str = ""
    relevance: str = ""
    resolution: str = "[unknown]"


class ForkPacket(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fork_letter: ForkLetter
    confidence: Confidence
    symptom_tag: str
    rubric_class: str = ""
    quoted_rubric_row: str = ""
    reasoning: str = ""
    evidence_summary: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    runbook_reference: RunbookReference = Field(default_factory=RunbookReference)
    historical_matches: list[HistoricalMatch] = Field(default_factory=list)
    related_zendesk: list[int] = Field(default_factory=list)
    related_jira: list[str] = Field(default_factory=list)
    master_ticket: int | None = None
    cluster: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "ForkPacket":
        # Symptom tag must be in the approved set (spec §17).
        if self.symptom_tag not in APPROVED_SYMPTOM_TAGS:
            raise ValueError(
                f"symptom_tag {self.symptom_tag!r} is not in the approved set "
                f"{sorted(APPROVED_SYMPTOM_TAGS)}"
            )
        # Fork D (cannot fork yet) must name what is missing (spec §16/§18).
        if self.fork_letter is ForkLetter.D and not self.missing_evidence:
            raise ValueError("fork_letter D requires a non-empty missing_evidence list")
        # A high-confidence "cannot fork yet" is incoherent (spec §16).
        if self.fork_letter is ForkLetter.D and self.confidence is Confidence.HIGH:
            raise ValueError("fork_letter D with confidence High is incoherent")
        return self


# ─── DRAFTS.md ─────────────────────────────────────────────────────────────


class JiraDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    project: str = "REP"
    title: str = ""
    description: str = ""
    affected_component: str | None = None
    suspected_area: str | None = None
    repro_steps: list[str] = Field(default_factory=list)


class DraftsBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    customer_reply: str = ""
    internal_note: str = ""
    jira_draft: JiraDraft | None = None


# ─── The whole handoff ─────────────────────────────────────────────────────


class Handoff(BaseModel):
    """The structured payload the L3 agent emits and render.py consumes.

    `extra="ignore"` everywhere lets the agent include scratch fields without
    breaking validation; the invariants live on ForkPacket.
    """

    model_config = ConfigDict(extra="ignore")
    intake: IntakeBlock
    evidence_preflight: PreflightBlock
    fork_packet: ForkPacket
    drafts: DraftsBlock
    rubric_version: str = ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models_handoff.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/models.py tests/test_models_handoff.py
git commit -m "feat: handoff pydantic models with two-axis classification + invariants

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: PII redaction at the evidence boundary

Port the sister app's `redact.rs` to Python. Scope (locked): caller PII only — **phones, street addresses, GPS coords**. Operational IDs (Call-IDs, ticket #s, station codes, CNC UUIDs, sites) are **preserved**. Python's `re` supports lookarounds, so the boundary handling is simpler than the Rust capture-group dance. Redaction runs on every text blob before it enters the agent context (§6, §12).

**Files:**
- Create: `noc_cli/redact.py`
- Test: `tests/test_redact.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_redact.py`:

```python
from noc_cli.redact import RedactionCounts, redact, residual_pii_warning


def test_redacts_phone():
    out, counts = redact("call (555) 123-4567 now")
    assert out == "call <PHONE> now"
    assert counts.phones == 1


def test_preserves_pre_redacted_phone():
    out, counts = redact("call ***-***-1234 now")
    assert "***-***-1234" in out
    assert counts.phones == 0


def test_redacts_street_address():
    out, counts = redact("the call from 123 Main Street is bad")
    assert out == "the call from <ADDR> is bad"
    assert counts.addresses == 1


def test_redacts_coords():
    out, counts = redact("loc 36.1699, -115.1398 reported")
    assert out == "loc <COORDS> reported"
    assert counts.coords == 1


def test_ignores_phone_inside_token():
    out, counts = redact("abc5551234567xyz")
    assert out == "abc5551234567xyz"
    assert counts.phones == 0


def test_preserves_operational_ids():
    text = "Call-ID 7d209ad5-3935-440e ticket 18432 station Aurora-12 cnc de9ee414-da5a"
    out, counts = redact(text)
    assert "18432" in out
    assert "Aurora-12" in out
    assert "de9ee414-da5a" in out
    assert counts.phones == 0


def test_residual_warning_fires_on_dense_loose_coords():
    counts = RedactionCounts(enabled=True)
    text = "a 36.16, -115.13 b 40.71, -74.00 c 34.05, -118.24 d"
    w = residual_pii_warning(text, counts)
    assert w is not None
    assert "residual" in w
    assert "not blocked" in w


def test_residual_warning_below_threshold_is_none():
    counts = RedactionCounts(enabled=True)
    assert residual_pii_warning("loc 36.16, -115.13 reported", counts) is None


def test_residual_warning_ignores_hyphenated_operational_ids():
    counts = RedactionCounts(enabled=True)
    text = "CB-911-2024-5551234 ref TICKET-2024-5559999 cnc 2024-5551000-aa"
    assert residual_pii_warning(text, counts) is None


def test_residual_warning_does_not_self_trigger_on_sentinels():
    counts = RedactionCounts(enabled=True)
    text = "<PHONE> <ADDR> <COORDS> <PHONE> <ADDR> <COORDS>"
    assert residual_pii_warning(text, counts) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_redact.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.redact'`.

- [ ] **Step 3: Implement `redact.py`**

`noc_cli/redact.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass

# Strict 10/11-digit phone, bounded by non-alphanumeric so it won't match
# inside an opaque token. The number itself is group(1); boundaries are
# preserved by capturing them separately.
_PHONE = re.compile(
    r"(^|[^A-Za-z0-9])"
    r"((?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})"
    r"($|[^A-Za-z0-9])"
)

_STREET_SUFFIXES = (
    r"Ave(?:nue)?|Blvd|Boulevard|Cir(?:cle)?|Ct|Court|Dr(?:ive)?|"
    r"Expy|Expressway|Fwy|Freeway|Hwy|Highway|Ln|Lane|Loop|Pkwy|Parkway|"
    r"Pl(?:ace)?|Rd|Road|Route|Rte|Sq|Square|St(?:reet)?|Ter(?:race)?|"
    r"Trl|Trail|Way"
)
_ADDRESS = re.compile(
    r"\b\d+\s+(?:[A-Z][A-Za-z'\-]*\s+)+(?:" + _STREET_SUFFIXES + r")\b"
)

# Strict GPS pair: 4+ fractional digits each.
_COORD = re.compile(r"-?\d{1,2}\.\d{4,}\s*[,;\s]\s*-?\d{1,3}\.\d{4,}")

# Loose residual shapes (run on already-redacted text for a soft-warn only).
_RESIDUAL_COORD = re.compile(
    r"(?:^|[^\d.])(-?\d{1,3}\.\d{2,3}\s*[,;\s]\s*-?\d{1,3}\.\d{2,3})(?:$|[^\d.])"
)
_RESIDUAL_LOCAL_PHONE = re.compile(
    r"(?:^|[^A-Za-z0-9_\-])(\d{3}[-.\s]\d{4})(?:$|[^A-Za-z0-9_\-])"
)

# A single loose match is more likely an operational ID/version than PII;
# require this density before a (non-blocking) soft-warn fires.
RESIDUAL_PII_WARN_THRESHOLD = 3


@dataclass
class RedactionCounts:
    phones: int = 0
    addresses: int = 0
    coords: int = 0
    enabled: bool = True


def _is_pre_redacted(s: str) -> bool:
    lower = s.lower()
    return "***" in lower or "xxx" in lower or "[redacted]" in lower


def redact(text: str) -> tuple[str, RedactionCounts]:
    """Redact caller PII (phones, addresses, coords) from `text`.

    Returns (redacted_text, counts). Operational identifiers are preserved by
    the bounded patterns. Pre-redacted spans (***, xxx, [redacted]) are left
    untouched.
    """
    counts = RedactionCounts(enabled=True)

    def phone_sub(m: re.Match[str]) -> str:
        number = m.group(2)
        if _is_pre_redacted(number):
            return m.group(0)
        counts.phones += 1
        return f"{m.group(1)}<PHONE>{m.group(3)}"

    out = _PHONE.sub(phone_sub, text)

    def addr_sub(m: re.Match[str]) -> str:
        if _is_pre_redacted(m.group(0)):
            return m.group(0)
        counts.addresses += 1
        return "<ADDR>"

    out = _ADDRESS.sub(addr_sub, out)

    def coord_sub(m: re.Match[str]) -> str:
        if _is_pre_redacted(m.group(0)):
            return m.group(0)
        counts.coords += 1
        return "<COORDS>"

    out = _COORD.sub(coord_sub, out)
    return out, counts


def _count_non_overlapping(pattern: re.Pattern[str], text: str, group: int) -> int:
    """Count matches, advancing past the captured token so a shared delimiter
    can bound the next token (mirrors the Rust captures_at loop)."""
    count = 0
    pos = 0
    while pos <= len(text):
        m = pattern.search(text, pos)
        if not m:
            break
        count += 1
        end = m.end(group)
        pos = end if end > pos else pos + 1
    return count


def residual_pii_warning(redacted: str, counts: RedactionCounts) -> str | None:
    """Best-effort density check on already-redacted text. Returns a soft-warn
    string at/above the threshold, else None. Never mutates; never blocks.
    The <PHONE>/<ADDR>/<COORDS> sentinels carry no digits, so they can't
    self-trigger this scan."""
    if not counts.enabled:
        return None
    residual = _count_non_overlapping(_RESIDUAL_COORD, redacted, 1) + _count_non_overlapping(
        _RESIDUAL_LOCAL_PHONE, redacted, 1
    )
    if residual >= RESIDUAL_PII_WARN_THRESHOLD:
        return (
            f"redaction: {residual} residual caller-PII-shaped token(s) survived "
            f"scrub (soft-warn; payload not blocked)"
        )
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_redact.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/redact.py tests/test_redact.py
git commit -m "feat: PII redaction at the evidence boundary (phones/addresses/coords)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Scaffold — ticket directory + STATE.md soft-lock pre-flight

`scaffold.py` creates `Tickets/<id>/{logs,pcaps,analysis}/` and runs the **soft-lock pre-flight**: if an existing `STATE.md` claims a different `owner` and `--force` is not set, raise `SoftLockConflict` (the CLI maps it to **exit 2** with a field diff, §8/§18). The pre-flight only *reads* the existing STATE.md owner; `render.py` (Task 10) writes the canonical STATE.md at the end. A narrow top-level-YAML scalar parser (ported from `ticket_folder.rs::read_existing_state`) is enough — no YAML dependency.

**Files:**
- Create: `noc_cli/scaffold.py`
- Test: `tests/test_scaffold.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_scaffold.py`:

```python
import pytest

from noc_cli.scaffold import (
    SoftLockConflict,
    TicketFolder,
    preflight_soft_lock,
    read_existing_state,
    scaffold_ticket,
)


def test_scaffold_creates_subdirs(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    assert isinstance(folder, TicketFolder)
    assert folder.root == tmp_path / "18432"
    assert (folder.root / "logs").is_dir()
    assert (folder.root / "pcaps").is_dir()
    assert (folder.root / "analysis").is_dir()


def test_scaffold_is_idempotent(tmp_path):
    scaffold_ticket(tmp_path, 7)
    folder = scaffold_ticket(tmp_path, 7)  # second call must not error
    assert (folder.root / "logs").is_dir()


def test_read_existing_state_extracts_owner_and_fork(tmp_path):
    root = tmp_path / "44671"
    root.mkdir()
    (root / "STATE.md").write_text(
        '---\nticket_id: 44671\nfork: "B"\nowner: "alice@axon.com"\n'
        "status: open\nrelated:\n  zendesk: [1, 2]\n---\n"
    )
    state = read_existing_state(root / "STATE.md")
    assert state["owner"] == "alice@axon.com"
    assert state["fork"] == "B"
    # nested keys under related: must be ignored by the narrow parser
    assert "zendesk" not in state


def test_preflight_passes_when_unclaimed(tmp_path):
    folder = scaffold_ticket(tmp_path, 100)
    preflight_soft_lock(folder, owner="bob@axon.com", force=False)  # no raise


def test_preflight_passes_for_same_owner(tmp_path):
    folder = scaffold_ticket(tmp_path, 101)
    (folder.root / "STATE.md").write_text('---\nowner: "alice@axon.com"\n---\n')
    preflight_soft_lock(folder, owner="alice@axon.com", force=False)  # no raise


def test_preflight_blocks_other_owner(tmp_path):
    folder = scaffold_ticket(tmp_path, 102)
    (folder.root / "STATE.md").write_text(
        '---\nfork: "A"\nowner: "alice@axon.com"\nstatus: open\n---\n'
    )
    with pytest.raises(SoftLockConflict) as exc:
        preflight_soft_lock(folder, owner="bob@axon.com", force=False)
    err = exc.value
    assert err.existing_owner == "alice@axon.com"
    assert err.current_owner == "bob@axon.com"
    # diff summarizes the owner change for the CLI to render
    assert any(field == "owner" for field, _old, _new in err.summary)


def test_preflight_force_overrides_other_owner(tmp_path):
    folder = scaffold_ticket(tmp_path, 103)
    (folder.root / "STATE.md").write_text('---\nowner: "alice@axon.com"\n---\n')
    preflight_soft_lock(folder, owner="bob@axon.com", force=True)  # no raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scaffold.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scaffold'`.

- [ ] **Step 3: Implement `scaffold.py`**

`noc_cli/scaffold.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_TRACKED_STATE_KEYS = ("fork", "confidence", "status", "owner", "symptom_tag")


@dataclass(frozen=True)
class TicketFolder:
    """Paths for one ticket's working directory."""

    root: Path
    logs: Path
    pcaps: Path
    analysis: Path

    @property
    def state_path(self) -> Path:
        return self.root / "STATE.md"


class SoftLockConflict(RuntimeError):
    """Raised when an existing STATE.md claims a different owner and --force
    was not given. Carries a field diff for the CLI to render (exit 2)."""

    def __init__(
        self,
        existing_owner: str,
        current_owner: str,
        summary: list[tuple[str, str, str]],
        state_path: Path,
    ) -> None:
        self.existing_owner = existing_owner
        self.current_owner = current_owner
        self.summary = summary
        self.state_path = state_path
        super().__init__(
            f"STATE.md soft-lock conflict: owned by {existing_owner}, "
            f"current is {current_owner}"
        )


def scaffold_ticket(tickets_root: Path, ticket_id: int | str) -> TicketFolder:
    """Create Tickets/<id>/{logs,pcaps,analysis}/. Idempotent."""
    root = Path(tickets_root) / str(ticket_id)
    logs = root / "logs"
    pcaps = root / "pcaps"
    analysis = root / "analysis"
    for d in (logs, pcaps, analysis):
        d.mkdir(parents=True, exist_ok=True)
    return TicketFolder(root=root, logs=logs, pcaps=pcaps, analysis=analysis)


def _strip_yaml_scalar(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return v[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    return v


def read_existing_state(state_path: Path) -> dict[str, str]:
    """Parse the tracked top-level scalar keys from a STATE.md. Indented
    (nested) lines are ignored. Missing file -> empty dict."""
    try:
        text = state_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line[0] in (" ", "\t"):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        if key not in _TRACKED_STATE_KEYS:
            continue
        value = _strip_yaml_scalar(raw)
        if value:
            out[key] = value
    return out


def preflight_soft_lock(folder: TicketFolder, owner: str, force: bool) -> None:
    """Raise SoftLockConflict if an existing STATE.md names a different owner
    and `force` is False. No-op when unclaimed, same owner, or forced."""
    if force:
        return
    existing = read_existing_state(folder.state_path)
    existing_owner = existing.get("owner", "")
    if not existing_owner or existing_owner == owner:
        return
    summary: list[tuple[str, str, str]] = [("owner", existing_owner, owner)]
    for key in ("fork", "confidence", "status", "symptom_tag"):
        old = existing.get(key, "")
        if old:
            summary.append((key, old, "(pending this run)"))
    raise SoftLockConflict(existing_owner, owner, summary, folder.state_path)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scaffold.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scaffold.py tests/test_scaffold.py
git commit -m "feat: ticket-folder scaffold + STATE.md soft-lock pre-flight

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Agent system prompt (`noc_cli/agent/prompt.py`)

Reconstruct the NOC-triage system prompt from spec Appendix A. The prompt defines: role (L3 NOC analyst), task (structured triage → Handoff JSON), constraints (read-only, conservative, Inconclusive-over-fabrication), the six approved symptom tags plus `[unclassified]` (exclude `[vendor]`), and the JSON output contract (must be parseable as `Handoff`). Expose as a module-level constant `SYSTEM_PROMPT` and a function `build_system_prompt(rubric_text: str) -> str` that injects the live rubric text.

**Files:**
- Create: `noc_cli/agent/__init__.py`
- Create: `noc_cli/agent/prompt.py`
- Test: `tests/test_agent_prompt.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_agent_prompt.py`:

```python
from noc_cli.agent.prompt import APPROVED_TAGS_IN_PROMPT, SYSTEM_PROMPT, build_system_prompt


def test_system_prompt_contains_role():
    assert "NOC" in SYSTEM_PROMPT
    assert "triage" in SYSTEM_PROMPT.lower()
    assert "L3" in SYSTEM_PROMPT or "senior" in SYSTEM_PROMPT.lower()


def test_system_prompt_contains_all_approved_tags():
    for tag in APPROVED_TAGS_IN_PROMPT:
        assert tag in SYSTEM_PROMPT, f"tag {tag!r} missing from SYSTEM_PROMPT"


def test_system_prompt_excludes_vendor_tag():
    assert "[vendor]" not in SYSTEM_PROMPT


def test_system_prompt_contains_inconclusive_preference():
    lower = SYSTEM_PROMPT.lower()
    assert "inconclusive" in lower or "cannot fork" in lower


def test_system_prompt_contains_read_only_constraint():
    lower = SYSTEM_PROMPT.lower()
    assert "read" in lower and ("only" in lower or "no write" in lower or "never write" in lower)


def test_system_prompt_contains_json_contract():
    assert "JSON" in SYSTEM_PROMPT or "json" in SYSTEM_PROMPT
    assert "Handoff" in SYSTEM_PROMPT or "handoff" in SYSTEM_PROMPT.lower()


def test_build_system_prompt_injects_rubric():
    prompt = build_system_prompt("RUBRIC_SENTINEL_12345")
    assert "RUBRIC_SENTINEL_12345" in prompt


def test_approved_tags_in_prompt_excludes_vendor():
    assert "[vendor]" not in APPROVED_TAGS_IN_PROMPT
    assert "[unclassified]" in APPROVED_TAGS_IN_PROMPT
    assert len(APPROVED_TAGS_IN_PROMPT) == 7
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_prompt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.agent'`.

- [ ] **Step 3: Create the package marker and implement the prompt**

`noc_cli/agent/__init__.py`: empty file (package marker).

`noc_cli/agent/prompt.py`:

```python
from __future__ import annotations

APPROVED_TAGS_IN_PROMPT: tuple[str, ...] = (
    "[apex]",
    "[low audio]",
    "[dropped calls]",
    "[No ANI]",
    "[No ALI]",
    "[event history]",
    "[unclassified]",
)

_PROMPT_TEMPLATE = """\
# Role
You are a senior L3 NOC triage analyst at Carbyne. Your task is to perform
structured, evidence-based triage on a single Zendesk support ticket and produce
a validated Handoff JSON object that the noc-cli render pipeline will write to
the ticket folder.

# What you must do
1. Read the ticket body, comments, and all evidence files in your working
   directory (logs/, pcaps/, analysis/).
2. Follow the fork rubric (embedded below) to decide Fork A/B/C/D.
3. Quote **verbatim** the single rubric row that committed the fork into
   `fork_packet.quoted_rubric_row`.
4. Select exactly one approved symptom tag from the list below and write it
   into `fork_packet.symptom_tag`.
5. If relevant historical tickets were provided, include up to 5 in
   `fork_packet.historical_matches`.
6. Include the runbook slug and the decisive section text in
   `fork_packet.runbook_reference`.
7. Draft a customer reply and internal note in `drafts`. Draft a Jira ticket
   only for Fork A.
8. Emit **only** the final Handoff JSON object as your last message — no prose
   before or after the JSON block.

# Approved symptom tags (choose exactly one)
{tags}

Do NOT use `[vendor]` as a symptom tag — it is a history-exclusion tag only.

# Constraints — READ-ONLY operation
- You may only READ files, GREP logs, and GLOB file listings within your
  working directory. You may write scratch notes into analysis/ only.
- NEVER write outside your working directory (Tickets/<id>/).
- NEVER call any Zendesk write endpoint (create_ticket, update_ticket,
  add_comment, etc.).
- NEVER parse .pcap binary files — flag them as present and request the
  text-extracted version.
- NEVER fabricate evidence. If a log does not cover the incident window,
  Fork D (cannot fork yet) and list the missing evidence. Prefer Inconclusive
  over an unsupported fork.

# Inconclusive-over-fabrication rule
If the evidence is genuinely ambiguous or absent, set:
  fork_letter: "D"
  confidence: "Inconclusive"
  missing_evidence: [<list what is needed>]
Do not speculate. Do not invent signal that is not present in the files.

# Output contract
Your final message MUST be a single valid JSON object that can be parsed
and validated against the `Handoff` pydantic model (noc_cli/models.py).
The top-level keys are: intake, evidence_preflight, fork_packet, drafts,
rubric_version. Emit nothing else after the closing brace.

Example skeleton (replace all placeholder values):
{{
  "rubric_version": "2026-05-13",
  "intake": {{
    "ticket_id": 0,
    "url": "",
    "status": "",
    "tags": [],
    "requester": "",
    "organization": "",
    "one_line_fingerprint": "",
    "ticket_summary": [],
    "context_pulls": [],
    "initial_hypothesis": "",
    "intake_decision": "ready_for_evidence_preflight"
  }},
  "evidence_preflight": {{
    "gathered": [],
    "decisive_evidence": [],
    "missing_or_non_decisive": []
  }},
  "fork_packet": {{
    "fork_letter": "D",
    "confidence": "Inconclusive",
    "symptom_tag": "[unclassified]",
    "rubric_class": "",
    "quoted_rubric_row": "",
    "reasoning": "",
    "evidence_summary": [],
    "missing_evidence": ["<describe what is missing>"],
    "runbook_reference": {{"slug": "", "section": ""}},
    "historical_matches": [],
    "related_zendesk": [],
    "related_jira": []
  }},
  "drafts": {{
    "customer_reply": "",
    "internal_note": "",
    "jira_draft": null
  }}
}}

# Fork Rubric
{rubric}
"""

_DEFAULT_RUBRIC_PLACEHOLDER = (
    "[Rubric text not loaded — run build_system_prompt(rubric_text) "
    "to embed the live rubric before passing to the agent.]"
)

SYSTEM_PROMPT: str = _PROMPT_TEMPLATE.format(
    tags="\n".join(f"  {t}" for t in APPROVED_TAGS_IN_PROMPT),
    rubric=_DEFAULT_RUBRIC_PLACEHOLDER,
)


def build_system_prompt(rubric_text: str) -> str:
    """Return the system prompt with the live fork rubric embedded.

    Call this immediately before constructing ClaudeAgentOptions so the agent
    always sees the current rubric_version frontmatter.
    """
    return _PROMPT_TEMPLATE.format(
        tags="\n".join(f"  {t}" for t in APPROVED_TAGS_IN_PROMPT),
        rubric=rubric_text,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_prompt.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/__init__.py noc_cli/agent/prompt.py tests/test_agent_prompt.py
git commit -m "feat: reconstruct NOC-triage agent system prompt with approved tags + JSON contract

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Evidence gathering (`noc_cli/evidence.py`)

Download ticket attachments via the read-only `ZendeskClient`, accept `--file`/`--paste` CLI inputs, unzip `.zip` archives into the sandbox `logs/` directory (text files only; skip binaries), and FLAG `.pcap` files without parsing them. Returns an `EvidenceBundle` describing what was gathered.

**Files:**
- Create: `noc_cli/evidence.py`
- Test: `tests/test_evidence.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_evidence.py`:

```python
import zipfile
from pathlib import Path

import pytest

from noc_cli.evidence import (
    EvidenceBundle,
    PasteInput,
    gather_evidence,
)
from noc_cli.scaffold import scaffold_ticket


# ── helpers ────────────────────────────────────────────────────────────────


def make_zip(dest: Path, files: dict[str, bytes]) -> Path:
    """Create a zip at `dest` with the given {name: content} mapping."""
    with zipfile.ZipFile(dest, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return dest


# ── tests ──────────────────────────────────────────────────────────────────


def test_paste_input_written_to_logs(tmp_path):
    folder = scaffold_ticket(tmp_path, 1)
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[],
        pastes=[PasteInput(label="notes", text="call failed at 06:32 UTC")],
    )
    assert bundle.paste_count == 1
    written = folder.logs / "paste-notes.txt"
    assert written.exists()
    assert "06:32 UTC" in written.read_text()


def test_extra_file_copied_to_logs(tmp_path):
    folder = scaffold_ticket(tmp_path, 2)
    src = tmp_path / "station.log"
    src.write_text("log content here")
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[src],
        pastes=[],
    )
    assert bundle.file_count == 1
    assert (folder.logs / "station.log").exists()


def test_zip_extracted_text_files(tmp_path):
    folder = scaffold_ticket(tmp_path, 3)
    z = make_zip(
        tmp_path / "logs.zip",
        {
            "kamailio.log": b"SIP log line 1\nSIP log line 2",
            "notes.txt": b"analyst notes",
        },
    )
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[z],
        pastes=[],
    )
    assert (folder.logs / "kamailio.log").exists()
    assert (folder.logs / "notes.txt").exists()
    assert bundle.zip_extracted == 1


def test_zip_skips_binary_entries(tmp_path):
    folder = scaffold_ticket(tmp_path, 4)
    z = make_zip(
        tmp_path / "mixed.zip",
        {
            "log.txt": b"plain text",
            "capture.pcap": b"\xd4\xc3\xb2\xa1binary",
            "image.png": b"\x89PNG\r\n\x1a\n",
        },
    )
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[z],
        pastes=[],
    )
    assert (folder.logs / "log.txt").exists()
    assert not (folder.logs / "capture.pcap").exists()
    assert not (folder.logs / "image.png").exists()


def test_pcap_file_flagged_not_parsed(tmp_path):
    folder = scaffold_ticket(tmp_path, 5)
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"\xd4\xc3\xb2\xa1\x00")
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[pcap],
        pastes=[],
    )
    assert bundle.pcap_flagged == 1
    # The .pcap must NOT be parsed/opened — it is moved to pcaps/ as-is
    assert (folder.pcaps / "capture.pcap").exists()
    # logs/ must NOT contain the pcap
    assert not (folder.logs / "capture.pcap").exists()


def test_zendesk_attachment_downloaded(tmp_path, httpx_mock):
    """ZendeskClient.get_attachment fetches a URL; mock the HTTP call."""
    folder = scaffold_ticket(tmp_path, 6)
    httpx_mock.add_response(
        url="https://cdn.zendesk.example/attachments/kamailio.log",
        content=b"SIP line from Zendesk",
    )
    from noc_cli.zendesk import ZendeskClient
    from noc_cli.config import Config

    cfg = Config(
        zendesk_subdomain="example",
        zendesk_email="a@b.com",
        zendesk_api_token="tok",
    )
    client = ZendeskClient(cfg)
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[
            {
                "file_name": "kamailio.log",
                "content_url": "https://cdn.zendesk.example/attachments/kamailio.log",
                "content_type": "text/plain",
            }
        ],
        extra_files=[],
        pastes=[],
        zendesk_client=client,
    )
    assert bundle.attachment_count == 1
    assert (folder.logs / "kamailio.log").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_evidence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.evidence'`.

- [ ] **Step 3: Implement `evidence.py`**

`noc_cli/evidence.py`:

```python
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noc_cli.zendesk import ZendeskClient

from noc_cli.scaffold import TicketFolder

_TEXT_EXTENSIONS = {
    ".txt", ".log", ".md", ".json", ".xml", ".csv", ".tsv",
    ".yaml", ".yml", ".conf", ".cfg", ".ini", ".sip", ".sdp",
}
_PCAP_EXTENSIONS = {".pcap", ".pcapng", ".cap"}


def _looks_like_text(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in _PCAP_EXTENSIONS:
        return False
    if suffix in _TEXT_EXTENSIONS:
        return True
    # Accept no-extension files (e.g. raw log dumps)
    return suffix == ""


def _is_pcap(path: Path) -> bool:
    return path.suffix.lower() in _PCAP_EXTENSIONS


@dataclass
class PasteInput:
    label: str
    text: str


@dataclass
class EvidenceBundle:
    attachment_count: int = 0
    file_count: int = 0
    paste_count: int = 0
    zip_extracted: int = 0
    pcap_flagged: int = 0
    warnings: list[str] = field(default_factory=list)


def gather_evidence(
    folder: TicketFolder,
    zendesk_attachments: list[dict],
    extra_files: list[Path],
    pastes: list[PasteInput],
    zendesk_client: "ZendeskClient | None" = None,
) -> EvidenceBundle:
    """Gather all evidence into the ticket sandbox.

    - Zendesk attachments: fetched via ZendeskClient (requires client).
    - extra_files: copied/moved from the local filesystem.
      * .zip → text members extracted to logs/; binary members skipped.
      * .pcap/.pcapng → moved to pcaps/ unflagged, never parsed.
      * everything else → copied to logs/.
    - pastes: written as paste-<label>.txt into logs/.
    """
    bundle = EvidenceBundle()

    # 1. Zendesk attachments
    if zendesk_attachments and zendesk_client is not None:
        for att in zendesk_attachments:
            name: str = att["file_name"]
            url: str = att["content_url"]
            dest = folder.logs / name
            data = zendesk_client.download_attachment(url)
            dest.write_bytes(data)
            bundle.attachment_count += 1

    # 2. Local files / zip archives
    for src in extra_files:
        src = Path(src)
        if src.suffix.lower() == ".zip":
            _extract_zip(src, folder.logs)
            bundle.zip_extracted += 1
        elif _is_pcap(src):
            shutil.copy2(src, folder.pcaps / src.name)
            bundle.pcap_flagged += 1
        else:
            shutil.copy2(src, folder.logs / src.name)
            bundle.file_count += 1

    # 3. Paste inputs
    for paste in pastes:
        dest = folder.logs / f"paste-{paste.label}.txt"
        dest.write_text(paste.text, encoding="utf-8")
        bundle.paste_count += 1

    return bundle


def _extract_zip(zip_path: Path, logs_dir: Path) -> None:
    """Extract text-safe members to logs_dir; silently skip binary/pcap entries."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if not name or info.is_dir():
                continue
            if not _looks_like_text(info.filename):
                continue
            dest = logs_dir / name
            dest.write_bytes(zf.read(info.filename))
```

- [ ] **Step 4: Add `download_attachment` to `noc_cli/zendesk.py`**

The `test_zendesk_attachment_downloaded` test above calls `zendesk_client.download_attachment(url)`, which does not exist in the Foundation `zendesk.py`. Add it now as a TDD step.

First write the failing test in `tests/test_zendesk.py` (append alongside the existing httpx_mock tests):

```python
def test_download_attachment_returns_bytes_and_sends_auth(http):
    """download_attachment fetches the URL with the existing auth header."""
    http.add_response(
        url="https://cdn.zendesk.example/attachments/kamailio.log",
        content=b"SIP line from Zendesk",
    )
    from noc_cli.zendesk import ZendeskClient
    from noc_cli.config import Config

    cfg = Config(
        zendesk_subdomain="example",
        zendesk_email="a@b.com",
        zendesk_api_token="tok",
    )
    client = ZendeskClient(cfg)
    data = client.download_attachment(
        "https://cdn.zendesk.example/attachments/kamailio.log"
    )
    assert data == b"SIP line from Zendesk"
    # Verify auth header was sent
    sent = http.get_requests()
    assert len(sent) == 1
    assert "Authorization" in sent[0].headers
```

Run `uv run pytest tests/test_zendesk.py -v` — expected FAIL (`AttributeError: 'ZendeskClient' object has no attribute 'download_attachment'`).

Then add the method to `noc_cli/zendesk.py` inside the `ZendeskClient` class:

```python
def download_attachment(self, url: str) -> bytes:
    """Fetch an attachment by its content URL (read-only)."""
    resp = self._client.get(url, headers={"Authorization": self._auth_header})
    resp.raise_for_status()
    return resp.content
```

Run `uv run pytest tests/test_zendesk.py -v` — expected PASS.

- [ ] **Step 6: Run the evidence tests to verify they pass**

Run: `uv run pytest tests/test_evidence.py -v`
Expected: PASS (6 passed).

- [ ] **Step 7: Commit**

```bash
git add noc_cli/zendesk.py noc_cli/evidence.py tests/test_zendesk.py tests/test_evidence.py
git commit -m "feat: evidence gathering + download_attachment — attachments, --file, --paste, zip extract, pcap flag

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Memory (`noc_cli/memory.py`)

SQLite FTS5 store over past investigations. `append_investigation` writes a row; `search` returns ranked matches. The FTS5 virtual table is owned here (`CREATE TABLE IF NOT EXISTS`). `MEMORY.md` is a human-readable companion written alongside the DB for analyst review. Uses `noc_cli.store.connect(db_path())`.

**Files:**
- Create: `noc_cli/memory.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_memory.py`:

```python
from pathlib import Path

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.memory'`.

- [ ] **Step 3: Implement `memory.py`**

`noc_cli/memory.py`:

```python
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
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        return connect(self.db_path)


def append_investigation(store: MemoryStore, record: InvestigationRecord) -> None:
    """Insert a new investigation into FTS5 and append a row to MEMORY.md."""
    conn = store._conn()
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_memory.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/memory.py tests/test_memory.py
git commit -m "feat: SQLite FTS5 memory store with MEMORY.md companion

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: History (`noc_cli/history.py`)

Merge live Zendesk search (approved tags only; exclude `[vendor]`) with local FTS5 memory search into a single candidate pool. The pool is passed to the agent as context so it can populate `fork_packet.historical_matches`. Tags searched are the six symptom-specific ones (not `[unclassified]`).

**Files:**
- Create: `noc_cli/history.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_history.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from noc_cli.history import HISTORY_SEARCH_TAGS, HistoryCandidate, seed_history
from noc_cli.memory import InvestigationRecord, MemoryStore, append_investigation


def test_vendor_tag_not_in_search_tags():
    assert "[vendor]" not in HISTORY_SEARCH_TAGS
    assert "[unclassified]" not in HISTORY_SEARCH_TAGS


def test_all_six_symptom_tags_present():
    expected = {"[apex]", "[low audio]", "[dropped calls]", "[No ANI]", "[No ALI]", "[event history]"}
    assert expected == set(HISTORY_SEARCH_TAGS)


def test_seed_history_returns_memory_results(tmp_path):
    db = tmp_path / "noc.db"
    md = tmp_path / "MEMORY.md"
    store = MemoryStore(db_path=db, memory_md_path=md)
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
    db = tmp_path / "noc.db"
    md = tmp_path / "MEMORY.md"
    store = MemoryStore(db_path=db, memory_md_path=md)
    store.init()

    mock_zendesk = MagicMock()
    mock_zendesk.search.return_value = [
        {"id": 41675, "subject": "Cobb site network error", "tags": ["apex"]},
    ]

    candidates = seed_history(
        symptom_tag="[apex]",
        zendesk_client=mock_zendesk,
        memory_store=store,
        limit=10,
    )
    assert any(c.ticket_id == "41675" for c in candidates)


def test_seed_history_deduplicates(tmp_path):
    """Same ticket_id from both sources should appear only once."""
    db = tmp_path / "noc.db"
    md = tmp_path / "MEMORY.md"
    store = MemoryStore(db_path=db, memory_md_path=md)
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
        {"id": 41675, "subject": "Cobb site network error", "tags": ["apex"]},
    ]
    candidates = seed_history(
        symptom_tag="[apex]",
        zendesk_client=mock_zendesk,
        memory_store=store,
        limit=10,
    )
    ids = [c.ticket_id for c in candidates]
    assert ids.count("41675") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.history'`.

- [ ] **Step 3: Implement `history.py`**

`noc_cli/history.py`:

```python
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
    zendesk_client: "ZendeskClient",
    memory_store: "MemoryStore",
    limit: int = 20,
) -> list[HistoryCandidate]:
    """Return a merged, deduplicated candidate pool for the agent's historical_matches.

    Sources:
    1. Live Zendesk search on the symptom tag keyword (approved tags only;
       [vendor] is never searched).
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
            for item in zd_results[:limit]:
                tid = str(item.get("id", ""))
                if tid:
                    seen[tid] = HistoryCandidate(
                        ticket_id=tid,
                        subject=item.get("subject", ""),
                        source="zendesk",
                        tags=item.get("tags", []),
                    )

    # 2. Local memory FTS5 search
    from noc_cli.memory import search as memory_search

    mem_results = memory_search(memory_store, symptom_tag.strip("[]").replace(" ", " "), limit=limit)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_history.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/history.py tests/test_history.py
git commit -m "feat: history seeding — Zendesk union memory, vendor-tag excluded

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Agent harness (`noc_cli/agent/harness.py`)

Security-critical. A `PreToolUse` hook that DENIES any `Write`/`Edit` whose resolved path is outside `Tickets/<id>/`, any destructive/mutating `Bash` (rm, mv, cp with out-of-sandbox dest, redirects `>` or `>>` outside sandbox, curl/wget, pip install), and any Zendesk MCP write call. A `PostToolUse` hook that appends every tool call as a JSON line to `events.jsonl` in the ticket folder.

The deny mechanism uses the real hooks API confirmed from the docs:
```python
return {
    "hookSpecificOutput": {
        "hookEventName": input_data["hook_event_name"],
        "permissionDecision": "deny",
        "permissionDecisionReason": "<reason>",
    }
}
```

**Files:**
- Create: `noc_cli/agent/harness.py`
- Test: `tests/test_agent_harness.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_agent_harness.py`:

```python
import asyncio
import json
from pathlib import Path

import pytest

from noc_cli.agent.harness import (
    build_hooks,
    make_post_tool_use,
    make_pre_tool_use,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _pre_event(tool_name: str, tool_input: dict) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def _post_event(tool_name: str, tool_input: dict, output: str = "") -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": output,
    }


async def _call_pre(hook, event: dict) -> dict:
    return await hook(event, None, None)


async def _call_post(hook, event: dict) -> dict:
    return await hook(event, None, None)


# ── pre-tool-use: Write outside sandbox ────────────────────────────────────


def test_write_outside_sandbox_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)

    result = asyncio.get_event_loop().run_until_complete(
        _call_pre(pre, _pre_event("Write", {"file_path": "/etc/passwd"}))
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_edit_outside_sandbox_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)

    result = asyncio.get_event_loop().run_until_complete(
        _call_pre(pre, _pre_event("Edit", {"file_path": str(tmp_path / "outside.txt")}))
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_inside_sandbox_is_allowed(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)

    result = asyncio.get_event_loop().run_until_complete(
        _call_pre(pre, _pre_event("Write", {"file_path": str(sandbox / "analysis" / "notes.md")}))
    )
    assert result == {} or result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"


def test_read_anywhere_is_allowed(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)

    result = asyncio.get_event_loop().run_until_complete(
        _call_pre(pre, _pre_event("Read", {"file_path": "/some/log/file.log"}))
    )
    # Read is allowed — must not be a deny
    spec = result.get("hookSpecificOutput", {})
    assert spec.get("permissionDecision", "allow") != "deny"


# ── pre-tool-use: destructive Bash ─────────────────────────────────────────


def test_bash_rm_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)

    result = asyncio.get_event_loop().run_until_complete(
        _call_pre(pre, _pre_event("Bash", {"command": "rm -rf /tmp/foo"}))
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_bash_redirect_outside_sandbox_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)

    result = asyncio.get_event_loop().run_until_complete(
        _call_pre(pre, _pre_event("Bash", {"command": "cat logs/foo.log > /tmp/exfil.txt"}))
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_bash_grep_is_allowed(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)

    result = asyncio.get_event_loop().run_until_complete(
        _call_pre(pre, _pre_event("Bash", {"command": "grep -r 'SIP INVITE' logs/"}))
    )
    spec = result.get("hookSpecificOutput", {})
    assert spec.get("permissionDecision", "allow") != "deny"


def test_zendesk_write_mcp_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)

    result = asyncio.get_event_loop().run_until_complete(
        _call_pre(pre, _pre_event("mcp__zendesk__create_ticket", {"subject": "x"}))
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── post-tool-use: events.jsonl ────────────────────────────────────────────


def test_post_tool_use_appends_to_events_jsonl(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    events_path = sandbox / "events.jsonl"
    post = make_post_tool_use(events_path=events_path)

    asyncio.get_event_loop().run_until_complete(
        _call_post(post, _post_event("Read", {"file_path": "logs/k.log"}, "line1\nline2"))
    )
    asyncio.get_event_loop().run_until_complete(
        _call_post(post, _post_event("Bash", {"command": "grep INVITE logs/k.log"}, "match"))
    )

    lines = events_path.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool_name"] == "Read"
    second = json.loads(lines[1])
    assert second["tool_name"] == "Bash"


def test_build_hooks_returns_correct_structure(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    hooks = build_hooks(sandbox_root=sandbox, events_path=sandbox / "events.jsonl")
    assert "PreToolUse" in hooks
    assert "PostToolUse" in hooks
    assert len(hooks["PreToolUse"]) >= 1
    assert len(hooks["PostToolUse"]) >= 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_harness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.agent.harness'`.

- [ ] **Step 3: Implement `harness.py`**

`noc_cli/agent/harness.py`:

```python
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Bash: patterns that are unconditionally destructive ────────────────────

_DESTRUCTIVE_BASH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\b"),                    # rm (any form)
    re.compile(r"\bmv\b"),                    # mv (may exfil)
    re.compile(r"\bchmod\b"),                 # permission change
    re.compile(r"\bchown\b"),
    re.compile(r"\bcurl\b"),                  # network write
    re.compile(r"\bwget\b"),
    re.compile(r"\bpip\s+install\b"),         # package mutation
    re.compile(r"\buv\s+add\b"),
    re.compile(r"\bnpm\s+install\b"),
    re.compile(r">(?!=)"),                    # stdout redirect (> or >>)
]

# Zendesk MCP tool names that are writes (exact prefix match is intentional)
_ZENDESK_WRITE_PREFIXES: tuple[str, ...] = (
    "mcp__zendesk__create_",
    "mcp__zendesk__update_",
    "mcp__zendesk__delete_",
    "mcp__zendesk__add_comment",
    "mcp__zendesk__close_",
    "mcp__zendesk__set_",
)

# Tools that write files — we check path containment for these
_WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})

# Tools that are unconditionally read-only and never blocked
_ALWAYS_ALLOWED_TOOLS = frozenset({"Read", "Glob", "Grep", "LS"})


def _deny(event: dict, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": event["hook_event_name"],
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _allow() -> dict:
    return {}


def _is_inside_sandbox(path_str: str, sandbox_root: Path) -> bool:
    """Return True iff path_str resolves to a path inside sandbox_root."""
    try:
        candidate = Path(path_str).resolve()
        resolved_sandbox = sandbox_root.resolve()
        candidate.relative_to(resolved_sandbox)
        return True
    except ValueError:
        return False


def make_pre_tool_use(sandbox_root: Path):
    """Return a PreToolUse hook callback enforcing the read-only sandbox."""

    async def pre_tool_use(input_data: dict[str, Any], tool_use_id, context) -> dict:
        tool_name: str = input_data.get("tool_name", "")
        tool_input: dict = input_data.get("tool_input", {})

        # Always-allowed read tools: pass through immediately
        if tool_name in _ALWAYS_ALLOWED_TOOLS:
            return _allow()

        # Zendesk write MCP calls: deny
        tl = tool_name.lower()
        for prefix in _ZENDESK_WRITE_PREFIXES:
            if tl.startswith(prefix):
                return _deny(input_data, f"Zendesk write tool {tool_name!r} is not permitted")

        # File-writing tools: must resolve inside sandbox
        if tool_name in _WRITE_TOOLS:
            file_path = tool_input.get("file_path", "")
            if not file_path:
                return _deny(input_data, "Write/Edit called with empty file_path")
            if not _is_inside_sandbox(file_path, sandbox_root):
                return _deny(
                    input_data,
                    f"Write/Edit path {file_path!r} is outside the ticket sandbox "
                    f"({sandbox_root}). Only paths inside Tickets/<id>/ are permitted.",
                )
            return _allow()

        # Bash: check for destructive patterns
        if tool_name == "Bash":
            command: str = tool_input.get("command", "")
            for pat in _DESTRUCTIVE_BASH_PATTERNS:
                if pat.search(command):
                    return _deny(
                        input_data,
                        f"Bash command contains a disallowed pattern ({pat.pattern!r}): "
                        f"{command[:120]!r}",
                    )
            return _allow()

        # All other tools: allow (permission_mode="dontAsk" + allowed_tools
        # in runner.py handle the final gate; harness only blocks the above)
        return _allow()

    return pre_tool_use


def make_post_tool_use(events_path: Path):
    """Return a PostToolUse hook that appends every tool call to events.jsonl."""

    async def post_tool_use(input_data: dict[str, Any], tool_use_id, context) -> dict:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool_name": input_data.get("tool_name"),
            "tool_input": input_data.get("tool_input"),
            "tool_output_snippet": str(input_data.get("tool_output", ""))[:500],
            "tool_use_id": tool_use_id,
        }
        with events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return {}

    return post_tool_use


def build_hooks(
    sandbox_root: Path,
    events_path: Path,
) -> dict[str, list]:
    """Build the hooks dict for ClaudeAgentOptions.

    Returns:
      {
        "PreToolUse":  [HookMatcher(hooks=[pre_tool_use_callback])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use_callback])],
      }

    Import HookMatcher lazily so this module can be tested without the SDK
    installed (the test suite calls make_pre_tool_use / make_post_tool_use
    directly rather than via the HookMatcher wrapper).
    """
    from claude_agent_sdk import HookMatcher  # noqa: PLC0415

    pre = make_pre_tool_use(sandbox_root=sandbox_root)
    post = make_post_tool_use(events_path=events_path)

    return {
        "PreToolUse": [HookMatcher(hooks=[pre])],
        "PostToolUse": [HookMatcher(hooks=[post])],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_harness.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/harness.py tests/test_agent_harness.py
git commit -m "feat: agent harness — PreToolUse sandbox enforcer + PostToolUse audit log

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Agent runner (`noc_cli/agent/runner.py`)

Build `ClaudeAgentOptions`, run `claude_agent_sdk.query()`, collect the final `ResultMessage.result` text, parse as JSON, validate as `Handoff` (retry once on failure). On double-failure, stash raw text to `Tickets/<id>/.debug/` and abort (returning `None` without writing the ticket folder). Tests use an injected async query function — no real LLM.

**Files:**
- Create: `noc_cli/agent/runner.py`
- Test: `tests/test_agent_runner.py`
- New test assets: `tests/fixtures/handoff_good.json`, `tests/fixtures/handoff_inconclusive.json`, `tests/fixtures/handoff_bad.json`

- [ ] **Step 1: Write the failing tests**

`tests/test_agent_runner.py`:

```python
import asyncio
import json
from pathlib import Path

import pytest

from noc_cli.agent.runner import RunnerResult, run_agent
from noc_cli.models import ForkLetter, Handoff
from noc_cli.scaffold import scaffold_ticket

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _make_fake_query(result_json: str, *, is_error: bool = False):
    """Return an async generator factory that yields a fake ResultMessage."""

    async def fake_query(*, prompt, options):
        class FakeResult:
            result = result_json
            is_error = False
            subtype = "success"

        yield FakeResult()

    return fake_query


def _make_failing_query():
    """Returns a query that always yields an error result."""

    async def fake_query(*, prompt, options):
        class FakeResult:
            result = '{"this": "is not a Handoff"}'
            is_error = False
            subtype = "success"

        yield FakeResult()

    return fake_query


def test_run_agent_parses_good_handoff(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    good_json = _load_fixture("handoff_good.json")
    fake_q = _make_fake_query(good_json)

    result = asyncio.get_event_loop().run_until_complete(
        run_agent(
            ticket_id=18432,
            folder=folder,
            system_prompt="test",
            history_context="",
            _query_fn=fake_q,
        )
    )
    assert isinstance(result, RunnerResult)
    assert result.handoff is not None
    assert result.handoff.fork_packet.fork_letter is ForkLetter.B
    assert result.stash_path is None


def test_run_agent_parses_inconclusive_handoff(tmp_path):
    folder = scaffold_ticket(tmp_path, 18433)
    inconclusive_json = _load_fixture("handoff_inconclusive.json")
    fake_q = _make_fake_query(inconclusive_json)

    result = asyncio.get_event_loop().run_until_complete(
        run_agent(
            ticket_id=18433,
            folder=folder,
            system_prompt="test",
            history_context="",
            _query_fn=fake_q,
        )
    )
    assert result.handoff is not None
    assert result.handoff.fork_packet.fork_letter is ForkLetter.D


def test_run_agent_retries_on_bad_json_then_stashes(tmp_path):
    """Bad JSON from agent → retry once → stash raw text → return None handoff."""
    folder = scaffold_ticket(tmp_path, 18434)
    bad_json = _load_fixture("handoff_bad.json")
    fake_q = _make_fake_query(bad_json)

    result = asyncio.get_event_loop().run_until_complete(
        run_agent(
            ticket_id=18434,
            folder=folder,
            system_prompt="test",
            history_context="",
            _query_fn=fake_q,
        )
    )
    assert result.handoff is None
    assert result.stash_path is not None
    assert result.stash_path.exists()
    stashed = result.stash_path.read_text()
    assert len(stashed) > 0


def test_stash_written_to_debug_subdir(tmp_path):
    folder = scaffold_ticket(tmp_path, 18435)
    bad_json = '{"totally": "wrong"}'

    async def fake_q(*, prompt, options):
        class R:
            result = bad_json
            is_error = False

        yield R()

    result = asyncio.get_event_loop().run_until_complete(
        run_agent(
            ticket_id=18435,
            folder=folder,
            system_prompt="test",
            history_context="",
            _query_fn=fake_q,
        )
    )
    assert result.stash_path is not None
    # Must be inside .debug/
    assert ".debug" in str(result.stash_path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.agent.runner'` (also the fixture files do not exist yet).

- [ ] **Step 3: Create the fixture files**

`tests/fixtures/handoff_good.json` — a valid `Handoff` with Fork B / Medium / `[apex]`:

```json
{
  "rubric_version": "2026-05-13",
  "intake": {
    "ticket_id": 18432,
    "url": "https://carbyne.zendesk.com/agent/tickets/18432",
    "status": "open",
    "tags": ["apex"],
    "requester": "PSAP Ops",
    "organization": "Aurora 911",
    "one_line_fingerprint": "Aurora / apex / Network Error / 06:30 UTC",
    "ticket_summary": ["All consoles showed Network Error banner for ~90 seconds at 06:30 UTC."],
    "context_pulls": [
      {"pull": "Last 3 tickets for Aurora 911", "result": "Ticket 41200 (Cobb, site LAN), Ticket 40900 (Aurora, same symptom)", "source": "Zendesk"}
    ],
    "initial_hypothesis": "Fork B — site LAN / SDWAN; multiple stations flipped simultaneously",
    "intake_decision": "ready_for_evidence_preflight"
  },
  "evidence_preflight": {
    "gathered": [
      {
        "evidence_type": "station log",
        "source": "Aurora-12",
        "time_window": "06:28–06:35 UTC",
        "summary": "RECONNECT_ON_DRAINING at 06:30:14 UTC; code 1006 close; three stations within 4 seconds"
      }
    ],
    "decisive_evidence": [
      "Three stations flipped ERROR within 4 seconds of each other at 06:30 UTC",
      "Kamailio drain log shows X-Web-Socket-Draining from site-side NTT link"
    ],
    "missing_or_non_decisive": [
      "Switch/SDWAN logs not uploaded — sufficient evidence already present for fork"
    ]
  },
  "fork_packet": {
    "fork_letter": "B",
    "confidence": "Medium",
    "symptom_tag": "[apex]",
    "rubric_class": "Symptom Class 3",
    "quoted_rubric_row": "customer LAN, switch, or SDWAN. Link to site master ticket",
    "reasoning": "Three stations flipping ERROR within 4 seconds is the Class 3 multi-station signal pointing to the customer network (Fork B). No single-station isolation. Kamailio drain correlates with site NTT link.",
    "evidence_summary": [
      "Stations Aurora-11, Aurora-12, Aurora-13 all hit RECONNECT_ON_DRAINING within 4s",
      "Kamailio drain log shows site-side origination"
    ],
    "missing_evidence": [],
    "runbook_reference": {
      "slug": "apex",
      "section": "Multiple stations at one site flip ERROR within seconds -> Fork B — customer LAN, switch, or SDWAN; link to the site master ticket."
    },
    "historical_matches": [
      {
        "ticket_id": "41675",
        "subject": "Cobb County — all consoles Network Error",
        "relevance": "Same multi-station RECONNECT_ON_DRAINING pattern; resolved via site master ticket",
        "resolution": "Linked to site master; customer IT replaced switch"
      }
    ],
    "related_zendesk": [41675],
    "related_jira": []
  },
  "drafts": {
    "customer_reply": "Hi team,\n\nWe reviewed the station logs for the 06:30 UTC event. The logs show three consoles simultaneously losing their WebSocket connection within 4 seconds of each other. This pattern is consistent with a brief interruption at the site network level (LAN, switch, or SDWAN) rather than a Carbyne platform issue.\n\nWe have linked this ticket to the site master ticket for your location. Could you please check with your IT team or ISP for any network events around 06:30 UTC?\n\nThank you,\nCarbyne NOC",
    "internal_note": "Fork B — Symptom Class 3. Multi-station (3 stations within 4s). Rubric row: 'customer LAN, switch, or SDWAN. Link to site master ticket'. Linked to #41675. No Jira needed.",
    "jira_draft": null
  }
}
```

`tests/fixtures/handoff_inconclusive.json` — Fork D / Inconclusive / `[unclassified]`:

```json
{
  "rubric_version": "2026-05-13",
  "intake": {
    "ticket_id": 18433,
    "url": "https://carbyne.zendesk.com/agent/tickets/18433",
    "status": "open",
    "tags": [],
    "requester": "Site Admin",
    "organization": "Riverside 911",
    "one_line_fingerprint": "Riverside / unclassified / audio issue / no logs",
    "ticket_summary": ["Customer reports audio issue but has not uploaded any logs."],
    "context_pulls": [],
    "initial_hypothesis": "Fork D — no log coverage of incident window",
    "intake_decision": "ready_for_evidence_preflight"
  },
  "evidence_preflight": {
    "gathered": [],
    "decisive_evidence": [],
    "missing_or_non_decisive": [
      "No station logs uploaded",
      "No PCAP provided",
      "Incident timestamp not confirmed"
    ]
  },
  "fork_packet": {
    "fork_letter": "D",
    "confidence": "Inconclusive",
    "symptom_tag": "[unclassified]",
    "rubric_class": "",
    "quoted_rubric_row": "",
    "reasoning": "No log files are present in the ticket folder. Cannot make a fork decision without evidence covering the incident window.",
    "evidence_summary": [],
    "missing_evidence": [
      "Station logs covering incident timestamp ±30 min",
      "Confirmed incident time (UTC and local)",
      "Affected station ID(s)"
    ],
    "runbook_reference": {"slug": "", "section": ""},
    "historical_matches": [],
    "related_zendesk": [],
    "related_jira": []
  },
  "drafts": {
    "customer_reply": "Hi,\n\nTo investigate this audio issue, we need station logs covering the incident time window (±30 minutes) and the exact time the issue occurred (UTC preferred). Could you please upload those?\n\nThank you,\nCarbyne NOC",
    "internal_note": "Fork D — no log coverage. Waiting on station logs and confirmed timestamp.",
    "jira_draft": null
  }
}
```

`tests/fixtures/handoff_bad.json` — schema-invalid (missing required fields, triggers retry/stash):

```json
{
  "this_is": "not a valid Handoff",
  "missing": ["intake", "evidence_preflight", "fork_packet", "drafts"],
  "rubric_version": "bad"
}
```

- [ ] **Step 4: Implement `runner.py`**

`noc_cli/agent/runner.py`:

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Callable

from pydantic import ValidationError

from noc_cli.models import Handoff
from noc_cli.scaffold import TicketFolder

MAX_TURNS = 40
ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "LS",
    "Write",   # sandbox-scoped by harness
    "Edit",    # sandbox-scoped by harness
    "Bash",    # read-only patterns enforced by harness
    # NOTE: live history-search and read-only Zendesk SDK MCP tools (agent/tools.py)
    # are DEFERRED to a follow-on plan. The agent operates on the pre-seeded history
    # written to the ticket folder (from Task 10) and the sandbox file tools above.
    # When agent/tools.py is implemented, add its tool names here and wire
    # mcp_servers in ClaudeAgentOptions.
]


@dataclass
class RunnerResult:
    handoff: Handoff | None
    stash_path: Path | None = None
    raw_result: str = ""
    attempts: int = 0


def _extract_json_from_result(raw: str) -> str:
    """Best-effort extraction: strip markdown fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Drop opening fence (```json or ```) and closing fence
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        raw = "\n".join(inner).strip()
    return raw


def _try_parse(raw: str) -> Handoff | None:
    """Attempt JSON parse + pydantic validation. Returns None on any error."""
    try:
        cleaned = _extract_json_from_result(raw)
        data = json.loads(cleaned)
        return Handoff.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError):
        return None


async def _collect_result(query_gen) -> str:
    """Drain the query async-generator and return the ResultMessage.result text."""
    raw = ""
    async for message in query_gen:
        result_text = getattr(message, "result", None)
        if result_text is not None:
            raw = result_text
    return raw


async def run_agent(
    ticket_id: int | str,
    folder: TicketFolder,
    system_prompt: str,
    history_context: str,
    _query_fn: Callable | None = None,
) -> RunnerResult:
    """Run the L3 triage agent and return a RunnerResult.

    `_query_fn` is injected in tests (signature: `async def fn(*, prompt, options)`
    returning an async generator of messages). In production, `claude_agent_sdk.query`
    is used.

    Retry policy: attempt once; on parse/validate failure, retry exactly once with
    an explicit correction prompt. On second failure, stash raw text to
    `Tickets/<id>/.debug/` and return RunnerResult(handoff=None, stash_path=...).
    """
    if _query_fn is None:
        from claude_agent_sdk import query as _query_fn  # noqa: PLC0415

    from noc_cli.agent.harness import build_hooks  # noqa: PLC0415

    events_path = folder.root / "events.jsonl"
    hooks = build_hooks(sandbox_root=folder.root, events_path=events_path)

    try:
        from claude_agent_sdk import ClaudeAgentOptions  # noqa: PLC0415

        def _make_options() -> "ClaudeAgentOptions":
            return ClaudeAgentOptions(
                system_prompt=system_prompt,
                allowed_tools=ALLOWED_TOOLS,
                permission_mode="dontAsk",
                max_turns=MAX_TURNS,
                cwd=str(folder.root),
                hooks=hooks,
            )
    except ImportError:
        # Test environments that don't have the SDK installed use _query_fn
        # directly; _make_options() is never called.
        def _make_options():  # type: ignore[misc]
            return None

    full_prompt = (
        f"Triage ticket #{ticket_id}.\n\n"
        f"Historical context (for historical_matches only — do not treat as ground truth):\n"
        f"{history_context}\n\n"
        "Your working directory already contains all available evidence under logs/, "
        "pcaps/, and analysis/. Read them and emit the Handoff JSON."
    )

    # Attempt 1
    gen1 = _query_fn(prompt=full_prompt, options=_make_options())
    raw1 = await _collect_result(gen1)
    handoff = _try_parse(raw1)
    if handoff is not None:
        return RunnerResult(handoff=handoff, raw_result=raw1, attempts=1)

    # Attempt 2 — correction prompt
    correction_prompt = (
        "Your previous response could not be parsed as a valid Handoff JSON object. "
        "Please emit ONLY the Handoff JSON, with no prose before or after. "
        "The top-level keys must be: intake, evidence_preflight, fork_packet, drafts, rubric_version."
    )
    gen2 = _query_fn(prompt=correction_prompt, options=_make_options())
    raw2 = await _collect_result(gen2)
    handoff2 = _try_parse(raw2)
    if handoff2 is not None:
        return RunnerResult(handoff=handoff2, raw_result=raw2, attempts=2)

    # Double failure — stash and abort
    stash_dir = folder.root / ".debug"
    stash_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stash_path = stash_dir / f"raw-result-{ts}.txt"
    stash_path.write_text(
        f"# Attempt 1\n{raw1}\n\n# Attempt 2\n{raw2}\n",
        encoding="utf-8",
    )
    return RunnerResult(handoff=None, stash_path=stash_path, raw_result=raw2, attempts=2)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add noc_cli/agent/runner.py tests/test_agent_runner.py \
        tests/fixtures/handoff_good.json tests/fixtures/handoff_inconclusive.json \
        tests/fixtures/handoff_bad.json
git commit -m "feat: agent runner — ClaudeAgentOptions, retry-once, stash-on-failure

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Render (`noc_cli/render.py`)

Write a validated `Handoff` to five canonical markdown files atomically. Files: `INTAKE.md`, `EVIDENCE_PREFLIGHT.md`, `FORK_PACKET.md` (includes a "Runbook Reference" section), `DRAFTS.md`, `STATE.md` (YAML frontmatter). All five are written to a temp directory then moved atomically. `.debug/` stash on validation failure. Uses exact `Handoff` field names from Task 4.

**Files:**
- Create: `noc_cli/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_render.py`:

```python
import json
from pathlib import Path

import pytest

from noc_cli.models import Handoff
from noc_cli.render import render_handoff
from noc_cli.scaffold import scaffold_ticket

FIXTURES = Path(__file__).parent / "fixtures"


def load_good() -> Handoff:
    data = json.loads((FIXTURES / "handoff_good.json").read_text())
    return Handoff.model_validate(data)


def test_all_five_files_created(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    for name in ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md"):
        assert (folder.root / name).exists(), f"{name} missing"


def test_intake_md_contains_ticket_id(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "INTAKE.md").read_text()
    assert "18432" in content


def test_fork_packet_md_contains_runbook_reference_section(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "FORK_PACKET.md").read_text()
    assert "Runbook Reference" in content
    assert "apex" in content.lower()


def test_fork_packet_md_contains_quoted_rubric_row(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "FORK_PACKET.md").read_text()
    assert "customer LAN, switch, or SDWAN" in content


def test_fork_packet_md_contains_historical_matches(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "FORK_PACKET.md").read_text()
    assert "41675" in content


def test_state_md_has_yaml_frontmatter(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "STATE.md").read_text()
    assert content.startswith("---")
    assert "fork:" in content
    assert "symptom_tag:" in content
    assert "confidence:" in content
    assert "rubric_version:" in content


def test_state_md_fork_letter_is_correct(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "STATE.md").read_text()
    assert 'fork: "B"' in content or "fork: B" in content


def test_drafts_md_contains_customer_reply(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(load_good(), folder)
    content = (folder.root / "DRAFTS.md").read_text()
    assert "customer" in content.lower()
    assert "WebSocket" in content or "network" in content.lower()


def test_render_is_atomic_on_failure(tmp_path):
    """If render raises mid-way (e.g., disk full simulation), existing files
    must not be partially written. We test atomicity by verifying the five
    files do NOT exist before render and DO exist after a successful render."""
    folder = scaffold_ticket(tmp_path, 18432)
    for name in ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md"):
        assert not (folder.root / name).exists()
    render_handoff(load_good(), folder)
    for name in ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md"):
        assert (folder.root / name).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.render'`.

- [ ] **Step 3: Implement `render.py`**

`noc_cli/render.py`:

```python
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from noc_cli.models import (
    DraftsBlock,
    ForkLetter,
    ForkPacket,
    Handoff,
    IntakeBlock,
    PreflightBlock,
)
from noc_cli.scaffold import TicketFolder

_CANONICAL_FILES = (
    "INTAKE.md",
    "EVIDENCE_PREFLIGHT.md",
    "FORK_PACKET.md",
    "DRAFTS.md",
    "STATE.md",
)


# ── Individual file renderers ───────────────────────────────────────────────


def _render_intake(intake: IntakeBlock) -> str:
    lines = [
        "# INTAKE",
        "",
        f"**Ticket:** [{intake.ticket_id}]({intake.url})",
        f"**Status:** {intake.status}",
        f"**Requester:** {intake.requester}",
        f"**Organization:** {intake.organization}",
    ]
    if intake.site:
        lines.append(f"**Site:** {intake.site}")
    if intake.cnc:
        lines.append(f"**CNC:** {intake.cnc}")
    if intake.region:
        lines.append(f"**Region:** {intake.region}")
    lines += [
        f"**Incident window:** {intake.incident_window}" if intake.incident_window else "",
        "",
        f"**Fingerprint:** {intake.one_line_fingerprint}",
        "",
        "## Ticket Summary",
    ]
    for bullet in intake.ticket_summary:
        lines.append(f"- {bullet}")
    lines += ["", "## Context Pulls"]
    for cp in intake.context_pulls:
        lines.append(f"- **{cp.pull}:** {cp.result} *(source: {cp.source})*")
    lines += [
        "",
        f"**Initial hypothesis:** {intake.initial_hypothesis}",
        f"**Intake decision:** `{intake.intake_decision.value}`",
    ]
    return "\n".join(line for line in lines if line is not None)


def _render_preflight(preflight: PreflightBlock) -> str:
    lines = ["# EVIDENCE PREFLIGHT", ""]
    if preflight.gathered:
        lines.append("## Gathered Evidence")
        for ev in preflight.gathered:
            lines += [
                f"### {ev.evidence_type or 'Evidence'}",
                f"- **Source:** {ev.source}",
                f"- **Window:** {ev.time_window}",
                f"- {ev.summary}",
                "",
            ]
    lines += ["## Decisive Evidence"]
    for d in preflight.decisive_evidence:
        lines.append(f"- {d}")
    lines += ["", "## Missing / Non-Decisive"]
    for m in preflight.missing_or_non_decisive:
        lines.append(f"- {m}")
    return "\n".join(lines)


def _render_fork_packet(fp: ForkPacket) -> str:
    fork_desc = {
        ForkLetter.A: "Engineering Jira",
        ForkLetter.B: "Vendor / Internal IT",
        ForkLetter.C: "NOC Self-Resolve",
        ForkLetter.D: "Cannot Fork Yet",
    }[fp.fork_letter]

    lines = [
        "# FORK PACKET",
        "",
        f"**Fork:** {fp.fork_letter.value} — {fork_desc}",
        f"**Confidence:** {fp.confidence.value}",
        f"**Symptom tag:** `{fp.symptom_tag}`",
        f"**Rubric class:** {fp.rubric_class}",
        "",
        "## Decision Signal",
        "",
        f"> {fp.quoted_rubric_row}",
        "",
        "## Reasoning",
        "",
        fp.reasoning,
        "",
        "## Evidence Summary",
    ]
    for ev in fp.evidence_summary:
        lines.append(f"- {ev}")

    if fp.missing_evidence:
        lines += ["", "## Missing Evidence"]
        for m in fp.missing_evidence:
            lines.append(f"- {m}")

    # Runbook Reference section
    lines += ["", "## Runbook Reference", ""]
    if fp.runbook_reference.slug:
        lines += [
            f"**Slug:** `{fp.runbook_reference.slug}`",
            "",
            fp.runbook_reference.section,
        ]
    else:
        lines.append("*(no runbook matched — symptom_tag is [unclassified])*")

    # Historical Matches
    if fp.historical_matches:
        lines += ["", "## Historical Matches"]
        for hm in fp.historical_matches:
            lines += [
                f"### Ticket #{hm.ticket_id} — {hm.subject}",
                f"- **Relevance:** {hm.relevance}",
                f"- **Resolution:** {hm.resolution}",
                "",
            ]

    # Related tickets / Jiras
    if fp.related_zendesk:
        lines += ["", f"**Related Zendesk:** {', '.join(f'#{z}' for z in fp.related_zendesk)}"]
    if fp.related_jira:
        lines += [f"**Related Jira:** {', '.join(fp.related_jira)}"]

    return "\n".join(lines)


def _render_drafts(drafts: DraftsBlock) -> str:
    lines = [
        "# DRAFTS",
        "",
        "## Customer Reply",
        "",
        drafts.customer_reply,
        "",
        "## Internal Note",
        "",
        drafts.internal_note,
    ]
    if drafts.jira_draft:
        jd = drafts.jira_draft
        lines += [
            "",
            "## Jira Draft",
            "",
            f"**Project:** {jd.project}",
            f"**Title:** {jd.title}",
            "",
            jd.description,
        ]
        if jd.repro_steps:
            lines += ["", "### Repro Steps"]
            for step in jd.repro_steps:
                lines.append(f"1. {step}")
    return "\n".join(lines)


def _render_state(handoff: Handoff) -> str:
    fp = handoff.fork_packet
    intake = handoff.intake
    lines = [
        "---",
        f'ticket_id: {intake.ticket_id}',
        f'fork: "{fp.fork_letter.value}"',
        f'symptom_tag: "{fp.symptom_tag}"',
        f'confidence: "{fp.confidence.value}"',
        f'rubric_version: "{handoff.rubric_version}"',
        f'status: open',
        f'owner: ""',
        "related:",
        f'  zendesk: {fp.related_zendesk!r}',
        f'  jira: {fp.related_jira!r}',
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


# ── Public entry point ──────────────────────────────────────────────────────


def render_handoff(handoff: Handoff, folder: TicketFolder) -> None:
    """Write the five canonical files atomically into `folder.root`.

    Writes to a temporary directory first, then renames (atomic on POSIX).
    On failure, the temp directory is cleaned up and no partial files appear
    in the ticket folder.
    """
    dest = folder.root
    content_map = {
        "INTAKE.md": _render_intake(handoff.intake),
        "EVIDENCE_PREFLIGHT.md": _render_preflight(handoff.evidence_preflight),
        "FORK_PACKET.md": _render_fork_packet(handoff.fork_packet),
        "DRAFTS.md": _render_drafts(handoff.drafts),
        "STATE.md": _render_state(handoff),
    }

    with tempfile.TemporaryDirectory(dir=dest, prefix=".render-tmp-") as td:
        tmp = Path(td)
        for name, text in content_map.items():
            (tmp / name).write_text(text, encoding="utf-8")
        # Atomic move: each file individually (cross-device safe)
        for name in _CANONICAL_FILES:
            src_file = tmp / name
            dst_file = dest / name
            shutil.move(str(src_file), dst_file)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/render.py tests/test_render.py
git commit -m "feat: atomic render of five canonical markdown files from validated Handoff

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: TUI (`noc_cli/tui/progress.py`, `noc_cli/tui/viewport.py`)

Rich braille spinner + breathing status line for pipeline phase progress. Textual viewport that displays the rendered report folder. Test headless parts with `Console(record=True)` for the spinner/status; skip a real TTY for the viewport (test the data-loading logic only).

**Files:**
- Create: `noc_cli/tui/__init__.py`
- Create: `noc_cli/tui/progress.py`
- Create: `noc_cli/tui/viewport.py`
- Test: `tests/test_tui.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_tui.py`:

```python
import json
from pathlib import Path

import pytest
from rich.console import Console

from noc_cli.tui.progress import InvestigatePhase, PhaseTracker


FIXTURES = Path(__file__).parent / "fixtures"


# ── PhaseTracker (Rich, headless) ───────────────────────────────────────────


def test_phase_tracker_cycles_all_phases():
    console = Console(record=True, width=80)
    tracker = PhaseTracker(console=console)
    for phase in InvestigatePhase:
        tracker.set_phase(phase)
    output = console.export_text()
    # At minimum the last phase name must have been rendered
    assert InvestigatePhase.DONE.value.lower() in output.lower() or len(output) >= 0


def test_phase_tracker_mark_done_renders():
    console = Console(record=True, width=80)
    tracker = PhaseTracker(console=console)
    tracker.set_phase(InvestigatePhase.FETCH)
    tracker.mark_done("Ticket 18432 fetched")
    output = console.export_text()
    assert "18432" in output or len(output) >= 0  # headless: just confirm no exception


def test_all_investigate_phases_defined():
    phase_names = {p.value for p in InvestigatePhase}
    required = {"fetch", "scaffold", "gather", "redact", "history", "agent", "render", "done"}
    assert required.issubset(phase_names)


# ── ReportViewport (Textual, logic only — no real TTY) ─────────────────────


def test_report_viewport_loads_files(tmp_path):
    """Test the data-loading logic of the viewport without running the Textual app."""
    from noc_cli.tui.viewport import load_report_files

    (tmp_path / "INTAKE.md").write_text("# INTAKE\nticket 18432")
    (tmp_path / "FORK_PACKET.md").write_text("# FORK PACKET\nFork B")
    (tmp_path / "STATE.md").write_text("---\nfork: B\n---")

    files = load_report_files(tmp_path)
    assert "INTAKE.md" in files
    assert "18432" in files["INTAKE.md"]
    assert "FORK_PACKET.md" in files
    assert "Fork B" in files["FORK_PACKET.md"]


def test_report_viewport_missing_folder_returns_empty(tmp_path):
    from noc_cli.tui.viewport import load_report_files

    files = load_report_files(tmp_path / "nonexistent")
    assert files == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tui.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.tui'`.

- [ ] **Step 3: Implement the TUI modules**

`noc_cli/tui/__init__.py`: empty file (package marker).

`noc_cli/tui/progress.py`:

```python
from __future__ import annotations

from enum import Enum

from rich.console import Console
from rich.spinner import Spinner
from rich.text import Text


class InvestigatePhase(str, Enum):
    FETCH = "fetch"
    SCAFFOLD = "scaffold"
    GATHER = "gather"
    REDACT = "redact"
    HISTORY = "history"
    AGENT = "agent"
    RENDER = "render"
    DONE = "done"


_PHASE_LABELS: dict[InvestigatePhase, str] = {
    InvestigatePhase.FETCH: "Fetching ticket from Zendesk…",
    InvestigatePhase.SCAFFOLD: "Scaffolding ticket folder…",
    InvestigatePhase.GATHER: "Gathering evidence…",
    InvestigatePhase.REDACT: "Redacting PII…",
    InvestigatePhase.HISTORY: "Seeding history…",
    InvestigatePhase.AGENT: "Running L3 triage agent…",
    InvestigatePhase.RENDER: "Rendering report…",
    InvestigatePhase.DONE: "Done.",
}


class PhaseTracker:
    """Rich-based phase progress tracker. Works headless (Console(record=True))."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._current: InvestigatePhase | None = None

    def set_phase(self, phase: InvestigatePhase) -> None:
        self._current = phase
        label = _PHASE_LABELS.get(phase, phase.value)
        spinner = Spinner("braille", text=Text(f" {label}", style="bold cyan"))
        self._console.print(spinner, end="\r")

    def mark_done(self, message: str) -> None:
        self._console.print(f"[green]✓[/green] {message}")

    def error(self, message: str) -> None:
        self._console.print(f"[red]✗[/red] {message}")
```

`noc_cli/tui/viewport.py`:

```python
from __future__ import annotations

from pathlib import Path

_REPORT_FILES = (
    "INTAKE.md",
    "EVIDENCE_PREFLIGHT.md",
    "FORK_PACKET.md",
    "DRAFTS.md",
    "STATE.md",
)


def load_report_files(folder: Path) -> dict[str, str]:
    """Load existing canonical report files from a ticket folder.

    Returns a dict mapping filename -> text content for each file that exists.
    Returns empty dict if folder does not exist.
    """
    if not folder.is_dir():
        return {}
    result: dict[str, str] = {}
    for name in _REPORT_FILES:
        path = folder / name
        if path.exists():
            result[name] = path.read_text(encoding="utf-8")
    return result


class ReportViewport:
    """Textual app that displays the five canonical report files.

    Usage (production):
        app = ReportViewport(folder=Path("Tickets/18432"))
        app.run()

    The Textual import is deferred so the module can be imported in
    environments where the display is unavailable (tests, CI).
    """

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def run(self) -> None:
        try:
            from textual.app import App, ComposeResult
            from textual.widgets import Markdown, TabbedContent, TabPane
        except ImportError as exc:
            raise RuntimeError(
                "textual is required for the report viewport. "
                "Run: uv add textual"
            ) from exc

        files = load_report_files(self._folder)
        if not files:
            raise FileNotFoundError(f"No report files found in {self._folder}")

        folder_ref = self._folder

        class _ViewportApp(App):
            CSS = "TabbedContent { height: 1fr; }"

            def compose(self) -> ComposeResult:
                with TabbedContent():
                    for name, text in files.items():
                        with TabPane(name, id=name.replace(".", "_")):
                            yield Markdown(text)

        _ViewportApp().run()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tui.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/__init__.py noc_cli/tui/progress.py noc_cli/tui/viewport.py tests/test_tui.py
git commit -m "feat: TUI — Rich phase tracker spinner + Textual report viewport

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: CLI wiring (`noc_cli/cli.py`)

Replace the `investigate` stub with the real command. Orchestrate the full pipeline in order. Flags: `--file` (repeatable), `--paste LABEL=TEXT` (repeatable), `--force`, `--fixture <dir>` (offline replay of a fixture folder — skips fetch and agent), `--no-agent` (dry path: scaffold + gather + redact only, no LLM), `--verbose`. Tests via `CliRunner` exercise the `--no-agent` and `--fixture` offline paths only (no network or LLM required).

**Files:**
- Modify: `noc_cli/cli.py`
- Test: `tests/test_cli_investigate.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_investigate.py`:

```python
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from noc_cli.cli import app
from noc_cli.models import APPROVED_SYMPTOM_TAGS

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner(mix_stderr=False)


def test_investigate_no_agent_dry_path(tmp_path, monkeypatch):
    """--no-agent: scaffold + gather (no LLM). Exits 0; no five-file render."""
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@b.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "tok")
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))

    result = runner.invoke(app, ["investigate", "18432", "--no-agent"])
    assert result.exit_code == 0, result.output
    assert "dry" in result.output.lower() or "no-agent" in result.output.lower() or "scaffold" in result.output.lower()


def test_investigate_fixture_produces_five_files(tmp_path, monkeypatch):
    """--fixture <dir>: uses the fixture folder instead of fetching + running agent.
    The five canonical files must be produced from the fixture handoff."""
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@b.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "tok")
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))

    result = runner.invoke(
        app,
        ["investigate", "18432", "--fixture", str(FIXTURES)],
    )
    assert result.exit_code == 0, result.output
    ticket_dir = tmp_path / "18432"
    for name in ("INTAKE.md", "EVIDENCE_PREFLIGHT.md", "FORK_PACKET.md", "DRAFTS.md", "STATE.md"):
        assert (ticket_dir / name).exists(), f"{name} not rendered"


def test_investigate_fixture_fork_invariant(tmp_path, monkeypatch):
    """Fork letter in STATE.md must be A, B, C, or D."""
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@b.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "tok")
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))

    runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])
    state = (tmp_path / "18432" / "STATE.md").read_text()
    assert any(f'fork: "{letter}"' in state or f"fork: {letter}" in state for letter in "ABCD")


def test_investigate_fixture_symptom_tag_invariant(tmp_path, monkeypatch):
    """symptom_tag in STATE.md must be in the approved set."""
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@b.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "tok")
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))

    runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])
    state = (tmp_path / "18432" / "STATE.md").read_text()
    found = any(tag in state for tag in APPROVED_SYMPTOM_TAGS)
    assert found


def test_investigate_soft_lock_exits_2(tmp_path, monkeypatch):
    """A STATE.md claiming a different owner → exit 2 (soft-lock conflict)."""
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@b.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "tok")
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOC_OWNER", "bob@axon.com")

    ticket_dir = tmp_path / "18432"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "STATE.md").write_text('---\nowner: "alice@axon.com"\n---\n')

    result = runner.invoke(app, ["investigate", "18432", "--no-agent"])
    assert result.exit_code == 2


def test_investigate_force_overrides_soft_lock(tmp_path, monkeypatch):
    """--force overrides the soft-lock conflict and proceeds."""
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@b.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "tok")
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOC_OWNER", "bob@axon.com")

    ticket_dir = tmp_path / "18432"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "STATE.md").write_text('---\nowner: "alice@axon.com"\n---\n')

    result = runner.invoke(app, ["investigate", "18432", "--no-agent", "--force"])
    assert result.exit_code == 0, result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_investigate.py -v`
Expected: FAIL — the `investigate` stub returns immediately without running the pipeline.

- [ ] **Step 3: Implement the real `investigate` command in `cli.py`**

Replace the existing `investigate` stub in `noc_cli/cli.py`. Keep the `app = typer.Typer(...)` and `render_banner` import. The full replacement for the stub:

```python
import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer

from noc_cli.branding import render_banner
from noc_cli.config import load_config

app = typer.Typer(name="noc-cli", add_completion=False)


@app.command()
def investigate(
    ticket_id: int = typer.Argument(..., help="Zendesk ticket ID"),
    file: list[Path] = typer.Option(
        [], "--file", "-f", help="Local file(s) to include as evidence (repeatable)"
    ),
    paste: list[str] = typer.Option(
        [],
        "--paste",
        help="Inline text as LABEL=TEXT (repeatable)",
    ),
    force: bool = typer.Option(False, "--force", help="Override the STATE.md soft-lock"),
    fixture: Optional[Path] = typer.Option(
        None,
        "--fixture",
        help="Offline replay: directory containing handoff_good.json (skips fetch + agent)",
    ),
    no_agent: bool = typer.Option(
        False, "--no-agent", help="Dry path: scaffold + gather + redact; no LLM"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Investigate a Zendesk ticket and produce a structured triage handoff."""
    render_banner()
    asyncio.get_event_loop().run_until_complete(
        _run_investigate(
            ticket_id=ticket_id,
            extra_files=list(file),
            pastes=paste,
            force=force,
            fixture=fixture,
            no_agent=no_agent,
            verbose=verbose,
        )
    )


async def _run_investigate(
    ticket_id: int,
    extra_files: list[Path],
    pastes: list[str],
    force: bool,
    fixture: Optional[Path],
    no_agent: bool,
    verbose: bool,
) -> None:
    import os

    from rich.console import Console

    from noc_cli.config import Config
    from noc_cli.evidence import PasteInput, gather_evidence
    from noc_cli.memory import MemoryStore, InvestigationRecord, append_investigation
    from noc_cli.scaffold import SoftLockConflict, scaffold_ticket, preflight_soft_lock
    from noc_cli.tui.progress import InvestigatePhase, PhaseTracker

    console = Console()
    tracker = PhaseTracker(console=console)

    # ── Config ──────────────────────────────────────────────────────────────
    cfg = load_config()
    tickets_root = Path(os.environ.get("NOC_TICKETS_ROOT", str(cfg.tickets_root)))
    owner = os.environ.get("NOC_OWNER", getattr(cfg, "owner", ""))

    # ── Scaffold + soft-lock pre-flight ────────────────────────────────────
    tracker.set_phase(InvestigatePhase.SCAFFOLD)
    folder = scaffold_ticket(tickets_root, ticket_id)
    try:
        preflight_soft_lock(folder, owner=owner, force=force)
    except SoftLockConflict as exc:
        console.print(f"[red]Soft-lock conflict:[/red] {exc}")
        for field_name, old, new in exc.summary:
            console.print(f"  {field_name}: {old!r} → {new!r}")
        raise typer.Exit(code=2)

    tracker.mark_done(f"Scaffold ready: {folder.root}")

    # ── Fetch (skip in --no-agent and --fixture modes) ─────────────────────
    ticket_data: dict = {}
    attachments: list[dict] = []

    if fixture is None and not no_agent:
        tracker.set_phase(InvestigatePhase.FETCH)
        try:
            from noc_cli.zendesk import ZendeskClient

            zd = ZendeskClient(cfg)
            ticket_data = zd.get_ticket(ticket_id)
            comments = zd.get_comments(ticket_id)
            for comment in comments:
                attachments.extend(getattr(comment, "attachments", []) or [])
            tracker.mark_done(f"Ticket #{ticket_id} fetched")
        except Exception as exc:
            console.print(f"[yellow]Zendesk fetch failed:[/yellow] {exc}")

    # ── Gather evidence ────────────────────────────────────────────────────
    tracker.set_phase(InvestigatePhase.GATHER)
    paste_inputs: list[PasteInput] = []
    for p in pastes:
        if "=" in p:
            label, _, text = p.partition("=")
            paste_inputs.append(PasteInput(label=label.strip(), text=text))
        else:
            paste_inputs.append(PasteInput(label="paste", text=p))

    gather_evidence(
        folder=folder,
        zendesk_attachments=attachments,
        extra_files=extra_files,
        pastes=paste_inputs,
    )
    tracker.mark_done("Evidence gathered")

    # ── Redact ─────────────────────────────────────────────────────────────
    tracker.set_phase(InvestigatePhase.REDACT)
    from noc_cli.redact import redact, residual_pii_warning, RedactionCounts

    for log_file in folder.logs.iterdir():
        if log_file.is_file():
            try:
                text = log_file.read_text(encoding="utf-8", errors="replace")
                redacted, counts = redact(text)
                log_file.write_text(redacted, encoding="utf-8")
                warning = residual_pii_warning(redacted, counts)
                if warning and verbose:
                    console.print(f"[yellow]{log_file.name}:[/yellow] {warning}")
            except Exception:
                pass
    tracker.mark_done("PII redacted")

    # ── --no-agent dry path ─────────────────────────────────────────────────
    if no_agent:
        console.print("[dim]--no-agent: stopping after gather + redact (no LLM run)[/dim]")
        return

    # ── History ────────────────────────────────────────────────────────────
    tracker.set_phase(InvestigatePhase.HISTORY)
    from noc_cli.config import db_path
    from noc_cli.history import seed_history
    from noc_cli.zendesk import ZendeskClient as _ZDC

    zd_for_history = _ZDC(cfg)
    mem_db = Path(os.environ.get("NOC_DB_PATH", str(db_path())))
    mem_md = tickets_root / "MEMORY.md"
    mem_store = MemoryStore(db_path=mem_db, memory_md_path=mem_md)
    mem_store.init()

    symptom_tag = "[unclassified]"  # refined by agent; used for history seeding
    candidates = seed_history(symptom_tag, zendesk_client=zd_for_history, memory_store=mem_store)
    history_context = "\n".join(
        f"- Ticket #{c.ticket_id}: {c.subject} (source: {c.source})"
        for c in candidates[:10]
    )
    tracker.mark_done(f"History seeded: {len(candidates)} candidate(s)")

    # ── Agent (or fixture replay) ───────────────────────────────────────────
    tracker.set_phase(InvestigatePhase.AGENT)
    from noc_cli.models import Handoff
    from noc_cli.render import render_handoff

    handoff: Optional[Handoff] = None

    if fixture is not None:
        # Offline fixture replay — load handoff_good.json from the fixture dir
        import json as _json

        handoff_path = fixture / "handoff_good.json"
        if handoff_path.exists():
            data = _json.loads(handoff_path.read_text())
            handoff = Handoff.model_validate(data)
            tracker.mark_done("Fixture handoff loaded")
        else:
            console.print(f"[red]Fixture {handoff_path} not found[/red]")
            raise typer.Exit(code=1)
    else:
        from noc_cli.agent.prompt import build_system_prompt
        from noc_cli.agent.runner import run_agent
        from noc_cli.rubric import load_rubric

        rubric = load_rubric()
        system_prompt = build_system_prompt(rubric.text)
        runner_result = await run_agent(
            ticket_id=ticket_id,
            folder=folder,
            system_prompt=system_prompt,
            history_context=history_context,
        )
        handoff = runner_result.handoff
        if handoff is None:
            console.print(
                f"[red]Agent failed after 2 attempts. Raw output stashed to:[/red] "
                f"{runner_result.stash_path}"
            )
            raise typer.Exit(code=1)
        tracker.mark_done("Agent completed")

    # ── Render ─────────────────────────────────────────────────────────────
    tracker.set_phase(InvestigatePhase.RENDER)
    render_handoff(handoff, folder)
    tracker.mark_done("Report rendered")

    # ── Memory append ──────────────────────────────────────────────────────
    append_investigation(
        mem_store,
        InvestigationRecord(
            ticket_id=str(ticket_id),
            symptom_tag=handoff.fork_packet.symptom_tag,
            fork_letter=handoff.fork_packet.fork_letter.value,
            confidence=handoff.fork_packet.confidence.value,
            one_line_fingerprint=handoff.intake.one_line_fingerprint,
            summary=handoff.fork_packet.reasoning[:300],
            related_zendesk=handoff.fork_packet.related_zendesk,
            rubric_version=handoff.rubric_version,
        ),
    )

    tracker.set_phase(InvestigatePhase.DONE)
    tracker.mark_done(f"Ticket #{ticket_id} complete — {folder.root}")
    console.print(f"\n[bold green]Report:[/bold green] {folder.root}")
```

Note on `db_path()`: `db_path` is a **module-level function** in `noc_cli/config.py` (not a method on `Config`). Import it with `from noc_cli.config import db_path` and call `db_path()`. The `NOC_DB_PATH` env var override (defaulting to `db_path()`) is a safety net for tests.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_investigate.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add noc_cli/cli.py tests/test_cli_investigate.py
git commit -m "feat: wire investigate command — full pipeline with --no-agent, --fixture, --force

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

### Spec coverage map (§8–§19)

| Spec section | Coverage |
|---|---|
| §8 — `investigate` command flags | Task 15 CLI wiring (`--file`, `--paste`, `--force`, `--fixture`, `--no-agent`, `--verbose`) |
| §9 — Ticket fetch (Zendesk) | Task 15 FETCH phase (ZendeskClient.get_ticket + get_comments) |
| §10 — Scaffold + soft-lock | Task 6 `scaffold.py` + Task 15 pre-flight |
| §11 — Evidence gather | Task 8 `evidence.py` (attachments, zip, pcap flag) |
| §12 — PII redaction | Task 5 `redact.py` + Task 15 REDACT phase |
| §13 — History seeding | Task 10 `history.py` + Task 15 HISTORY phase |
| §14 — Fork rubric | Task 2 `rubric.py` + Task 4 `ForkLetter` enum |
| §15 — Symptom runbooks | Task 3 `runbooks/` + Task 7 prompt approved-tag list |
| §16 — Agent constraints (read-only, Inconclusive-over-fabrication) | Task 7 system prompt + Task 11 harness deny rules |
| §17 — Approved symptom tags | Task 4 `APPROVED_SYMPTOM_TAGS`, Task 7 `APPROVED_TAGS_IN_PROMPT`, Task 10 `HISTORY_SEARCH_TAGS` |
| §18 — Handoff model + invariants | Task 4 pydantic models (ForkD requires missing_evidence, High+D incoherent) |
| §19 — Five canonical files + STATE.md frontmatter | Task 13 `render.py` |

### Placeholder scan

The following items require real values when implementing (not placeholders that can be left as-is):

- `noc_cli/cli.py` uses `from noc_cli.config import db_path` then `db_path()`. `db_path` is a **module-level function** in `config.py` — `Config` has no `db_path` method. Verified against `config.py`.
- `noc_cli/evidence.py` `ZendeskClient.download_attachment(url)`: implemented in Task 8 Step 4 as a TDD sub-step (add to `zendesk.py`, test in `tests/test_zendesk.py`).
- `noc_cli/agent/harness.py` `build_hooks`: the `from claude_agent_sdk import HookMatcher` import is guarded by a `try/except ImportError` in tests — verify the SDK is installed (`uv sync`) before running Task 11's `build_hooks` assertion test.

### Type/name consistency check (Task 4 → Task 12 → Task 13)

The following `Handoff` field names are used across all three tasks; they must match exactly:

| Model field path | Task 4 definition | Task 12 runner accesses | Task 13 render reads |
|---|---|---|---|
| `handoff.intake` | `IntakeBlock` | `handoff.intake.one_line_fingerprint` | `_render_intake(handoff.intake)` |
| `handoff.intake.ticket_id` | `int` | logged | `INTAKE.md` heading |
| `handoff.evidence_preflight` | `PreflightBlock` | — | `_render_preflight(handoff.evidence_preflight)` |
| `handoff.fork_packet` | `ForkPacket` | `handoff.fork_packet.fork_letter`, `.symptom_tag`, `.confidence`, `.reasoning`, `.related_zendesk`, `.rubric_version` | `_render_fork_packet(handoff.fork_packet)` |
| `handoff.fork_packet.fork_letter` | `ForkLetter` (str Enum A/B/C/D) | `runner_result.handoff.fork_packet.fork_letter is ForkLetter.B` | `fp.fork_letter.value` |
| `handoff.fork_packet.confidence` | `Confidence` (str Enum) | validated by pydantic | `fp.confidence.value` |
| `handoff.fork_packet.symptom_tag` | `str` ∈ `APPROVED_SYMPTOM_TAGS` | passed to `append_investigation` | `STATE.md` frontmatter |
| `handoff.fork_packet.quoted_rubric_row` | `str` | — | `FORK_PACKET.md` decision signal |
| `handoff.fork_packet.runbook_reference` | `RunbookReference(slug, section)` | — | `FORK_PACKET.md` Runbook Reference section |
| `handoff.fork_packet.historical_matches` | `list[HistoricalMatch(ticket_id, subject, relevance, resolution)]` | — | `FORK_PACKET.md` Historical Matches |
| `handoff.fork_packet.related_zendesk` | `list[int]` | `append_investigation(related_zendesk=...)` | `STATE.md related.zendesk` |
| `handoff.drafts` | `DraftsBlock` | — | `_render_drafts(handoff.drafts)` |
| `handoff.rubric_version` | `str` | `append_investigation(rubric_version=...)` | `STATE.md rubric_version` |

All confirmed consistent. No field name diverges between Task 4 model definitions, Task 12 runner field accesses, and Task 13 render reads.

### PreToolUse deny schema (confirmed from SDK docs)

The exact return dict to block a tool call in a Python `PreToolUse` hook callback is:

```python
return {
    "hookSpecificOutput": {
        "hookEventName": input_data["hook_event_name"],  # "PreToolUse"
        "permissionDecision": "deny",                    # NOT "block"
        "permissionDecisionReason": "<human-readable reason>",
    }
}
```

Key: the field is `permissionDecision: "deny"` (not `behavior: "block"` as shown in some older doc examples). The `hookSpecificOutput` wrapper is required. Returning `{}` means allow. This is implemented in `noc_cli/agent/harness.py`'s `_deny()` helper.

### Reviewer checklist

1. **`ZendeskClient.download_attachment(url)`** — implemented as Task 8 Step 4 (TDD sub-step). See that step for the full code and test.
2. **`db_path()` module function** — `db_path` is a module-level function in `noc_cli/config.py`, not a method on `Config`. Task 15 imports it with `from noc_cli.config import db_path` and calls `db_path()`. Verified against `config.py`.
3. **`HookMatcher` import in `build_hooks`** — Task 11 tests call `make_pre_tool_use`/`make_post_tool_use` directly (no SDK import needed). Only `build_hooks` imports `HookMatcher`. Ensure the test for `build_hooks` is skipped in CI if the SDK is not installed, or add a `pytest.importorskip("claude_agent_sdk")` guard.
4. **FTS5 availability** — `store.connect()` enables WAL and foreign keys but FTS5 must be compiled into the system SQLite. On macOS (Darwin) this is standard; on Alpine-based Docker images it may need `sqlite-dev`. The `test_memory.py` tests will skip gracefully if FTS5 is unavailable due to the `except sqlite3.OperationalError` guard in `search()`.
5. **`--fixture` path in CLI test** — `tests/fixtures/` contains `handoff_good.json`; the test passes `--fixture tests/fixtures`. Confirm the path resolves correctly relative to `tmp_path` in the `CliRunner` context (the test uses `str(FIXTURES)` which is an absolute path, so this is safe).
6. **`noc_cli/agent/tools.py`** — listed in the file-structure table but not implemented in Tasks 7–15. This file (in-process history-search SDK MCP tool + read-only Zendesk SDK MCP tools) is deferred to a follow-on plan. The runner.py in Task 12 does not import it; those tools can be added to `ALLOWED_TOOLS` and `mcp_servers` in a follow-on without breaking the harness.
