# Backlog Scout Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the headless Backlog Scout pipeline — discover stale Tier-1 NOC tickets, pre-screen their triability against runbooks, synthesize a ranked report, and (on explicit confirm) assign + investigate one — exposed via a `noc scout` CLI command.

**Architecture:** A three-stage funnel: (1) pure-Python staleness ranking over Zendesk view metadata — no agent; (2) a bounded `asyncio.Semaphore(3)` fan-out of fresh Haiku 4.5 `query()` calls, one per top-K candidate, each emitting a `ScreenReport`; (3) a single Opus 4.8 high-effort synthesis call folding the reports into a ranked `ScoutReport`. The agent stays 100% read-only (existing `build_hooks` sandbox); the lone Zendesk write (assign) lives in an isolated `ZendeskWriter`, invoked only behind a confirm in the CLI. This plan is **Plan 1 of 2**; the in-`watch` TUI panel is a follow-on plan that wraps `run_scout`.

**Tech Stack:** Python 3.11, pydantic v2, `claude-agent-sdk` 0.2.88 (`ClaudeAgentOptions.model`/`effort`), httpx, Typer, pytest.

---

## File Structure

- Create `noc_cli/scout/__init__.py` — package marker.
- Create `noc_cli/scout/models.py` — `Candidate`, `ScreenReport`, `RankedCandidate`, `ScoutReport`.
- Create `noc_cli/scout/rank.py` — Stage 1: `rank_candidates()` (pure arithmetic).
- Create `noc_cli/scout/profiles.py` — `Profile`, `SCREEN`, `SYNTHESIS`, `build_options()`.
- Create `noc_cli/scout/workspace.py` — `materialize_workspace()` (stage runbooks for the read-only agent).
- Create `noc_cli/scout/screen.py` — Stage 2: `screen_ticket()`, `screen_candidates()`.
- Create `noc_cli/scout/synthesize.py` — Stage 3: `synthesize()` with deterministic fallback.
- Create `noc_cli/scout/runner.py` — `run_scout()` orchestrator (read-only).
- Create `noc_cli/scout/acquire.py` — `ZendeskWriter.assign_ticket()` (the only Zendesk write).
- Create `noc_cli/scout/render.py` — `render_scout_report()` terminal table.
- Modify `noc_cli/models.py` — add `priority` to `Ticket`.
- Modify `noc_cli/zendesk.py` — extract `build_auth_header()` (DRY for the writer).
- Modify `noc_cli/config.py` — add `scout_view` field + `NOC_SCOUT_VIEW` env mapping.
- Modify `noc_cli/cli.py` — add the `scout` command.
- Create tests under `tests/` mirroring each module.

---

## Task 1: Add `priority` to the Ticket model

Stage-1 ranking weights tickets by Zendesk priority, which the view endpoint returns but the model currently drops.

**Files:**
- Modify: `noc_cli/models.py:24-37` (the `Ticket` class)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
from noc_cli.models import Ticket


def test_ticket_captures_priority():
    t = Ticket.model_validate({"id": 1, "subject": "x", "priority": "high"})
    assert t.priority == "high"


def test_ticket_priority_defaults_empty():
    t = Ticket.model_validate({"id": 2})
    assert t.priority == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::test_ticket_captures_priority -v`
Expected: FAIL — `assert '' == 'high'` (field is silently dropped today).

- [ ] **Step 3: Add the field**

In `noc_cli/models.py`, inside `class Ticket`, add after the `status` line (line 33):

```python
    priority: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS (both new tests + existing).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/models.py tests/test_models.py
git commit -m "feat(models): capture ticket priority for scout ranking"
```

---

## Task 2: Scout result models

The typed contracts every stage produces. `extra="ignore"` mirrors the existing `IntakeBlock`/`Handoff` tolerance for chatty agent JSON.

**Files:**
- Create: `noc_cli/scout/__init__.py`
- Create: `noc_cli/scout/models.py`
- Test: `tests/test_scout_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_models.py`:

```python
from noc_cli.scout.models import Candidate, RankedCandidate, ScoutReport, ScreenReport


def test_candidate_defaults():
    c = Candidate(ticket_id=10)
    assert c.score == 0.0
    assert c.staleness_days == 0


def test_screen_report_ignores_extra_keys():
    r = ScreenReport.model_validate(
        {
            "ticket_id": 10,
            "runbook_id": "low-audio",
            "runbook_match_confidence": 0.8,
            "resolvable": True,
            "missing_evidence": ["pcap"],
            "one_line": "ok",
            "chatter": "ignored",
        }
    )
    assert r.runbook_id == "low-audio"
    assert r.missing_evidence == ["pcap"]


def test_scout_report_holds_ranked_list():
    report = ScoutReport(
        ranked=[RankedCandidate(ticket_id=10, rank=1, rationale="why")]
    )
    assert report.ranked[0].rank == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout'`.

- [ ] **Step 3: Create the package and models**

Create `noc_cli/scout/__init__.py`:

```python
```

(empty file — package marker)

Create `noc_cli/scout/models.py`:

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Candidate(BaseModel):
    """Stage-1 output: a stale ticket worth screening, ranked by metadata only."""

    ticket_id: int
    subject: str = ""
    status: str = ""
    priority: str = ""
    staleness_days: int = 0
    score: float = 0.0


class ScreenReport(BaseModel):
    """Stage-2 output: one Haiku triability pre-screen, parsed from agent JSON."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: int
    runbook_id: str = ""
    runbook_match_confidence: float = 0.0  # 0.0–1.0
    resolvable: bool = False
    missing_evidence: list[str] = Field(default_factory=list)
    one_line: str = ""


class RankedCandidate(BaseModel):
    """One entry in the synthesized ranking shown to the engineer."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: int
    rank: int
    rationale: str = ""
    runbook_id: str = ""
    runbook_match_confidence: float = 0.0
    missing_evidence: list[str] = Field(default_factory=list)


