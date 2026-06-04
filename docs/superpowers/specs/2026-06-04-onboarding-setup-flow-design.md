# Self-Service Onboarding — Packaging, Distribution & `setup` Flow — Design Spec

- **Date:** 2026-06-04
- **Status:** Approved in brainstorm; pending implementation plan.
- **Scope of this session:** the **onboarding architecture** — how a teammate goes
  from a fresh machine to a working `noc-cli` (engine + curated skills/agents +
  authenticated integrations + verified config) with **no live hand-holding**. Defines
  the phase engine, the shared `setup`/`doctor` check registry, the capability
  manifest, and the guided human-gates.
- **Explicitly deferred:** the actual *content* of the per-runbook subagents and skills
  (their prompts/tool scopes) → a follow-on "agent-per-runbook" spec. This spec defines
  how those artifacts are **packaged, installed, and verified**, not what they say.

---

## 1. Goal

Make `noc-cli` shareable across the NOC team without the author sitting on a Slack
huddle per user. Today the app *works* on the author's machine only because the
author's Claude Code instance (`~/.claude`) is hand-curated with skills, integrations,
and settings that **do not travel with the repo**. The Python package ships the code,
runbooks, fork rubric, system prompt, and safety harness; the *harness curation* does
not.

The goal is a single, idempotent, **resumable** command that:

1. installs/verifies the Claude Code engine and confirms it is authenticated,
2. installs the curated **skills + per-runbook subagents** into the user's own
   `~/.claude` (so they appear everywhere the teammate uses Claude Code, not only inside
   `noc-cli` runs),
3. collects and **verifies** Zendesk credentials,
4. registers and **verifies** the MCP integrations (guiding the human only through the
   irreducible OAuth/token gates),
5. writes local config (paths, owner, watch, notifications),
6. ends on **proof** — a green `doctor` plus a headless end-to-end smoke test.

The success test: *a brand-new teammate on a fresh machine runs one command, follows
the inline prompts, and finishes with a working `investigate` — and when something is
wrong, re-running the same command (or `doctor`) tells them exactly how to fix it,
without a call.*

## 2. Background / current state

- `noc-cli` drives Claude through the **Claude Agent SDK** (`agent/runner.py` →
  `claude_agent_sdk.query` with `ClaudeAgentOptions`). The SDK depends on a working,
  authenticated Claude Code engine on the user's machine.
- `setup.py::run_setup` is an interactive wizard that re-prompts all 8 config fields and
  writes the data-dir `.env` (`build_env_lines`). It does **one** capability nudge: a
  best-effort `shutil.which("claude")` warning (`cli.py:85`). It does not install skills,
  register MCP servers, verify the token, or prove the chain works.
- `doctor.py` runs five checks (`run_checks`) returning `CheckResult(ok, label, message)`
  and renders them with a critical/non-critical split (`print_report`). Checks are
  **report-only** — there is no remediation and no machine-parseable state.
  `check_zendesk_live_auth` already implements the 404-is-good / 401-is-bad token probe
  we want to promote into a setup gate.
- `config.py` owns the data dir (`data_dir()`, `NOC_HOME` override), the `.env` path, and
  the `_FIELD_ENV` field↔env mapping. `set_config_value` already does atomic single-field
  upsert.
- The harness (`agent/harness.py`) references read-only Zendesk MCP tool prefixes
  (`mcp__zendesk__*`) but `ALLOWED_TOOLS` (`agent/runner.py:16`) does not yet include
  them — live MCP is deferred. This spec lays the **registration + verification** track
  that a future live-MCP plan will switch on.
- Distribution precedent: the sister Rust app shipped a signed install one-liner +
  versioned releases + platform data dirs (`Legacy/README.md`). We cannot ship a single
  static binary (Python + SDK + Node engine), so our equivalent is a **bootstrap script
  that orchestrates the chain and ends green in `doctor`** — i.e. `setup` itself.

### 2.1 What travels with the repo vs. what is local (the gap this spec closes)

