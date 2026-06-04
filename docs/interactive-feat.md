# Interactive Features Roadmap — `interactive-feat`

> **Status: PARKING LOT.** Captured 2026-06-04 during the `watch` two-pane viewer
> brainstorm. Purpose: scope the path from noc-cli's current *one-shot* `investigate`
> to triage-cli's full *interactive* inbox. **Nothing here is committed** — this is the
> agenda for a dedicated follow-on session.
>
> The viewer shipping now is **display-only**: it renders fork packets that already
> exist on disk. Everything below is about letting the engineer *drive the agent*
> from inside the inbox.

---

## 1. Where we are today

- **Agent execution is one-shot.** `investigate` calls `claude_agent_sdk.query()`,
  drains a single `ResultMessage`, extracts + validates a `Handoff` JSON against the
  pydantic schema, and renders five markdown files. No conversation, no session.
  (`noc_cli/agent/runner.py`)
- **Read-only safety lives in hooks**, not the SDK: `PreToolUse` enforces sandbox
  path-containment + a destructive-Bash denylist + a Zendesk-write denylist;
  `PostToolUse` logs every tool call to `events.jsonl`. Combined with
  `allowed_tools` + `permission_mode="bypassPermissions"`.
  (`noc_cli/agent/harness.py`)
- **SDK is `claude-agent-sdk`, pinned `0.2.88`** (`uv.lock`; pyproject floor is
  `>=0.1.0`). Every interactive API named below — `ClaudeSDKClient`, `interrupt()`,
  `list_sessions()`, partial-message streaming, schema output — is **already
  installed**. No version bump required to start.

---

## 2. The target — triage-cli's interactive workflows

triage-cli exposes **five** agent/LLM-backed workflows from its inbox. Only **one**
is genuinely multi-turn:

| # | Workflow | Trigger | Interaction model | Stream | Persist | Resume |
|---|----------|---------|-------------------|--------|---------|--------|
| 1 | Initial triage | `Enter` on queued | **one-shot** pipeline | phase events | 5-md folder | n/a |
| 2 | Site disambiguation | auto modal mid-triage | one-shot text input | no | override string | n/a |
| 3 | **Chat** | `a` on triaged | **multi-turn, stateful** | no | `CONVERSATION.jsonl` | yes |
| 4 | `/revise` | in chat | one-shot pipeline re-run | phase events | rewrites 5-md folder | n/a |
| 5 | `/retry` | in chat | one-shot re-call of last turn | no | `CONVERSATION.jsonl` | yes |

**Per-workflow shape (for porting):**

- **Initial triage** = noc-cli's `investigate`, but launched in-process with a live
  phase gauge. triage-cli's gauge sequence: *Fetching ticket → Querying prior
  investigations → Reviewing evidence → Building timeline → Querying Datadog →
  Asking LLM → Writing ticket folder*. (noc-cli's equivalent phases already exist in
  `tui/progress.py::InvestigatePhase`.)
- **Site disambiguation** = a modal that blocks the run to ask the engineer for a
  site name when automatic lookup is ambiguous; answer is fed back into the pipeline.