class ScoutReport(BaseModel):
    """Stage-3 output: the ranked deliverable."""

    model_config = ConfigDict(extra="ignore")

    generated_at: datetime | None = None
    ranked: list[RankedCandidate] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scout_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/__init__.py noc_cli/scout/models.py tests/test_scout_models.py
git commit -m "feat(scout): result models for the three-stage pipeline"
```

---

## Task 3: Stage 1 — staleness ranking (no agent)

Pure arithmetic over view metadata. Drops assigned, closed/solved, and undated tickets; ranks the rest by `staleness_days × priority_weight`.

**Files:**
- Create: `noc_cli/scout/rank.py`
- Test: `tests/test_scout_rank.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_rank.py`:

```python
from datetime import datetime, timedelta, timezone

from noc_cli.models import Ticket
from noc_cli.scout.rank import rank_candidates

NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _ticket(tid, *, days_stale, priority="normal", status="open", assignee=None):
    return Ticket(
        id=tid,
        subject=f"ticket {tid}",
        status=status,
        priority=priority,
        assignee_id=assignee,
        updated_at=NOW - timedelta(days=days_stale),
    )


def test_ranks_by_staleness_times_priority():
    tickets = [
        _ticket(1, days_stale=2, priority="urgent"),  # 2 * 4 = 8
        _ticket(2, days_stale=10, priority="low"),     # 10 * 1 = 10
        _ticket(3, days_stale=3, priority="normal"),   # 3 * 2 = 6
    ]
    ranked = rank_candidates(tickets, now=NOW, top_k=5)
    assert [c.ticket_id for c in ranked] == [2, 1, 3]
    assert ranked[0].score == 10.0
    assert ranked[0].staleness_days == 10


def test_drops_assigned_and_closed_and_undated():
    tickets = [
        _ticket(1, days_stale=5, assignee=999),        # assigned -> drop
        _ticket(2, days_stale=5, status="solved"),     # closed -> drop
        Ticket(id=3, subject="no date", status="open"),  # no updated_at -> drop
        _ticket(4, days_stale=5),                       # keep
    ]
    ranked = rank_candidates(tickets, now=NOW, top_k=5)
    assert [c.ticket_id for c in ranked] == [4]


def test_top_k_truncates():
    tickets = [_ticket(i, days_stale=i) for i in range(1, 11)]
    ranked = rank_candidates(tickets, now=NOW, top_k=3)
    assert len(ranked) == 3
    assert [c.ticket_id for c in ranked] == [10, 9, 8]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_rank.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout.rank'`.

- [ ] **Step 3: Implement the ranker**

Create `noc_cli/scout/rank.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from noc_cli.models import Ticket
from noc_cli.scout.models import Candidate

_PRIORITY_WEIGHT = {"urgent": 4.0, "high": 3.0, "normal": 2.0, "low": 1.0}
_ACTIVE_STATUSES = {"new", "open", "pending", "hold"}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rank_candidates(
    tickets: list[Ticket], *, now: datetime, top_k: int = 8
) -> list[Candidate]:
    """Stage 1: rank unassigned, active, stale tickets by staleness × priority.

    Pure metadata arithmetic — no agent, no tokens. Tickets that are already
    assigned, closed/solved, or missing ``updated_at`` are dropped. Ties break
    on raw staleness so a long-idle low-priority ticket still surfaces.
    """
    now_utc = _as_utc(now)
    candidates: list[Candidate] = []
    for t in tickets:
        if t.assignee_id is not None:
            continue
        if t.status and t.status.lower() not in _ACTIVE_STATUSES:
            continue
        updated = _as_utc(t.updated_at)
        if updated is None:
            continue
        staleness_days = max((now_utc - updated).days, 0)
        weight = _PRIORITY_WEIGHT.get((t.priority or "").lower(), 1.0)
        candidates.append(
            Candidate(
                ticket_id=t.id,
                subject=t.subject,
                status=t.status,
                priority=t.priority,
                staleness_days=staleness_days,
                score=staleness_days * weight,
            )
        )
    candidates.sort(key=lambda c: (c.score, c.staleness_days), reverse=True)
    return candidates[:top_k]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scout_rank.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/rank.py tests/test_scout_rank.py
git commit -m "feat(scout): stage-1 staleness ranking (pure arithmetic)"
```

---

## Task 4: Per-role agent profiles

Frozen `(model, effort, max_turns, allowed_tools)` factories. `SCREEN` = Haiku 4.5, read-only tools; `SYNTHESIS` = Opus 4.8, `effort="high"`, no tools. `build_options()` lazily constructs `ClaudeAgentOptions` and accepts an injected class for testing without the SDK.

**Files:**
- Create: `noc_cli/scout/profiles.py`
- Test: `tests/test_scout_profiles.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_profiles.py`:

```python
from noc_cli.scout.profiles import SCREEN, SYNTHESIS, build_options


def test_profile_model_and_effort():
    assert SCREEN.model == "claude-haiku-4-5"
    assert SCREEN.effort == "medium"
    assert "Read" in SCREEN.allowed_tools
    assert "Write" not in SCREEN.allowed_tools  # screening never writes
    assert SYNTHESIS.model == "claude-opus-4-8"
    assert SYNTHESIS.effort == "high"
    assert SYNTHESIS.allowed_tools == ()


def test_build_options_passes_model_and_effort():
    captured = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    build_options(
        SYNTHESIS,
        system_prompt="sp",
        cwd="/tmp/ws",
        hooks={"x": 1},
        options_cls=FakeOptions,
    )
    assert captured["model"] == "claude-opus-4-8"
    assert captured["effort"] == "high"
    assert captured["permission_mode"] == "bypassPermissions"
    assert captured["cwd"] == "/tmp/ws"
    assert captured["hooks"] == {"x": 1}
    assert captured["allowed_tools"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout.profiles'`.

