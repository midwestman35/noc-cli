# noc-cli — Design Specification

**Date:** 2026-06-02
**Status:** Approved in brainstorming; ready for implementation planning
**Supersedes:** `noc-cli-design.md` (original draft — arrived corrupted/truncated; its intent is reconstructed in Appendix A and becomes the agent system prompt)

---

## 1. Purpose

`noc-cli` is a strictly read-only triage assistant for the Carbyne APEX NG911/E911 NOC. It has two surfaces:

1. **`investigate`** — given a Zendesk ticket, an autonomous Claude agent pulls the ticket + customer history, ingests analyst-provided logs, investigates, and produces an evidence-grounded **five-file ticket folder** that commits an A/B/C/D routing fork and a symptom classification, grounded in a versioned rubric and symptom runbooks.
2. **`watch`** — a deterministic, non-LLM TUI that continuously polls the analyst's Zendesk queue and notifies them of status changes on their assigned tickets (e.g., a customer reply flipping a ticket Pending → Open).

It is the Python rebuild of the `triage-cli` sister app (itself a Rust port of an earlier Python tool), refined to a small surface and rebuilt around the **Claude Agent SDK**.

---

## 2. Relationship to the sister app (`triage-cli`)

- **Inherits:** the five-markdown ticket-folder contract, the A/B/C/D fork rubric, soft-lock `STATE.md` ownership, SQLite FTS5 memory, PII redaction at the LLM boundary, the strict read-only posture, and the fixture/offline testing ethos.
- **Drops:** Datadog enrichment, the cnc-map + `build-map` machinery, the Unleash/Codex provider abstraction, and the browse-past-reports inbox.
- **Changes:** Rust → Python; a structured single-shot LLM call → an **L3 autonomous agent** (Claude Agent SDK) under a hook-enforced harness; adds **runbook grounding** and a **two-axis classification**; adds the **live queue watcher/notifier**.

---

## 3. Locked decisions

