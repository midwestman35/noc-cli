# Interactive TUI Shell — Design

> **Date:** 2026-06-05
> **Status:** APPROVED (brainstorm complete) — ready for implementation plan.
> **Feature branch:** `feat/interactive-tui-shell` (to be created off `main`).
> **Companion roadmap:** `docs/interactive-feat.md` (Phases A–D, §C chat, §5 SDK
> ceiling). This spec *realizes* the §C chat surface and folds it into a single
> interactive shell.

---

## 1. Intent

Today `noc-cli` is a multi-command CLI: you run `noc-cli investigate <id>` or
`noc-cli watch` as separate one-shot entry points. The application has no
"interactivity" — no persistent place to type, no conversation, no landing
surface.

This feature makes the **two-pane `watch` TUI the home of the application** and
gives it an always-present, codex/claude-style **input-first text box**. Running
bare `noc-cli` shows a brief splash (version + loading), then drops you straight
into the live inbox. From the box you type `/commands` to drive actions and
freeform text to **chat with the agent about the selected ticket**.

The guiding constraint, in the user's words: *"It should closely resemble what we
already have. In essence, we are just moving everything into the TUI."* The two
panes, the queue, the detail tabs, the poll loop, the read-only safety model — all
unchanged. We add a splash, a text box, a command router, and the chat subsystem,
and we move `investigate`/`watch`/`scout` into the shell while keeping hidden
compatibility aliases for one release.

## 2. Goals & non-goals

**Goals**
- Bare `noc-cli` launches the TUI (replaces `noc-cli watch`).
- A brief cold-start **splash** (logo + tagline + version) that auto-dissolves
  into the panes when the first poll lands — no keypress.
- Version number visible in the TUI header.
- An always-focused **input-first text box** between the panes and the footer.
- **Hybrid routing:** `/`-prefixed input = command; everything else = a chat turn
  about the selected ticket.
- Full `/command` set covering every action that used to be a single key.
- **Chat subsystem** (the §C surface): stateful per-ticket session, persisted
  transcript, per-turn PII redaction, interruptible.
- Make the TUI the primary surface and hide/deprecate `investigate`/`watch`/`scout`
  from normal help; keep compatibility aliases for one release so scripts and
  runbooks do not break without warning. Keep `setup`, `doctor`, `config` as CLI
  escape hatches.

**Non-goals (this feature)**
- Auto-triage on poll (still engineer-triggered — `interactive-feat.md` §8).
- A session picker / "reopen past chats" browser (`list_sessions()` UI) — §D polish.
- Live token streaming in chat (SDK exposes phase/tool boundaries only — §5.3).
- Migrating investigate's hand-rolled extract-and-retry to SDK structured output.
- Multi-candidate bulk actions, configurable discovery pools (Scout §9.6).

## 3. The shape — before → after

**Before — `noc-cli watch`**
```
 noc-cli watch · my tickets · 5 tickets · last poll 3:43:30 PM
╭──────────────────────────────╮╭────────────────────────────────────╮
│ Recently worked (3d) / queue ││ detail tabs / live investigate     │
╰──────────────────────────────╯╰────────────────────────────────────╯
 tab Next file  k Up  j Down  i Investigate  r Refresh  q Quit  ^p palette
```

**After — `noc-cli` (bare)**

Splash (transient, ~1–2s, auto-dissolves on first poll — no keypress):
```
                          noc-cli
            Read-only NOC triage · Carbyne APEX NG911/E911
                          v0.1.0

                    ⠹ Loading my tickets …
```
↓
```
 noc-cli v0.1.0 · my tickets · 5 tickets · last poll 3:43:30 PM
╭──────────────────────────────╮╭────────────────────────────────────╮
│ Recently worked (3d) / queue ││ Summary / Chat / INTAKE.md / …     │
╰──────────────────────────────╯╰────────────────────────────────────╯
╭──────────────────────────────────────────────────────────────────────╮
│ ❯ ask about #45747, or /investigate /scout /help…                     │
╰──────────────────────────────────────────────────────────────────────╯
 ↑↓ select  ⏎ send  esc interrupt  pgup/pgdn scroll          ^p palette
```