- [ ] **Step 3: Implement the profiles**

Create `noc_cli/scout/profiles.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

# Stage-2 screening reads runbooks; it never writes (unlike investigate).
SCREEN_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "LS", "Bash")


@dataclass(frozen=True)
class Profile:
    """A frozen per-role agent profile: model + effort + turn cap + tools."""

    model: str
    effort: str
    max_turns: int
    allowed_tools: tuple[str, ...]


SCREEN = Profile(
    model="claude-haiku-4-5",
    effort="medium",
    max_turns=12,
    allowed_tools=SCREEN_TOOLS,
)

SYNTHESIS = Profile(
    model="claude-opus-4-8",
    effort="high",
    max_turns=6,
    allowed_tools=(),  # operates on in-prompt gap-reports; no tools needed
)


def build_options(
    profile: Profile,
    *,
    system_prompt: str,
    cwd,
    hooks=None,
    options_cls=None,
):
    """Build ClaudeAgentOptions for *profile*. ``options_cls`` is injected in
    tests; in production the SDK class is imported lazily (the SDK spawns a
    subprocess, so we never import it at module load)."""
    if options_cls is None:
        from claude_agent_sdk import ClaudeAgentOptions as options_cls  # noqa: PLC0415

    kwargs = dict(
        system_prompt=system_prompt,
        allowed_tools=list(profile.allowed_tools),
        permission_mode="bypassPermissions",
        max_turns=profile.max_turns,
        model=profile.model,
        effort=profile.effort,
        cwd=str(cwd),
    )
    if hooks is not None:
        kwargs["hooks"] = hooks
    return options_cls(**kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scout_profiles.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/profiles.py tests/test_scout_profiles.py
git commit -m "feat(scout): per-role model/effort profiles (Haiku screen, Opus synth)"
```

---

## Task 5: Scout workspace — materialize runbooks

The screening agent reads runbooks from disk under a sandboxed cwd. Reuse the existing `stage_runbooks` so Scout and `investigate` ground on identical runbooks.

**Files:**
- Create: `noc_cli/scout/workspace.py`
- Test: `tests/test_scout_workspace.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_workspace.py`:

```python
from noc_cli.scout.workspace import materialize_workspace


def test_materialize_stages_runbooks(tmp_path):
    runbooks_dir = materialize_workspace(tmp_path)
    assert runbooks_dir == tmp_path / "runbooks"
    staged = {p.name for p in runbooks_dir.glob("*.md")}
    assert "low-audio.md" in staged
    assert "dropped-calls.md" in staged


def test_materialize_is_idempotent(tmp_path):
    materialize_workspace(tmp_path)
    runbooks_dir = materialize_workspace(tmp_path)  # second call must not raise
    assert (runbooks_dir / "low-audio.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_workspace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout.workspace'`.

- [ ] **Step 3: Implement the workspace**

Create `noc_cli/scout/workspace.py`:

```python
from __future__ import annotations

from pathlib import Path

from noc_cli.runbooks import stage_runbooks


def materialize_workspace(root: Path) -> Path:
    """Stage the embedded runbooks under ``root/runbooks`` and return that dir.

    The Stage-2 screening agent runs with ``cwd=root`` under the read-only hook
    sandbox, so it can Read the runbooks but nothing outside ``root``. Idempotent.
    """
    root.mkdir(parents=True, exist_ok=True)
    runbooks_dir = root / "runbooks"
    runbooks_dir.mkdir(parents=True, exist_ok=True)
    stage_runbooks(runbooks_dir)
    return runbooks_dir
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scout_workspace.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/workspace.py tests/test_scout_workspace.py
git commit -m "feat(scout): materialize runbooks into the screening workspace"
```

---

## Task 6: Stage 2 — bounded Haiku triability pre-screen

One fresh `query()` per candidate (rot-proof), bounded by `asyncio.Semaphore`. Each run emits a `ScreenReport`; parse failures are dropped, not fatal. `query_fn` and `options_factory` are injected for tests.

**Files:**
- Create: `noc_cli/scout/screen.py`
- Test: `tests/test_scout_screen.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_screen.py`:

```python
import asyncio

from noc_cli.scout.models import Candidate
from noc_cli.scout.screen import screen_candidates, screen_ticket


def _run(coro):
    return asyncio.run(coro)


def _fake_query(result_json: str):
    async def fake_query(*, prompt, options):
        class FakeResult:
            result = result_json

        yield FakeResult()

    return fake_query


GOOD = (
    '{"ticket_id": 42, "runbook_id": "low-audio", '
    '"runbook_match_confidence": 0.7, "resolvable": true, '
    '"missing_evidence": ["pcap"], "one_line": "matches low-audio"}'
)


def test_screen_ticket_parses_report(tmp_path):
    cand = Candidate(ticket_id=42, subject="audio dropouts")
    report = _run(
        screen_ticket(
            cand,
            runbooks_dir=tmp_path / "runbooks",
            query_fn=_fake_query(GOOD),
            options_factory=lambda: None,
        )
    )
    assert report is not None
    assert report.runbook_id == "low-audio"
    assert report.resolvable is True


def test_screen_ticket_returns_none_on_garbage(tmp_path):
    cand = Candidate(ticket_id=42)
    report = _run(
        screen_ticket(
            cand,
            runbooks_dir=tmp_path / "runbooks",
            query_fn=_fake_query("not json at all"),
            options_factory=lambda: None,
        )
    )
    assert report is None


def test_screen_candidates_drops_failures_and_bounds_concurrency(tmp_path):
    cands = [Candidate(ticket_id=i) for i in range(5)]
    live = 0
    peak = 0

    async def fake_query(*, prompt, options):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1

        class FakeResult:
            result = GOOD

        yield FakeResult()

    reports = _run(
        screen_candidates(
            cands,
            runbooks_dir=tmp_path / "runbooks",
            query_fn=fake_query,
            concurrency=2,
            options_factory=lambda: None,
        )
    )
    assert len(reports) == 5
    assert peak <= 2  # semaphore held the line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout.screen'`.

