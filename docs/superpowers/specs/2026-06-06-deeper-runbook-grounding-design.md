# Deeper Runbook Grounding — Design

**Date:** 2026-06-06
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Layer:** Layer 2 — the application's runtime agent. See [[layer1-vs-layer2-tooling]].
**Sub-project:** 2 of 3 (final remaining). #1 live read-only tools = MERGED (PR #9). #3 SDK tuning = open in PR #10.

**Dependency:** builds on #3's `noc_cli/model_profiles.py` (adds a `grounding` profile). This branch (`feat/runbook-grounding`) is **stacked on `feat/sdk-tuning`**; rebase onto `main` once PR #10 merges.

## Goal

Make the L3 triage agent's runbook grounding **reliable** by (1) **selecting** the right runbook from the ticket evidence with a cheap LLM classifier instead of relying on the operator's hypothesis tag, and (2) enforcing **adherence** by injecting the selected runbook into the prompt and softly verifying the agent cited it.

## Context (current state)

Grounding today (`noc_cli/runbooks/`, `noc_cli/agent/prompt.py`):
- `stage_runbooks()` copies all 6 thin domain runbooks (~1.6–1.8 KB: *"what X means" / "decisive evidence" / "fork decision" / "exclusions"*) + the full `fork-rubric.md` into the ticket folder.
- The system prompt embeds a static tag→runbook **domain map** + the rubric core.
- The agent **self-selects** a runbook from the operator's `initial_hypothesis`/symptom tag and `Read`s it, re-steering if it doesn't fit, falling back to the rubric core.

**The two pains:** *Selection* — grounding starts from the operator's tag, which may be wrong/missing. *Adherence* — the agent self-selects via `Read` and may skim or skip, so forks aren't truly grounded.

## Verified facts