| Artifact | Today | After this spec |
|---|---|---|
| Python code, runbooks, fork rubric, system prompt, safety hooks | ✅ in package | unchanged |
| Curated **skills** + per-runbook **subagents** | ❌ author's `~/.claude` | shipped as package data; **installed** into `~/.claude` by `setup` (phase 2) |
| **MCP integrations** (Zendesk, later Jira/Slack/…) | ❌ author's local config | declared in a **manifest**; **registered + verified** by `setup` (phase 4) |
| Engine install + **auth** | ❌ assumed | detected, guided, and **proven** (phases 1, 6) |
| Credentials | ⚠️ wizard writes `.env`; never verified | wizard + **live verify** gate (phase 3) |

## 3. Scope

**In:** the phase-engine contract; refactor of `doctor.py` into a shared, ordered
**check registry** consumed by both `doctor` (report+remediate) and `setup` (act); the
capability **manifest** (`onboarding.yaml`) that data-drives skill/agent install and MCP
registration; the **guided human-gate** pattern (deep-link → pause → re-detect); the
**capability-install** mechanism (copy package-data skills/agents into `~/.claude` with a
version stamp); the **MCP registration** mechanism (`claude mcp add` with `${ENV}`
interpolation + `claude mcp list` parsing); the **headless smoke test**; and the
`doctor --fix` / `setup --resume` UX.

**Out:** the prompts/tool-scopes of individual subagents and skills (follow-on spec);
turning on live MCP tools in `ALLOWED_TOOLS` (separate plan — this spec only makes the
servers *present and authenticated*); the corporate-gateway vs. per-seat **auth-model**
decision (§10 — an org decision, surfaced but not resolved here); a published
plugin/marketplace (§10 — manifest is designed so this is a later swap, not a rewrite).

---

## 4. Architecture — the phase engine

`setup` and `doctor` are **the same ordered list of phases**, driven by two verbs. This
is the load-bearing decision: one registry, two drivers, so they can never drift.

### 4.1 The `Phase` contract

```python
class PhaseState(StrEnum):
    SATISFIED    = "satisfied"      # nothing to do
    NEEDS_ACTION = "needs_action"   # fixable (auto or guided)
    BLOCKED      = "blocked"        # cannot proceed (e.g. no network, engine refuses)

@dataclass
class PhaseResult:
    state: PhaseState
    label: str
    message: str               # human status line (doctor shows this)
    fix_hint: str | None = None  # exact command / URL to remediate (doctor shows on red)

class Phase(Protocol):
    name: str
    critical: bool
    def detect(self, ctx: SetupContext) -> PhaseResult: ...
    def act(self, ctx: SetupContext, io: ConsoleIO) -> PhaseResult: ...  # returns post-act detect()
```

- `detect()` is **pure-ish and side-effect-free** (probes only): safe to call from
  `doctor` and from `setup`'s resume logic.
- `act()` performs the automated fix **or** runs a guided human-gate, then returns a
  fresh `detect()` (its own verify). `setup` advances only when `act()` yields
  `SATISFIED`.
- `critical=True` phases halt the run on `BLOCKED`; non-critical ones warn and continue.

### 4.2 The two drivers

- **`doctor`** — runs every `phase.detect(ctx)` in order, renders status + `fix_hint`
  for non-satisfied ones, returns exit 0 iff all *critical* phases are `SATISFIED`.
  Pure read-only. This subsumes today's `run_checks`/`print_report`.
- **`doctor --fix`** — alias for the relevant subset of `setup` (acts on red phases only;
  skips config prompts already satisfied).
- **`setup`** — walks phases in order, resuming at the first non-`SATISFIED` phase;
  calls `act()` on each; stops on a critical `BLOCKED`. Re-running is always safe and is
  the primary support story ("re-run `noc-cli setup`").

### 4.3 `SetupContext` / `ConsoleIO`

- `SetupContext`: resolved `Config`, `data_dir()`, the loaded manifest, and injectable
  side-effect seams — `which_fn`, `run_cmd_fn` (subprocess wrapper), `open_url_fn`,
  `claude_mcp_fn`, `zd_factory`, and `now_fn`. Every external effect is injected so the
  whole engine is unit-testable with fakes (mirrors the existing injection style in
  `setup.py`/`doctor.py`).