Chat view (right pane) — freeform box input renders here:
```
 ZD-45747 · 4 stuck text… · open
 Chat · about this ticket

 ⠹ spinning up session…              ← cold-start, first turn only (~20–30s)

 you ❯ why is this call stuck?
 ◆ Held in queue — the PSAP's ALI link timed
   out at 14:02; calls fell back to ten-digit…   (esc to interrupt)

 you ❯ /file ~/Desktop/pcap-excerpt.txt
 ✓ attached pcap-excerpt.txt as evidence
```

## 4. Architecture

### 4.1 CLI surface

`noc_cli/cli.py` becomes thin. The Typer app gains `invoke_without_command=True`
and drops `no_args_is_help=True`; the root callback, when `ctx.invoked_subcommand
is None`, launches the TUI.

| Command | Fate |
|---|---|
| `noc-cli` (bare) | **launches the TUI** (was `noc-cli watch`) |
| `noc-cli setup` | **stays** — bootstrapping (chicken-and-egg: the TUI needs creds to poll, `setup` writes them) |
| `noc-cli doctor` | **stays** (CI/scripting); also mirrored as `/doctor` in-TUI |
| `noc-cli config …` | **stays** (scripting) |
| `noc-cli --version` | **stays** |
| `noc-cli investigate` | **deprecated/hidden compatibility alias** → `/investigate`; logic extracted to a worker-callable module |
| `noc-cli watch` | **deprecated/hidden compatibility alias** → bare `noc-cli` |
| `noc-cli scout` | **deprecated/hidden compatibility alias** → `/scout` (integration point for the parallel Scout branch) |

