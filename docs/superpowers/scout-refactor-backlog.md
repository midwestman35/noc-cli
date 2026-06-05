# Backlog Scout — Refactor Backlog

Prioritized follow-up work from the thermo-nuclear code quality review of `feat/backlog-scout-engine` (2026-06-05). Items are sorted **P0 → P3**; lower numbers ship first.

**Status key:** `done` = addressed on branch; `open` = still pending.

---

## P0 — Operator-facing / correctness gaps

| # | Item | Source | Status | Notes |
|---|------|--------|--------|-------|
| 1 | **Scout error handling** — catch `ZendeskError`, `ZendeskWriteError`, `httpx.HTTPError`, and agent failures; map to red stderr + exit code 1 (preflight aborts stay exit 2) | Critical C1 | **done** | `scout` command in `cli.py`; tests in `test_cli_scout.py` |
| 2 | **Runner hook integration test** — default `screen_options_factory` / `synth_options_factory` path must assert `build_hooks(..., restrict_read_tools=True)` | Critical C2 | **done** | `test_run_scout_default_factories_build_restricted_hooks` |

---

## P1 — High ROI structure (next sprint)

| # | Item | Source | Status | Notes |
|---|------|--------|--------|-------|
| 3 | **Extract Scout orchestration from `cli.py`** — move `_run_scout_report`, `_take_*`, `_preflight_*`, `_make_writer`, `_resolve_owner_id`, `_invoke_investigate` into `noc_cli/scout/commands.py` and `noc_cli/scout/take.py`; leave `scout()` as parse + dispatch (~15 lines) | Important I1, I6; Refactor #2 | open | Stops CLI monolith growth; `cli.py` is 689 lines (+31% on this branch) |
| 4 | **Single hooks factory** — `build_scout_options(workspace) -> (screen_factory, synth_factory)` in `profiles.py` or `scout/hooks.py` | Important I2; Refactor #3 | open | Eliminates duplication between `screen.py` and `runner.py` |
| 5 | **Shared take eligibility** — `take_ineligible_reason(ticket, *, now, min_staleness_days) -> str \| None` in `rank.py` (or `scout/take.py`), shared with `rank_candidates` filters | Important I4; Refactor #5 | open | One ruleset for list + `--take` preflight |
| 6 | **Shared LLM I/O helpers** — extract `_JSON_FENCE_RE`, `_extract_json`, `_final_result` to `noc_cli/scout/llm_io.py` | Important I3; Refactor #4 | open | ~40 LOC duplicated in `screen.py` and `synthesize.py` |

---

## P2 — Maintainability and observability

| # | Item | Source | Status | Notes |
|---|------|--------|--------|-------|
| 7 | **Typed injectable boundaries** — `Protocol` for Scout Zendesk reader (`view_tickets`, `get_ticket`); type `query_fn`, `cwd` as `Path` | Important I5; Refactor #6 | open | Enables static checking; documents contracts |
| 8 | **Surface dropped screen count** — when `len(reports) < len(candidates)`, add metadata to `ScoutReport` or stderr warning | Important I7; Refactor #8 | open | Operators trust the report when LLM JSON parse fails silently |
| 9 | **CLI tests against public Scout API** — reduce patching of private `noc_cli.cli._*` helpers | Minor (test_cli_scout) | open | Follows naturally from #3 |
| 10 | **Rename `acquire.py` → `writer.py`** (or `zendesk_write.py`) | Minor; Refactor #9 | open | File only holds `ZendeskWriter`; name suggests fetch |

---

## P3 — Pre-existing debt / deferred

| # | Item | Source | Status | Notes |
|---|------|--------|--------|-------|
| 11 | **Split `investigate` out of `cli.py`** — `_run_investigate` is ~220 lines | Refactor #10; 1k-line rule | open | Required before the next large CLI feature; not Scout-specific |
| 12 | **Move `_now_utc()` out of `cli.py`** | Minor | open | Trivial util; belongs in scout or shared `time` helper |
| 13 | **Document `permission_mode: bypassPermissions`** — one-line comment that hooks are the real gate for Scout profiles | Minor | open | Acceptable today; clarity for future readers |
| 14 | **Synthesis `cwd` default** — `synthesize(..., cwd=".")` is untyped; runner always overrides via factory | Minor | open | Low risk while synthesis has no tools |
| 15 | **Optional: single `get_ticket` on `--take`** | Minor | open | Double fetch is correct for TOCTOU; optimize only if latency matters |

---

## Suggested implementation order

1. ~~P0 (#1–2)~~ — complete.
2. **P1 batch A:** #4 (hooks factory) + #6 (llm_io) — small, removes duplication without moving CLI surface.
3. **P1 batch B:** #3 + #5 — extract `scout/commands.py` and `scout/take.py` with shared eligibility.
4. **P2:** #7–#10 as needed before wider operator rollout or mypy enforcement.
5. **P3:** #11 when adding the next major CLI command; rest are opportunistic.

---

## References

- Branch: `feat/backlog-scout-engine`
- Implementation plan: [2026-06-05-backlog-scout-engine.md](./plans/2026-06-05-backlog-scout-engine.md)
- Review score at time of backlog: **76/100** (ship after P0; P1 reduces CLI debt)
