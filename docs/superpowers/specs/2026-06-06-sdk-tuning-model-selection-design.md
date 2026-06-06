# SDK Tuning — Model Selection + Caching Observability — Design

**Date:** 2026-06-06
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Layer:** Layer 2 — the application's runtime agents. See [[layer1-vs-layer2-tooling]].
**Sub-project:** 3 of 3 (final). #1 live read-only tools = DONE (PR #9 merged). #2 deeper runbook grounding = held.

## Goal

Centralize per-surface **model selection** (pinned defaults, env-overridable, with fallback) across all three model-using surfaces, and add **prompt-cache / usage observability**, for cost control and consistent behaviors.

## Context (current state)

Three surfaces construct `ClaudeAgentOptions` and hit a model:
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
3. **Approach B:** a central registry with **pinned defaults + a thin env-override layer** (`NOC_MODEL_<SURFACE>`). Pinned IDs serve "consistent behaviors"; env overrides give operators cost-control / version-bump flexibility without code changes.
4. **Caching = observability, not a knob:** keep each surface's prefix stable (the runner already reuses one `system_prompt` across both attempts ✓); log `usage`/`total_cost_usd`/`model_usage` from the terminal `ResultMessage` to `events.jsonl`. Best-effort — never breaks a run.
5. **Investigate `fallback_model`** = Sonnet, for graceful degradation if Opus is unavailable.
6. **`effort` is in scope** (already part of the `Profile` pattern). **`thinking` / `max_thinking_tokens` / `max_budget_usd` are out** (YAGNI).

## Architecture & file structure

| File | Responsibility |
|---|---|
| `noc_cli/profiles.py` *(new)* | The central registry. `ModelProfile(model, fallback_model=None, effort="medium")`; `_DEFAULTS` dict for the 4 surfaces; `profile_for(surface) -> ModelProfile` applying the `NOC_MODEL_<SURFACE>` env override and emitting the resolved model via `logging`. The four model IDs live **only here**. |
| `noc_cli/usage.py` *(new)* | `log_usage(events_path, result_message) -> None` — best-effort append of one `{"type":"usage", …}` line to `events.jsonl`. Wrapped so any failure (missing fields, unwritable path) is swallowed. |
| `noc_cli/agent/runner.py` *(modify)* | Read `profile_for("investigate")`; set `model` / `fallback_model` / `effort` on `ClaudeAgentOptions`. Call `log_usage(events_path, message)` in `_drain` when the terminal `ResultMessage` is seen. |
| `noc_cli/tui/chat.py` *(modify)* | Read `profile_for("chat")`; set `model` (+ `effort`) on its `ClaudeAgentOptions`. Call `log_usage` where the result is drained. |
| `noc_cli/scout/profiles.py` *(modify)* | `SCREEN`/`SYNTHESIS` source their `model` from `profile_for("scout_screen"/"scout_synth").model` — **behavior unchanged** (still haiku / opus); the IDs just stop being duplicated. |

The registry centralizes *model settings* (model / fallback / effort) only; each surface keeps its own `ClaudeAgentOptions` construction (their tool sets, mcp_servers, client-vs-query shapes differ). This avoids re-refactoring the just-merged `runner.py` option-building.

## Data flow

1. At option-build time, a surface calls `profile_for(<surface>)` → an env-resolved `ModelProfile`, and sets `model` / `fallback_model` / `effort`.
2. After the run, the terminal `ResultMessage` (already drained by `runner._drain` / scout `final_result` / chat) is passed to `log_usage(events_path, message)`, appending a usage line to `events.jsonl`:
   ```json
   {"type":"usage","model":"claude-opus-4-8","usage":{"input_tokens":…,"cache_read_input_tokens":…,"cache_creation_input_tokens":…,"output_tokens":…},"total_cost_usd":0.0,"num_turns":N}
   ```

## Error handling

- **Fallback:** `fallback_model` (Opus→Sonnet) lets an Opus outage degrade rather than fail the investigate run.
- **Bad env override:** an invalid `NOC_MODEL_*` value is operator error; the resolved model is emitted via `logging` at resolution time and recorded in every usage line, so it's visible. No validation gate (the SDK call surfaces the error).
- **Usage logging:** fully best-effort — `log_usage` catches all exceptions internally (mirrors the existing `events.jsonl` PostToolUse logging discipline). A logging failure never affects the agent result.

## Testing (no live model calls)

- **`profiles.py`:** pinned defaults are correct for all 4 surfaces; `NOC_MODEL_INVESTIGATE=x` override is applied by `profile_for`; investigate carries `fallback_model="claude-sonnet-4-6"`; scout surfaces still resolve to haiku / opus.
- **`usage.py`:** `log_usage` writes a parseable `{"type":"usage",…}` line given a fake `ResultMessage` with `usage`/`total_cost_usd`; is best-effort — a `ResultMessage` missing `usage`, or an unwritable path, produces no exception and no crash.
- **`runner.py` / `chat.py`:** captured `ClaudeAgentOptions` carry the registry's `model` / `fallback_model` / `effort` (same `_query_fn` / options-capture injection pattern as sub-project #1).
- **`scout/profiles.py`:** `SCREEN.model == "claude-haiku-4-5"` and `SYNTHESIS.model == "claude-opus-4-8"` still hold (regression guard that centralization changed nothing).

## Scope / non-goals

- **In:** centralized model selection (4 surfaces; pinned + `NOC_MODEL_<SURFACE>` override + investigate fallback + per-surface `effort`); caching/usage observability via `log_usage`.
- **Out:** `thinking` / `max_thinking_tokens`, `max_budget_usd` cost caps, any change to Scout's actual model choices, any change to system-prompt content or tool sets.
