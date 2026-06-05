# Runbook-Grounded Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the background of `noc-cli investigate <id>` so the L3 agent is grounded in the relevant symptom runbook(s) — analyst-seeded, dynamically re-steered, with an inspectable `REASONING.md` transcript — instead of triaging every ticket off the generic catch-all rubric.

**Architecture:** Layered grounding. The system prompt carries only the rubric *core* (domain-agnostic Step 0 + fork definitions) plus a compact domain map; the six runbooks + full rubric are staged into the ticket sandbox for the agent to `Read` on demand. An analyst seed (TUI prompt or `--suspect`) sets the initial runbook as a *soft prior*; the agent re-steers as evidence dictates and narrates pivots. The discarded SDK message stream is captured into a transcript and rendered to `REASONING.md`.

**Tech Stack:** Python 3.10+, Typer (CLI), Pydantic v2 (models), `claude-agent-sdk` (agent), pytest + anyio (tests). Run tests with `python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-05-runbook-grounded-investigate-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `noc_cli/runbooks/__init__.py` | Symptom taxonomy + runbook loading/staging | Add `DOMAIN_MAP`, `Symptom`, `runbook_slug_from_path`, `stage_runbooks` |
| `noc_cli/rubric.py` | Fork-rubric loading | Add `Rubric.core`; widen `contains_row` |
| `noc_cli/agent/prompt.py` | Agent system prompt | Restructure template: core + domain map + grounding protocol |
| `noc_cli/scaffold.py` | Ticket sandbox creation | Add `TicketFolder.runbooks`; stage runbooks in `scaffold_ticket` |
| `noc_cli/agent/runner.py` | Agent run loop | Add `TranscriptEntry`, transcript capture, `initial_hypothesis` param |
| `noc_cli/render.py` | Markdown rendering | Add `render_reasoning`, consulted-slug + warning helpers, STATE.md additions |
| `noc_cli/seed.py` (new) | Analyst seed resolution | `resolve_seed`, menu helpers (testable, no TTY) |
| `noc_cli/cli.py` | `investigate` orchestration | `--suspect`, seed prompt, wire core/seed/transcript/reasoning |
| `tests/fixtures/handoff_pivot.json` (new) | Pivot test fixture | Hypothesis ≠ final tag |

**Note on the "runbook-read hook":** the existing `make_post_tool_use` (`noc_cli/agent/harness.py`) already appends every tool call — including `Read` — to `events.jsonl`. The transcript captured in Task 5 also records `Read` calls. We therefore derive "runbooks consulted" from the **transcript** (Task 6) rather than adding a new hook or parsing `events.jsonl`. `harness.py` is **not** modified.

---

## Task 1: Symptom taxonomy — `DOMAIN_MAP` + `runbook_slug_from_path`

**Files:**
- Modify: `noc_cli/runbooks/__init__.py`
- Test: `tests/test_runbooks.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runbooks.py`:

```python
from noc_cli.runbooks import DOMAIN_MAP, runbook_slug_from_path
from noc_cli.models import APPROVED_SYMPTOM_TAGS


def test_domain_map_covers_all_runbook_slugs():
    slugs = {s.slug for s in DOMAIN_MAP}
    assert slugs == set(RUNBOOK_SLUGS)
    assert len(DOMAIN_MAP) == 6


def test_domain_map_tags_are_all_approved():
    for s in DOMAIN_MAP:
        assert s.tag in APPROVED_SYMPTOM_TAGS, f"{s.tag!r} not approved"
        assert s.domain and s.label


def test_runbook_slug_from_path_matches_known_slugs():
    assert runbook_slug_from_path("/t/18432/runbooks/low-audio.md") == "low-audio"
    assert runbook_slug_from_path("runbooks/apex.md") == "apex"
    assert runbook_slug_from_path("no-ani.md") == "no-ani"