- **Chat** = a full conversational loop *about a specific ticket*. Context preloaded:
  prior transcript + the fork packet's evidence summary. Slash commands: `/file
  <path>`, `/paste <label>=<body>`, `/revise`, `/retry`, `/quit`. Every turn is
  appended to `CONVERSATION.jsonl` (source of truth) and re-rendered to
  `CONVERSATION.md`. PII is redacted at the model boundary. Session is resumable.
- **/revise** = re-run the whole structured pipeline with *new* evidence attached
  since the last revise; rewrites the five files. Requires at least one new evidence
  item. Runbook-bound.
- **/retry** = re-send the last analyst turn (transient-failure recovery). Freeform.

---

## 3. Feasibility verdict — **yes, with caveats**

Every workflow maps onto a primitive noc-cli can already reach:

- **The 4 one-shot workflows are just `query()` tasks** with tailored prompts/inputs
  — the *same shape* as today's `investigate`. They reuse the existing system prompt,
  rubric embedding, `Handoff` schema, and read-only hooks almost verbatim.
- **The 1 chat workflow needs a persistent `ClaudeSDKClient` session** plus an
  append-only conversation log. This is the only genuinely new machinery.

> **The single architectural fork for the whole roadmap:**
> `query()` (stateless, what we do now) vs. `ClaudeSDKClient` (stateful, needed for
> chat + retry). Decide this once and the rest follows.

---

## 4. Runbook-bound vs. freeform — the useful split

"Parity" is **not** one monolithic thing. The workflows fall into two families:

- **Rubric / runbook-bound** — *initial triage, `/revise`.* The agent must quote a
  verbatim rubric row and is validated against the fork rubric **and** the `Handoff`
  pydantic schema before the result is accepted. These are deterministic-ish,
  structured-output, and inherit the existing safety gates. **Cheap to add** — they
  reuse `agent/prompt.py`, `rubric.py`, `models.py`, `render.py`.
- **Freeform** — *chat (`a`), `/retry`.* Open conversation about the ticket with
  evidence attach; no rubric, no schema. **More new surface** (session lifecycle,
  transcript persistence, redaction at each turn).

**Answer to "is this a runbook-specific solution?":** *Partly.* The high-value,
low-risk wins (triage-from-inbox, revise) are runbook-bound and reuse what exists.
Only the chat loop is freeform and needs net-new plumbing. You do **not** need a
runbook for every interactive feature.

---

## 5. SDK ceiling — limits & footguns to design around

These are the constraints that actually shape what's worth building:

1. **Cold-start ~20–30 s per new session** — the SDK spawns the `claude` subprocess
   on each fresh `query()` / `ClaudeSDKClient`. Resuming is cheap; opening fresh is
   not → **one client per open ticket**, not a pool.
2. **No blanket concurrency** — N parallel agents = N cold-starts + contention on the
   session `.jsonl` store. If we ever **auto-triage on poll**, it must be a
   **bounded queue (semaphore 2–3)**, never fire-all. *(This is precisely why
   auto-triage was kept out of the viewer.)*
3. **Progress is phase-level, not live tokens** — streaming exposes tool-use
   *boundaries* (`StreamEvent`, `PreToolUse`/`PostToolUse`), **not** live assistant
   text or tool stdout. A *"Asking LLM…"* gauge is feasible; a live token stream is
   not.
4. **Interrupt requires draining** — `client.interrupt()` (e.g. on `Esc`) does not
   stop iteration immediately; you must consume messages to the terminal
   `ResultMessage` before issuing the next turn, or the session corrupts.
5. **Sessions resume by cwd** — they persist to
   `~/.claude/projects/<encoded-cwd>/*.jsonl` and resume keyed on the working
   directory. A *"reopen past chat"* feature needs a **consistent cwd** (or capturing
   `session_id` and passing `resume=session_id` explicitly).
6. **Conversation persistence ≠ file-state persistence** — resuming restores the
   *conversation*, not filesystem changes. Forking a chat does not revert edits the
   agent made.
7. **Structured output** — the SDK can validate against a JSON schema
   (`Handoff.model_json_schema()`) and re-prompt internally, but only *after* all
   tool use. noc-cli's current hand-rolled extract-and-retry gives more explicit
   control; either is fine. Migration is optional, not required.

---

## 6. Safety rails carry over for free

The read-only hook sandbox applies **unchanged** to every interactive feature:
hooks run **before** permission-mode checks, and a hook `deny` is final. So
triage-from-inbox, revise, retry, and chat all inherit the same sandbox
containment, destructive-Bash denylist, and Zendesk-write denylist. No new safety
design needed — reuse `agent/harness.py::build_hooks`.

---

## 7. Suggested phasing (for discussion)

Ordered by value-to-surface ratio — earliest phases reuse the most existing code:

- **Phase A — Triage from the inbox.** `Enter` (or `i`) on a queued ticket runs
  `run_agent` as a Textual worker (it's already `async`), row goes
  Queued `○` → Triaging `→` (phase gauge from `StreamEvent`/hooks) → Triaged `✓`,
  right pane reloads from disk on completion. Optional **opt-in** bounded auto-triage
  on poll. Runbook-bound; smallest new surface.
- **Phase B — `/revise` + `/retry`.** One-shot re-runs with new evidence; reuse
  `evidence.py` intake (`--file` / `--paste` already exist). Revise rewrites the five
  files; retry re-calls. Mostly runbook-bound.
- **Phase C — Chat session (`a`).** `ClaudeSDKClient` per ticket; append-only
  `CONVERSATION.jsonl` + derived `CONVERSATION.md`; resume by `session_id`;
  `Esc` → `interrupt()` + drain; per-turn PII redaction. Freeform; biggest new
  surface.
- **Phase D — Polish.** Site-disambiguation modal; session picker
  (`list_sessions()`) to reopen past chats; optional migration to SDK structured
  output.

---

## 8. Open questions for the dedicated session

- **Auto-triage on poll**: opt-in only? scoped to which tickets (new in queue?
  high-priority?)? what's the per-cycle cost ceiling?
- **Concurrency cap**: what semaphore value, and how to surface a queue in the UI?
- **Conversation storage**: ticket folder (`Tickets/<id>/CONVERSATION.jsonl`) vs.
  the SDK's `~/.claude/projects/...`? The former keeps everything per-ticket and
  portable; the latter is automatic but cwd-coupled.
- **Structured output**: keep manual extract-and-retry, or adopt SDK schema output?
- **Provider/cost guardrails** for an open-ended chat loop.
- **Interrupt UX**: what does a half-finished, interrupted triage leave on disk?

---

## Appendix — sources

- Agent SDK reference (Python): https://code.claude.com/docs/en/agent-sdk/python
- Agent SDK overview: https://code.claude.com/docs/en/agent-sdk/overview
- Sessions: https://platform.claude.com/docs/en/agent-sdk/sessions
- Permissions: https://code.claude.com/docs/en/agent-sdk/permissions
- Structured outputs: https://code.claude.com/docs/en/agent-sdk/structured-outputs
- Hooks: https://code.claude.com/docs/en/agent-sdk/hooks

*Reference implementation studied: `triage-cli-rs/src/tui/inbox/{app,poll,chat,mod}.rs`,
`tui/chat.rs`, `pipeline/`, `providers/`, `chat.rs`.*