- `ForkPacket` fields include `symptom_tag`, `quoted_rubric_row`, `runbook_reference` (`RunbookReference{slug, section}`), `reasoning`. Adding defaulted fields keeps existing Handoff JSON/fixtures valid.
- `noc_cli/model_profiles.py` (from #3): `profile_for(surface) -> ModelProfile`; surfaces `investigate/chat/scout_screen/scout_synth`; `NOC_MODEL_<SURFACE>` override. Adding a `grounding` surface follows the same pattern.
- `noc_cli/runbooks/__init__.py`: `RUNBOOK_SLUGS`, `DOMAIN_MAP` (6 `Symptom(tag, slug, domain, label)`), `runbook_for_tag(tag) -> (slug, text) | None`, `_load_runbook(slug)`.
- `noc_cli/agent/runner.py`: builds `full_prompt` (turn prompt) with hypothesis + history; `run_agent(...)` is injectable via `_query_fn` for tests.
- `noc_cli/investigate.py`: after seeding, computes `symptom_tag = initial_hypothesis or "[unclassified]"`, runs `seed_history(symptom_tag, ...)`, then `run_agent(...)`.

## Decisions (all approved during brainstorming)

1. **Selection = LLM classifier (Haiku).** A dedicated single-ticket classifier reads redacted intake + the operator hypothesis (as **one signal**, not a verdict) and picks the **single best** runbook + a confidence. `confidence == low` or no valid slug → `None` (`[unclassified]`, rubric-core-only). Operator hypothesis stays a signal.
2. **Dedicated module** (`noc_cli/grounding.py`), NOT reuse of Scout's `screen_ticket` (Scout is batch-oriented and left as-is per #3).
3. **Adherence = inject + soft verify.** Inject the selected runbook's full text into the **turn prompt** (the variable suffix — keeps #3's cached system-prompt prefix stable). After parse, softly verify the agent's citation; record `grounding_verified` + `grounding_note` on the Handoff **and** mirror to `events.jsonl`. **No retry** on mismatch (soft flag only).
4. **All 6 runbooks stay staged** — the agent can re-steer to a different one if evidence contradicts the classifier, recording why in `reasoning`.
5. **Grounding fields on the `Handoff`** (`ForkPacket.grounding_verified`, `grounding_note`), set post-hoc by the verifier (defaulted so the agent needn't emit them).
6. **Bonus:** feed the classified symptom to `seed_history` (today it uses the raw hypothesis), improving history candidates too.

## Architecture & file structure

| File | Responsibility |
|---|---|
| `noc_cli/grounding.py` *(new)* | `RunbookSelection(slug: str \| None, confidence: str, rationale: str)`; `select_runbook(ticket_text, hypothesis, *, query_fn=None, options_factory=None) -> RunbookSelection` (Haiku screen); `verify_grounding(handoff, *, selected_slug, runbook_text) -> tuple[bool \| None, str]`. |
| `noc_cli/model_profiles.py` *(modify)* | Add `"grounding": ModelProfile("claude-haiku-4-5", None, "medium")`. |
| `noc_cli/models.py` *(modify)* | `ForkPacket`: add `grounding_verified: bool \| None = None`, `grounding_note: str = ""`. |
| `noc_cli/agent/prompt.py` *(modify)* | Grounding-protocol language for the injected runbook (ground in it, quote its decisive row, re-steer only with a recorded reason). |
| `noc_cli/agent/runner.py` *(modify)* | New params `selected_runbook_slug: str \| None`, `selected_runbook_text: str \| None`; inject the runbook text into the turn prompt. System prompt unchanged (cache-stable). |
| `noc_cli/investigate.py` *(modify)* | Run `select_runbook` after seeding/before `run_agent`; use the result for the injected runbook **and** the `seed_history` tag; run `verify_grounding` after parse; set the fields + log to `events.jsonl`. |
| `noc_cli/render.py` *(modify)* | One-line grounding status (✓ grounded in `<slug>` / ⚠ unverified: `<note>`). |

## Data flow

1. Seed evidence (unchanged).
2. **Classify:** `select_runbook(redacted_ticket_text, initial_hypothesis)` (Haiku) → `RunbookSelection(slug, confidence, rationale)`. Low-confidence / invalid slug → `slug=None`.
3. Use the classified symptom tag for `seed_history` (falls back to the hypothesis / `[unclassified]` when `slug is None`).
4. Load the selected runbook text (`_load_runbook(slug)`); pass `selected_runbook_slug` + `selected_runbook_text` to `run_agent` → injected in the turn prompt.
5. Agent investigates grounded in the injected runbook; may re-steer to another staged runbook (records why in `reasoning`).
6. Parse Handoff → `verify_grounding(handoff, selected_slug=slug, runbook_text=...)` → set `grounding_verified` + `grounding_note`; mirror to `events.jsonl`.
7. Render shows the grounding status line.

## Classifier (`select_runbook`)

A cheap Haiku call (`profile_for("grounding")`). System prompt: "classify this 911 ticket into exactly one runbook symptom class, or none if nothing fits." Input: redacted ticket text + the 6-runbook domain map + the operator hypothesis (labeled a signal, not a verdict). Output: JSON `{slug, confidence: "high"|"medium"|"low", rationale}`, parsed + validated against `RUNBOOK_SLUGS`. `confidence == "low"` or slug ∉ `RUNBOOK_SLUGS` → `slug=None`. `query_fn`/`options_factory` injectable for tests (no live calls), mirroring runner/scout.

## Injection & adherence

The selected runbook's full text is injected into the **turn prompt** with explicit framing: "This runbook was selected from the evidence — ground your fork in it and quote its decisive row verbatim into `quoted_rubric_row`; set `runbook_reference.slug` to it. Re-steer to a different *staged* runbook only if the evidence contradicts this one, and explain the pivot in `reasoning`." When `slug is None`: no injection; instruct rubric-core-only triage tagged `[unclassified]` (current behavior). All 6 runbooks remain staged.

## Verification (soft flag)

`verify_grounding`: normalize (case-fold, collapse whitespace) the Handoff's `quoted_rubric_row`; `grounding_verified = True` iff it appears in the cited runbook's text — where "cited" is the selected runbook, or another staged runbook when `runbook_reference.slug` indicates a deliberate re-steer (load that slug's text and check there). Else `False` with a `grounding_note` (e.g. `quoted row not found in 'low-audio'; possible drift`). When `slug is None` (rubric-core path) verification targets the rubric core text. Any error → `(None, "verification skipped")`. Never blocks; recorded on the Handoff + `events.jsonl`.

## Error handling

- Classifier failure (network/parse/timeout) → treated as low-confidence → `slug=None`; the investigation proceeds rubric-core-only exactly as today (no crash).
- Verification is best-effort: any exception → `grounding_verified=None`, note "verification skipped."
- A wrong classification is recoverable: the agent can re-steer (all runbooks staged), and verification surfaces persistent mismatches in the log for later tuning.

## Testing (no live model calls)

- `select_runbook`: injected `query_fn` returning a fake classification → correct slug/confidence/rationale; `confidence=="low"` → `None`; invalid/unknown slug → `None`; hypothesis influences input but a contradicting evidence classification wins; classifier error/garbage → `None`.
- `verify_grounding`: exact + normalized (case/whitespace) match → `True`; quote absent → `False` + note; re-steer to another staged runbook whose text contains the quote → `True`; rubric-core path (`slug=None`) checks rubric text; exception → `None`.
- `runner`: when `selected_runbook_text` is provided it appears in the turn prompt; when `None` it does not; the system prompt is unchanged (cache-stable) in both cases; existing runner tests unaffected (new params default `None`).
- `investigate`: classifier runs before `run_agent`; the selection is threaded into `run_agent` and into the `seed_history` tag; `verify_grounding` sets the Handoff fields (mocked `run_agent` + classifier).
- `models`/`render`: defaulted `grounding_*` fields validate existing Handoff fixtures; render shows the status line for verified / unverified / skipped.

## Scope / non-goals

- **In:** Haiku evidence-driven runbook selection (hypothesis as a signal); turn-prompt injection of the selected runbook; soft grounding verification on the Handoff + events; render surfacing; reuse of the classified symptom for history seeding; a `grounding` model profile.
- **Out:** authoring richer runbook *content* (separate effort); retrieval/embeddings (a classifier over 6 classes suffices); multi-runbook selection; retry-on-mismatch (soft flag only); any change to the rubric, the fork definitions, or Scout.