def test_runbook_slug_from_path_rejects_non_runbooks():
    assert runbook_slug_from_path("/t/18432/runbooks/fork-rubric.md") is None
    assert runbook_slug_from_path("/t/18432/logs/station.log") is None
    assert runbook_slug_from_path("/t/18432/analysis/notes.md") is None
    assert runbook_slug_from_path("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runbooks.py -q`
Expected: FAIL with `ImportError: cannot import name 'DOMAIN_MAP'`.

- [ ] **Step 3: Implement the taxonomy**

In `noc_cli/runbooks/__init__.py`, add `from dataclasses import dataclass` to the imports at the top, then append after the `RUNBOOK_SLUGS` definition (after line 17):

```python
@dataclass(frozen=True)
class Symptom:
    """One operator-selectable symptom: approved tag, runbook slug, domain group, menu label."""

    tag: str
    slug: str
    domain: str
    label: str


# Ordered catalog — the single source of truth for BOTH the operator seed menu
# (noc_cli/seed.py) and the domain map embedded in the agent system prompt
# (noc_cli/agent/prompt.py). Order defines the menu numbering.
DOMAIN_MAP: tuple[Symptom, ...] = (
    Symptom("[dropped calls]", "dropped-calls", "SIP / UC", "Dropped or failed calls"),
    Symptom("[No ANI]", "no-ani", "SIP / UC", "Caller number missing (No ANI)"),
    Symptom("[No ALI]", "no-ali", "SIP / UC", "Caller location missing (No ALI)"),
    Symptom("[low audio]", "low-audio", "Media", "Audio / media quality"),
    Symptom("[apex]", "apex", "Operator Client", "APEX / station-client behavior"),
    Symptom("[event history]", "event-history", "Data", "Event history / analytics gap"),
)


def runbook_slug_from_path(path: str) -> str | None:
    """Return the runbook slug if `path` points at a staged runbook markdown
    (`.../runbooks/<slug>.md` for a known slug), else None. `fork-rubric.md`
    and non-runbook files return None."""
    if not path:
        return None
    name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if not name.endswith(".md"):
        return None
    slug = name[:-3]
    return slug if slug in RUNBOOK_SLUGS else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runbooks.py -q`
Expected: PASS (all, including the four pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/runbooks/__init__.py tests/test_runbooks.py
git commit -m "feat(runbooks): add DOMAIN_MAP taxonomy and runbook_slug_from_path" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Rubric core split + widened `contains_row`

**Files:**
- Modify: `noc_cli/rubric.py`
- Test: `tests/test_rubric.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rubric.py`:

```python
def test_core_stops_before_symptom_class_tables():
    r = load_rubric()
    assert "## Symptom Class" not in r.core
    assert "Step 0" in r.core
    assert "Engineering Jira" in r.core  # fork definition present
    assert len(r.core) < len(r.text)


def test_contains_row_accepts_extra_texts():
    r = load_rubric()
    needle = "RTP present, timestamps healthy"  # lives in a runbook, not the core
    assert not r.contains_row(needle, extra_texts=[])
    assert r.contains_row(needle, extra_texts=["... RTP present, timestamps healthy ..."])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rubric.py -q`
Expected: FAIL — `AttributeError: 'Rubric' object has no attribute 'core'`.

- [ ] **Step 3: Implement core + widened validator**

In `noc_cli/rubric.py`, add `from typing import Iterable` to the imports. Replace the `Rubric` dataclass (lines 10-26) and `load_rubric` (lines 35-42) with:

```python
@dataclass(frozen=True)
class Rubric:
    """The embedded fork rubric: full text, parsed version, and domain-agnostic core.

    `core` is the preamble (purpose + fork definitions + stop rule + Step 0 intake),
    i.e. everything before the first per-symptom `## Symptom Class` header. It is what
    the agent system prompt embeds; the full per-symptom tables stay in `text` (staged
    into the sandbox for on-demand reading).

    `contains_row` is the soft-warn validator: True when `quoted` is a verbatim
    substring of the rubric text OR any of `extra_texts` (e.g. a loaded runbook).
    """

    text: str
    version: str
    core: str = ""

    def contains_row(self, quoted: str, *, extra_texts: Iterable[str] = ()) -> bool:
        if not quoted or not quoted.strip():
            return False
        if quoted in self.text:
            return True
        return any(quoted in t for t in extra_texts)


_CORE_BOUNDARY = "## Symptom Class"


def _extract_core(text: str) -> str:
    return text.split(_CORE_BOUNDARY, 1)[0].rstrip() + "\n"
```

Then update `load_rubric` to populate `core`:

```python
def load_rubric() -> Rubric:
    """Load the embedded fork rubric. Raises ValueError if the version
    frontmatter is missing (a packaging error, caught in tests)."""
    text = _read_rubric_text()
    match = _VERSION_RE.search(text)
    if not match:
        raise ValueError("fork-rubric.md is missing the rubric_version frontmatter")
    return Rubric(text=text, version=match.group(1).strip(), core=_extract_core(text))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rubric.py -q`
Expected: PASS (including the three pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/rubric.py tests/test_rubric.py
git commit -m "feat(rubric): expose domain-agnostic core and widen contains_row" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Prompt restructure — base + domain map + grounding protocol

**Files:**
- Modify: `noc_cli/agent/prompt.py`
- Test: `tests/test_agent_prompt.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_prompt.py`:

```python
from noc_cli.runbooks import RUNBOOK_SLUGS
from noc_cli.rubric import load_rubric


def test_system_prompt_lists_all_runbook_slugs():
    for slug in RUNBOOK_SLUGS:
        assert f"runbooks/{slug}.md" in SYSTEM_PROMPT, f"{slug} missing from domain map"


def test_system_prompt_has_grounding_protocol():
    lower = SYSTEM_PROMPT.lower()
    assert "hypothesis" in lower
    assert "re-steer" in lower or "re-ground" in lower or "pivot" in lower


def test_build_system_prompt_with_core_excludes_symptom_class_tables():
    prompt = build_system_prompt(load_rubric().core)
    assert "## Symptom Class" not in prompt
    # but a per-symptom fork-table observation must NOT be inlined either:
    assert "SBC sends unsolicited BYE" not in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_prompt.py -q`
Expected: FAIL — `runbooks/<slug>.md` strings not in `SYSTEM_PROMPT`.

- [ ] **Step 3: Restructure the prompt module**

Replace the entire contents of `noc_cli/agent/prompt.py` with:

```python
from __future__ import annotations

from noc_cli.runbooks import DOMAIN_MAP

APPROVED_TAGS_IN_PROMPT: tuple[str, ...] = (
    "[apex]",
    "[low audio]",
    "[dropped calls]",
    "[No ANI]",
    "[No ALI]",
    "[event history]",
    "[unclassified]",
)


def _domain_map_block() -> str:
    """Compact, always-on map of which runbook covers which symptom. The full
    runbook text is staged in the sandbox under runbooks/ for on-demand reading."""
    return "\n".join(
        f"  - {s.tag} → `runbooks/{s.slug}.md` — {s.label} ({s.domain})" for s in DOMAIN_MAP
    )


_DOMAIN_MAP_BLOCK = _domain_map_block()

_PROMPT_TEMPLATE = """\
# Role
You are a senior L3 NOC triage analyst at Carbyne. Your task is to perform
structured, evidence-based triage on a single Zendesk support ticket and produce
a validated Handoff JSON object that the noc-cli render pipeline will write to
the ticket folder.

# What you must do
1. Read the ticket body, comments, and all evidence files in your working
   directory (logs/, pcaps/, analysis/). Complete the Step 0 intake from the
   rubric core below.
2. Ground the investigation in the relevant runbook (see "Grounding protocol").
3. Decide Fork A/B/C/D using the four fork definitions in the rubric core and the
   decisive-evidence / fork-decision / stop-conditions of the runbook you loaded.
4. Quote **verbatim** the single decisive row that committed the fork into
   `fork_packet.quoted_rubric_row` — from the runbook you used, or the rubric core.
5. Select exactly one approved symptom tag from the list below and write it
   into `fork_packet.symptom_tag`.
6. If relevant historical tickets were provided, include up to 5 in
   `fork_packet.historical_matches`.
7. Record the runbook you used (or "consulted, ruled out") in
   `fork_packet.runbook_reference` (slug + the decisive section text).
8. Draft a customer reply and internal note in `drafts`. Draft a Jira ticket
   only for Fork A.
9. Emit **only** the final Handoff JSON object as your last message — no prose
   before or after the JSON block.

# Grounding protocol
- The operator's initial hypothesis (if any) is in the turn prompt. Treat it as a
  **soft prior**, not a verdict.
- Pick the runbook for that hypothesis (or, if none was given, the symptom you
  infer from intake) and `Read` it from the `runbooks/` directory in your working
  directory. Ground evidence-gathering and the fork decision in it.
- **Re-steer freely:** if the evidence does not correlate with that runbook, load
  a different runbook or fall back to the rubric core — and record *why* you
  pivoted in `fork_packet.reasoning`.
- If nothing fits, triage on the rubric core alone, tag `[unclassified]` (or
  `[apex]` for general platform behavior), and state plainly that you triaged
  without a specialized runbook.
- The full rubric (`runbooks/fork-rubric.md`) is also staged in your working
  directory if you need a per-symptom class table you have not loaded.

# Available runbooks (domain map)
{domain_map}

# Approved symptom tags (choose exactly one)
{tags}

Do NOT use a vendor tag as a symptom tag — vendor is a history-exclusion
classification only, never a symptom.

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

# Fork Rubric — core (domain-agnostic)
{rubric}
"""

_DEFAULT_RUBRIC_PLACEHOLDER = (
    "[Rubric core not loaded — run build_system_prompt(rubric_core) "
    "to embed the live rubric core before passing to the agent.]"
)

SYSTEM_PROMPT: str = _PROMPT_TEMPLATE.format(
    tags="\n".join(f"  {t}" for t in APPROVED_TAGS_IN_PROMPT),
    domain_map=_DOMAIN_MAP_BLOCK,
    rubric=_DEFAULT_RUBRIC_PLACEHOLDER,
)


def build_system_prompt(rubric_text: str) -> str:
    """Return the system prompt with the live fork-rubric *core* embedded.

    Call this immediately before constructing ClaudeAgentOptions, passing
    `load_rubric().core` so the agent sees the domain-agnostic base plus the
    domain map (the per-symptom tables are read on demand from runbooks/).
    """
    return _PROMPT_TEMPLATE.format(
        tags="\n".join(f"  {t}" for t in APPROVED_TAGS_IN_PROMPT),
        domain_map=_DOMAIN_MAP_BLOCK,
        rubric=rubric_text,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_prompt.py -q`
Expected: PASS (all, including the eight pre-existing tests — role, tags, vendor-exclusion, inconclusive, read-only, JSON contract, `RUBRIC_SENTINEL` injection).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/prompt.py tests/test_agent_prompt.py
git commit -m "feat(prompt): lean rubric core + domain map + grounding protocol" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Stage runbooks into the sandbox

**Files:**
- Modify: `noc_cli/runbooks/__init__.py` (add `stage_runbooks`)
- Modify: `noc_cli/scaffold.py` (add `TicketFolder.runbooks`; call `stage_runbooks`)
- Test: `tests/test_runbooks.py`, `tests/test_scaffold.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runbooks.py`:

```python
def test_stage_runbooks_copies_six_runbooks_and_rubric(tmp_path):
    from noc_cli.runbooks import stage_runbooks

    written = stage_runbooks(tmp_path / "runbooks")
    assert "fork-rubric.md" in written
    for slug in RUNBOOK_SLUGS:
        assert f"{slug}.md" in written
        staged = (tmp_path / "runbooks" / f"{slug}.md").read_text()
        assert len(staged) > 100
    assert (tmp_path / "runbooks" / "fork-rubric.md").read_text().startswith("---")
```

Append to `tests/test_scaffold.py`:

```python
def test_scaffold_stages_runbooks(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    assert folder.runbooks == folder.root / "runbooks"
    assert (folder.runbooks / "low-audio.md").is_file()
    assert (folder.runbooks / "apex.md").is_file()
    assert (folder.runbooks / "fork-rubric.md").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runbooks.py tests/test_scaffold.py -q`
Expected: FAIL — `cannot import name 'stage_runbooks'` and `TicketFolder` has no `runbooks`.

- [ ] **Step 3a: Implement `stage_runbooks`**

In `noc_cli/runbooks/__init__.py`, add `from pathlib import Path` to the imports, then append at the end of the file:

```python
_RUBRIC_PACKAGE = "noc_cli.data"
_RUBRIC_FILENAME = "fork-rubric.md"


def stage_runbooks(dest: Path) -> list[str]:
    """Copy the six runbooks + the full fork-rubric.md into `dest` so the
    sandbox-scoped agent can Read them. Idempotent (overwrites). Returns the
    filenames written, in a stable order."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for slug in RUNBOOK_SLUGS:
        text = _load_runbook(slug)
        if text is None:
            continue
        (dest / f"{slug}.md").write_text(text, encoding="utf-8")
        written.append(f"{slug}.md")
    rubric_res = resources.files(_RUBRIC_PACKAGE).joinpath(_RUBRIC_FILENAME)
    if rubric_res.is_file():
        (dest / _RUBRIC_FILENAME).write_text(
            rubric_res.read_text(encoding="utf-8"), encoding="utf-8"
        )
        written.append(_RUBRIC_FILENAME)
    return written
```

- [ ] **Step 3b: Wire staging into scaffold**

In `noc_cli/scaffold.py`, add the `runbooks` property to `TicketFolder` (after the `state_path` property, line 21):

```python
    @property
    def runbooks(self) -> Path:
        return self.root / "runbooks"
```

Then replace the body of `scaffold_ticket` (lines 44-52) with:

```python
def scaffold_ticket(tickets_root: Path, ticket_id: int | str) -> TicketFolder:
    """Create Tickets/<id>/{logs,pcaps,analysis,runbooks}/ and stage the symptom
    runbooks + fork rubric for the agent to read. Idempotent."""
    root = Path(tickets_root) / str(ticket_id)
    logs = root / "logs"
    pcaps = root / "pcaps"
    analysis = root / "analysis"
    for d in (logs, pcaps, analysis):
        d.mkdir(parents=True, exist_ok=True)
    folder = TicketFolder(root=root, logs=logs, pcaps=pcaps, analysis=analysis)

    from noc_cli.runbooks import stage_runbooks  # noqa: PLC0415

    stage_runbooks(folder.runbooks)
    return folder
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runbooks.py tests/test_scaffold.py -q`
Expected: PASS (including the pre-existing scaffold tests — `logs`/`pcaps`/`analysis` dirs are still created).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/runbooks/__init__.py noc_cli/scaffold.py tests/test_runbooks.py tests/test_scaffold.py
git commit -m "feat(scaffold): stage runbooks + rubric into the ticket sandbox" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Runner — transcript capture + `initial_hypothesis`

**Files:**
- Modify: `noc_cli/agent/runner.py`
- Test: `tests/test_agent_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_runner.py`:

```python
from noc_cli.agent.runner import TranscriptEntry


def _make_multi_turn_query(result_json: str):
    """Yield an assistant reasoning turn, an assistant tool-use turn, then the result."""

    class _Text:
        def __init__(self, text):
            self.text = text

    class _ToolUse:
        def __init__(self, name, inp):
            self.name = name
            self.input = inp

    class _Assistant:
        def __init__(self, content):
            self.content = content

    class _Result:
        def __init__(self, r):
            self.result = r
            self.is_error = False

    async def q(*, prompt, options):
        yield _Assistant([_Text("Reading the ticket and the low-audio runbook.")])
        yield _Assistant([_ToolUse("Read", {"file_path": "runbooks/low-audio.md"})])
        yield _Result(result_json)

    return q


def test_run_agent_captures_transcript(tmp_path):
    folder = scaffold_ticket(tmp_path, 18440)
    q = _make_multi_turn_query(_load_fixture("handoff_good.json"))
    result = _run(
        run_agent(ticket_id=18440, folder=folder, system_prompt="test",
                  history_context="", _query_fn=q)
    )
    assert result.handoff is not None
    kinds = [e.kind for e in result.transcript]
    assert "reasoning" in kinds and "tool" in kinds
    tool = next(e for e in result.transcript if e.kind == "tool")
    assert tool.tool_name == "Read"
    assert "low-audio" in tool.tool_args


def test_run_agent_weaves_initial_hypothesis_into_prompt(tmp_path):
    folder = scaffold_ticket(tmp_path, 18441)
    seen: list[str] = []

    async def capturing(*, prompt, options):
        seen.append(prompt)

        class R:
            result = _load_fixture("handoff_good.json")
            is_error = False

        yield R()

    _run(
        run_agent(ticket_id=18441, folder=folder, system_prompt="test",
                  history_context="", _query_fn=capturing,
                  initial_hypothesis="[low audio]")
    )
    assert "Triage ticket #18441" in seen[0]
    assert "[low audio]" in seen[0]


def test_transcript_stashed_on_double_failure(tmp_path):
    folder = scaffold_ticket(tmp_path, 18442)
    q = _make_multi_turn_query('{"totally": "wrong"}')
    result = _run(
        run_agent(ticket_id=18442, folder=folder, system_prompt="test",
                  history_context="", _query_fn=q)
    )
    assert result.handoff is None
    stash_dir = folder.root / ".debug"
    transcripts = list(stash_dir.glob("transcript-*.jsonl"))
    assert transcripts and transcripts[0].read_text().strip()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_runner.py -q`
Expected: FAIL — `cannot import name 'TranscriptEntry'`.

- [ ] **Step 3: Implement transcript capture + hypothesis**

In `noc_cli/agent/runner.py`:

(a) Change the dataclass import (line 5) to include `asdict` and `field`:

```python
from dataclasses import asdict, dataclass, field
```

(b) Add `TranscriptEntry` and `transcript` to `RunnerResult` — replace the `RunnerResult` dataclass (lines 30-35) with:

```python
@dataclass
class TranscriptEntry:
    """One captured step of the agent's logic stream."""

    kind: str  # "reasoning" | "tool" | "result"
    text: str = ""
    tool_name: str = ""
    tool_args: str = ""


@dataclass
class RunnerResult:
    handoff: Handoff | None
    stash_path: Path | None = None
    raw_result: str = ""
    attempts: int = 0
    transcript: list[TranscriptEntry] = field(default_factory=list)
```

(c) Replace `_collect_result` (lines 71-78) with `_drain` + an arg summarizer:

```python
def _summarize_tool_args(tool_input: dict) -> str:
    """One-line summary of the salient tool argument (path / pattern / command)."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("file_path", "path", "pattern", "command", "query"):
        val = tool_input.get(key)
        if val:
            return str(val)[:160]
    return ""


async def _drain(query_gen) -> tuple[str, list[TranscriptEntry]]:
    """Drain the query async-generator. Return (final result text, transcript).

    The agent SDK streams assistant messages (text + tool_use blocks) and a final
    ResultMessage carrying `.result`. We keep the final result for parsing AND
    retain the intermediate turns as the transcript of the agent's logic.
    """
    raw = ""
    transcript: list[TranscriptEntry] = []
    async for message in query_gen:
        result_text = getattr(message, "result", None)
        if result_text is not None:
            raw = result_text
            transcript.append(TranscriptEntry(kind="result", text=str(result_text)[:4000]))
            continue
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if isinstance(text, str) and text.strip():
                    transcript.append(TranscriptEntry(kind="reasoning", text=text.strip()))
                    continue
                name = getattr(block, "name", None)
                if name:
                    transcript.append(
                        TranscriptEntry(
                            kind="tool",
                            tool_name=str(name),
                            tool_args=_summarize_tool_args(getattr(block, "input", {}) or {}),
                        )
                    )
    return raw, transcript
```

(d) Update `run_agent` — change the signature to add `initial_hypothesis` (after `history_context`, line 85):

```python
async def run_agent(
    ticket_id: int | str,
    folder: TicketFolder,
    system_prompt: str,
    history_context: str,
    initial_hypothesis: str = "",
    _query_fn: Callable | None = None,
) -> RunnerResult:
```

(e) Replace the `full_prompt` assignment (lines 117-125) with a hypothesis-aware version:

```python
    if initial_hypothesis:
        hypothesis_line = (
            f"Analyst's initial hypothesis: {initial_hypothesis} — treat as a "
            f"starting point, not a verdict; re-steer if evidence does not correlate.\n\n"
        )
    else:
        hypothesis_line = (
            "No analyst hypothesis was provided — infer the symptom from intake.\n\n"
        )

    full_prompt = (
        f"Triage ticket #{ticket_id}.\n\n"
        f"{hypothesis_line}"
        f"Historical context (for historical_matches only — do not treat as ground truth):\n"
        f"{history_context}\n\n"
        "The ticket body and comments are in logs/00-ticket.md. Read it and every "
        "other file under logs/, pcaps/, and analysis/. Ground your investigation in "
        "the matching runbook under runbooks/. If no evidence covers the incident "
        "window, return Fork D and list what is missing — do not fabricate. "
        "Emit only the Handoff JSON."
    )
```

(f) Replace the two attempt blocks + the double-failure stash (lines 127-155) with transcript-aware versions:

```python
    # Attempt 1
    gen1 = _query_fn(prompt=full_prompt, options=_make_options())
    raw1, transcript1 = await _drain(gen1)
    handoff = _try_parse(raw1)
    if handoff is not None:
        return RunnerResult(handoff=handoff, raw_result=raw1, attempts=1, transcript=transcript1)

    # Attempt 2 — correction prompt
    correction_prompt = (
        "Your previous response could not be parsed as a valid Handoff JSON object. "
        "Please emit ONLY the Handoff JSON, with no prose before or after. "
        "The top-level keys must be: intake, evidence_preflight, fork_packet, drafts, rubric_version."
    )
    gen2 = _query_fn(prompt=correction_prompt, options=_make_options())
    raw2, transcript2 = await _drain(gen2)
    handoff2 = _try_parse(raw2)
    combined = transcript1 + transcript2
    if handoff2 is not None:
        return RunnerResult(handoff=handoff2, raw_result=raw2, attempts=2, transcript=combined)

    # Double failure — stash raw text + transcript, then abort
    stash_dir = folder.root / ".debug"
    stash_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    stash_path = stash_dir / f"raw-result-{ts}.txt"
    stash_path.write_text(f"# Attempt 1\n{raw1}\n\n# Attempt 2\n{raw2}\n", encoding="utf-8")
    transcript_path = stash_dir / f"transcript-{ts}.jsonl"
    with transcript_path.open("w", encoding="utf-8") as f:
        for entry in combined:
            f.write(json.dumps(asdict(entry)) + "\n")
    return RunnerResult(
        handoff=None, stash_path=stash_path, raw_result=raw2, attempts=2, transcript=combined
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_runner.py -q`
Expected: PASS (including the five pre-existing tests — `_make_fake_query` yields only `.result`, so `_drain` records a single `result` entry and parsing is unchanged).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/runner.py tests/test_agent_runner.py
git commit -m "feat(runner): capture reasoning transcript and accept initial_hypothesis" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Render — `REASONING.md`, consulted slugs, STATE.md additions

**Files:**
- Modify: `noc_cli/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render.py`:

```python
from noc_cli.agent.runner import TranscriptEntry
from noc_cli.render import (
    consulted_runbook_slugs,
    render_reasoning,
    runbook_reference_warnings,
)


def _sample_transcript():
    return [
        TranscriptEntry(kind="reasoning", text="Accepting analyst hypothesis [apex]."),
        TranscriptEntry(kind="tool", tool_name="Read", tool_args="runbooks/apex.md"),
        TranscriptEntry(kind="reasoning", text="Three stations flipped — Fork B."),
        TranscriptEntry(kind="result", text='{"...": "..."}'),
    ]


def test_consulted_runbook_slugs_from_transcript():
    assert consulted_runbook_slugs(_sample_transcript()) == ["apex"]
    assert consulted_runbook_slugs([]) == []


def test_runbook_reference_warnings_flags_mismatch():
    handoff = load_good()  # runbook_reference.slug == "apex"
    assert runbook_reference_warnings(handoff, ["apex"]) == []
    warns = runbook_reference_warnings(handoff, ["low-audio"])
    assert len(warns) == 1 and "apex" in warns[0]


def test_render_reasoning_creates_file(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_reasoning(_sample_transcript(), load_good(), folder)
    md = (folder.root / "REASONING.md").read_text()
    assert "REASONING" in md
    assert "apex" in md  # consulted runbook surfaced
    assert "Fork B" in md
    assert "Decision summary" in md


def test_render_reasoning_handles_none_handoff(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_reasoning(_sample_transcript(), None, folder)
    md = (folder.root / "REASONING.md").read_text()
    assert "REASONING" in md
    assert "unparseable" in md.lower() or "unknown" in md.lower()


def test_state_md_includes_consulted_and_warnings(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    render_handoff(
        load_good(), folder,
        consulted_runbooks=["apex"],
        validator_warnings=["slug mismatch example"],
    )
    state = (folder.root / "STATE.md").read_text()
    assert "Runbooks consulted: apex" in state
    assert "Validator Warnings" in state
    assert "slug mismatch example" in state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_render.py -q`
Expected: FAIL — `cannot import name 'render_reasoning'`.

- [ ] **Step 3: Implement render helpers**

In `noc_cli/render.py`:

(a) Add imports near the top (after line 16). The `TranscriptEntry` import is guarded so `render` does not import `runner` at runtime:

```python
from typing import TYPE_CHECKING

from noc_cli.runbooks import runbook_slug_from_path

if TYPE_CHECKING:
    from noc_cli.agent.runner import TranscriptEntry
```

(b) Update `_render_state` to accept and render the two new optional lists. Replace its signature and `return` (lines 175 and 199) — change `def _render_state(handoff: Handoff, owner: str) -> str:` to:

```python
def _render_state(
    handoff: Handoff,
    owner: str,
    consulted_runbooks: list[str] | None = None,
    validator_warnings: list[str] | None = None,
) -> str:
```

and replace the final `return "\n".join(lines)` of `_render_state` with:

```python
    consulted_runbooks = consulted_runbooks or []
    validator_warnings = validator_warnings or []
    if consulted_runbooks:
        lines += ["", f"**Runbooks consulted:** {', '.join(consulted_runbooks)}"]
    if validator_warnings:
        lines += ["", "## Validator Warnings"]
        lines += [f"- {w}" for w in validator_warnings]
    return "\n".join(lines)
```

(c) Update `render_handoff` to accept and forward the two lists. Change its signature (line 202) to:

```python
def render_handoff(
    handoff: Handoff,
    folder: TicketFolder,
    owner: str = "",
    *,
    consulted_runbooks: list[str] | None = None,
    validator_warnings: list[str] | None = None,
) -> None:
```

and change the `"STATE.md"` entry of `content_map` (line 219) to:

```python
        "STATE.md": _render_state(handoff, owner, consulted_runbooks, validator_warnings),
```

(d) Append the new public helpers at the end of the file:

```python
def consulted_runbook_slugs(transcript: "list[TranscriptEntry]") -> list[str]:
    """Ordered, de-duplicated runbook slugs the agent actually read."""
    out: list[str] = []
    for entry in transcript:
        if getattr(entry, "kind", "") != "tool":
            continue
        if getattr(entry, "tool_name", "") not in ("Read", "Glob", "Grep"):
            continue
        slug = runbook_slug_from_path(getattr(entry, "tool_args", ""))
        if slug and slug not in out:
            out.append(slug)
    return out


def runbook_reference_warnings(handoff: Handoff, consulted: list[str]) -> list[str]:
    """Soft-warn (never reject) when the cited runbook was never opened."""
    ref_slug = handoff.fork_packet.runbook_reference.slug
    if ref_slug and consulted and ref_slug not in consulted:
        return [
            f"runbook_reference.slug '{ref_slug}' was not among the runbooks actually "
            f"read ({', '.join(consulted)})"
        ]
    return []


def render_reasoning(
    transcript: "list[TranscriptEntry]",
    handoff: Handoff | None,
    folder: TicketFolder,
) -> None:
    """Write REASONING.md — the chronological transcript of the agent's logic plus
    a decision summary. Renders even when `handoff is None` (parse failure)."""
    consulted = consulted_runbook_slugs(transcript)
    if handoff is not None:
        fp = handoff.fork_packet
        ticket = handoff.intake.ticket_id
        hypothesis = handoff.intake.initial_hypothesis or "(none)"
        final = f"Fork {fp.fork_letter.value} · {fp.symptom_tag} · {fp.confidence.value}"
    else:
        ticket = folder.root.name
        hypothesis = "(unknown — handoff failed to parse)"
        final = "(no handoff — agent output was unparseable)"

    turns = sum(1 for e in transcript if getattr(e, "kind", "") in ("reasoning", "tool"))
    lines = [
        f"# REASONING — Ticket #{ticket}",
        "",
        f"**Hypothesis:** {hypothesis} → **Final:** {final}",
        f"**Runbooks consulted:** {', '.join(consulted) if consulted else '(none)'} · "
        f"**Turns:** {turns}",
        "",
        "---",
        "",
        "## Transcript",
        "",
    ]
    step = 0
    for entry in transcript:
        kind = getattr(entry, "kind", "")
        if kind == "reasoning":
            step += 1
            lines += [f"**{step}.** {getattr(entry, 'text', '')}", ""]
        elif kind == "tool":
            args = getattr(entry, "tool_args", "")
            lines += [f"> 🔧 {getattr(entry, 'tool_name', '')} {args}".rstrip(), ""]

    if handoff is not None:
        fp = handoff.fork_packet
        decisive = "; ".join(handoff.evidence_preflight.decisive_evidence) or "(none)"
        lines += [
            "---",
            "",
            "## Decision summary (from handoff)",
            "",
            f"- **Decisive evidence:** {decisive}",
            f"- **Fork:** {fp.fork_letter.value} · {fp.confidence.value}",
            f"- **Reasoning:** {fp.reasoning}",
        ]

    (folder.root / "REASONING.md").write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS (including the ten pre-existing render tests — `render_handoff`'s new params are keyword-only with defaults).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/render.py tests/test_render.py
git commit -m "feat(render): REASONING.md transcript + consulted-runbook STATE.md surfacing" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Seed resolution module

**Files:**
- Create: `noc_cli/seed.py`
- Test: `tests/test_seed.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_seed.py`:

```python
import pytest

from noc_cli.seed import menu_lines, resolve_seed


def test_suspect_slug_maps_to_tag():
    assert resolve_seed("low-audio", interactive=False) == "[low audio]"
    assert resolve_seed("apex", interactive=False) == "[apex]"


def test_unknown_suspect_raises():
    with pytest.raises(ValueError):
        resolve_seed("nonsense", interactive=False)


def test_non_interactive_no_suspect_returns_empty():
    assert resolve_seed(None, interactive=False) == ""


def test_interactive_number_maps_to_tag():
    tag = resolve_seed(
        None, interactive=True, prompt_fn=lambda _label: "4", echo_fn=lambda _msg: None
    )
    assert tag == "[low audio]"  # 4th entry in DOMAIN_MAP order


def test_interactive_not_sure_returns_empty():
    tag = resolve_seed(
        None, interactive=True, prompt_fn=lambda _label: "7", echo_fn=lambda _msg: None
    )
    assert tag == ""


def test_menu_lines_list_all_labels_and_domains():
    text = "\n".join(menu_lines())
    assert "SIP / UC" in text
    assert "Audio / media quality" in text
    assert "Not sure" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_seed.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.seed'`.

- [ ] **Step 3: Implement `noc_cli/seed.py`**

Create `noc_cli/seed.py`:

```python
from __future__ import annotations

from typing import Callable

from noc_cli.runbooks import DOMAIN_MAP

_NOT_SURE_LABEL = "Not sure — let the agent decide"


def slug_to_tag(slug: str) -> str | None:
    for s in DOMAIN_MAP:
        if s.slug == slug:
            return s.tag
    return None


def menu_lines() -> list[str]:
    """Numbered selection menu grouped by domain, with a final 'not sure' option."""
    lines: list[str] = []
    last_domain: str | None = None
    n = 0
    for s in DOMAIN_MAP:
        if s.domain != last_domain:
            lines.append(f"  {s.domain}")
            last_domain = s.domain
        n += 1
        lines.append(f"    {n}) {s.label}")
    lines.append(f"    {n + 1}) {_NOT_SURE_LABEL}")
    return lines


def _choice_to_tag(choice: str) -> str:
    """Map a 1-based menu number to a symptom tag; '' for not-sure / invalid."""
    try:
        idx = int((choice or "").strip())
    except ValueError:
        return ""
    if 1 <= idx <= len(DOMAIN_MAP):
        return DOMAIN_MAP[idx - 1].tag
    return ""


def resolve_seed(
    suspect: str | None,
    *,
    interactive: bool,
    prompt_fn: Callable[[str], str] | None = None,
    echo_fn: Callable[[str], None] | None = None,
) -> str:
    """Resolve the analyst's symptom seed to an approved tag (or '' for none).

    - `suspect` (from --suspect): a runbook slug; raises ValueError if unknown.
    - else if `interactive` and `prompt_fn`: print the menu and read a number.
    - else: '' (no seed) — the --fixture / --no-agent / non-TTY path.
    """
    if suspect:
        tag = slug_to_tag(suspect.strip())
        if tag is None:
            valid = ", ".join(s.slug for s in DOMAIN_MAP)
            raise ValueError(f"unknown --suspect {suspect!r}; valid slugs: {valid}")
        return tag
    if not interactive or prompt_fn is None:
        return ""
    if echo_fn is not None:
        echo_fn("What do you suspect this ticket is? (you can change course mid-investigation)")
        for line in menu_lines():
            echo_fn(line)
    return _choice_to_tag(prompt_fn("Selection"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_seed.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/seed.py tests/test_seed.py
git commit -m "feat(seed): analyst symptom-seed resolution (menu + --suspect)" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: CLI wiring

**Files:**
- Modify: `noc_cli/cli.py`
- Test: `tests/test_cli_investigate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_investigate.py`:

```python
def test_investigate_invalid_suspect_exits_2(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["investigate", "18432", "--no-agent", "--suspect", "bogus"])
    assert result.exit_code == 2, result.output


def test_investigate_valid_suspect_no_agent_ok(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["investigate", "18432", "--no-agent", "--suspect", "low-audio"])
    assert result.exit_code == 0, result.output


def test_investigate_fixture_writes_reasoning_md(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["investigate", "18432", "--fixture", str(FIXTURES)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "18432" / "REASONING.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_investigate.py -q`
Expected: FAIL — `--suspect` is an unknown option / `REASONING.md` not written.

- [ ] **Step 3a: Add the `--suspect` option and resolve the seed**

In `noc_cli/cli.py`, add the `suspect` option to the `investigate` command signature (after the `no_agent` option, around line 182):

```python
    suspect: Optional[str] = typer.Option(
        None,
        "--suspect",
        help="Runbook slug to seed grounding (e.g. low-audio); skips the interactive prompt.",
    ),
```

Replace the body of `investigate` (lines 185-197) with the seed-resolving version:

```python
    """Run the L3 agent investigation on a Zendesk ticket and produce a triage handoff."""
    import sys

    from noc_cli.seed import resolve_seed

    branding.render_banner()
    interactive = sys.stdin.isatty() and fixture is None and not no_agent
    try:
        initial_hypothesis = resolve_seed(
            suspect,
            interactive=interactive,
            prompt_fn=lambda label: typer.prompt(label, default=""),
            echo_fn=typer.echo,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    asyncio.run(
        _run_investigate(
            ticket_id=ticket_id,
            extra_files=list(file),
            pastes=paste,
            force=force,
            fixture=fixture,
            no_agent=no_agent,
            initial_hypothesis=initial_hypothesis,
            verbose=verbose,
        )
    )
```

- [ ] **Step 3b: Thread `initial_hypothesis` through `_run_investigate`**

Change the `_run_investigate` signature (lines 200-208) to add the parameter:

```python
async def _run_investigate(
    ticket_id: int,
    extra_files: list[Path],
    pastes: list[str],
    force: bool,
    fixture: Optional[Path],
    no_agent: bool,
    initial_hypothesis: str,
    verbose: bool,
) -> None:
```

- [ ] **Step 3c: Seed history on the analyst's tag**

Replace the hardcoded seed line (line 320) — change:

```python
        symptom_tag = "[unclassified]"  # refined by the agent; default for seeding
```

to:

```python
        symptom_tag = initial_hypothesis or "[unclassified]"  # analyst seed; agent re-steers
```

- [ ] **Step 3d: Use the rubric core and pass the hypothesis to the agent**

In the agent branch (lines 346-358), change the rubric line and the `run_agent` call:

```python
        rubric = load_rubric()
        system_prompt = build_system_prompt(rubric.core)
        runner_result = await run_agent(
            ticket_id=ticket_id,
            folder=folder,
            system_prompt=system_prompt,
            history_context=history_context,
            initial_hypothesis=initial_hypothesis,
        )
        handoff = runner_result.handoff
        if handoff is None:
            from noc_cli.render import render_reasoning

            try:
                render_reasoning(runner_result.transcript, None, folder)
            except Exception:
                pass
            console.print(
                f"[red]Agent failed after 2 attempts. Raw output stashed to:[/red] "
                f"{runner_result.stash_path}"
            )
            raise typer.Exit(code=1)
        tracker.mark_done("Agent completed")
```

- [ ] **Step 3e: Capture the transcript for both branches and render reasoning**

The fixture branch has no agent run, so it has no transcript. Introduce a `transcript` variable. In the fixture branch (lines 337-345), after `tracker.mark_done("Fixture handoff loaded")`, add:

```python
        transcript = []
```

and in the agent branch, after `tracker.mark_done("Agent completed")`, add:

```python
        transcript = runner_result.transcript
```

Then replace the RENDER block (lines 368-371) with the consulted/warnings-aware render:

```python
    # ── Render (owner recorded → drives the soft-lock on re-run) ─────────────
    tracker.set_phase(InvestigatePhase.RENDER)
    from noc_cli.render import (
        consulted_runbook_slugs,
        render_reasoning,
        runbook_reference_warnings,
    )

    consulted = consulted_runbook_slugs(transcript)
    warnings = runbook_reference_warnings(handoff, consulted)
    render_handoff(
        handoff, folder, owner=owner,
        consulted_runbooks=consulted, validator_warnings=warnings,
    )
    try:
        render_reasoning(transcript, handoff, folder)
    except Exception:
        pass
    tracker.mark_done("Report rendered")
```

(Note: `render_handoff` and `render_reasoning` are imported here; the existing
`from noc_cli.render import render_handoff` at line 334 can stay or be removed —
leaving it is harmless.)

- [ ] **Step 4: Run the investigate tests to verify they pass**

Run: `python -m pytest tests/test_cli_investigate.py -q`
Expected: PASS (including the pre-existing no-agent / fixture / soft-lock tests — under `CliRunner`, `sys.stdin.isatty()` is False so the prompt never fires).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/cli.py tests/test_cli_investigate.py
git commit -m "feat(cli): wire analyst seed, rubric core, and REASONING.md into investigate" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Pivot fixture + end-to-end reasoning test

**Files:**
- Create: `tests/fixtures/handoff_pivot.json`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`:

```python
def test_reasoning_md_shows_pivot(tmp_path):
    folder = scaffold_ticket(tmp_path, 1234)
    handoff = Handoff.model_validate(json.loads((FIXTURES / "handoff_pivot.json").read_text()))
    transcript = [
        TranscriptEntry(kind="reasoning", text="Accepting analyst hypothesis [low audio]."),
        TranscriptEntry(kind="tool", tool_name="Read", tool_args="runbooks/low-audio.md"),
        TranscriptEntry(kind="reasoning", text="RTP healthy; leaving low-audio — user error."),
    ]
    render_reasoning(transcript, handoff, folder)
    md = (folder.root / "REASONING.md").read_text()
    assert "low audio" in md.lower()
    assert "Fork C" in md
    assert "[unclassified]" in md
    assert "low-audio" in md  # runbook consulted then ruled out


def test_pivot_handoff_state_is_unclassified_fork_c(tmp_path):
    folder = scaffold_ticket(tmp_path, 1234)
    handoff = Handoff.model_validate(json.loads((FIXTURES / "handoff_pivot.json").read_text()))
    render_handoff(handoff, folder)
    state = (folder.root / "STATE.md").read_text()
    assert 'fork: "C"' in state
    assert "[unclassified]" in state
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_render.py::test_reasoning_md_shows_pivot -q`
Expected: FAIL — `handoff_pivot.json` does not exist.

- [ ] **Step 3: Create the pivot fixture**

Create `tests/fixtures/handoff_pivot.json`:

```json
{
  "rubric_version": "2026-05-13",
  "intake": {
    "ticket_id": 1234,
    "url": "https://carbyne.zendesk.com/agent/tickets/1234",
    "status": "open",
    "tags": ["audio"],
    "requester": "PSAP Ops",
    "organization": "Example 911",
    "one_line_fingerprint": "Example / suspected low audio / vague complaint",
    "ticket_summary": ["Caller said a recent 911 call 'sounded bad'; no Call-ID, no timestamp."],
    "context_pulls": [],
    "initial_hypothesis": "[low audio] — suspected media quality",
    "intake_decision": "ready_for_evidence_preflight"
  },
  "evidence_preflight": {
    "gathered": [
      {
        "evidence_type": "PCAP",
        "source": "uploaded capture",
        "time_window": "approximate",
        "summary": "RTP present end-to-end; timestamps healthy; no jitter spikes; no renderer hang"
      }
    ],
    "decisive_evidence": [
      "RTP present, timestamps healthy",
      "No station-side renderer hang or heap spike",
      "No matching REP-class regression in the window"
    ],
    "missing_or_non_decisive": [
      "Complaint is vague; no Call-ID; no reproduction steps"
    ]
  },
  "fork_packet": {
    "fork_letter": "C",
    "confidence": "Medium",
    "symptom_tag": "[unclassified]",
    "rubric_class": "",
    "quoted_rubric_row": "",
    "reasoning": "Suspected low audio. Loaded the low-audio runbook; RTP is present and healthy, no station renderer hang, and no matching REP-class regression. The complaint is vague with no Call-ID and no reproduction. None of the low-audio fork rows correlate. Reclassified as an ill-defined user report — Fork C self-resolve.",
    "evidence_summary": [
      "RTP healthy across the call lifecycle",
      "No engineering signal on the station side"
    ],
    "missing_evidence": [],
    "runbook_reference": {
      "slug": "low-audio",
      "section": "Consulted, ruled out — RTP present and healthy; no fork-row match. See low-audio decisive-evidence checklist."
    },
    "historical_matches": [],
    "related_zendesk": [],
    "related_jira": []
  },
  "drafts": {
    "customer_reply": "Hi team, we reviewed the available media for this call. The audio path looks healthy on our side. Could you share the exact call time and number so we can pull the specific call record and investigate further?",
    "internal_note": "Suspected low audio; RTP healthy, no station errors. Reclassified Fork C — ill-defined complaint. Requested Call-ID + timestamp from customer.",
    "jira_draft": null
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Run the FULL suite + commit**

Run: `python -m pytest -q`
Expected: PASS — all tests, no regressions.

```bash
git add tests/fixtures/handoff_pivot.json tests/test_render.py
git commit -m "test(reasoning): pivot fixture proving hypothesis→rule-out narrative" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (spec §-by-§):
- §4 layered grounding → Task 2 (`Rubric.core` = Layer 1), Task 3 (prompt embeds core + domain map; protocol covers Layers 2 & 3).
- §5 A-guided / soft prior → Task 5 (`initial_hypothesis` woven as "starting point, not a verdict") + Task 7/8 (seed) + Task 3 (re-steer/pivot instructions).
- §6 flow → Task 8 (seed prompt after banner; history seeded on tag; render handoff + reasoning).
- §7.1 seeding UX → Task 7 (`menu_lines`, six grouped symptoms + "not sure") + Task 8 (`--suspect`, isatty/fixture/no-agent skip).
- §7.2 lean base + domain map → Task 2 + Task 3 (caller passes `rubric.core`).
- §7.3 sandbox staging → Task 4 (`stage_runbooks`, `TicketFolder.runbooks`).
- §7.4 grounding protocol / turn-prompt seed → Task 3 + Task 5.
- §7.5 pivot trail / fallback → Task 9 fixture asserts the rule-out narrative; Task 3 protocol.
- §7.6 inspectability / `REASONING.md` (curated + `.debug` stash, renders on failure) → Task 5 (transcript + `.debug/transcript-*.jsonl`) + Task 6 (`render_reasoning`) + Task 8 (failure-path render).
- §7.7 no schema changes → confirmed; only existing fields used.
- §8 touchpoints → Tasks 1-9 cover every row; `harness.py` intentionally unchanged (transcript supplies consulted runbooks — documented in File Structure).
- §9 testing → each task is TDD; full-suite gate in Task 9 Step 5.
- §10 error handling → `render_reasoning` wrapped in try/except (Task 8); `--suspect` validation → exit 2 (Task 8); soft-warn only via `runbook_reference_warnings` (Task 6), never rejecting.

**Placeholder scan:** none — every code/test step contains complete content.

**Type consistency:** `TranscriptEntry(kind, text, tool_name, tool_args)` defined in Task 5 and consumed identically in Tasks 6 & 9. `Symptom(tag, slug, domain, label)` defined in Task 1, used in Tasks 3 & 7. `DOMAIN_MAP`, `runbook_slug_from_path`, `stage_runbooks`, `Rubric.core`, `build_system_prompt(rubric_text)`, `render_reasoning(transcript, handoff, folder)`, `consulted_runbook_slugs`, `runbook_reference_warnings`, `resolve_seed(...)` — signatures match across all referencing tasks. `render_handoff`'s new params are keyword-only with defaults, preserving every existing call site.