- [ ] **Step 3: Implement the screener**

Create `noc_cli/scout/screen.py`:

```python
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from noc_cli.scout.models import Candidate, ScreenReport
from noc_cli.scout.profiles import SCREEN, build_options

SCREEN_SYSTEM_PROMPT = (
    "You are a NOC triability pre-screener. Given one stale ticket and the "
    "runbooks staged under runbooks/, decide ONLY two things: is there a runbook "
    "whose symptom class plausibly covers this ticket, and what evidence is "
    "missing to form a hypothesis. Do NOT investigate, fetch the ticket body, or "
    "draft a resolution — that is a separate, later step. Be terse and honest; if "
    "no runbook fits, say so with low confidence."
)

_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    fenced = _JSON_FENCE_RE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1].strip()
    return raw


async def _final_result(query_gen) -> str:
    """Drain a query stream, keeping only the terminal ResultMessage text.

    Scout does not need the rich transcript stash that ``agent/runner`` keeps —
    only the final JSON — so this is intentionally lighter than ``_drain`` there.
    """
    raw = ""
    async for message in query_gen:
        text = getattr(message, "result", None)
        if text is not None:
            raw = text
    return raw


def _parse(raw: str, ticket_id: int) -> ScreenReport | None:
    try:
        data = json.loads(_extract_json(raw))
        if isinstance(data, dict):
            data.setdefault("ticket_id", ticket_id)
        return ScreenReport.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError):
        return None


async def screen_ticket(
    candidate: Candidate,
    *,
    runbooks_dir: Path,
    query_fn: Callable,
    options_factory: Callable | None = None,
) -> ScreenReport | None:
    """Stage 2 (one ticket): a fresh Haiku run → ScreenReport, None on parse fail."""
    if options_factory is None:
        options_factory = lambda: build_options(
            SCREEN, system_prompt=SCREEN_SYSTEM_PROMPT, cwd=runbooks_dir.parent
        )

    prompt = (
        f"Ticket #{candidate.ticket_id}: {candidate.subject!r} "
        f"(status={candidate.status}, stale {candidate.staleness_days}d).\n\n"
        "Read the runbooks under runbooks/. Emit ONLY this JSON object:\n"
        '{"ticket_id": <int>, "runbook_id": "<slug or empty>", '
        '"runbook_match_confidence": <0.0-1.0>, "resolvable": <true|false>, '
        '"missing_evidence": ["<what is needed to form a hypothesis>"], '
        '"one_line": "<=120 char summary"}'
    )
    raw = await _final_result(query_fn(prompt=prompt, options=options_factory()))
    return _parse(raw, candidate.ticket_id)


async def screen_candidates(
    candidates: list[Candidate],
    *,
    runbooks_dir: Path,
    query_fn: Callable,
    concurrency: int = 3,
    options_factory: Callable | None = None,
) -> list[ScreenReport]:
    """Stage 2 (bounded fan-out): screen all candidates under a semaphore.

    N parallel agents = N subprocess cold-starts, so the semaphore is mandatory
    (see interactive-feat.md §5.1/§5.2). Failed parses are dropped silently."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(c: Candidate) -> ScreenReport | None:
        async with sem:
            return await screen_ticket(
                c,
                runbooks_dir=runbooks_dir,
                query_fn=query_fn,
                options_factory=options_factory,
            )

    results = await asyncio.gather(*[_one(c) for c in candidates])
    return [r for r in results if r is not None]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scout_screen.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/screen.py tests/test_scout_screen.py
git commit -m "feat(scout): stage-2 bounded Haiku triability pre-screen"
```

---

## Task 7: Stage 3 — Opus synthesis with deterministic fallback

One Opus 4.8 high-effort call ranks the `ScreenReport`s into a `ScoutReport`. If the model output won't parse, fall back to a deterministic ranking by confidence so the pipeline never hard-fails.

**Files:**
- Create: `noc_cli/scout/synthesize.py`
- Test: `tests/test_scout_synthesize.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_synthesize.py`:

```python
import asyncio
from datetime import datetime, timezone

from noc_cli.scout.models import ScreenReport
from noc_cli.scout.synthesize import synthesize

NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _fake_query(result_json: str):
    async def fake_query(*, prompt, options):
        class FakeResult:
            result = result_json

        yield FakeResult()

    return fake_query


REPORTS = [
    ScreenReport(ticket_id=1, runbook_id="low-audio", runbook_match_confidence=0.4),
    ScreenReport(ticket_id=2, runbook_id="no-ani", runbook_match_confidence=0.9),
]


def test_synthesize_parses_ranked_report():
    out = (
        '{"ranked": [{"ticket_id": 2, "rank": 1, "rationale": "strong match"}, '
        '{"ticket_id": 1, "rank": 2, "rationale": "weak"}]}'
    )
    report = _run(
        synthesize(REPORTS, query_fn=_fake_query(out), now=NOW, options_factory=lambda: None)
    )
    assert [r.ticket_id for r in report.ranked] == [2, 1]
    assert report.ranked[0].rank == 1
    assert report.generated_at == NOW


def test_synthesize_falls_back_to_confidence_order_on_garbage():
    report = _run(
        synthesize(
            REPORTS, query_fn=_fake_query("garbage"), now=NOW, options_factory=lambda: None
        )
    )
    # deterministic fallback: highest confidence first
    assert [r.ticket_id for r in report.ranked] == [2, 1]
    assert report.ranked[0].rank == 1
    assert report.ranked[0].runbook_id == "no-ani"


def test_synthesize_empty_reports_returns_empty():
    report = _run(
        synthesize([], query_fn=_fake_query("{}"), now=NOW, options_factory=lambda: None)
    )
    assert report.ranked == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_synthesize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout.synthesize'`.

