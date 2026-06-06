# SDK Tuning — Model Selection + Caching Observability — Design

**Date:** 2026-06-06
**Status:** Reviewed and revised (ready for implementation plan)
**Layer:** Layer 2 — the application's runtime agents. See [[layer1-vs-layer2-tooling]].
**Sub-project:** 3 of 3 (final). #1 live read-only tools = DONE (PR #9 merged). #2 deeper runbook grounding = held.

## Goal

Centralize per-surface **model selection** (pinned defaults, env-overridable, with fallback) across all model-using surfaces, and add **prompt-cache / usage observability**, for cost control and consistent behavior without changing prompts, tools, or ticket evidence flow.

## Context (current state)

Three surface families construct `ClaudeAgentOptions` and hit a model; Scout has two distinct profiles, so the registry contains four model profiles:
- **Investigate / triage** (`noc_cli/agent/runner.py`): sets **no `model`** → SDK/CLI default. Highest-stakes runs (produces the Handoff JSON).
- **Chat** (`noc_cli/tui/chat.py`): sets **no `model`** → default. Per-ticket conversational helper (tools: Read/Glob/Grep/LS).
- **Scout** (`noc_cli/scout/profiles.py`): **already differentiated** via a frozen `Profile(model, effort, max_turns, allowed_tools)` + `build_options(profile, …)` — `SCREEN = claude-haiku-4-5` (effort medium), `SYNTHESIS = claude-opus-4-8` (effort high).

No explicit prompt-caching control exists in the Agent SDK — the engine caches the stable system-prompt + tools prefix automatically. Caching is therefore an *observability* concern, not a switch.

## Verified facts (claude-agent-sdk 0.2.88, installed)

- `ClaudeAgentOptions` exposes `model: str | None`, `fallback_model: str | None`, `effort: 'low'|'medium'|'high'|'xhigh'|'max'`, `max_thinking_tokens`, `thinking`, `max_budget_usd` (last three out of scope).
- `ResultMessage` exposes `usage`, `total_cost_usd`, `model_usage`, `num_turns` (plus `duration_ms`, etc.). `usage` carries cache-read / cache-creation input-token counts.
- `noc_cli/scout/profiles.py`: `Profile` frozen dataclass + `build_options(profile, *, system_prompt, cwd, hooks=None, options_cls=None)` already sets `model` and `effort` from the profile.
- `noc_cli/config.py`: env wiring via a `_FIELD_ENV` map + pydantic `Config` + dotenv; env-var convention is `NOC_*` / `ZENDESK_*`.

## Decisions (all approved during brainstorming)

1. **Scope:** both investigate and chat get explicit, differentiated models; Scout's choices are left unchanged but centralized.
2. **Model tiers (pinned defaults):**
   | Surface | model | fallback_model | effort |
   |---|---|---|---|
   | `investigate` | `claude-opus-4-8` | `claude-sonnet-4-6` | high |
   | `chat` | `claude-sonnet-4-6` | — | medium |
   | `scout_screen` | `claude-haiku-4-5` (existing) | — | medium |
   | `scout_synth` | `claude-opus-4-8` (existing) | — | high |
3. **Approach B:** a central registry with **pinned defaults + a thin env-override layer** (`NOC_MODEL_<SURFACE>`). Pinned IDs serve "consistent behaviors"; env overrides give operators cost-control / version-bump flexibility without code changes. Exact override names are `NOC_MODEL_INVESTIGATE`, `NOC_MODEL_CHAT`, `NOC_MODEL_SCOUT_SCREEN`, and `NOC_MODEL_SCOUT_SYNTH`.
4. **Caching = observability, not a knob:** keep each surface's prefix stable (the runner already reuses one `system_prompt` across both attempts; Chat keeps one `ClaudeSDKClient` per open ticket; Scout keeps its current cold-start-per-call behavior). Log `usage`/`total_cost_usd`/`model_usage` from the terminal `ResultMessage` to `events.jsonl`. Best-effort — never breaks a run.
5. **Investigate `fallback_model`** = Sonnet, for graceful degradation if Opus is unavailable.
6. **`effort` is in scope** (already part of the `Profile` pattern). **`thinking` / `max_thinking_tokens` / `max_budget_usd` are out** (YAGNI).

## Architecture & file structure

| File | Responsibility |
|---|---|
| `noc_cli/model_profiles.py` *(new)* | The central registry. `ModelProfile(model, fallback_model=None, effort="medium", source="default")`; `_DEFAULTS` dict for the 4 profiles; `profile_for(surface) -> ModelProfile` applying the exact `NOC_MODEL_<SURFACE>` override and emitting the resolved model via `logging`. Reads process env first, then the data-dir `.env` via `config_path()`; blank values are ignored. The four pinned model IDs live **only here**. |
| `noc_cli/usage.py` *(new)* | `log_usage(events_path, *, surface, profile, result_message, attempt=None) -> None` — best-effort append of one `{"type":"usage", ...}` line to `events.jsonl`. Wrapped so any failure (missing fields, unwritable path) is swallowed. |
| `noc_cli/agent/runner.py` *(modify)* | Read `profile_for("investigate")`; set `model` / `fallback_model` / `effort` on `ClaudeAgentOptions`. Call `log_usage(..., surface="investigate", attempt=1/2)` in `_drain` when the terminal `ResultMessage` is seen, logging both attempts if retry occurs. |
| `noc_cli/tui/chat.py` *(modify)* | Read `profile_for("chat")`; set `model` (+ `effort`) on its `ClaudeAgentOptions`. Capture the terminal `ResultMessage` while draining `receive_response()`, then call `log_usage(..., surface="chat")` in the same abort-safe `finally` path that persists the agent turn. |
| `noc_cli/scout/profiles.py` *(modify)* | `SCREEN`/`SYNTHESIS` source their `model` and `effort` from `profile_for("scout_screen"/"scout_synth")` — **behavior unchanged** (still haiku / opus); the IDs just stop being duplicated. |
| `noc_cli/scout/llm_io.py`, `screen.py`, `synthesize.py` *(modify)* | Preserve the existing `final_result(query_gen) -> str` API but add an optional terminal-result callback, or equivalent helper, so Scout can pass each terminal `ResultMessage` to `log_usage` without changing JSON parsing behavior or concurrency semantics. |