| Axis | Decision |
|---|---|
| Language | **Python** (returns to the sister app's pre-Rust roots) |
| LLM | **Claude Agent SDK** (`claude-agent-sdk`); **claude.ai Enterprise seat** auth, inherited from the analyst's existing Claude Code login; draws on the Agent SDK credit pool (eff. 2026-06-15) |
| Agent altitude | **L3 full agent**, inside a **hook-enforced read-only sandbox harness** |
| Output | **5-markdown folder + A/B/C/D fork + runbook reference**, rendered from a pydantic-validated handoff |
| Classification | **Two axes** — symptom tag (`[No ANI]`…) picks the runbook + filters history; fork letter (A/B/C/D) = routing |
| Surface | `setup`, `investigate`, `watch`, `doctor` — **browse-past-reports inbox deferred** |
| Data | **Zendesk + local dropped files only** — no Datadog / cnc-map / build-map |
| History | **Both** — live Zendesk search (tag-filtered, exclude `[vendor]`, LLM-ranked) + local SQLite FTS5 memory |
| Watcher | Deterministic, **non-LLM**, read-only poll of a Zendesk view; banner + OS ping on status/comment changes; `Enter` → `investigate` |
| Grounding | embedded `fork-rubric.md` + authored `runbooks/` symptom playbooks |
| Visuals | Rich braille spinners + Textual breathing status; Textual report viewport; Textual live-queue watch app |

---

## 4. Non-goals (YAGNI)

Datadog, cnc-map, `build-map`, the browse-past-reports inbox (deferred), the Unleash/Codex/multi-provider abstraction, and **posting anything back to Zendesk, Jira, or any audited surface** (the tool prepares drafts; the analyst acts).

---

## 5. Technology stack

| Concern | Library |
|---|---|
| CLI | **Typer** |
| TUI / render | **Textual + Rich** |
| Models / LLM-output validation | **pydantic** |
| HTTP (Zendesk) | **httpx** |
| SQLite FTS5 (memory + watch state) | **`sqlite3`** (standard library; FTS5 built in) |
| Embed rubric/runbooks | **`importlib.resources`** |
| LLM engine | **`claude-agent-sdk`** |
| OS notifications (macOS) | `osascript` (built-in), upgraded to `terminal-notifier` if present |

Python **3.10+** (Agent SDK requirement).

---

## 6. Command surface

- `noc-cli setup` — interactive onboarding wizard (see §14).
- `noc-cli investigate <ticket-id-or-url>` — the L3 agent investigation (§8). Flags: `--file` (repeatable), `--paste LABEL=TEXT` (repeatable), `--force` (override soft-lock), `--fixture <dir>` (offline replay), `--no-agent` (dry path for CI), `--verbose`.
- `noc-cli watch` — the live queue watcher/notifier TUI (§13). Flags: `--view <id>`, `--assignee <name-or-email>`, `--interval <seconds>` (default 60).
- `noc-cli doctor` — green/red health checks (§14).

Deferred: `inbox` (browse the finished `Tickets/` corpus).

---

## 7. Architecture & module layout (`noc_cli/`)

```
cli.py            Typer app: setup, investigate, watch, doctor
config.py         config load/save, paths, env, owner, watch settings
models.py         pydantic: Ticket, Evidence, Match, Investigation, ForkPacket
zendesk.py        read-only Zendesk client (httpx): get_ticket, search, view_tickets
history.py        "both": Zendesk search + local memory -> candidate pool
memory.py         SQLite FTS5 over MEMORY.md
redact.py         PII scrub at the evidence boundary
scaffold.py       ticket dir (logs/ pcaps/ analysis/) + STATE soft-lock
render.py         validated payload -> 5 markdown files (atomic)
rubric.py         load embedded fork-rubric.md
runbooks/         authored symptom playbooks (no-ani.md ...) + lookup
agent/
  runner.py       builds ClaudeAgentOptions, runs query(), collects result
  prompt.py       system prompt = reconstructed spec (role/task/constraints)
  harness.py      PreToolUse/PostToolUse guardrail + audit hooks
  tools.py        history-search tool + read-only Zendesk MCP wiring
watch/
  poller.py       Zendesk view polling + assignee filter
  diff.py         current vs last-seen state -> classified change events
  state.py        last-seen state (in the SQLite DB)
  notify.py       Notifier interface; macOS impl (osascript/terminal-notifier)
tui/
  progress.py     Rich braille spinner + Textual breathing status (investigate)
  viewport.py     Textual report viewer (investigate)
  watch_app.py    Textual live-queue app: list + banner + poll spinner + Enter->investigate
data/
  fork-rubric.md  embedded via importlib.resources
```

~15 well-bounded modules vs the sister app's ~20 — the "refined" cut. Each module has one purpose and a narrow interface (e.g., `zendesk.py` exposes read methods only; the watcher never imports the agent; the agent never writes canonical files — `render.py` does).

---

## 8. Data flow: an `investigate` run

1. **Parse** ticket ID/URL → **pre-flight** soft-lock check (`STATE.md` `owner`; `--force` to override; exit 2 with a field diff on conflict).
2. **Fetch** the ticket (Zendesk read).
3. **Scaffold** `Tickets/<id>/{logs,pcaps,analysis}/`.
4. **Gather evidence:** download attachments + prompt the analyst to drop logs (or pre-supply via `--file` / `--paste`); unzip `.zip`; **flag `.pcap` (never parse inline)**.
5. **History (both), seeded:** gather initial candidates — live Zendesk search (approved tags only; exclude `[vendor]`) ∪ local FTS5 memory. These are passed to the agent, which may query further via its history-search tool and performs the final relevance ranking.
6. **Redact** PII from everything entering the agent context.
7. **Launch the L3 agent** (§9) with `cwd=Tickets/<id>/`.
8. The agent **investigates freely** — greps logs, cross-references history + runbooks, examines evidence, writes scratch notes into `analysis/`.
9. The agent **emits a structured final payload**: fork letter, confidence, symptom tag, evidence summary, historical matches, runbook reference, drafts.
10. **pydantic validates** the payload (retry once; on double-failure stash raw to `.debug/` and abort with no folder written).
11. **`render.py`** writes the five canonical files atomically; updates `STATE.md`; appends a memory entry; flushes `events.jsonl`. The TUI shows braille/breathing progress during, renders the report in a Textual viewport at the end, and echoes `FORK_PACKET.md` to stdout (pipeable).

---

## 9. The agent and its harness (L3 + guardrails)

The investigation is a single **L3 `query()`** against the Agent SDK. Claude drives; the harness is a hard wall enforced **in code, not in the prompt**.

**Allowed tools:** `Read`, `Grep`, `Glob`, read-only `Bash` (`grep`, `unzip -l`, `zcat`), `Write` **scoped to the sandbox**, a read-only **Zendesk MCP/tool**, and a **history-search** tool. `max_turns` is capped.

**`PreToolUse` hook denies:**
- any `Write`/`Edit` whose path resolves outside `Tickets/<id>/`;
- destructive / mutating `Bash` (`rm`, output redirection outside the sandbox, network-mutating commands);
- any Zendesk write operation.

**`PostToolUse` hook:** appends every tool call to `events.jsonl` (NOC audit trail).

This converts "full agent" into "full agent on a leash": Claude chooses freely among *allowed* actions; out-of-policy actions never execute. Two write-paths coexist by design — the agent writes scratch freely into the sandbox during investigation; the **canonical five files are rendered deterministically from the validated handoff** (§10).

---

## 10. Output contract (five files, faithful to the sister app)

Written atomically under `${NOC_TICKETS_ROOT:-./Tickets}/<id>/`:

| File | Content |
|---|---|
| `INTAKE.md` | Ticket facts, fingerprint, summary bullets, context pulls, initial hypothesis, intake decision. |
| `EVIDENCE_PREFLIGHT.md` | Gathered-evidence table, decisive evidence, missing/non-decisive evidence. |
| `FORK_PACKET.md` | Fork letter (A/B/C/D), confidence, reasoning, quoted rubric row, evidence summary, related work, handoff checklist, **+ a "Runbook Reference" section**. |
| `DRAFTS.md` | CONFIRM-gated drafts: customer reply, internal note, Jira draft (fork A only). |
| `STATE.md` | YAML frontmatter: `ticket_id`, `fork`, `symptom_tag`, `confidence`, `quoted_rubric_row`, `rubric_version`, `owner`, `status`, `related`, `validator_warnings`. |

The runbook reference is a **section inside `FORK_PACKET.md`**, and `symptom_tag` is a **field in `STATE.md`** — the contract stays five files (no sixth).

---

## 11. History and memory ("both")

- **Live Zendesk search:** query the customer's prior tickets, filter to the approved tag set, **exclude `[vendor]`**, then rank for relevance to the subject (the agent performs the semantic ranking, since Zendesk search is keyword/filter-based). Each match's "solution summary" is extracted from the old ticket's comments.
- **Local memory:** SQLite FTS5 over `MEMORY.md`, accruing noc-cli's own prior assessments. Cold-start empty; warms over time.
- The two candidate sets form a pool; the **agent ranks and selects up to 5** as HISTORICAL MATCHES.

---

## 12. Data sources & evidence handling

- **Zendesk** (read-only) for the ticket, comments, attachment metadata, and history search.
- **Local files** dropped into `logs/` (or `--file` / `--paste`). `.zip` archives are unzipped before analysis; `.pcap` files are **flagged, never parsed inline**.
- **No Datadog, no cnc-map, no `build-map`.**
- PII is redacted at the boundary before any evidence enters the agent context.

---

## 13. The queue watcher / notifier (`watch`)

A full-screen **Textual** TUI. **Deterministic and non-LLM** — it never invokes the Agent SDK (zero credit cost, safe to leave running all day). Strictly read-only on Zendesk.

**Loop:** poll the configured view on an interval (default 60s) → filter to the watched assignee → for each ticket, diff current `status` + latest-comment timestamp against the **persisted last-seen state** (in the SQLite DB) → emit a notification only on a real change.

**Triggers (option b):** status transitions **and** new public requester comments. The **Pending → Open** transition (Zendesk's signal that a customer replied to a ticket awaiting their response) is flagged specially.

**Notifications (both channels):**
- In-TUI **banner** (animated) while the app is focused.
- OS **desktop ping** via a `Notifier` interface — macOS impl using `osascript` (zero-install), upgraded to `terminal-notifier` when present. Linux (`notify-send`) / Windows impls are a later seam.

**Identity:** the watched view ID and assignee come from `setup` config (§14); `watch` can override via `--view` / `--assignee`. The assignee defaults to the configured self and is resolved to a Zendesk assignee filter.

**Integration:** `Enter` on a selected ticket launches `investigate` on it.

**Visuals:** a live queue list, **breathing/pulsing rows** for "waiting on customer" (Pending) tickets, a **braille spinner** on each poll, and an animated banner when a change lands.

---

## 14. Configuration, auth & onboarding (`setup`) + `doctor`

Per the original spec's "configure it once" pattern, **`setup` is the single onboarding wizard** and configures everything:

- Zendesk: `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN` (the client appends `/token` to the email for basic auth).
- Local: `NOC_TICKETS_ROOT` (base directory for ticket folders), `NOC_OWNER` (recorded in `STATE.md`; defaults to `$USER`).
- Watch: the **watched view ID**, the **assignee to watch** (default self), and **notification preferences**.
- Claude Code engine: **probe that it is installed and logged in** (the Enterprise-seat auth lives in Claude Code, not in noc-cli).

`setup` is idempotent (existing values become defaults on re-run). Config persists to a `.env` / config file under the data dir.

`doctor` prints green/red checks: Zendesk credentials, tickets-dir writability, the Claude Code engine reachable + authenticated, and **notification capability** (`osascript` / `terminal-notifier`). Exits 0 when all critical checks pass.

---

## 15. Runbooks (a content sub-project)

Author a starter set of **symptom playbooks** keyed to the approved tags: `no-ani.md`, `no-ali.md`, `low-audio.md`, `dropped-calls.md`, `event-history.md`, `apex.md`, embedded via `importlib.resources`. Lookup is by **symptom tag**; the agent quotes the matched section into `FORK_PACKET.md`'s "Runbook Reference". (The `Legacy/runbooks/` files are *operator* docs, not symptom playbooks, and are not used for grounding.)

---

## 16. Fork rubric

Port `playbook/fork-rubric.md` from the sister app, embed it via `importlib.resources`, and have the agent commit a fork letter against it (quoting the matched row verbatim into `FORK_PACKET.md`). Rubric-row drift is a **soft warning** stashed in `STATE.md` (`validator_warnings`), not a hard failure.

**Fork letters:** **A** Engineering Jira · **B** Vendor or Internal IT · **C** NOC self-resolve · **D** Cannot fork yet (rubric demands more evidence).

---

## 17. Classification model (two axes)

- **Symptom tag** — the approved set: `[apex]`, `[low audio]`, `[dropped calls]`, `[No ANI]`, `[No ALI]`, `[event history]`, plus `[unclassified]` as the catch-all. Drives runbook lookup and history filtering. `[vendor]` is **excluded from history search** (never a symptom tag).
- **Fork letter** — A/B/C/D routing (§16).
- **Confidence** — High / Medium / Low / Inconclusive.

The agent emits all three. Symptom answers *"what's broken,"* fork answers *"who acts next."*

---

## 18. Error handling

- **No / insufficient logs** → the agent must return **Inconclusive** (validated: evidence-absent ⇒ `fork=D` or `confidence=Inconclusive`); never fabricate a root cause.
- **Zendesk auth failure** → exit with a clear message naming the likely cause (email/token).
- **Schema-validation double-failure** → stash the raw response to `.debug/`, abort, write no folder.
- **Guardrail block** → logged to `events.jsonl`; the agent continues within bounds.
- **Soft-lock conflict** → exit 2 with a summarized field diff (sister-app semantics).
- **Watch** → poll failures are transient: log, keep the last-seen state, retry next interval; never crash the TUI on a single failed poll.

---

## 19. Testing strategy

- **Invariant tests over offline fixtures** (`--fixture`): valid schema? `fork ∈ {A,B,C,D}`? `symptom_tag` in the approved set? Inconclusive when evidence absent?
- **Harness tests:** assert that an out-of-sandbox `Write` and a destructive `Bash` are **blocked** by `PreToolUse`.
- **`--no-agent` dry path** for CI (exercises gather/scaffold/render without an LLM).
- **Watcher tests:** drive `diff.py` with a mock Zendesk client through scripted state transitions (incl. Pending → Open and a new requester comment) and assert the correct notification events fire.

---

## 20. Distribution

`uv tool install` / `pipx` (per-user). Python 3.10+. Requires the **Claude Code engine present and logged in** — which the target NOC analysts already have. The exact runtime dependency (Node + `claude` CLI version) is pinned during planning. A PyInstaller bundle is a later option.

---

## 21. Superseded constraints (reconciling the original spec)

The original `noc-cli-design.md` constrained the LLM to *"gather all data before analysis rather than interleaving API calls with reasoning,"* a strict single-shot read-only model, and full determinism. The **L3 agent decision supersedes these**: Claude interleaves reasoning and read-only tool use within the hook-enforced sandbox. The read-only *intent* is preserved and made stronger (enforced in code). The watcher is the one component that polls — but it is non-LLM, so it does not violate the "minimize LLM rounds" spirit.

---

## 22. Open work items (work, not decisions)

1. **Reconstruct** the corrupted original spec into the agent **system prompt** (role / task / constraints / approved tags / examples) and run it through the `opus-prompt-optimizer` skill.
2. **Author** the runbook starter set (§15).
3. **Port** `fork-rubric.md` from the sister app and embed it.
4. **Pin** the exact Claude Code runtime dependency for the Python Agent SDK (Node + CLI version).
5. **Implement** the macOS `Notifier` (osascript + terminal-notifier detection).

---

## Appendix A: reconstructed intent of the original spec

The original draft framed an *expert NOC triage analyst* persona, **operationally conservative — never guesses when evidence is absent**, specializing in 911/public-safety telecom, Zendesk workflows, and log-based root-cause analysis. Its workflow: parse the ticket → search customer history (approved tags only, exclude `[vendor]`, surface 5 most relevant) → scaffold the local working directory → ingest & analyze provided logs (unzip `.zip`; flag `.pcap`) → generate a triage assessment with a confidence level → classify with a symptom tag. Its constraints: strict read-only to Zendesk; work only with what Zendesk returns + local files; tag discipline; confidence honesty (Inconclusive over fabrication); runbook grounding (no invented steps); local writes scoped to the ticket directory. This intent is preserved and becomes the basis for the agent system prompt; where it conflicts with the L3 decision, §21 governs.