- [ ] **Step 3: Implement synthesis**

Create `noc_cli/scout/synthesize.py`:

```python
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Callable

from pydantic import ValidationError

from noc_cli.scout.models import RankedCandidate, ScoutReport, ScreenReport
from noc_cli.scout.profiles import SYNTHESIS, build_options

SYNTHESIS_SYSTEM_PROMPT = (
    "You are the NOC backlog synthesizer. You receive triability pre-screens for "
    "several stale tickets and must rank them by which most deserves a human "
    "engineer's attention NOW. Weigh runbook_match_confidence, whether it is "
    "resolvable, and how little evidence is missing. Give each a one-sentence "
    "rationale. This ranked list is what the engineer sees — be decisive."
)

_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    fenced = _JSON_FENCE_RE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1].strip()
    return raw


async def _final_result(query_gen) -> str:
    raw = ""
    async for message in query_gen:
        text = getattr(message, "result", None)
        if text is not None:
            raw = text
    return raw


def _fallback(reports: list[ScreenReport]) -> list[RankedCandidate]:
    """Deterministic ranking when the model output is unusable: confidence desc."""
    ordered = sorted(
        reports, key=lambda r: r.runbook_match_confidence, reverse=True
    )
    return [
        RankedCandidate(
            ticket_id=r.ticket_id,
            rank=i + 1,
            rationale=r.one_line or "(synthesis unavailable — ranked by confidence)",
            runbook_id=r.runbook_id,
            runbook_match_confidence=r.runbook_match_confidence,
            missing_evidence=r.missing_evidence,
        )
        for i, r in enumerate(ordered)
    ]


def _parse_ranked(raw: str) -> list[RankedCandidate] | None:
    try:
        data = json.loads(_extract_json(raw))
        ranked = [RankedCandidate.model_validate(r) for r in data["ranked"]]
        return ranked or None
    except (json.JSONDecodeError, ValidationError, ValueError, KeyError, TypeError):
        return None


async def synthesize(
    reports: list[ScreenReport],
    *,
    query_fn: Callable,
    now: datetime,
    options_factory: Callable | None = None,
    cwd=".",
) -> ScoutReport:
    """Stage 3: Opus-4.8-high folds K pre-screens into the ranked deliverable.

    On parse failure, falls back to a deterministic confidence-ordered ranking so
    the pipeline always returns something usable."""
    if not reports:
        return ScoutReport(generated_at=now, ranked=[])

    if options_factory is None:
        options_factory = lambda: build_options(
            SYNTHESIS, system_prompt=SYNTHESIS_SYSTEM_PROMPT, cwd=cwd
        )

    payload = json.dumps([r.model_dump() for r in reports], indent=2)
    prompt = (
        "Triability pre-screens for stale NOC tickets:\n"
        f"{payload}\n\n"
        "Rank them best-first. Emit ONLY this JSON object:\n"
        '{"ranked": [{"ticket_id": <int>, "rank": <1-based int>, '
        '"rationale": "<one sentence>", "runbook_id": "<slug>", '
        '"runbook_match_confidence": <0.0-1.0>, "missing_evidence": ["..."]}]}'
    )
    raw = await _final_result(query_fn(prompt=prompt, options=options_factory()))
    ranked = _parse_ranked(raw) or _fallback(reports)
    return ScoutReport(generated_at=now, ranked=ranked)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scout_synthesize.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/synthesize.py tests/test_scout_synthesize.py
git commit -m "feat(scout): stage-3 Opus synthesis with deterministic fallback"
```

---

## Task 8: Orchestrator — `run_scout`

Wires Stage 1 → 2 → 3, read-only throughout. Builds the read-only hook sandbox over the workspace and threads per-stage option factories. Option factories are injectable so the orchestrator is testable without the SDK.

**Files:**
- Create: `noc_cli/scout/runner.py`
- Test: `tests/test_scout_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_runner.py`:

```python
import asyncio
from datetime import datetime, timedelta, timezone

from noc_cli.models import Ticket
from noc_cli.scout.runner import run_scout

NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    def __init__(self, tickets):
        self._tickets = tickets

    def view_tickets(self, view_id):
        assert view_id == "6490757606044"
        return self._tickets


def _fake_query(*, prompt, options):
    # Distinguish synthesis from screen by the prompt's leading text.
    if prompt.startswith("Triability pre-screens"):
        out = '{"ranked": [{"ticket_id": 1, "rank": 1, "rationale": "match"}]}'
    else:
        out = (
            '{"ticket_id": 1, "runbook_id": "low-audio", '
            '"runbook_match_confidence": 0.8, "resolvable": true, '
            '"missing_evidence": [], "one_line": "ok"}'
        )

    async def gen():
        class FakeResult:
            result = out

        yield FakeResult()

    return gen()


def test_run_scout_full_pipeline(tmp_path):
    tickets = [
        Ticket(id=1, subject="audio", status="open", priority="high",
               updated_at=NOW - timedelta(days=5)),
        Ticket(id=2, subject="assigned", status="open", assignee_id=99,
               updated_at=NOW - timedelta(days=9)),  # dropped by stage 1
    ]
    report = _run(
        run_scout(
            client=_FakeClient(tickets),
            view_id="6490757606044",
            workspace=tmp_path,
            now=NOW,
            query_fn=_fake_query,
            screen_options_factory=lambda: None,
            synth_options_factory=lambda: None,
        )
    )
    assert [r.ticket_id for r in report.ranked] == [1]
    assert report.generated_at == NOW


def test_run_scout_empty_candidates_skips_agent(tmp_path):
    report = _run(
        run_scout(
            client=_FakeClient([]),
            view_id="6490757606044",
            workspace=tmp_path,
            now=NOW,
            query_fn=_fake_query,
            screen_options_factory=lambda: None,
            synth_options_factory=lambda: None,
        )
    )
    assert report.ranked == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout.runner'`.

