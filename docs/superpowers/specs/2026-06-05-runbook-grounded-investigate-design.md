# Runbook-Grounded Investigation — Design Spec

**Date:** 2026-06-05
**Status:** Approved (design) — pending implementation plan
**Command affected:** `noc-cli investigate <id>` — single command, background refinement only. No new subcommands, no schema changes.

---

## 1. Problem

`noc-cli investigate <id>` runs an L3 triage agent that today triages every ticket off the **generic 237-line fork rubric** (`noc_cli/data/fork-rubric.md`), which is force-fed into the system prompt via `build_system_prompt(rubric.text)`.

The six symptom runbooks (`noc_cli/runbooks/*.md`) are **orphaned from the agent**:

- `runbooks/runbook_for_tag()` maps a symptom tag → runbook markdown, but it is **never called** in `cli.py`, `runner.py`, or `prompt.py`.
- The agent is instructed to fill `fork_packet.runbook_reference.slug` + `section` — i.e., to *cite* a runbook — but it never actually reads one.
- The runbook markdowns and the generic rubric overlap: each runbook is a per-symptom expansion of one rubric class table, adding **decisive-evidence checklists** and **stop conditions** the rubric only gestures at.

Running every ticket through one catch-all process produces shallow, less-trustworthy diagnoses. A SIP/UC issue and an Operator-Client bug are investigated differently; a generic process that tries to be all things at once dilutes focus. The goal of building operator trust is served by **domain-scoped grounding**: the agent should reason like a focused specialist in the subject matter actually in hand.

---

## 2. Goals

- Keep the **single** `investigate` invocation; change only what happens in the background.
- Ground the agent in the **relevant** runbook(s) for the symptom being diagnosed, not the full catch-all.
- Stay robust to **nuance and novelty**: tickets that fit no runbook, straddle two, or pivot mid-investigation must degrade gracefully, never break.
- Produce an **inspectable transcript of the agent's logic** (markdown) so the team can iterate on runbooks and prompts.

## 3. Non-goals

- No new subcommands or flags beyond a single non-interactive `--suspect`.
- No changes to the `Handoff` pydantic schema (reuse existing fields).
- The runbook is **grounding + common-case shortcuts, not a rigid decision tree**. The agent retains judgment to reason beyond a runbook's fork table.
- The general triage capability is **never removed** — only layered on top of.

---

## 4. Architecture — Layered grounding (never exclusive)

A naive "classify into one of N domains, load it, done" router is a cage: the first ticket that fits nothing, or straddles two domains, is misrouted or falls through a crack. The architecture is therefore **layered and additive**:

- **Layer 1 — General triage base (always present).** The domain-agnostic core: role, Step 0 intake (customer/site/CNC/region/timestamp/affected-stations/master-ticket/log-coverage), the four fork definitions (A=Engineering, B=Vendor/IT, C=NOC self-resolve, D=cannot-fork-yet), the stop rule, the Inconclusive-over-fabrication rule, the output contract, and the approved tags. This layer alone can triage *anything*, including a symptom no runbook covers.

- **Layer 2 — Domain grounding (added only when a domain is identified).** The relevant runbook(s), loaded on demand, turning a competent generalist into a focused specialist for this ticket. Adds expertise; never removes the base.

- **Layer 3 — Graceful fallback.** When the domain is unknown, ambiguous, spans domains, or matches nothing, the agent runs on Layer 1 alone, tags `[unclassified]` (or `[apex]`, the existing platform catch-all), and **explicitly states it triaged without a specialized runbook**. This path is identical to today's behavior — "outside the runbooks" degrades to exactly the current generic triage, honestly flagged.

The seam already exists in the data: `fork-rubric.md` is structured as **domain-agnostic preamble (Step 0 + fork defs + stop rule)** followed by **per-symptom class tables (Class 1–6)**. The first `## Symptom Class` header is the Layer 1 / Layer 2 boundary.

### Edge cases *within* a domain

When a ticket is clearly in a domain but the evidence matches no row in that runbook's fork table, the runbook is treated as guidance, not a verdict generator: the agent reasons from first principles on the Layer 1 fork definitions and, if still ambiguous, returns Fork D — rather than forcing a bad match to a table row.

---

## 5. Approach — A-guided (analyst-seeded, single-run, dynamic re-steer)

