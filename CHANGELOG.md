# Changelog

All notable changes to **noc-cli** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-06-06

The runtime triage agent gains live data, model tuning, and reliable runbook
grounding; the TUI gains a command palette and an in-app Backlog Scout.

### Added
- **Live read-only agent tools** (#9): during an investigation the agent can
  fetch a ticket and its comments and search Zendesk + prior investigations on
  demand — via an external read-only Zendesk MCP server (FastMCP, PII-redacted
  at the trust boundary) and an in-process history server.
- **Deeper runbook grounding** (#12): a Haiku classifier selects the runbook
  from the ticket evidence (the operator hypothesis is a signal, not a verdict),
  injects it into the agent's prompt, and softly verifies the fork cites it —
  surfaced as `grounding_verified` on the Handoff, in `events.jsonl`, and in the
  rendered output.
- **Model selection + usage observability** (#10): a centralized model registry
  (investigate→Opus with Sonnet fallback, chat→Sonnet, Scout→Haiku/Opus) with
  per-surface `NOC_MODEL_<SURFACE>` overrides, plus best-effort usage/cache-token
  logging to the event log.
- **Slash-command autocomplete palette** (#8) for the command box.
- **In-TUI Backlog Scout panel** (#11): `/scout` runs the report and `/take`
  claims a candidate and auto-investigates it.

### Changed
- Adopted **ruff** (format + lint) with a format-on-edit hook and a
  protected-path edit guardrail (#7).

### Internal
- Test suite hardened to stay fully offline — no accidental live-model calls (#13).

## [0.5.0] - 2026-06-05

Backlog Scout — surface and claim stale tickets before they fall through ([#6]).

### Added
- **`noc scout`** scans the Tier-1 NOC backlog for stale, unassigned, runbook-backed
  tickets and prints a ranked "candidates needing review" report. The pipeline is a
  three-stage funnel: pure-Python staleness × priority ranking over view metadata (no
  agent), a bounded `Semaphore(3)` fan-out of fresh **Haiku 4.5** triage-readiness
  pre-screens (one per top-K candidate), and a single **Opus 4.8** high-effort synthesis
  into the final ranking.
- **`noc scout --take <id>`** claims a ticket via propose-then-confirm: re-read the
  ticket, prompt for confirmation, re-read again to catch races (TOCTOU), assign it to you
  through an isolated `ZendeskWriter`, then hand off to `investigate`. Supports `--top-k`,
  `--min-staleness-days` (default 7), and `--yes`.
- `scout_view` config / `NOC_SCOUT_VIEW` env, defaulting to the Tier-1 NOC queue.
- `Ticket.priority` is now read from Zendesk view metadata (drives the staleness ranking).
- Dropped-screen visibility: `ScoutReport` carries screened/parsed counts, and a stderr
  note appears when any pre-screen's output fails to parse — so a short list is never
  mistaken for a quiet backlog.

### Changed
- The Scout agent is **100% read-only**, enforced by the `build_hooks` sandbox
  (`restrict_read_tools=True`); the lone Zendesk write lives outside the agent, behind the
  `--take` confirmation.
- Internal refactor: `cli.py` shrank from 708 to 578 lines as Scout logic moved into
  focused modules (`commands`, `rank`, `hooks`, `llm_io`, `ports`, `writer`, `screen`,
  `synthesize`, `runner`), with typed injection boundaries (`TicketReader`, `QueryFn`).

## [0.4.0] - 2026-06-05

Runbook-grounded investigate ([#5]).

### Added
- `investigate` now grounds the agent in staged symptom runbooks, keeping the single
  command flow while constraining the analysis to documented procedures.
- Analyst seeding: `--suspect <slug>` or an interactive prompt resolves the starting
  runbook before the agent runs.
- Rubric-core system prompts, full agent transcript capture, and a generated
  `REASONING.md` that records how the agent reached its conclusion.
- `STATE.md` audit warnings surface when runbook grounding is missing or pivoted.

## [0.3.0] - 2026-06-05

OpenPets desktop-pet notifier ([#4]).

### Added
- Opt-in `openpets` notifier announces ticket changes on the OpenPets desktop companion
  over its dependency-free local IPC (Windows named pipe / Unix socket / loopback TCP).
- Messages are minimal and PII-free (`ZD-<id> · <change>`); if the pet app is closed or
  unreachable the notifier drops silently and never blocks or crashes the watcher.
- `CompositeNotifier` + `build_notifier` let the OS ping and OpenPets run together; enable
  by adding `openpets` to the `notify` config (e.g. `notify = banner,ping,openpets`).

## [0.2.0] - 2026-06-05

Watch inbox UX ([#3]).

### Added
- My-tickets queue filter — the watch queue shows only tickets assigned to you, with each
  row surfacing the subject and status.
- `timezone` config / `NOC_TZ` env (`local` | `utc` | IANA name) controls timestamp
  display throughout the watch view.
- Persistent detail pane: a permanent `ZD-<id> · subject · status` header on every tab,
  descriptive tab labels (e.g. `INTAKE.md — What are we looking at?`), and Zendesk-style
  comments color-coded by author role (internal / customer / agent), refreshing each poll.

### Fixed
- The detail pane no longer drops the subject and live comment thread after a ticket is
  investigated — context now stays pinned exactly when you act on the triage.

## [0.1.0] - 2026-06-04

Initial watch inbox viewer ([#2]).

### Added
- `noc-cli config set/get/list/path` for reading and editing configuration, with API-token
  masking and atomic `.env` writes.
- A two-pane, display-only `watch` TUI backed by disk-scan inbox scanning and rendering
  helpers.
- Additional `STATE.md` summary fields persisted to drive inbox rendering.

[Unreleased]: https://github.com/midwestman35/noc-cli/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/midwestman35/noc-cli/pull/6
[0.4.0]: https://github.com/midwestman35/noc-cli/pull/5
[0.3.0]: https://github.com/midwestman35/noc-cli/pull/4
[0.2.0]: https://github.com/midwestman35/noc-cli/pull/3
[0.1.0]: https://github.com/midwestman35/noc-cli/pull/2
[#6]: https://github.com/midwestman35/noc-cli/pull/6
[#5]: https://github.com/midwestman35/noc-cli/pull/5
[#4]: https://github.com/midwestman35/noc-cli/pull/4
[#3]: https://github.com/midwestman35/noc-cli/pull/3
[#2]: https://github.com/midwestman35/noc-cli/pull/2