- [ ] **Step 3: Implement the orchestrator**

Create `noc_cli/scout/runner.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from noc_cli.scout.models import ScoutReport
from noc_cli.scout.profiles import SCREEN, SYNTHESIS, build_options
from noc_cli.scout.rank import rank_candidates
from noc_cli.scout.screen import SCREEN_SYSTEM_PROMPT, screen_candidates
from noc_cli.scout.synthesize import SYNTHESIS_SYSTEM_PROMPT, synthesize
from noc_cli.scout.workspace import materialize_workspace


async def run_scout(
    *,
    client,
    view_id: str,
    workspace: Path,
    now: datetime,
    query_fn: Callable,
    top_k: int = 8,
    concurrency: int = 3,
    screen_options_factory: Callable | None = None,
    synth_options_factory: Callable | None = None,
) -> ScoutReport:
    """Run the full read-only Scout pipeline: rank → screen → synthesize.

    Performs NO writes. ``client`` only needs a ``view_tickets(view_id)`` method.
    The ``*_options_factory`` params are injected in tests; in production they
    build SDK options under the read-only hook sandbox rooted at ``workspace``."""
    tickets = client.view_tickets(view_id)
    candidates = rank_candidates(tickets, now=now, top_k=top_k)
    if not candidates:
        return ScoutReport(generated_at=now, ranked=[])

    runbooks_dir = materialize_workspace(workspace)

    if screen_options_factory is None or synth_options_factory is None:
        from noc_cli.agent.harness import build_hooks  # noqa: PLC0415

        hooks = build_hooks(
            sandbox_root=workspace, events_path=workspace / "events.jsonl"
        )
        if screen_options_factory is None:
            screen_options_factory = lambda: build_options(
                SCREEN, system_prompt=SCREEN_SYSTEM_PROMPT, cwd=workspace, hooks=hooks
            )
        if synth_options_factory is None:
            synth_options_factory = lambda: build_options(
                SYNTHESIS,
                system_prompt=SYNTHESIS_SYSTEM_PROMPT,
                cwd=workspace,
                hooks=hooks,
            )

    reports = await screen_candidates(
        candidates,
        runbooks_dir=runbooks_dir,
        query_fn=query_fn,
        concurrency=concurrency,
        options_factory=screen_options_factory,
    )
    return await synthesize(
        reports, query_fn=query_fn, now=now, options_factory=synth_options_factory
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scout_runner.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/runner.py tests/test_scout_runner.py
git commit -m "feat(scout): read-only orchestrator wiring all three stages"
```

---

## Task 9: Isolated Zendesk assign-writer

The ONLY module that mutates Zendesk. Keeps `ZendeskClient` ("Performs no writes, ever.") untouched. Extract `build_auth_header` so both share auth without the writer importing the read-only client.

**Files:**
- Modify: `noc_cli/zendesk.py:25-27` (extract auth helper)
- Create: `noc_cli/scout/acquire.py`
- Test: `tests/test_scout_acquire.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_acquire.py`:

```python
import httpx
import pytest

from noc_cli.config import Config
from noc_cli.scout.acquire import ZendeskWriteError, ZendeskWriter

CFG = Config(
    zendesk_subdomain="acme",
    zendesk_email="me@acme.com",
    zendesk_api_token="tok",
)


def test_assign_ticket_puts_assignee():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"ticket": {"id": 42, "assignee_id": 7}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ZendeskWriter(CFG, client=client).assign_ticket(42, 7)

    assert seen["method"] == "PUT"
    assert seen["url"] == "https://acme.zendesk.com/api/v2/tickets/42.json"
    assert '"assignee_id": 7' in seen["body"]
    assert seen["auth"].startswith("Basic ")


def test_assign_ticket_raises_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "denied"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ZendeskWriteError):
        ZendeskWriter(CFG, client=client).assign_ticket(42, 7)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_acquire.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout.acquire'`.

- [ ] **Step 3: Extract the auth helper in `zendesk.py`**

In `noc_cli/zendesk.py`, add a module-level function after the imports (after line 8):

```python
def build_auth_header(config: Config) -> str:
    """Zendesk token auth header: ``Basic base64("<email>/token:<api_token>")``."""
    raw = f"{config.zendesk_email}/token:{config.zendesk_api_token}".encode()
    return "Basic " + base64.b64encode(raw).decode()
```

Then replace lines 25-27 inside `ZendeskClient.__init__`:

```python
        # Zendesk token auth: "<email>/token:<api_token>". Do not pre-append /token.
        raw = f"{config.zendesk_email}/token:{config.zendesk_api_token}".encode()
        self._auth_header = "Basic " + base64.b64encode(raw).decode()
```

with:

```python
        self._auth_header = build_auth_header(config)
```

- [ ] **Step 4: Implement the writer**

Create `noc_cli/scout/acquire.py`:

```python
from __future__ import annotations

import httpx

from noc_cli.config import Config
from noc_cli.zendesk import build_auth_header


class ZendeskWriteError(RuntimeError):
    pass


class ZendeskWriter:
    """The ONLY Zendesk-mutating surface in noc-cli.

    Deliberately separate from the read-only ``ZendeskClient`` so the agent's
    read-only invariant is structural, not just convention. Invoked only from
    deterministic CLI code behind an explicit engineer confirm — never by the
    agent (whose harness denylists Zendesk writes regardless)."""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        if not (config.zendesk_subdomain and config.zendesk_email and config.zendesk_api_token):
            raise ZendeskWriteError(
                "Zendesk is not configured. Run `noc-cli setup` first."
            )
        self._base_url = config.zendesk_base_url
        self._auth_header = build_auth_header(config)
        self._client = client or httpx.Client(timeout=30.0)

    def assign_ticket(self, ticket_id: int, assignee_id: int) -> None:
        """PUT the ticket's assignee. Raises ZendeskWriteError on auth failure."""
        resp = self._client.put(
            f"{self._base_url}/tickets/{ticket_id}.json",
            json={"ticket": {"assignee_id": assignee_id}},
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code == 401:
            raise ZendeskWriteError(
                "Zendesk auth failed on assign - check ZENDESK_EMAIL and ZENDESK_API_TOKEN."
            )
        resp.raise_for_status()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_scout_acquire.py tests/test_zendesk.py -v`