The registry centralizes *model settings* (model / fallback / effort) only; each surface keeps its own `ClaudeAgentOptions` construction (their tool sets, mcp_servers, client-vs-query shapes differ). This avoids re-refactoring the just-merged `runner.py` option-building.

## Data flow

1. At option-build time, a surface calls `profile_for(<surface>)` -> an env-resolved `ModelProfile`, and sets `model` / `fallback_model` / `effort`.
2. The resolved profile includes `source="default" | "env" | "dotenv"` and the override variable name, which usage logs record. Invalid nonblank override values are intentionally passed through to the SDK so operator mistakes fail loudly instead of being silently rewritten.
3. After each model call, the terminal `ResultMessage` (drained by `runner._drain`, chat's `receive_response()`, or Scout's `final_result` helper) is passed to `log_usage(...)`, appending a usage line to `events.jsonl`:
   ```json
   {"type":"usage","surface":"investigate","attempt":1,"model":"claude-opus-4-8","fallback_model":"claude-sonnet-4-6","effort":"high","profile_source":"default","usage":{"input_tokens":123,"cache_read_input_tokens":456,"cache_creation_input_tokens":78,"output_tokens":90},"model_usage":{},"total_cost_usd":0.0,"num_turns":2,"session_id":"..."}
   ```
4. `log_usage` serializes only JSON-safe values (`usage`, `model_usage`, cost, turns, duration/session fields if present). Missing SDK fields are omitted, not treated as errors. The cache signal is taken directly from `usage` keys; no implementation relies on a computed cache-hit ratio.

## Error handling

- **Fallback:** `fallback_model` (Opus→Sonnet) lets an Opus outage degrade rather than fail the investigate run.
- **Bad env override:** an invalid nonblank `NOC_MODEL_*` value is operator error; the resolved model and source are emitted via `logging` at resolution time, and successful calls record the profile in usage lines. No validation gate (the SDK call surfaces the error). Blank/whitespace overrides are ignored so a partially edited `.env` does not accidentally select an empty model.
- **Usage logging:** fully best-effort — `log_usage` catches all exceptions internally (mirrors the existing `events.jsonl` PostToolUse logging discipline). A logging failure never affects the agent result or Chat persistence.
- **Fallback visibility:** usage lines record the requested `model` and `fallback_model`; if the SDK later exposes an actual served model separately, add it as `served_model` without changing the existing fields.

## Testing (no live model calls)

- **`model_profiles.py`:** pinned defaults are correct for all 4 profiles; exact env names resolve; process env beats data-dir `.env`; blank overrides fall back to defaults; unknown surface raises a clear `KeyError` or `ValueError`; investigate carries `fallback_model="claude-sonnet-4-6"`; scout surfaces still resolve to haiku / opus.
- **`usage.py`:** `log_usage` writes a parseable `{"type":"usage", ...}` line given a fake `ResultMessage` with `usage`/`total_cost_usd`/`model_usage`; omits missing optional fields; preserves `surface`, `attempt`, `model`, `fallback_model`, `effort`, and `profile_source`; is best-effort — a missing `usage`, unserializable SDK field, or unwritable path produces no exception and no crash.
- **`runner.py`:** captured `ClaudeAgentOptions` carry the registry's `model` / `fallback_model` / `effort`; both first-attempt success and retry paths emit usage for each terminal result without changing parse/retry behavior.
- **`chat.py`:** captured `ClaudeAgentOptions` carry `model` / `effort`; a mocked `ClaudeSDKClient` result emits usage in the abort-safe persistence path; the existing abort-persistence test still passes.
- **`scout/profiles.py` / `scout/llm_io.py`:** `SCREEN.model == "claude-haiku-4-5"` and `SYNTHESIS.model == "claude-opus-4-8"` still hold; screen and synthesis calls can log terminal usage without changing `final_result()`'s current string return or JSON extraction behavior.

## Scope / non-goals

- **In:** centralized model selection (4 profiles; pinned + `NOC_MODEL_<SURFACE>` override + investigate fallback + per-surface `effort`); caching/usage observability via `log_usage`.
- **Out:** `thinking` / `max_thinking_tokens`, `max_budget_usd` cost caps, any change to Scout's actual model choices, any change to system-prompt content or tool sets.

## Review score

Initial review score: **86/100**.

Primary gaps found:
- Scout usage observability was claimed in the goal but not wired in the architecture; `final_result()` currently returns only text and drops the terminal message object.
- The env override layer did not say whether data-dir `.env` values work; relying only on process env would be surprising in this repo.
- Usage events lacked `surface`, `attempt`, profile source, and `fallback_model`, making them weak for cost attribution and retry/fallback diagnosis.
- Chat usage logging needed placement that preserves the existing abort-persistence behavior.

Revised score: **96/100**.

The remaining nonblocking risk is that the design intentionally passes invalid model overrides through to the SDK instead of validating model IDs locally. That is acceptable for this slice because model IDs change over time, local validation would become stale, and the SDK failure path is the right source of truth.