- `ConsoleIO`: `prompt(label, default, hide_input)`, `print`, `confirm`, and the
  **gate primitive** `wait_until(detect, *, poll_s, give_up_after, skippable)` used by
  every human-gate (§6).

## 5. The phases

Ordered as a **dependency chain** (later phases need earlier ones). Phase 2 and 4 are
**manifest-driven** (§7).

| # | Phase | Critical | detect() probes | act() does | Human gate? |
|---|---|---|---|---|---|
| 0 | Preflight | yes | `python` ≥3.10 (implicit), `claude` + `node` on PATH | print install one-liners for anything missing | no |
| 1 | Engine | yes | `claude --version`; headless auth probe (`claude -p` returns a result) | install engine (or print installer); guided `claude login` gate | **login** |
| 2 | Capabilities | yes | manifest `version` vs `~/.claude/skills/.noc-version` stamp | copy skills+agents from package data; rewrite stamp | no |
| 3 | Credentials | yes | three Zendesk fields present in `.env`; `get_ticket(1)` → 404 ok / 401 bad | run credential wizard; deep-link token page; live-verify | **mint token** |
| 4 | Connections | mixed | per manifest server: parse `claude mcp list` for name + connected/auth status | `claude mcp add …`; guided `/mcp` OAuth gate for OAuth servers | **OAuth** (per server) |
| 5 | Config | no | remaining fields present (paths/owner/watch/notify) | existing `run_setup` wizard prompts | no |
| 6 | Proof | yes | n/a (always runs last) | `doctor` all-green + headless smoke test on a fixture | no |

### 5.1 Phase 0 — Preflight

`detect`: `which("claude")`, `which("node")`; Python version is implicit (the package is
already running). `act`: for each missing prereq, print the canonical install command
(`uv`, Node, Claude Code) — we **print, not auto-install**, system-level tools by
default to avoid surprising mutation; auto-install is opt-in via `--install-prereqs`.
`BLOCKED` only if the platform is unsupported.

### 5.2 Phase 1 — Engine (critical, human gate: login)

`detect`: `claude --version` for presence; then an **auth probe** — `claude -p "ok"
--output-format json` and assert a result comes back (this is the real
"engine + auth works" signal, stronger than today's PATH-only check in
`doctor.check_claude_engine`). Three outcomes:

- binary missing → `act` runs the official installer (if `--install-prereqs`) or prints
  it; `fix_hint` is the installer command.
- present but unauthenticated → **guided gate**: `io.print("Run `claude login` in
  another terminal")`, then `wait_until(detect, poll_s=3, …)` until the auth probe
  passes. `fix_hint` = `claude login`.
- authed → `SATISFIED` (message includes the resolved account, e.g. the logged-in email).

> **Auth-model note (see §10):** the login gate assumes per-seat Claude Code auth. If the
> org chooses a gateway/Bedrock model, this phase instead verifies the relevant env
> (`ANTHROPIC_BASE_URL` / `CLAUDE_CODE_USE_BEDROCK`) and the gate becomes "set this env",
> not "run `claude login`". The phase boundary is the same; only `detect/act` bodies
> change.

### 5.3 Phase 2 — Capabilities (critical, automated)

Ships the curated skills + per-runbook subagents as **package data** and installs them
into the user's own `~/.claude`.

- **detect:** compare manifest `version` against the stamp file
  `~/.claude/skills/.noc-version` (and an equivalent for agents). Missing/stale →
  `NEEDS_ACTION`.
- **act:** copy each `skills/<name>/` and `agents/<name>.md` from
  `noc_cli/data/claude/{skills,agents}/` into `~/.claude/{skills,agents}/`, namespaced
  with a `noc-` prefix to avoid collisions with the user's own curation, then write the
  version stamp. Copy (not symlink) by default for cross-platform robustness (Windows);
  symlink is an opt-in dev mode. Claude Code picks up new skills next session; for an
  active session a SessionStart hook can request a reload (§8).
- **Idempotent:** re-run with an unchanged manifest version → skipped.
- **Update story:** bump manifest `version` → teammates re-run `setup` → converges. This
  is what makes "we added a new runbook-agent" a non-event.

### 5.4 Phase 3 — Credentials (critical, human gate: mint token)