Launch guard: if config is missing/incomplete (no `watch_view` or creds), the TUI
does **not** crash — it shows a "Not configured — run `noc-cli setup`" screen and
exits cleanly. (Replaces the current `watch` command's `typer.Exit` precheck.)

Compatibility rule: deprecated aliases emit a warning that names the in-TUI
replacement, then run the same underlying implementation. They are hidden from
`--help` so the product direction is still interactive-first. Removing them becomes
a later breaking-change decision, not part of this feature.

### 4.2 Splash

A Textual overlay (or pushed `SplashScreen`) shown from `on_mount` until the first
`PollComplete` message arrives, then removed to reveal the panes. Content: centered
`noc-cli`, the `branding.TAGLINE`, `v{__version__}`, and the braille spinner driven
by the existing `_tick_spinner`. If the first poll errors, the splash dissolves
anyway and the error surfaces in the notification line (panes render empty). The
splash carries **no input box** — it lasts ~1–2s and is purely a loading frame.

### 4.3 Input model — input-first + command router

A new **`CommandInput`** widget (Textual `Input`) mounts between `#body` and the
`Footer` in `WatchApp.compose`. It holds focus by default and on every poll/repaint
(focus never leaves it except for explicit `^p` palette use). Because the box owns
typing, **no bare letter is a binding** — the entire `BINDINGS` table is rebuilt:

| Today | Input-first |
|---|---|
| `↑`/`k`, `↓`/`j` | `↑`/`↓` move the queue cursor (priority bindings; arrows aren't text) |
| `enter` (focus detail) | `⏎` = **submit** the box |
| `i` investigate | `/investigate [id]` |
| `r` refresh | `/refresh` |
| `y` copy | `/copy` |
| `o` open | `/open` |
| `q` quit | `/quit` (and `⌃c`) |
| `tab`/`shift+tab` | `tab`/`shift+tab` cycle right-pane **views** (unchanged behavior) |
| `esc` (summary) | `esc` = **interrupt** a running agent turn; if idle, clear the box |
| — | `pgup`/`pgdn` scroll the right pane (arrows are taken by the queue) |
| `^p` palette | unchanged (Textual built-in) |

**Router** (`noc_cli/tui/command.py`, new): parses the submitted string.
- Leading `/` → split into `(name, args)`; dispatch to a handler. Unknown command →
  notification "unknown command; /help".
- No leading `/` → a **chat turn** about the currently selected ticket (§4.5).
- Empty submit → no-op.

Command set: `/investigate [id]`, `/scout`, `/doctor`, `/help`, `/refresh`,
`/copy`, `/open`, `/clear`, `/quit`, plus the in-chat commands `/file`, `/paste`,
`/revise`, `/retry` (§4.5). `/investigate` with no id targets the selected row;
with an id, that ticket. `/doctor` runs `doctor.run_checks` and renders the report
in a transient right-pane view. `/help` lists commands in the right pane. `/clear`
clears the current draft or Chat view transcript render only; it never deletes
`CONVERSATION.jsonl`. `/scout` opens the Scout flow in the right pane (hooks into
`noc_cli/scout/` as it lands; until then `/scout` reports "Scout not yet available
on this build").

Shell ergonomics expected from Codex/Claude-style CLIs:
- Command history for submitted slash commands and freeform turns (`up/down` only
  when the queue is not consuming them; otherwise `ctrl+p`/`ctrl+n` inside the
  input).
- Prefix completion for slash commands and file paths used by `/file`.
- Draft preservation per selected ticket. Switching rows does not lose a half-typed
  analyst turn; the input restores the draft for the selected ticket.
- Explicit busy state: while an agent turn is running, the box is disabled except
  for `esc`; submitted text is never silently queued behind an active turn.

### 4.4 Investigate as an in-process worker

Removing the `investigate` CLI command removes the subprocess the TUI currently
spawns (`watch_app.py:_run_investigate` → `python -m noc_cli.cli investigate`).
Investigate moves **in-process**:

- Extract the orchestration in `cli.py::_run_investigate` into
  `noc_cli/investigate.py::run_investigation(...)`, parameterized with a
  **progress callback** (replacing the `PhaseTracker`-to-stdout-to-line-parse hop).
- The TUI's investigate worker (`@work(thread=False)`, async — `run_agent` is
  already async) calls `run_investigation` directly and renders phases from the
  callback. No more `_strip_ansi`/`detect_phase` stdout scraping.
- The seed prompt (`resolve_seed`) becomes non-interactive in the TUI path: the
  selected runbook/hypothesis is passed in, or left blank (agent re-steers).
- Concurrency guard unchanged: one investigation at a time via `_investigating_id`;
  the poll worker stays independent.

`run_investigation` remains importable for tests and is the single source of truth
for the pipeline; the CLI command is gone but the logic is not.

### 4.5 Chat subsystem (the §C surface)

Freeform box input is a chat turn about the **selected ticket** (scope **(a)** —
any selected ticket, not just triaged ones).

- **Context loading.** Always: the live ticket subject/status/comments from the
  current poll. If the ticket has been investigated (a fork-packet folder exists),
  also load its 5-md files + prior transcript as context. Chatting an
  **un-investigated** ticket first calls `scaffold.scaffold_ticket` so the session
  has a folder/cwd and a home for its transcript.
- **Session model.** One `ClaudeSDKClient` **per open ticket** (never a pool —
  §5.1). `cwd` = the ticket folder, so the read-only sandbox is naturally contained
  and SDK resume-by-cwd works (§5.5). Cold-start (~20–30s) is surfaced as a
  "spinning up session…" line on the **first turn only**; later turns are cheap.
  Sessions are created lazily on the first freeform turn for a ticket and cached by
  ticket id for the app's lifetime, with a small LRU cap (default 5 active clients).
  Evicting a client closes it cleanly; the transcript remains resumable from the
  ticket folder.
- **Persistence.** Append-only `CONVERSATION.jsonl` in the ticket folder (portable,
  per-ticket — §8 pick over the SDK's `~/.claude/projects`), with a derived
  `CONVERSATION.md` re-rendered for reading. The `.jsonl` is the source of truth and
  enables resume.
- **Redaction.** Every analyst turn passes through `redact.redact` at the model
  boundary, identical to investigate's log scrub.
- **Interrupt.** `esc` → `client.interrupt()` **and drains messages to the terminal
  `ResultMessage`** before accepting the next turn (skip the drain → session
  corrupts, §5.4). UI shows "(esc to interrupt)" while a turn runs.
- **Safety.** The read-only hooks (`agent/harness.py::build_hooks`) apply unchanged
  — sandbox containment, destructive-Bash denylist, Zendesk-write denylist. The
  chat agent **never** gets Zendesk-write tools (§6).
- **In-chat commands.** `/file <path>` and `/paste label=body` attach evidence
  (reusing `evidence.PasteInput`/`gather_evidence`); `/revise` re-runs the structured
  investigate pipeline with newly attached evidence (rewrites the 5-md folder);
  `/retry` re-sends the last analyst turn (transient-failure recovery).
- **Rendering.** Progress is phase-level, not live tokens (§5.3). The transcript
  renders in the Chat view (§4.6), newest turn at the bottom.
- **Selection discipline.** A running chat turn is bound to the ticket id selected
  at submit time. If the operator changes selection while the turn runs, progress
  continues in that ticket's Chat view and the banner shows which ticket is busy;
  the answer is never appended to the newly selected ticket by accident.

### 4.6 Right-pane view model

The detail pane's view list (`_DETAIL_MODES`) gains **Chat**:

```
[ Summary, Chat, INTAKE.md, EVIDENCE_PREFLIGHT.md, FORK_PACKET.md, DRAFTS.md, STATE.md ]
```

- `tab`/`shift+tab` cycle the views (as today).
- Sending a **freeform** turn auto-switches the right pane to **Chat**.
- A running **investigation** takes over its view (the existing live phase panel),
  exactly as today.
- File views (INTAKE.md …) are only populated/reachable on triaged rows (existing
  guard); Chat is reachable on any selected ticket.

## 5. Component & file map

**New**
- `noc_cli/tui/command.py` — the command router (parse + dispatch). Pure-ish;
  unit-testable without a running app.
- `noc_cli/tui/chat.py` — chat session manager: per-ticket `ClaudeSDKClient`
  lifecycle, `CONVERSATION.jsonl`/`.md` I/O, redaction, interrupt/drain.
- `noc_cli/investigate.py` — `run_investigation(...)` extracted from `cli.py`, with
  a progress-callback seam.
- `noc_cli/tui/splash.py` — splash overlay/screen.

**Changed**
- `noc_cli/cli.py` — `invoke_without_command`; root callback launches the TUI;
  remove `investigate`/`watch`/`scout` commands; keep `setup`/`doctor`/`config`.
- `noc_cli/tui/watch_app.py` — mount `CommandInput`; rebuild `BINDINGS`
  (input-first); wire the router; swap subprocess investigate for the in-process
  worker; add Chat to the view list; splash on mount.
- `noc_cli/branding.py` — header string helper exposing `v{__version__}` (reused by
  the TUI banner); CLI `render_banner` unchanged.

**Reused unchanged**
- `agent/runner.py` (`run_agent`), `agent/harness.py` (`build_hooks`),
  `agent/prompt.py`, `rubric.py`, `models.py`, `render.py`, `redact.py`,
  `scaffold.py`, `evidence.py`, `tui/progress.py`, `watch/poller.py`,
  `watch/diff.py`, `watch/inbox.py`, `watch/state.py`.

## 6. Data flow

1. `noc-cli` → Typer root callback → `WatchApp.run()`.
2. `on_mount` → show splash → start poll interval → first `_run_poll` (worker).
3. First `PollComplete` → dissolve splash → `_rebuild_rows` → panes live.
4. Box submit → router: `/cmd` → action; freeform → chat turn.
5. `/investigate` → in-process worker → `run_investigation` → phases via callback →
   on success `action_poll_now` flips row to ✓ and the right pane reloads from disk.
6. Freeform → `chat.py`: ensure session (scaffold if needed) → redact → send →
   stream phase events → append `CONVERSATION.jsonl` → render Chat view.
7. `esc` while a turn runs → `interrupt()` + drain.

## 7. Error handling & edge cases

- **Not configured at launch** → "run `noc-cli setup`" screen, clean exit (no stack
  trace).
- **First poll errors** → splash dissolves, error in notification line, empty panes,
  poll retries on interval.
- **Freeform with empty queue / no selection** → notification "select a ticket to
  chat about"; box content preserved.
- **Cold-start latency** → "spinning up session…" on first turn; box disabled until
  the session is ready to avoid a queued second turn corrupting the session.
- **Ticket switch mid-turn** → turn remains attached to the original ticket id; the
  new row can be inspected, but another chat/investigate action is blocked until the
  active turn is interrupted or completes.
- **`/investigate` while one runs** → existing "An investigation is already
  running." notification.
- **Interrupt mid-investigate** → partial files may remain; the existing
  scaffold/soft-lock handles a clean re-run (no new machinery).
- **Unknown `/command`** → notification "unknown command; try /help".
- **Chat session spawn failure** → notification with the error; box re-enabled.
- **Deprecated command use** → warning plus successful execution; this protects
  existing scripts while teaching the new in-TUI command.
- **Long-running app memory growth** → close least-recently-used chat clients beyond
  the active-client cap; transcripts remain on disk for resume.

## 8. Safety

No change to the read-only model. The agent (investigate *and* chat) runs under the
same `build_hooks` sandbox: path containment, destructive-Bash denylist,
Zendesk-write denylist — hooks run before permission checks and a `deny` is final
(§6). The only mutating action in the whole app remains a deterministic,
engineer-confirmed Zendesk assign (Scout's `/take`-equivalent, on its own branch).
The chat agent is never granted Zendesk-write tools.

## 9. Testing strategy

- **CLI surface** — bare `noc-cli` launches the TUI (patched `WatchApp.run`);
  `investigate`/`watch`/`scout` are hidden deprecated aliases with warnings;
  `setup`/`doctor`/`config`/`--version` remain. (`tests/test_cli_*`.)
- **Router** — slash vs freeform classification; arg parsing; unknown-command path.
  Pure unit tests on `tui/command.py`; includes `/clear` and deprecated alias parity.
- **TUI interaction** — Textual `Pilot`: splash → dissolve on first poll; box always
  focused; `↑/↓` navigate; `tab` cycles views incl. Chat; `esc` interrupts;
  `/refresh`/`/open`/`/clear`/`/quit` dispatch; command history/completion works;
  per-ticket draft text survives row changes.
- **Investigate worker** — phases surfaced via the progress callback (not stdout
  parsing); row flips ○→✓; reuses the `--fixture` `handoff_good.json` offline path.
- **Chat** — session lifecycle with a mocked `ClaudeSDKClient`; `CONVERSATION.jsonl`
  append + `.md` render; redaction at the boundary; interrupt+drain ordering;
  scaffold-on-first-chat for an un-investigated ticket; LRU close behavior; switching
  selected tickets mid-turn does not mis-attribute transcript output.
- **Config-missing launch** — shows the setup hint, exits clean.

## 10. Suggested implementation order

Monolithic delivery (one branch, lands together), but build/review in this order so
each layer rests on a tested base:

1. **CLI surface** — `invoke_without_command`, bare-`noc-cli`→TUI,
   hidden/deprecated `investigate`/`watch`/`scout` aliases, config-missing guard.
   (Smallest, unblocks the rest.)
2. **Splash + version header.**
3. **Input-first model** — `CommandInput`, rebuilt `BINDINGS`, router over existing
   actions, command history/completion/drafts, investigate-as-in-process-worker,
   Chat added to the view list (freeform still a stub hint).
4. **Chat subsystem** — `tui/chat.py`, bounded sessions, persistence, redaction,
   interrupt, ticket-id-bound turns, `/file`/`/paste`/`/revise`/`/retry`,
   Chat-view rendering.

## 11. Deferred / out of scope (banked)

- Auto-triage on poll; veto-window / act-then-report autonomy (`interactive-feat.md`
  §6, §8).
- Session picker UI (`list_sessions()`) to reopen past chats (§D).
- SDK structured-output migration (§5.7).
- Type-ahead on the splash screen (decided against; splash is a sub-2s loading frame).
- Scout's full in-TUI panel (parallel branch); this spec only reserves `/scout`.

## Appendix — command reference

```
Navigation / app
  ↑ / ↓            move queue cursor
  pgup / pgdn      scroll right pane
  tab / shift+tab  cycle right-pane views (Summary · Chat · INTAKE.md · …)
  ⏎                submit the box
  esc              interrupt a running agent turn; if idle, clear the box
  ^p               command palette (Textual built-in)
  ⌃c               quit

Slash commands
  /investigate [id]  investigate the selected (or given) ticket
  /scout             open Backlog Scout (parallel branch integration)
  /doctor            run health checks, render report in the right pane
  /refresh           poll now
  /copy              copy current detail
  /open              open the ticket in the browser
  /help              list commands
  /clear             clear the current draft/rendered Chat view only
  /quit              quit
  /file <path>       (in chat) attach a local file as evidence
  /paste label=body  (in chat) attach inline text as evidence
  /revise            (in chat) re-run the structured pipeline with new evidence
  /retry             (in chat) re-send the last analyst turn

Freeform (no leading /)
  …anything…         a chat turn about the selected ticket
```

## Appendix — design evaluation and scoring

### Initial score: 88/100

**Codex/Claude-style CLI resemblance: 33/40.** The spec had the right foundation:
bare launch, input-first typing, slash commands, interrupt, transcript, and
ticket-scoped conversational turns. It was weaker on day-to-day shell ergonomics:
history, completion, draft preservation, explicit busy state, and compatibility
aliases were not specified.

**NOC-specific harness fit: 36/40.** The design strongly preserves the watch inbox,
read-only hooks, fork-packet files, runbook-grounded investigate flow, and
per-ticket evidence folders. The main gap was lifecycle discipline: a per-ticket
agent session could leak resources or mis-attribute a response if the operator
changed selection while a turn was running.

**Failure-mode coverage: 19/20.** Good coverage for config, first-poll failure,
cold start, unknown commands, interrupt, and chat spawn failure. Missing coverage
was mostly compatibility breakage and long-running session growth.

### Revisions made to reach >=95

- Kept the TUI as the primary product surface while changing
  `investigate`/`watch`/`scout` from immediate removals to hidden deprecated aliases
  for one release.
- Added shell ergonomics expected from similar interactive CLIs: history,
  completion, per-ticket drafts, `/clear`, and explicit busy-state behavior.
- Added a bounded chat-session lifecycle with LRU close and transcript-based resume.
- Added ticket-id binding for running chat turns so row changes cannot append output
  to the wrong ticket.
- Expanded tests to cover compatibility aliases, history/completion/drafts,
  `/clear`, LRU close, and mid-turn ticket switching.

### Revised score: 96/100

**Codex/Claude-style CLI resemblance: 38/40.** It now has the core REPL shape and
the operational shell affordances an operator will expect. It intentionally does
not chase full parity with general-purpose coding agents: no broad project-wide
workspace chat, no unconstrained tool editing, and no live token stream.

**NOC-specific harness fit: 39/40.** The design is clearly a custom NOC
troubleshooting harness rather than a generic chat wrapper: queue-first,
ticket-scoped, fork-packet backed, runbook-aware, read-only by default, and
evidence-persisting.

**Failure-mode coverage: 19/20.** The remaining risk is implementation complexity
inside Textual focus/keyboard handling and SDK interrupt/drain behavior. The design
now calls out the risk and gives testable acceptance points, but the exact polish
will depend on implementation feedback.

### Concessions

- Deprecated CLI aliases remain for one release, so the first implementation will
  not be as clean as a hard cutover to bare `noc-cli` plus slash commands.
- Live assistant token streaming remains out of scope because the installed SDK
  exposes phase/tool boundaries, not stable token-level streaming for this use.
- The design keeps the current five-file fork packet as the durable artifact model
  instead of making chat the source of truth for investigations.
