# Deeper Runbook Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select the right runbook from ticket evidence with a cheap Haiku classifier (operator hypothesis as a signal), inject the selected runbook into the agent's turn prompt, and softly verify the agent cited it — recording the result on the Handoff + `events.jsonl`.

**Architecture:** A new `noc_cli/grounding.py` holds `select_runbook` (Haiku classifier → `RunbookSelection`) and `verify_grounding` (normalized citation check). `investigate.py` runs the classifier after seeding, uses its result for both the injected runbook and the `seed_history` tag, threads the selection into `run_agent` (injected into the *turn* prompt — system prompt stays cache-stable), then verifies after parse. Grounding status lands on `ForkPacket` + `events.jsonl` + the rendered output. All 6 runbooks stay staged for re-steer.

**Tech Stack:** Python 3.10+, `uv`, `claude-agent-sdk` 0.2.88, pydantic v2, pytest + anyio. Reuses #3's `model_profiles.py` and scout's generic `extract_json`/`final_result` helpers.

**Spec:** `docs/superpowers/specs/2026-06-06-deeper-runbook-grounding-design.md`

**Branch:** `feat/runbook-grounding`, stacked on `feat/sdk-tuning` (needs #3's `model_profiles.py`). Rebase onto `main` after PR #10 merges. The ruff format-on-edit hook is active — **add each new import together with the code that references it** (a just-added unused import gets stripped as F401 by the hook).

---

## File structure

| File | Responsibility |
|---|---|
| `noc_cli/grounding.py` *(new)* | `RunbookSelection`, `select_runbook(...)`, `verify_grounding(...)`. |
| `noc_cli/model_profiles.py` *(modify)* | Add `"grounding"` profile = Haiku. |
| `noc_cli/models.py` *(modify)* | `ForkPacket`: `grounding_verified: bool \| None = None`, `grounding_note: str = ""`. |
| `noc_cli/agent/runner.py` *(modify)* | New params `selected_runbook_slug`/`selected_runbook_text`; inject into the turn prompt. |
| `noc_cli/agent/prompt.py` *(modify)* | System-prompt grounding-protocol wording acknowledging a pre-selected/injected runbook. |
| `noc_cli/investigate.py` *(modify)* | Classify → seed_history tag + injection; verify after parse; set fields + log. |
| `noc_cli/render.py` *(modify)* | One-line grounding status. |

---

### Task 1: Add the `grounding` model profile

**Files:** Modify `noc_cli/model_profiles.py`; Test `tests/test_model_profiles.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_model_profiles.py`:
```python
def test_grounding_profile_is_haiku():
    p = profile_for("grounding")
    assert p.model == "claude-haiku-4-5"
    assert p.effort == "medium"
    assert p.source == "default"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_model_profiles.py -k grounding_profile -v`
Expected: FAIL — `KeyError: 'grounding'`.

- [ ] **Step 3: Implement**

In `noc_cli/model_profiles.py`, add to the `_DEFAULTS` dict:
```python
    "grounding": ModelProfile("claude-haiku-4-5", None, "medium"),
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_model_profiles.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/model_profiles.py tests/test_model_profiles.py
git commit -m "feat(agent): add grounding (Haiku) model profile"
```

---

### Task 2: `ForkPacket` grounding fields

**Files:** Modify `noc_cli/models.py`; Test `tests/test_models.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:
```python
def test_forkpacket_grounding_fields_default_and_settable():
    from noc_cli.models import ForkPacket, ForkLetter, Confidence

    fp = ForkPacket(fork_letter=ForkLetter.B, confidence=Confidence.HIGH, symptom_tag="[low audio]")
    # defaulted so existing agent JSON (without these keys) still validates
    assert fp.grounding_verified is None
    assert fp.grounding_note == ""
    # set post-hoc by the verifier
    fp.grounding_verified = True
    fp.grounding_note = "quoted row found in low-audio"
    assert fp.grounding_verified is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_models.py -k grounding_fields -v`
Expected: FAIL — `ForkPacket` has no `grounding_verified`.

- [ ] **Step 3: Implement**

In `noc_cli/models.py`, add two fields to `ForkPacket` (after `cluster`):
```python
    grounding_verified: bool | None = None
    grounding_note: str = ""
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (new + existing — the new fields are defaulted, so existing Handoff fixtures still validate).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/models.py tests/test_models.py
git commit -m "feat(models): add grounding_verified/grounding_note to ForkPacket"
```

---

### Task 3: `select_runbook` classifier

**Files:** Create `noc_cli/grounding.py`; Test `tests/test_grounding.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_grounding.py`:
```python
import anyio

from noc_cli.grounding import RunbookSelection, select_runbook


def _fake_query(result_text):
    async def q(*, prompt, options):
        class R:
            result = result_text

        yield R()

    return q


def test_select_runbook_picks_valid_slug():
    q = _fake_query('{"slug":"low-audio","confidence":"high","rationale":"audio dropouts"}')
    sel = anyio.run(lambda: select_runbook("caller reports choppy audio", "[low audio]", query_fn=q, options_factory=lambda: None))
    assert sel == RunbookSelection(slug="low-audio", confidence="high", rationale="audio dropouts")


def test_low_confidence_yields_no_slug():
    q = _fake_query('{"slug":"low-audio","confidence":"low","rationale":"unsure"}')
    sel = anyio.run(lambda: select_runbook("vague text", "", query_fn=q, options_factory=lambda: None))
    assert sel.slug is None
    assert sel.confidence == "low"


def test_unknown_slug_yields_no_slug():
    q = _fake_query('{"slug":"not-a-runbook","confidence":"high","rationale":"x"}')
    sel = anyio.run(lambda: select_runbook("t", "", query_fn=q, options_factory=lambda: None))
    assert sel.slug is None


def test_unparseable_output_yields_no_slug():
    q = _fake_query("the model rambled with no json")
    sel = anyio.run(lambda: select_runbook("t", "", query_fn=q, options_factory=lambda: None))
    assert sel.slug is None
    assert sel.confidence == "low"


def test_classifier_error_yields_no_slug():
    async def boom(*, prompt, options):
        raise RuntimeError("network")
        yield  # pragma: no cover

    sel = anyio.run(lambda: select_runbook("t", "", query_fn=boom, options_factory=lambda: None))
    assert sel.slug is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_grounding.py -k select_runbook -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.grounding'`.

- [ ] **Step 3: Implement**

Create `noc_cli/grounding.py`:
```python
from __future__ import annotations

import json
from dataclasses import dataclass

from noc_cli.model_profiles import profile_for
from noc_cli.runbooks import DOMAIN_MAP, RUNBOOK_SLUGS
from noc_cli.scout.llm_io import extract_json, final_result

GROUNDING_SYSTEM_PROMPT = (
    "You classify a single 911/NG911 support ticket into exactly one runbook "
    "symptom class, or none if nothing fits. You do not investigate or fetch "
    "anything; you read the provided ticket text and pick the best-matching "
    "runbook from the catalog. The operator hypothesis is one signal, not a "
    "verdict — let the ticket evidence decide."
)

_CONFIDENCE = {"high", "medium", "low"}


@dataclass(frozen=True)
class RunbookSelection:
    slug: str | None
    confidence: str
    rationale: str


def _catalog() -> str:
    return "\n".join(
        f"  - {s.slug}: {s.label} ({s.domain}) [tag {s.tag}]" for s in DOMAIN_MAP
    )


def _parse_selection(raw: str) -> RunbookSelection:
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, ValueError, TypeError):
        return RunbookSelection(None, "low", "unparseable classifier output")
    if not isinstance(data, dict):
        return RunbookSelection(None, "low", "classifier output not an object")
    slug = (data.get("slug") or "").strip() or None
    conf = str(data.get("confidence", "low")).strip().lower()
    rationale = str(data.get("rationale", ""))[:300]
    if conf not in _CONFIDENCE:
        conf = "low"
    if slug not in RUNBOOK_SLUGS or conf == "low":
        return RunbookSelection(None, conf, rationale)
    return RunbookSelection(slug, conf, rationale)


def _default_options_factory():
    from claude_agent_sdk import ClaudeAgentOptions  # noqa: PLC0415

    p = profile_for("grounding")
    return ClaudeAgentOptions(
        system_prompt=GROUNDING_SYSTEM_PROMPT,
        model=p.model,
        effort=p.effort,
        max_turns=1,
        allowed_tools=[],
        permission_mode="bypassPermissions",
    )


async def select_runbook(
    ticket_text: str,
    hypothesis: str,
    *,
    query_fn=None,
    options_factory=None,
) -> RunbookSelection:
    """Classify a ticket into one runbook (or none). Never raises."""
    if query_fn is None:
        from claude_agent_sdk import query as query_fn  # noqa: PLC0415
    if options_factory is None:
        options_factory = _default_options_factory

    prompt = (
        "Classify this ticket into exactly one runbook from the catalog, or "
        'leave slug empty if none fits.\n\n'
        f"Operator hypothesis (a signal, not a verdict): {hypothesis or '(none)'}\n\n"
        f"Runbook catalog:\n{_catalog()}\n\n"
        f"Ticket text:\n{(ticket_text or '')[:6000]}\n\n"
        'Emit ONLY this JSON: {"slug":"<runbook slug or empty>",'
        '"confidence":"high|medium|low","rationale":"<=200 chars"}'
    )
    try:
        raw = await final_result(query_fn(prompt=prompt, options=options_factory()))
    except Exception:  # noqa: BLE001 — classifier failure degrades to rubric-core
        return RunbookSelection(None, "low", "classifier call failed")
    return _parse_selection(raw)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_grounding.py -k select_runbook -v` then `-k "low_confidence or unknown_slug or unparseable or classifier_error"`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/grounding.py tests/test_grounding.py
git commit -m "feat(agent): Haiku runbook classifier (select_runbook)"
```

---

### Task 4: `verify_grounding`

**Files:** Modify `noc_cli/grounding.py`; Test `tests/test_grounding.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_grounding.py`:
```python
from noc_cli.grounding import verify_grounding
from noc_cli.models import Confidence, ForkLetter, ForkPacket, Handoff, Intake, RunbookReference


def _handoff(quote, slug):
    fp = ForkPacket(
        fork_letter=ForkLetter.B,
        confidence=Confidence.HIGH,
        symptom_tag="[low audio]",
        quoted_rubric_row=quote,
        runbook_reference=RunbookReference(slug=slug, section="Fork decision"),
    )
    return Handoff(rubric_version="t", intake=Intake(ticket_id=1), fork_packet=fp, drafts={})


def test_verify_true_on_normalized_match():
    text = "## Fork decision\nIf RTP shows packet loss > 5%, fork B (media)."
    h = _handoff("if rtp shows   PACKET LOSS > 5%, fork b (media).", "low-audio")
    ok, note = verify_grounding(h, selected_slug="low-audio", runbook_text=text)
    assert ok is True


def test_verify_false_when_quote_absent():
    h = _handoff("a row that is nowhere in the runbook", "low-audio")
    ok, note = verify_grounding(h, selected_slug="low-audio", runbook_text="unrelated text")
    assert ok is False
    assert "low-audio" in note


def test_verify_none_when_no_quote():
    h = _handoff("", "low-audio")
    ok, note = verify_grounding(h, selected_slug="low-audio", runbook_text="x")
    assert ok is None
```

(If `Handoff`/`Intake` constructors need more required fields, mirror the construction used in existing `tests/test_models.py`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_grounding.py -k verify -v`
Expected: FAIL — `cannot import name 'verify_grounding'`.

- [ ] **Step 3: Implement**

Add to `noc_cli/grounding.py`:
```python
import re  # add to imports at top

from noc_cli.runbooks import _load_runbook  # add to imports at top


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def verify_grounding(handoff, *, selected_slug, runbook_text):
    """Soft check that the Handoff's quoted row traces to the cited runbook.

    Returns (verified, note): True/False on a real check, None when verification
    can't run. Never raises.
    """
    try:
        fp = handoff.fork_packet
        quote = _norm(fp.quoted_rubric_row)
        if not quote:
            return None, "no quoted_rubric_row to verify"
        cited_slug = (getattr(fp.runbook_reference, "slug", "") or selected_slug) or None
        text = None
        if cited_slug and cited_slug in RUNBOOK_SLUGS:
            text = _load_runbook(cited_slug)
        if text is None:
            text = runbook_text  # selected runbook text, or rubric core when slug is None
        if text is None:
            return None, "no runbook text to verify against"
        label = cited_slug or "rubric-core"
        if quote in _norm(text):
            return True, f"quoted row found in {label}"
        return False, f"quoted row not found in {label}; possible drift"
    except Exception:  # noqa: BLE001 — verification is best-effort
        return None, "verification skipped"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_grounding.py -v`
Expected: PASS (all grounding tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/grounding.py tests/test_grounding.py
git commit -m "feat(agent): soft grounding verification (verify_grounding)"
```

---

### Task 5: Inject the selected runbook into the turn prompt (runner.py)

**Files:** Modify `noc_cli/agent/runner.py`; Test `tests/test_agent_runner.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_runner.py`:
```python
def test_selected_runbook_injected_into_turn_prompt(tmp_path):
    from noc_cli.scaffold import scaffold_ticket

    folder = scaffold_ticket(tmp_path, 8100)
    prompts = []

    async def capture(*, prompt, options):
        prompts.append(prompt)

        class R:
            result = '{"totally":"wrong"}'
            is_error = False

        yield R()

    _run(
        run_agent(
            ticket_id=8100, folder=folder, system_prompt="sys", history_context="",
            selected_runbook_slug="low-audio",
            selected_runbook_text="## Fork decision\nMEDIA-MARKER-XYZ rule.",
            _query_fn=capture,
        )
    )
    assert "MEDIA-MARKER-XYZ" in prompts[0]
    assert "low-audio" in prompts[0]


def test_no_runbook_injected_when_none(tmp_path):
    from noc_cli.scaffold import scaffold_ticket

    folder = scaffold_ticket(tmp_path, 8101)
    prompts = []

    async def capture(*, prompt, options):
        prompts.append(prompt)

        class R:
            result = '{"totally":"wrong"}'
            is_error = False

        yield R()

    _run(run_agent(ticket_id=8101, folder=folder, system_prompt="sys", history_context="", _query_fn=capture))
    assert "MEDIA-MARKER-XYZ" not in prompts[0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent_runner.py -k injected_into_turn_prompt -v`
Expected: FAIL — `run_agent` has no `selected_runbook_slug` param.

- [ ] **Step 3: Implement**

In `noc_cli/agent/runner.py`:

(a) Add the two params to `run_agent` (before `config`):
```python
async def run_agent(
    ticket_id: int | str,
    folder: TicketFolder,
    system_prompt: str,
    history_context: str,
    initial_hypothesis: str = "",
    selected_runbook_slug: str | None = None,
    selected_runbook_text: str | None = None,
    config=None,
    memory_store=None,
    _query_fn: Callable | None = None,
) -> RunnerResult:
```

(b) Build a grounding block and insert it into `full_prompt`. Replace the existing `full_prompt = (...)` assignment with:
```python
    if selected_runbook_text and selected_runbook_slug:
        grounding_block = (
            f"## Selected runbook: {selected_runbook_slug}\n"
            "This runbook was selected from the ticket evidence. Ground your fork "
            "in it: quote its decisive row verbatim into `quoted_rubric_row` and set "
            f"`runbook_reference.slug` to {selected_runbook_slug!r}. Re-steer to a "
            "different staged runbook ONLY if the evidence contradicts this one, and "
            "explain the pivot in `reasoning`.\n\n"
            f"{selected_runbook_text}\n\n"
        )
    else:
        grounding_block = (
            "No runbook was confidently selected. Triage on the rubric core, tag "
            "`[unclassified]` (or `[apex]` for general platform behavior), and say so "
            "in `reasoning`.\n\n"
        )

    full_prompt = (
        f"Triage ticket #{ticket_id}.\n\n"
        f"{hypothesis_line}"
        f"{grounding_block}"
        f"Historical context (for historical_matches only — do not treat as ground truth):\n"
        f"{history_context}\n\n"
        "The ticket body and comments are in logs/00-ticket.md. Read it and every "
        "other file under logs/, pcaps/, and analysis/. If no evidence covers the "
        "incident window, return Fork D and list what is missing — do not fabricate. "
        "Emit only the Handoff JSON."
    )
```
(Note: the generic "Ground your investigation in the matching runbook under runbooks/" sentence is now replaced by the explicit grounding block; all runbooks remain staged for re-steer.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: PASS — new tests + existing (new params default `None`, so existing callers/tests are unaffected; the retry prompt path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/runner.py tests/test_agent_runner.py
git commit -m "feat(agent): inject selected runbook into the turn prompt"
```

---

### Task 6: System-prompt grounding-protocol wording (prompt.py)

**Files:** Modify `noc_cli/agent/prompt.py`; Test `tests/test_agent_prompt.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_prompt.py`:
```python
def test_system_prompt_mentions_pre_selected_runbook():
    sp = build_system_prompt("## Symptom Class\nx")
    assert "selected" in sp.lower()
    assert "turn prompt" in sp.lower() or "your prompt" in sp.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent_prompt.py -k pre_selected -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `noc_cli/agent/prompt.py`, in `_PROMPT_TEMPLATE`'s "# Grounding protocol" section, replace the first bullet about the operator hypothesis with wording that acknowledges injection. Add this line at the top of the Grounding protocol bullets:
```
- A runbook is usually **pre-selected from the evidence and injected into your
  turn prompt** under "## Selected runbook". Ground your fork in that runbook and
  quote its decisive row. The operator hypothesis is a soft prior, not a verdict.
- **Re-steer freely:** if the evidence contradicts the selected runbook, `Read` a
  different one from `runbooks/` and record why you pivoted in `fork_packet.reasoning`.
```
(Keep the rest of the Grounding protocol section, including the fall-back-to-rubric-core and "full rubric is staged" lines.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_agent_prompt.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/prompt.py tests/test_agent_prompt.py
git commit -m "feat(agent): system prompt acknowledges the pre-selected injected runbook"
```

---

### Task 7: Wire classify → inject → verify in investigate.py

**Files:** Modify `noc_cli/investigate.py`; Test `tests/test_investigate_module.py`.

**Context:** After seeding, the redacted ticket text is at `folder.root / "logs" / "00-ticket.md"`. The classifier picks a runbook; its slug feeds both `seed_history` and the injected runbook; after parse, `verify_grounding` sets the Handoff fields and a usage-style line is appended to `events.jsonl`. **Read `noc_cli/investigate.py` around lines 119–175 first** to match the exact seeding/`run_agent` call shape, and mirror existing tests' invocation in `tests/test_investigate_module.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_investigate_module.py` (mirror the file's existing agent-path harness; the contract asserted is below):
```python
def test_investigate_classifies_injects_and_verifies(monkeypatch, tmp_path):
    import noc_cli.investigate as inv
    from noc_cli.grounding import RunbookSelection

    captured = {}

    async def fake_select(ticket_text, hypothesis, **kw):
        return RunbookSelection(slug="low-audio", confidence="high", rationale="r")

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        # return a RunnerResult whose handoff cites low-audio
        from noc_cli.agent.runner import RunnerResult
        from noc_cli.models import Confidence, ForkLetter, ForkPacket, Handoff, Intake, RunbookReference
        fp = ForkPacket(fork_letter=ForkLetter.B, confidence=Confidence.HIGH,
                        symptom_tag="[low audio]", quoted_rubric_row="row",
                        runbook_reference=RunbookReference(slug="low-audio", section="s"))
        ho = Handoff(rubric_version="t", intake=Intake(ticket_id=1), fork_packet=fp, drafts={})
        return RunnerResult(handoff=ho, raw_result="{}", attempts=1)

    monkeypatch.setattr("noc_cli.grounding.select_runbook", fake_select, raising=True)
    monkeypatch.setattr("noc_cli.agent.runner.run_agent", fake_run_agent, raising=True)
    # drive the existing agent-path entry the other tests use; then assert:
    # assert captured["selected_runbook_slug"] == "low-audio"
    # assert captured["selected_runbook_text"]  # non-empty (runbook injected)
    # and the returned handoff has grounding_verified set (True/False/None, not unset)
```

**Implementer note:** wire `monkeypatch` to the actual call sites used in `investigate.run`/`run_investigation` (both `select_runbook` and `run_agent` are imported lazily inside the function — patch them at their definition modules as shown). Mirror the existing non-fixture invocation in the file. The asserted contract: (1) `run_agent` receives `selected_runbook_slug="low-audio"` and a non-empty `selected_runbook_text`; (2) the final Handoff has `fork_packet.grounding_verified` set by `verify_grounding`. If a clean unit test needs heavy scaffolding, implement the production wiring and write the lightest honest test, reporting DONE_WITH_CONCERNS — do NOT fake the assertion.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_investigate_module.py -k classifies_injects -v`
Expected: FAIL — classifier not wired / `selected_runbook_*` not passed.

- [ ] **Step 3: Implement**

In `noc_cli/investigate.py`, in the non-fixture branch (after `mem_store.init()` / before `seed_history`):
```python
        from noc_cli.grounding import select_runbook  # noqa: PLC0415

        ticket_md = folder.root / "logs" / "00-ticket.md"
        ticket_text = ticket_md.read_text(encoding="utf-8") if ticket_md.exists() else ""
        selection = await select_runbook(ticket_text, initial_hypothesis)
        emit(f"Runbook selected: {selection.slug or '[unclassified]'} ({selection.confidence})")
```
Use the selection for the `seed_history` tag (replace `symptom_tag = initial_hypothesis or "[unclassified]"`):
```python
        from noc_cli.runbooks import DOMAIN_MAP  # noqa: PLC0415

        if selection.slug:
            _slug_to_tag = {s.slug: s.tag for s in DOMAIN_MAP}
            symptom_tag = _slug_to_tag.get(selection.slug, initial_hypothesis or "[unclassified]")
        else:
            symptom_tag = initial_hypothesis or "[unclassified]"
```
Load the selected runbook text and pass both into `run_agent`:
```python
        from noc_cli.runbooks import _load_runbook  # noqa: PLC0415

        selected_runbook_text = _load_runbook(selection.slug) if selection.slug else None
        runner_result = await run_agent(
            ticket_id=ticket_id,
            folder=folder,
            system_prompt=system_prompt,
            history_context=history_context,
            initial_hypothesis=initial_hypothesis,
            selected_runbook_slug=selection.slug,
            selected_runbook_text=selected_runbook_text,
            config=config,
            memory_store=mem_store,
        )
```
After a Handoff is obtained (when `handoff is not None`), verify + record:
```python
        from noc_cli.grounding import verify_grounding  # noqa: PLC0415
        from noc_cli.rubric import load_rubric  # noqa: PLC0415

        verify_text = selected_runbook_text if selection.slug else load_rubric().core
        verified, note = verify_grounding(
            handoff, selected_slug=selection.slug, runbook_text=verify_text
        )
        handoff.fork_packet.grounding_verified = verified
        handoff.fork_packet.grounding_note = note
        try:
            import json as _json  # noqa: PLC0415

            with (folder.root / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(_json.dumps({
                    "type": "grounding",
                    "selected_slug": selection.slug,
                    "confidence": selection.confidence,
                    "verified": verified,
                    "note": note,
                }) + "\n")
        except Exception:  # noqa: BLE001
            pass
```

**Implementer note:** place these blocks to match the actual control flow you read (the `run_agent` call and the `handoff is None` error branch already exist around lines 155–173). The verify/record block runs only on the success path where `handoff` is set.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_investigate_module.py -v`
Expected: PASS (new + existing; fixture-mode tests bypass this path and are unaffected).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/investigate.py tests/test_investigate_module.py
git commit -m "feat(investigate): classify runbook, inject, verify grounding"
```

---

### Task 8: Render the grounding status

**Files:** Modify `noc_cli/render.py`; Test `tests/test_render.py`.

**Context:** `render.py` formats the Handoff (markdown). **Read it first** to find where `fork_packet` (fork letter / runbook_reference) is rendered and add a grounding line in the same block, matching the file's style.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_render.py` (mirror how existing render tests build a Handoff + call the render function):
```python
def test_render_shows_grounding_status():
    from noc_cli.models import Confidence, ForkLetter, ForkPacket, Handoff, Intake, RunbookReference

    fp = ForkPacket(fork_letter=ForkLetter.B, confidence=Confidence.HIGH, symptom_tag="[low audio]",
                    runbook_reference=RunbookReference(slug="low-audio", section="s"))
    fp.grounding_verified = True
    fp.grounding_note = "quoted row found in low-audio"
    ho = Handoff(rubric_version="t", intake=Intake(ticket_id=1), fork_packet=fp, drafts={})

    out = _render_handoff_markdown(ho)  # use the file's actual render entrypoint
    assert "grounded" in out.lower() or "grounding" in out.lower()
    assert "low-audio" in out
```

**Implementer note:** replace `_render_handoff_markdown` with the file's real render function. The contract: the rendered output includes a grounding status (verified → "✓ grounded in <slug>"; False → "⚠ unverified: <note>"; None → "grounding not checked").

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_render.py -k grounding_status -v`
Expected: FAIL — no grounding line in output.

- [ ] **Step 3: Implement**

In `noc_cli/render.py`, in the fork-packet rendering block, add a status line derived from `fork_packet.grounding_verified`:
```python
    gv = fork_packet.grounding_verified
    slug = fork_packet.runbook_reference.slug or "rubric-core"
    if gv is True:
        grounding_line = f"- Grounding: ✓ grounded in `{slug}`"
    elif gv is False:
        grounding_line = f"- Grounding: ⚠ unverified ({fork_packet.grounding_note})"
    else:
        grounding_line = "- Grounding: not checked"
```
and include `grounding_line` in the rendered fork section (append to the same list/section the runbook reference is rendered in, matching the surrounding markdown).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/render.py tests/test_render.py
git commit -m "feat(render): show runbook grounding status in the handoff"
```

---

### Task 9: Full-suite regression + ruff

**Files:** none (verification).

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass (prior count + new tests).

- [ ] **Step 2: Lint/format**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: `All checks passed!` and all files formatted. Fix at source (or `ruff check --fix .` for autofixable) and re-run if needed.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A && git commit -m "style(ruff): tidy runbook-grounding modules" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- `grounding` Haiku profile → Task 1 ✔
- Handoff grounding fields (defaulted, post-hoc) → Task 2 ✔
- Evidence-driven classifier, hypothesis-as-signal, single-best+confidence, low/invalid/error → None → Task 3 ✔
- Soft verification (normalized match; selected/re-steer/rubric-core; error → None) → Task 4 ✔
- Inject selected runbook into the turn prompt (system prompt cache-stable; None → rubric-core framing) → Task 5 ✔
- System-prompt wording acknowledging injection + re-steer → Task 6 ✔
- investigate wiring: classify → seed_history tag + injection + verify + events log → Task 7 ✔
- Render surfacing → Task 8 ✔
- All runbooks stay staged for re-steer → unchanged `stage_runbooks` + re-steer framing (Tasks 5/6) ✔
- No live calls in tests; full regression → Tasks 3–8 use injected `query_fn`/mocks; Task 9 ✔

**Placeholder scan:** Tasks 7 and 8 carry explicit *implementer notes* (mirror the existing investigate agent-path harness / find render's real entrypoint) rather than guessed assertions — the exact local shapes must be read at build time. All production-code steps show complete code.

**Type/name consistency:** `RunbookSelection(slug, confidence, rationale)`, `select_runbook(ticket_text, hypothesis, *, query_fn, options_factory)`, and `verify_grounding(handoff, *, selected_slug, runbook_text) -> (bool|None, str)` are used identically in Tasks 3/4/7. `run_agent`'s new `selected_runbook_slug`/`selected_runbook_text` params match between Task 5 and the Task 7 call. `ForkPacket.grounding_verified`/`grounding_note` (Task 2) match the verifier (Task 4/7) and render (Task 8). The `grounding` profile key (Task 1) matches `profile_for("grounding")` (Task 3).