Reuses the Zendesk portion of `run_setup` for prompts, then **verifies**:

- **detect:** all three Zendesk fields present in `.env`; if present, construct the
  client (`zd_factory`) and `get_ticket(1)` — **404 = good** (creds valid, ticket absent),
  **401/"auth failed" = bad** (re-prompt), network/other = warn-but-pass (promoted from
  `doctor.check_zendesk_live_auth`).
- **act:** prompt subdomain/email (defaults from existing `.env`), then for the token,
  `open_url_fn` deep-links to
  `https://<subdomain>.zendesk.com/admin/apps-integrations/apis/zendesk-api/settings/tokens`
  with exact instructions, read the token (`hide_input=True`), persist via
  `set_config_value`, and re-verify inline. A bad token is caught **now**, not on first
  `investigate`.

### 5.5 Phase 4 — Connections (mixed criticality, human gate: OAuth)

For each MCP server in the manifest:

- **detect:** parse `claude mcp list` for the server name and its status; treat presence
  of pending/rejected markers (`⏸`/`✗`) or absence as non-satisfied; connected = ok.
  `claude mcp get <name>` for detail when needed.
- **act:**
  - *not registered* → `claude mcp add --transport <t> --scope user <name> <url>` with
    secrets passed via **`${ENV}` interpolation** (e.g. `Authorization: Bearer
    ${ZENDESK_API_TOKEN}`) so tokens are never written into committed config. `api_token`
    servers (Zendesk) are fully scriptable — **no browser**.
  - *registered but unauthenticated (OAuth)* → **guided gate**: instruct `/mcp` in Claude
    Code, then `wait_until` polling `claude mcp list` until the server is connected.
- **criticality:** `required: true` servers are critical (block); `required: false`
  (v1: everything except Zendesk) are skippable with `s`.

> **v1 scope lever:** if v1 ships **Zendesk-only**, phase 4 has **no OAuth gate at all**
> (pure api_token), and the guided-OAuth machinery can be built lazily when the first
> OAuth server (Jira/Slack/M365) is added. The manifest makes that a data change.

### 5.6 Phase 5 — Config (non-critical, automated)

The remainder of today's wizard: `tickets_root`, `owner`, `watch_view`,
`watch_assignee`, `notify`. Unchanged behavior; just reordered to run after the harder
gates so a user who bails early still has a working engine + creds.

### 5.7 Phase 6 — Proof (critical, automated)

The closer. Two steps:

1. `doctor` — run every phase `detect()`; require all critical `SATISFIED`.
2. **Headless smoke test** — `claude -p` against a **checked-in fixture ticket**
   (`tests/fixtures/…`) under read-only/no-write constraints, asserting a parseable
   `Handoff` (or the deterministic Fork-D "insufficient evidence" path) comes back. This
   exercises engine + auth + at least one installed skill end-to-end **without** touching
   live Zendesk or writing customer data. Green here ⇒ the last line the user sees is
   "✅ Ready," not "should work."

## 6. The guided human-gate pattern

Every irreducible human step uses one primitive so the UX is uniform and self-verifying:

```
deep-link  →  open the exact page/command for the user (open_url_fn / printed cmd)
pause      →  io.wait_until(detect, poll_s=3, give_up_after=5m, skippable=<bool>)
verify     →  re-run detect(); only advance on SATISFIED; on give-up, print fix_hint
```

There are exactly **three** such gates, and they are the only places a human is required:

1. **Engine login** (phase 1) — `claude login`.
2. **Zendesk token** (phase 3) — mint a read-scope token in the Zendesk UI.
3. **MCP OAuth** (phase 4) — browser consent via `/mcp`, **only** for OAuth servers
   (none in a Zendesk-only v1).

Everything else is automated. The gate never leaves the user guessing "did that take?" —
it re-detects and confirms before moving on.

## 7. The capability manifest (single source of truth)

Shipped as package data (`noc_cli/data/onboarding.yaml`); read by phases 2 & 4, reported
by `doctor`, and exercised by the phase-6 smoke test.