Expected: PASS (new writer tests + existing zendesk tests still green after the refactor).

- [ ] **Step 6: Commit**

```bash
git add noc_cli/zendesk.py noc_cli/scout/acquire.py tests/test_scout_acquire.py
git commit -m "feat(scout): isolated ZendeskWriter for the lone assign write"
```

---

## Task 10: Config — `scout_view` default

Default the discovery pool to the Tier-1 (NOC) Queue `6490757606044`, overridable via `NOC_SCOUT_VIEW`.

**Files:**
- Modify: `noc_cli/config.py:18-23` (the `_FIELD_ENV` map) and `noc_cli/config.py:104-107` (the `Config` fields)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_scout_view_defaults_to_tier1_queue():
    from noc_cli.config import Config

    assert Config().scout_view == "6490757606044"


def test_scout_view_is_a_valid_config_key():
    from noc_cli.config import valid_config_keys

    assert "scout_view" in valid_config_keys()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_scout_view_defaults_to_tier1_queue -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'scout_view'`.

- [ ] **Step 3: Add the field and env mapping**

In `noc_cli/config.py`, add to the `_FIELD_ENV` dict (after the `timezone` line, line 23):

```python
    "scout_view": "NOC_SCOUT_VIEW",
```

And in `class Config`, add after the `timezone` field (line 107):

```python
    scout_view: str = "6490757606044"  # Tier-1 (NOC) Queue; the Backlog Scout pool
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (new tests + existing).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/config.py tests/test_config.py
git commit -m "feat(config): add scout_view (NOC_SCOUT_VIEW), default Tier-1 NOC queue"
```

---

## Task 11: Render the ScoutReport as a terminal table

A plain renderer the CLI prints and the future TUI panel can reuse.

**Files:**
- Create: `noc_cli/scout/render.py`
- Test: `tests/test_scout_render.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scout_render.py`:

```python
from datetime import datetime, timezone

from noc_cli.scout.models import RankedCandidate, ScoutReport
from noc_cli.scout.render import render_scout_report


def test_render_lists_ranked_tickets_in_order():
    report = ScoutReport(
        generated_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        ranked=[
            RankedCandidate(ticket_id=2, rank=1, rationale="strong", runbook_id="no-ani",
                            runbook_match_confidence=0.9),
            RankedCandidate(ticket_id=1, rank=2, rationale="weak", runbook_id="low-audio",
                            runbook_match_confidence=0.4),
        ],
    )
    text = render_scout_report(report)
    assert "#2" in text and "#1" in text
    assert text.index("#2") < text.index("#1")  # rank order preserved
    assert "no-ani" in text
    assert "strong" in text


def test_render_empty_report():
    text = render_scout_report(ScoutReport())
    assert "No stale tickets" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scout_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.scout.render'`.

- [ ] **Step 3: Implement the renderer**

Create `noc_cli/scout/render.py`:

```python
from __future__ import annotations

from noc_cli.scout.models import ScoutReport


def render_scout_report(report: ScoutReport) -> str:
    """Render the ranked Scout deliverable as plain text for the terminal."""
    if not report.ranked:
        return "No stale tickets worth screening right now."

    lines = ["Backlog Scout — candidates worth your time:", ""]
    for rc in report.ranked:
        conf = f"{rc.runbook_match_confidence:.0%}"
        runbook = rc.runbook_id or "(no runbook match)"
        lines.append(f"  {rc.rank}. #{rc.ticket_id}  [{runbook} · {conf}]  {rc.rationale}")
        if rc.missing_evidence:
            lines.append(f"       missing: {', '.join(rc.missing_evidence)}")
    lines.append("")
    lines.append("Run `noc scout --take <id>` to assign one to yourself and investigate.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scout_render.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/render.py tests/test_scout_render.py
git commit -m "feat(scout): terminal renderer for the ranked report"
```

---

## Task 12: CLI `scout` command

Without `--take`: run the pipeline and print the ranked report. With `--take <id>`: confirm (unless `--yes`), resolve the owner's Zendesk user id, assign via `ZendeskWriter`, then hand off to the existing `investigate` flow. Propose-then-confirm: nothing is written until the engineer confirms.

**Files:**
- Modify: `noc_cli/cli.py` (add the `scout` command + a `_take_ticket` helper)
- Test: `tests/test_cli_scout.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_scout.py`:

```python
from datetime import datetime, timezone
from unittest import mock

from typer.testing import CliRunner

from noc_cli.cli import app
from noc_cli.scout.models import RankedCandidate, ScoutReport

runner = CliRunner()

_REPORT = ScoutReport(
    generated_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    ranked=[RankedCandidate(ticket_id=42, rank=1, rationale="matches low-audio",
                            runbook_id="low-audio", runbook_match_confidence=0.8)],
)


def test_scout_lists_candidates():
    with mock.patch("noc_cli.cli._run_scout_report", return_value=_REPORT):
        result = runner.invoke(app, ["scout"])
    assert result.exit_code == 0
    assert "#42" in result.output
    assert "low-audio" in result.output


def test_scout_take_confirms_then_assigns_and_investigates():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.cli._make_writer", return_value=writer), \
         mock.patch("noc_cli.cli._resolve_owner_id", return_value=7), \
         mock.patch("noc_cli.cli._invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])
    assert result.exit_code == 0
    writer.assign_ticket.assert_called_once_with(42, 7)
    inv.assert_called_once_with(42)


def test_scout_take_aborts_without_confirmation():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.cli._make_writer", return_value=writer), \
         mock.patch("noc_cli.cli._resolve_owner_id", return_value=7), \
         mock.patch("noc_cli.cli._invoke_investigate") as inv:
        # no --yes; simulate the engineer answering "n" at the prompt
        result = runner.invoke(app, ["scout", "--take", "42"], input="n\n")
    assert result.exit_code != 0 or "Aborted" in result.output
    writer.assign_ticket.assert_not_called()
    inv.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_scout.py -v`