The chicken-and-egg (you must read the ticket to know the domain, but the domain shapes the investigation) is resolved by letting the **human break the loop**: at invocation the analyst states what they suspect, which seeds the initial runbook. The agent then keeps full authority to re-steer as evidence accumulates.

This is Approach A (agent self-grounds in a single run) with a human-provided seed replacing cold self-classification. Consequences:

- **No pre-classifier turn** — stays single-run, no second agent cold-start (~20–30s saved).
- **History seeding fixes itself for free** — today it seeds with `[unclassified]` because "the tag is refined by the agent"; with the seed available up front, history seeds on the *real* suspected tag.

### Rejected alternatives

- **B — two-phase pre-classifier:** strongest auditability and a pre-classified tag for history, but pays a second model cold-start and can lock in a wrong domain before evidence is seen. The analyst seed delivers B's history benefit without the extra turn.
- **C — inject all six runbooks:** simplest, but *is* the catch-all carried into every ticket — the exact thing we are eliminating. Focus would depend entirely on the agent's self-discipline to ignore irrelevant runbooks.

### Behavioral commitments

1. **Seed = soft prior, never a lock.** The agent starts there but is explicitly free to load a different runbook or drop to the base.
2. **Pivots must be narrated.** Leaving the seeded runbook records *why* (the rule-out trail) — this narrative is the trust artifact.
3. **"Not sure" is first-class.** No suspicion → no seed → pure-A self-grounding / general base.
4. **Soft-warn, never reject.** All runbook/slug/quote validation warns into `STATE.md`; it never fails the handoff (consistent with the existing `Rubric.contains_row` philosophy).
5. **Transcript even on failure.** `REASONING.md` renders from the captured stream even when the handoff fails to parse — that is when it is most useful.

---

## 6. Flow

```
banner
 └─ analyst seed prompt        ← six symptoms grouped by domain + "not sure"
                                  (skipped for --suspect / --fixture / --no-agent / non-TTY)
 └─ scaffold + soft-lock        ← stage_runbooks(): copy 6 runbooks + fork-rubric.md into sandbox
 └─ fetch ticket
 └─ gather evidence
 └─ redact PII
 └─ seed history               ← seeded on the analyst's tag (not hardcoded [unclassified])
 └─ run agent (single session) ← Layer-1 base + domain map in system prompt;
                                  honors seed as soft prior; Reads runbooks/<slug>.md;
                                  re-steers + narrates pivots; falls back to base
 └─ render handoff (INTAKE.md, EVIDENCE_PREFLIGHT.md, FORK_PACKET.md, DRAFTS.md, STATE.md)
 └─ render REASONING.md         ← also on the handoff-is-None failure path
 └─ memory append
```

---

## 7. Components

### 7.1 Invocation & analyst seeding

- Fires immediately after `branding.render_banner()`, **before** scaffold/fetch — the analyst acts on a pre-existing suspicion, so we don't block the prompt on a network fetch.
- Reuses the `setup`-style injected `prompt_fn` pattern (`run_setup(prompt_fn=_prompt)`) so the menu is unit-testable without a TTY.
- Menu = **six specific symptoms grouped under domain headers** + "Not sure — let the agent decide":

  ```
  SIP / UC          1) Dropped or failed calls
                    2) Caller number missing (No ANI)
                    3) Caller location missing (No ALI)
  Media             4) Audio / media quality
  Operator Client   5) APEX / station-client behavior
  Data              6) Event history / analytics gap
                    7) Not sure — let the agent decide
  ```

- `--suspect <slug>` provides the seed non-interactively (validated against known slugs with a friendly error). `--fixture`, `--no-agent`, and non-TTY runs default to **no seed** so CI and offline replay never hang.
- The selection is written to `intake.initial_hypothesis` (already rendered by `render.py` `INTAKE.md`) and passed as the symptom tag to history seeding.

### 7.2 Prompt restructure — lean base + domain map

- **System prompt (Layer 1, always-on):** the rubric **core** (`Rubric.core` = text up to the first `## Symptom Class`) + a compact **domain map** (`tag → slug + one-line description`, from `DOMAIN_MAP`) + the **grounding protocol** + output contract + approved tags. The per-symptom Class 1–6 tables are **no longer** in the prompt.
- **Sandbox references:** all six runbooks **and** the full `fork-rubric.md` are copied into `Tickets/<id>/runbooks/`, readable on demand.
- `fork_packet.quoted_rubric_row` now quotes the decisive row from whatever grounding committed the fork — the runbook when one was loaded (runbooks already embed verbatim rubric rows, e.g. `no-ani.md` quotes *"Carrier INVITE has wrong destination data…"*), the rubric core otherwise. `Rubric.contains_row()` is widened to check the quote against *core + runbook text*, still soft-warning.