```yaml
version: "2026-06-04"          # bump → triggers phase-2 reinstall on next setup
skills:
  - noc-redact
  - sip-log-parse
  - jira-draft-format
agents:                         # per-runbook subagents (content = follow-on spec)
  - no-ani
  - no-ali
  - low-audio
  - dropped-calls
  - event-history
  - apex
mcp:
  - name: zendesk
    transport: http
    url: "${ZENDESK_MCP_URL}"
    auth: api_token             # scriptable; no browser gate
    headers: { Authorization: "Bearer ${ZENDESK_API_TOKEN}" }
    required: true
  - name: jira                  # example future server — optional in v1
    transport: http
    url: "https://mcp.atlassian.com/v1/sse"
    auth: oauth                 # browser gate via /mcp
    required: false
```

Adding an integration later = a **one-file PR** (edit the manifest + add the skill/agent
data), not a flow change. This is the concrete payoff of "expand the integrations
without re-rolling-out."

## 8. SessionStart hook (optional, complementary)

A repo-shipped `SessionStart` hook (`.claude/settings.json`) can run a lightweight
`doctor`-style probe at session start and surface drift (stale capability stamp,
disconnected MCP) into Claude's context, and request `reloadSkills` when phase 2 just
installed new skills. This is **complementary** to `setup`, not a replacement — `setup`
remains the canonical, resumable installer.

## 9. Module layout

| Module | Change | Responsibility |
|--------|--------|----------------|
| `noc_cli/onboarding/__init__.py` | **new** | package for the phase engine |
| `noc_cli/onboarding/phases.py` | **new** | `Phase` protocol, `PhaseState`, `PhaseResult`, the six phase impls |
| `noc_cli/onboarding/engine.py` | **new** | `run_setup_flow` (act-driver) + `run_doctor` (detect-driver) over the ordered registry |
| `noc_cli/onboarding/context.py` | **new** | `SetupContext`, `ConsoleIO`, injectable seams, `wait_until` gate primitive |
| `noc_cli/onboarding/manifest.py` | **new** | load + validate `onboarding.yaml` (pydantic); version-stamp helpers |
| `noc_cli/onboarding/capabilities.py` | **new** | copy skills/agents into `~/.claude`; stamp compare/write |
| `noc_cli/onboarding/mcp.py` | **new** | `claude mcp add` builder + `claude mcp list` parser (status enum) |
| `noc_cli/onboarding/smoke.py` | **new** | headless `claude -p` fixture smoke test |
| `noc_cli/data/onboarding.yaml` | **new** | the manifest (package data) |
| `noc_cli/data/claude/skills/…`, `…/agents/…` | **new** | shipped skill + subagent definitions (content: follow-on spec) |
| `noc_cli/doctor.py` | **refactor** | re-express the five checks as phase `detect()`s with `fix_hint`; keep thin back-compat shims |
| `noc_cli/setup.py` | **reuse** | credential/config prompts called by phases 3 & 5 (logic unchanged) |
| `noc_cli/cli.py` | **extend** | `setup` → phase driver; `doctor` → detect driver + `--fix`; `--resume`, `--install-prereqs`, `--skip-optional` flags |
| `pyproject.toml` | **extend** | `force-include` `noc_cli/data/claude` and `noc_cli/data/onboarding.yaml` in the wheel |

## 10. Open decisions (surface, don't resolve here)

- **Auth model — per-seat vs. gateway/Bedrock.** The single biggest rollout lever.
  Per-seat → phase 1 is `claude login`. Gateway/Bedrock (as the sister app's `unleash`
  path implies Axon prefers) → phase 1 verifies env vars instead. Phase boundary is
  identical; bodies differ. **Decide before implementing phase 1.**
- **v1 MCP scope — Zendesk-only vs. +Jira/Slack/M365.** Zendesk-only ⇒ no OAuth gate ⇒
  phase 4 is fully scriptable and the guided-OAuth code is deferred. Each OAuth server
  added is one more human gate. **Decide before implementing phase 4.**
- **Distribution wrapper — `uv tool install` vs. bootstrap one-liner vs. plugin +
  internal marketplace.** The manifest + phase engine is agnostic to this; a plugin is a
  later swap of phase 2's install mechanism, not a rewrite. **Decide for the team
  presentation, not for v1 code.**