Expected: FAIL — `Error: No such command 'scout'`.

- [ ] **Step 3: Implement the command and helpers**

In `noc_cli/cli.py`, add these helpers near the other private helpers (after `_handoff_with_initial_hypothesis`, around line 256), then the command after the `investigate` command (before `watch` at line 459). Keep imports local to match the file's lazy-import style.

```python
def _run_scout_report(cfg, *, top_k: int):
    """Build a read-only client and run the Scout pipeline; return a ScoutReport."""
    import asyncio as _asyncio
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path

    from noc_cli.scout.runner import run_scout
    from noc_cli.zendesk import ZendeskClient

    client = ZendeskClient(cfg)

    async def _go():
        from claude_agent_sdk import query  # noqa: PLC0415

        with tempfile.TemporaryDirectory(prefix="noc-scout-") as tmp:
            return await run_scout(
                client=client,
                view_id=cfg.scout_view,
                workspace=Path(tmp),
                now=datetime.now(timezone.utc),
                query_fn=query,
                top_k=top_k,
            )

    return _asyncio.run(_go())


def _make_writer(cfg):
    from noc_cli.scout.acquire import ZendeskWriter

    return ZendeskWriter(cfg)


def _resolve_owner_id(cfg) -> int | None:
    from noc_cli.zendesk import ZendeskClient

    return ZendeskClient(cfg).find_user_id(cfg.watch_assignee or cfg.owner)


def _invoke_investigate(ticket_id: int) -> None:
    asyncio.run(
        _run_investigate(
            ticket_id=ticket_id,
            extra_files=[],
            pastes=[],
            force=False,
            fixture=None,
            no_agent=False,
            initial_hypothesis="",
            verbose=False,
        )
    )


@app.command()
def scout(
    take: Optional[int] = typer.Option(
        None, "--take", help="Confirm, assign this ticket id to yourself, then investigate"
    ),
    top_k: int = typer.Option(8, "--top-k", help="How many candidates to screen"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the --take confirmation prompt"),
) -> None:
    """Scan the Tier-1 NOC backlog for stale, runbook-backed tickets worth your time."""
    from noc_cli.config import load_config
    from noc_cli.scout.render import render_scout_report

    branding.render_banner()
    cfg = load_config()

    if take is None:
        report = _run_scout_report(cfg, top_k=top_k)
        typer.echo(render_scout_report(report))
        return

    # --take: propose-then-confirm. Nothing is written until the engineer agrees.
    owner_id = _resolve_owner_id(cfg)
    if owner_id is None:
        typer.secho(
            f"Could not resolve a Zendesk user id for {cfg.watch_assignee or cfg.owner!r}. "
            "Set NOC_WATCH_ASSIGNEE / NOC_OWNER to your Zendesk email.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if not yes:
        typer.confirm(f"Assign ticket #{take} to yourself and start investigating?", abort=True)

    _make_writer(cfg).assign_ticket(take, owner_id)
    typer.secho(f"Assigned #{take} to you. Investigating…", fg=typer.colors.GREEN)
    _invoke_investigate(take)
```

Note: `Optional` and `typer` are already imported at the top of `cli.py`; confirm with `grep -n "Optional" noc_cli/cli.py` before running.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_scout.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `pytest -q`
Expected: PASS (all existing tests + the new scout suite).

- [ ] **Step 6: Commit**

```bash
git add noc_cli/cli.py tests/test_cli_scout.py
git commit -m "feat(scout): noc scout command (report + propose-then-confirm --take)"
```

---

## Task 13: Manual smoke test (no commit)

Verify the wiring end-to-end with the agent stubbed, then confirm the command is registered.

- [ ] **Step 1: Confirm the command is registered**

Run: `noc-cli scout --help`
Expected: usage text showing `--take`, `--top-k`, `--yes`.

- [ ] **Step 2: Confirm the full suite is green**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 3: (Optional, live) dry list against the real queue**

Only if Zendesk is configured (`noc-cli doctor` green). This makes real read-only API + agent calls and costs tokens:

Run: `noc-cli scout --top-k 3`
Expected: a ranked list (or "No stale tickets…"). Do **not** pass `--take` during smoke testing unless you intend to actually assign a ticket.

---

## Notes for the implementer

- **Read-only invariant:** Tasks 1–8 and 10–11 never mutate Zendesk. The single write is Task 9's `ZendeskWriter`, reached only via Task 12's `--take` behind a confirm. Do not add Zendesk-write tools to any agent profile.
- **Model IDs:** `claude-haiku-4-5` (screen) and `claude-opus-4-8` (synthesis) are aliases the SDK resolves; `effort` is a first-class `ClaudeAgentOptions` field (`EffortLevel`).
- **Out of scope (Plan 2 and beyond):** the in-`watch` TUI panel, auto-eval on poll, graduated autonomy (veto-window/act-then-report), persisted `SCOUT.md`, desktop ping, configurable multi-view pools, bulk acquisition, and the metric feedback loop — all banked in `docs/interactive-feat.md` §9.6.
- **Spec:** `docs/interactive-feat.md` §9 (Backlog Scout).
