# In-TUI Backlog Scout Panel — Design

**Date:** 2026-06-06
**Status:** Design approved; implementation plan to follow.
**Branch:** `feat/scout-tui-panel` (based on `feat/command-autocomplete` / PR #8)

## Goal

Make `/scout` in the watch TUI run the existing Backlog Scout engine and show
the ranked attention report in the detail pane, and let the operator claim a
candidate (`/take <id>`) with an explicit confirm — replacing today's
*"Scout runs from the CLI for now"* stub. The single write is changing the
Zendesk **Assignee** field of the claimed ticket to the current user, after
which the TUI starts investigating it in-process.

## Background

The Backlog Scout engine shipped in PR #6 and runs today via `noc-cli scout`.
But the CLI command is deprecated and its warning points users to
*"`/scout` inside the TUI"* — while `/scout` inside the TUI points back to the
CLI. The two halves point at each other in a loop. This closes it.

The engine is complete and exposes clean seams (`noc_cli/scout/commands.py`):

- `run_scout_report(cfg, *, top_k, min_staleness_days) -> ScoutReport` — runs the
  full read-only pipeline (blocking, agent-backed → must run off the UI thread).
- `render_scout_report(report) -> str` — plain-text render of the ranking.
- `preflight_current_ticket(cfg, *, ticket_id, min_staleness_days) -> str | None`
  — re-reads the ticket now; returns an ineligibility reason or `None`.
- `resolve_owner_id(cfg) -> int | None` — the current user's Zendesk id.
- `make_writer(cfg).assign_ticket(ticket_id, assignee_id)` — the **only** Zendesk
  write in noc-cli: `PUT /tickets/{id}.json` with `{"ticket": {"assignee_id": …}}`
  and nothing else (group and all other fields untouched).

## Scope

**In scope:** `/scout` runs the report in a worker and renders it as a detail-pane
takeover; `/take <id>` claims a candidate via preflight → Enter-confirm →
TOCTOU re-preflight → assignee write → in-TUI investigation.

**Out of scope (Backlog Scout roadmap):** auto-eval on each poll, graduated
autonomy (auto-claim high-confidence), persisted `SCOUT.md`, the ranking
metric-feedback loop (all banked in `interactive-feat.md` §9.6); plus in-panel
arrow-select of candidates, a full-screen Scout mode, and TUI-tunable
`top_k`/`min_staleness` (fixed at 8 / 7 here).

## Architecture

A thin orchestration layer in `noc_cli/tui/watch_app.py` over the existing engine
seams — **no new engine logic.** The CLI's `take_ticket` is deliberately *not*
reused: it is coupled to `typer.confirm`/`typer.Exit` and calls the blocking CLI
investigate. The TUI calls the underlying building blocks
(`preflight_current_ticket`, `resolve_owner_id`, `make_writer().assign_ticket`)
directly, with its own confirm gesture and its own in-process investigation.

Engine calls go through `from noc_cli.scout import commands` and are invoked as
`commands.run_scout_report(...)`, so tests patch `noc_cli.scout.commands.*`
exactly as `tests/test_cli_scout.py` already does.

## `/scout` — run and render

- **Single-flight:** guarded by `_scouting`; a second `/scout` while one is running
  shows "Scout is already running."
- `/scout` enters **scout mode** (`_scout_active = True`) — a detail-pane takeover,
  the same mechanism a running investigation uses — and starts a
  `@work(thread=True)` worker that calls
  `commands.run_scout_report(cfg, top_k=8, min_staleness_days=7)`.
- **While running:** the pane shows a braille spinner and `Scouting the Tier-1
  backlog …`.
- **On success:** store the `ScoutReport`; the pane shows `render_scout_report(report)`
  followed by a TUI footer: `/take <id> to claim · Esc to leave`. If
  `report.dropped > 0`, append the engine's "N of M screens could not be parsed"
  note.
- **On engine error** (e.g. `ZendeskError`, agent failure): the pane shows the
  error, mirroring the investigate-failure panel.
- Your queue stays on the left and remains navigable while scout mode is shown;
  `Esc` leaves scout mode and the pane reverts to the selected ticket's detail.

## `/take <id>` — claim with Enter-to-confirm

The id may be any ticket (preflight is the eligibility gate, matching the CLI).
Each network hop runs in a thread worker so the UI never blocks.

1. **Propose** (`/take 5012`): worker runs
   `commands.preflight_current_ticket(cfg, ticket_id=5012, min_staleness_days=7)`.
   - Returns a reason (e.g. "already assigned", "no longer stale", "solved",
     "closed", "updated_at missing") → notify the reason; stop.
   - Eligible → `commands.resolve_owner_id(cfg)`; `None` → notify
     "Could not resolve your Zendesk user id — set NOC_OWNER / NOC_WATCH_ASSIGNEE";
     stop.
   - Else set `_pending_take = 5012`; the pane shows
     `▶ Assign #5012 to you and investigate? Enter to confirm · Esc to cancel`.
2. **Confirm** (bare `Enter` with an empty box while `_pending_take` is set):
   worker re-runs preflight (TOCTOU — the ticket may have changed during the
   prompt). Still eligible → `make_writer(cfg).assign_ticket(5012, owner_id)`.
   `ZendeskWriteError` → notify; clear pending.
3. **Cancel** (`Esc` while `_pending_take` is set) → clear pending, notify
   "Cancelled."

## Take → investigate handoff

On a successful assign, the panel hands off to the TUI's existing in-process
investigation for the claimed id:

1. Clear `_pending_take`, leave scout mode, and notify "Assigned #5012 to you —
   investigating…".
2. Trigger a poll (`action_poll_now`); the ticket — now assigned to the user —
   enters "my queue" on completion.
3. On poll completion, move the cursor to the claimed ticket and start the
   existing `_run_investigate(5012)` worker, so the established investigate
   takeover renders its live progress.
4. **Fallback:** if the poll has not yet surfaced the ticket (e.g. the watch view
   excludes it), the investigation still runs and the row appears under
   "Recently worked" on completion; the assignment notification already
   confirmed success, so no state is lost.

## State and key precedence

New `WatchApp` fields: `_scout_active: bool`, `_scouting: bool`,
`_scout_report: ScoutReport | None`, `_scout_error: str | None`,
`_pending_take: int | None`.

- **Enter** (`on_input_submitted`): if the autocomplete menu is open → run the
  highlighted command *(existing)*; else if the box is empty and `_pending_take`
  is set → confirm the take; else normal dispatch (a typed `/take 5012` is a
  normal command → step 1 above).
- **Esc** (`action_interrupt`): menu open → close menu *(existing)*; elif
  `_pending_take` set → cancel the take; elif `_scout_active` → leave scout mode;
  elif a chat turn is running → interrupt it *(existing)*; else clear the box.

`/take` is added to `KNOWN_COMMANDS` as a `(scout)` command so the palette lists
it. The `scout` entry's description returns to naming the panel (it now exists),
superseding PR #8's interim "(runs via CLI for now)" text.

## Rendering

`_render_scout_panel()` builds a Rich `Text`:
- `_scouting` → spinner line + "Scouting the Tier-1 backlog …".
- `_scout_error` → the error message.
- `_scout_report` → `render_scout_report(report)`; if `_pending_take` is set,
  prepend the confirm prompt line; always end with the footer
  `/take <id> to claim · Esc to leave`.

`_refresh_detail` renders the scout panel whenever `_scout_active` (taking over
the right pane regardless of the left-pane selection), parallel to how
`_selected_is_investigating()` gates the investigate panel.

## Error handling & safety invariant

Every failure path is a notification or an in-panel message — never a crash:
engine errors, the engine's exact ineligibility reason, an unresolved owner id,
and `ZendeskWriteError`. The read-only contract is preserved: the **only** Zendesk
write remains `assign_ticket` (Assignee field only), reachable solely through the
double preflight + explicit Enter-confirm.

## Testing plan

TUI-level tests (`tests/test_watch_app.py`, `run_test`/`pilot`), patching
`noc_cli.scout.commands.*` as `tests/test_cli_scout.py` does (no real agent or
network):

- `/scout` with `run_scout_report` patched to a canned `ScoutReport` → pane shows
  the ranked candidates (`#<id>`, runbook, rationale); spinner state before
  completion.
- `/scout` while already running → single-flight notice; no second worker.
- engine raises → error shown in the pane.
- `/take <id>` eligible (preflight `None`, owner resolved) → proposal shown,
  `_pending_take` set; **Enter** → re-preflight → `assign_ticket(id, owner)` called
  once → investigate kicked off.
- `/take <id>` ineligible (preflight returns a reason) → reason notified, **no**
  `assign_ticket`, no pending.
- TOCTOU: first preflight `None`, re-preflight returns a reason on confirm →
  **no** `assign_ticket`; pending cleared.
- `Esc` while pending → pending cleared, **no** `assign_ticket`.
- `assign_ticket` raises `ZendeskWriteError` → notified; no investigation started.

## Invariants preserved

- Read-only everywhere except the single confirmed Assignee write; the chat
  agent's tool whitelist is untouched.
- Menu-closed and non-scout behavior is unchanged; the new code paths are
  reachable only while `_scout_active` / `_pending_take` are set.