- **Auto-install prereqs default.** Ship `--install-prereqs` opt-in (print by default) or
  auto-install Claude Code/Node? Defaulting to print is the conservative, less-surprising
  choice.

## 11. Error handling

- Engine probe fails transiently (network) → `BLOCKED` with a network `fix_hint`; halts
  (critical) rather than proceeding into guaranteed-failing phases.
- Token verify 401 → re-prompt inline (max 3 attempts) then `NEEDS_ACTION` with the token
  page `fix_hint`; never persists an unverified token silently.
- `claude mcp list` unparseable / engine absent → phase 4 reports `BLOCKED` referencing
  phase 1, not a crash.
- Capability copy partial failure → leave the version stamp **unwritten** so the next run
  retries cleanly (never stamp a partial install).
- Smoke test failure → non-zero exit, raw `claude -p` output stashed to
  `data_dir()/.debug/` (mirrors `runner.py`'s stash-on-failure pattern), `fix_hint`
  points at `doctor`.
- Every gate is `give_up_after`-bounded: on timeout, print the `fix_hint` and exit
  non-zero (resumable) rather than hanging.

## 12. Testing strategy

- **Pure logic (pytest, fully mocked seams):**
  - manifest load/validate incl. bad schema; version-stamp compare (fresh / stale /
    current).
  - `claude mcp list` parser: connected / `⏸` pending / `✗` rejected / absent → status
    enum.
  - `claude mcp add` command builder: correct flags per transport; `${ENV}` interpolation
    preserved (secrets never expanded into committed strings).
  - phase `detect()` truth tables for all six phases via injected fakes (engine present
    /authed matrix; token 404 vs 401; capability stamp states).
  - engine driver: resume-at-first-unsatisfied; critical `BLOCKED` halts; non-critical
    `BLOCKED` continues.
  - `wait_until`: satisfies on Nth poll; gives up after bound; `skippable` path.
- **CLI (typer `CliRunner`):** `doctor` exit codes (all-green vs. one critical red);
  `doctor --fix` delegates to act-driver; `setup --resume` skips satisfied phases;
  `--skip-optional` bypasses non-critical MCP servers.
- **Capability install (tmp `NOC_HOME` + fake `~/.claude`):** copies namespaced
  skills/agents; writes stamp; idempotent re-run skips; partial-failure leaves stamp
  unwritten.
- **Smoke test:** injected `run_cmd_fn` returns a canned `claude -p` JSON → asserts the
  Handoff-parse path and the failure-stash path. **No live engine, Zendesk, or network in
  ordinary tests** (house rule).

## 13. Future / out of scope

- **Agent-per-runbook content** — the prompts, tool-scopes, and stop-conditions of each
  subagent in the manifest (prototyped by `Legacy/agents/*.md`). Separate spec; this one
  only packages/installs/verifies them.
- **Turning on live MCP tools** in `agent/runner.py::ALLOWED_TOOLS` — separate plan;
  this spec only makes the servers present + authenticated.
- **Plugin + internal marketplace** distribution — a later swap of phase 2's mechanism
  (`/plugin install` + `extraKnownMarketplaces` auto-prompt) once the team standardizes
  on it; the manifest is deliberately shaped to map onto a plugin definition.
- **Orchestrator dispatch** — `investigate` routing to the matching per-runbook subagent
  after the fork decision (the "agent per item" payoff) — depends on this spec landing.

## Appendix — phase ↔ verb matrix

| Phase | `doctor` (detect) | `setup` (act) | Gate |
|---|---|---|---|
| 0 Preflight | shows missing prereqs + install cmd | prints (or installs w/ flag) | — |
| 1 Engine | shows auth state + `fix_hint` | install + guided login | login |
| 2 Capabilities | shows stamp drift | copy + stamp | — |
| 3 Credentials | shows token verify state | wizard + deep-link + verify | mint token |
| 4 Connections | parses `claude mcp list` per server | `mcp add` + guided OAuth | OAuth (optional servers) |
| 5 Config | shows missing fields | wizard prompts | — |
| 6 Proof | runs full detect + reports | detect + headless smoke | — |