### 7.3 Runbook staging into the sandbox

The agent's `cwd` is `folder.root` and its file tools are sandbox-scoped by the harness — it cannot read `noc_cli/runbooks/` in the Python package. `stage_runbooks(dest)` copies the six `*.md` + `fork-rubric.md` into the sandbox during `scaffold_ticket()`. All six are copied (≈140 lines total) so re-steering to a different runbook needs no re-scaffold.

### 7.4 Grounding protocol (system-prompt instructions)

The per-ticket seed rides the **turn prompt** (not the static system prompt): `run_agent` weaves `initial_hypothesis` into `full_prompt` ("Analyst's initial hypothesis: `[low audio]` — treat as a starting point, not a verdict…"). The static protocol:

1. Read the ticket + evidence; complete Step 0 intake.
2. Honor the analyst hypothesis as a **soft prior** — `Read runbooks/<slug>.md` for it (or, if "not sure," for the symptom you infer) and ground evidence-gathering + the fork decision in its decisive-evidence / fork-decision / stop-conditions sections.
3. **Re-steer freely:** if evidence does not correlate, load a different runbook or drop to the base — record *why* in `reasoning`.
4. If nothing fits, triage on the base, tag `[unclassified]`/`[apex]`, and state plainly you worked without a specialized runbook.
5. Set `runbook_reference.slug`/`section` to what you actually used (or "consulted, ruled out").

### 7.5 Re-steer, pivot trail, fallback

The pivot narrative in `fork_packet.reasoning` is the trust artifact. Fallback ("not sure" seed, a no-match, or low confidence) is identical to today's behavior: base-only triage, honestly flagged.

### 7.6 Inspectability — hooks + `REASONING.md`

- **Hook:** extend `build_hooks(sandbox_root, events_path)` with a `PostToolUse` hook recording `Read runbooks/<slug>.md` events → `events.jsonl` + a STATE.md "runbooks consulted" line; feeds the slug soft-validation (mismatch → `validator_warning`, never a hard fail).
- **Transcript capture (nearly free):** `_collect_result` already drains the **entire** SDK message stream but keeps only `message.result`, discarding every intermediate turn. We retain those intermediate turns — assistant text + `tool_use` (name + args) + truncated `tool_result`s — as structured transcript entries. `RunnerResult` gains a `transcript` field.
- **`render_reasoning(transcript, handoff, folder)`** writes `Tickets/<id>/REASONING.md`: a chronological interleave of *what the agent reasoned* and *what it ran*, with decision points (hypothesis accepted → runbook loaded → pivot → fork committed) as headers, plus a decision summary derived from the handoff. **Default = curated** (reasoning + tool actions + truncated results); the **full raw stream** is stashed to `.debug/transcript-<ts>.jsonl` so nothing is lost. Renders even when `handoff is None`.

Example `REASONING.md`:

```markdown
# REASONING — Ticket #1234
**Hypothesis:** [low audio] → **Final:** Fork C · [unclassified] · Medium
**Runbooks consulted:** low-audio (ruled out) · **Pivoted:** yes · **Turns:** 7

## 1 · Intake
> Read logs/00-ticket.md · Grep "incident" logs/
Customer X, CNC-123, 14:02–14:10Z. Complaint: "calls sound bad."
Accepting analyst hypothesis [low audio] as starting point.

## 2 · Grounding — low-audio
> Read runbooks/low-audio.md · Glob pcaps/ · Grep "RTP" analysis/
Runbook decisive evidence = full-lifecycle PCAP, jitter/loss per stream.
RTP present, timestamps healthy, no renderer hang. No fork-row matches.

## 3 · Pivot
Complaint is vague, no Call-ID, no repro. Leaving low-audio —
this is an ill-defined user report, not a media defect.

## 4 · Determination — Fork C (self-resolve)

## Decision summary (from handoff)
- Decisive evidence: RTP healthy; no station errors; no matching REP Jira
- Fork: C · Medium · Reasoning: suspected low audio… reclassified user error
```

### 7.7 Data model — no changes

All required fields already exist:

- `IntakeBlock.initial_hypothesis` — the analyst seed (already rendered in `INTAKE.md`).
- `IntakeBlock.site / cnc / region / call_id / incident_window / affected_stations` — mirror the rubric's Step 0 intake.
- `ForkPacket.reasoning` — the pivot / rule-out narrative.
- `ForkPacket.runbook_reference` — the runbook actually used.

---

## 8. Code touchpoints

| File | Change |
|---|---|
| `noc_cli/runbooks/__init__.py` | Add `DOMAIN_MAP` (single source of truth: `tag → slug, domain, description`) driving both the TUI menu and the prompt domain-map block. Add `stage_runbooks(dest)`. Wire `runbook_for_tag()`. |
| `noc_cli/rubric.py` | Add `Rubric.core` (text up to first `## Symptom Class`). Widen `contains_row()` to validate against core + runbook text. |
| `noc_cli/agent/prompt.py` | Restructure `_PROMPT_TEMPLATE`: core + domain map + grounding protocol; drop the full per-symptom tables. `build_system_prompt()` takes `rubric.core`. |
| `noc_cli/scaffold.py` | `scaffold_ticket()` calls `stage_runbooks(folder.root/"runbooks")`. |
| `noc_cli/agent/runner.py` | `run_agent()` gains `initial_hypothesis` (woven into `full_prompt`). `_collect_result` retains the intermediate stream. `RunnerResult` gains `transcript`. Failure path stashes `.debug/transcript-<ts>.jsonl`. |
| `noc_cli/agent/harness.py` | `build_hooks` gains a `PostToolUse` hook recording runbook `Read`s → `events.jsonl` + STATE.md; feeds slug soft-validation. |
| `noc_cli/render.py` | Add `render_reasoning(transcript, handoff, folder)` → `REASONING.md`. STATE.md gains "runbooks consulted / pivoted / validator_warnings". |
| `noc_cli/cli.py` | Add `--suspect <slug>`; fire the six-symptom prompt after banner (skip for `--suspect`/`--fixture`/`--no-agent`/non-TTY); seed replaces hardcoded `[unclassified]` at `cli.py:320`; pass `initial_hypothesis` into `run_agent`; call `render_reasoning` in RENDER, including on the `handoff is None` failure path. |

---

## 9. Testing strategy (TDD)

- **prompt:** domain map (all 6 slugs) + protocol + rubric core present; per-symptom Class tables **absent**.
- **rubric:** `core` ends before `## Symptom Class 1`; `contains_row` accepts runbook quotes.
- **runbooks/scaffold:** `DOMAIN_MAP` covers all 6 tags; `stage_runbooks` lands 6 md + rubric in the sandbox.
- **cli seeding:** `--suspect low-audio` → history seeds `[low audio]` + hypothesis reaches `run_agent`; non-TTY/`--fixture`/`--no-agent` never prompt and fall back to `[unclassified]`; injected `prompt_fn` drives the menu→slug mapping.
- **runner:** scripted `_query_fn` multi-turn stream → `transcript` captures turns + tool calls; `initial_hypothesis` appears in the dispatched prompt.
- **render:** `REASONING.md` carries header (hypothesis, final fork, runbooks consulted, pivoted) + turns + decision summary; renders even when `handoff is None`.
- **harness:** a `Read runbooks/…` event lands in STATE.md "runbooks consulted"; slug mismatch → soft `validator_warning`, never a hard fail.
- **e2e:** existing `handoff_good.json` fixture still passes; add a **pivot fixture** (hypothesis ≠ final tag) asserting `REASONING.md` shows the rule-out.

---

## 10. Error handling

- Transcript capture and `REASONING.md` rendering are **best-effort** (like the redact pass): a failure logs and continues; the handoff report stays the primary deliverable.
- `--suspect` validates against known slugs with a friendly error.
- All runbook / slug / quote validation stays **soft-warn into STATE.md**, never rejecting the handoff.

---

## 11. Canonical example

Analyst suspects audio on ticket #1234 → seed `[low audio]` → agent loads `low-audio.md`, gathers RTP/jitter/station evidence + customer + version history → no fork-row correlates → agent pivots, recognizing a poorly-defined customer complaint → lands on **Fork C (self-resolve)** via the Layer 1 base (a verdict no runbook covers) → `reasoning` documents the rule-out, `symptom_tag` → `[unclassified]`, `runbook_reference` notes low-audio was consulted and ruled out, and `REASONING.md` renders the full chain for iterative review.
