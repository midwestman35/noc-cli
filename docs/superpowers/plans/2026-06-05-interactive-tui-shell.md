# Interactive TUI Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the two-pane `watch` TUI the home of `noc-cli` — a splash on launch, an always-focused input-first text box that runs `/commands` and chats with the agent about the selected ticket — and demote `investigate`/`watch`/`scout` to hidden, deprecated CLI aliases.

**Architecture:** Bare `noc-cli` launches `WatchApp` (no subcommand). A `CommandInput` widget sits between the panes and the footer; submitted text is parsed by a pure router (`tui/command.py`) into either a slash-command (dispatched to existing app actions) or a freeform chat turn. Investigate moves in-process: `cli.py`'s pipeline is extracted to `noc_cli/investigate.py::run_investigation(...)` with an `on_line` progress callback, and the TUI runs it in a thread worker (replacing the subprocess spawn). Chat is a per-ticket `ClaudeSDKClient` session (`tui/chat.py`) persisted to `CONVERSATION.jsonl` in the ticket folder, redacted per turn, rendered in a new right-pane **Chat** view, interruptible with `esc`.

**Tech Stack:** Python 3.11, Typer (CLI), Textual (TUI), Rich, `claude-agent-sdk` 0.2.88 (`query` for investigate, `ClaudeSDKClient` for chat), pytest + `pytest.mark.anyio` (Textual `Pilot`), `typer.testing.CliRunner`.

---

## Conventions (read once)

- **Test runner:** `uv run pytest <path> -v`.
- **TUI tests** mirror `tests/test_watch_app.py`: module-level `pytestmark = pytest.mark.anyio`, an `anyio_backend` fixture returning `"asyncio"`, the `_make_config`/`_make_app`/`_FakeClient`/`_poll`/`_text` helpers, and `async with app.run_test(size=(120, 40)) as pilot:`. New TUI tests reuse those helpers (copy them into the new test file or import from a shared `tests/_tui.py` if one is introduced — for this plan, copy, matching the existing duplication).
- **CLI tests** mirror `tests/test_cli.py`: module-level `runner = CliRunner()`, `runner.invoke(app, [...])`, `monkeypatch.setenv("NOC_HOME", str(tmp_path))`.
- **Commits:** conventional-commit subject; end the body with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Spec:** `docs/superpowers/specs/2026-06-05-interactive-tui-shell-design.md`. Each task names the spec section it implements.

---

## Phase 0 — PR #6 (Backlog Scout) integration  ✅ DONE

PR #6 (`feat/backlog-scout-engine`) was merged into this branch **before** implementation so the shipped package includes both features. Record + verification:

- **Merge:** `git merge origin/feat/backlog-scout-engine` → commit `263efb5`, **clean, zero conflicts** (this branch held only docs; PR #6's code does not overlap the TUI files this plan edits).
- **Baseline:** `uv run pytest -q` → **371 passed**. The integrated branch is green before Task 1 — keep it green task-by-task.
- **Signatures verified against the merged tree:**
  - `agent/harness.py::build_hooks(sandbox_root, events_path)` — **unchanged** (PR #6 added `restrict_read_tools` only to `make_pre_tool_use`); the Task 10 chat factory is safe as written.
  - `cli.py` line refs are valid as-is: `app`@16, `main`@56, `doctor`@141, `investigate`@166, `scout`@459, `watch`@512.
  - New modules (`investigate.py`, `tui/command.py`, `tui/chat.py`) and `tui/watch_app.py` do not overlap any `scout/*` module.
- **Scout decision (option B, confirmed):** demote all three — `investigate`, `watch`, **and `scout`** — to hidden/deprecated aliases per spec §4.1 (Task 2 unchanged). `noc-cli scout` keeps working (hidden, with a deprecation warning); `/scout` stays a stub this release (Task 7) that points at the CLI command. PR #6's scout tests use substring assertions, so the added warning does not break them — Task 2 Step 4 re-runs `tests/test_cli_scout.py` to confirm.

---

## Phase 1 — CLI surface (the shell entry)

### Task 1: Bare `noc-cli` launches the TUI

Implements spec §4.1 (bare `noc-cli` → TUI). Today the Typer app has `no_args_is_help=True`; we make the root callback launch the TUI when no subcommand is given.

**Files:**
- Modify: `noc_cli/cli.py:16-20` (app construction), `noc_cli/cli.py:55-65` (`main` callback)
- Create: `noc_cli/cli.py` helper `_launch_tui()` (extract from the current `watch` body)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_bare_invocation_launches_tui(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    called = {}

    def fake_launch():
        called["launched"] = True

    monkeypatch.setattr("noc_cli.cli._launch_tui", fake_launch)
    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    assert called.get("launched") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_bare_invocation_launches_tui -v`
Expected: FAIL — `AttributeError: ... has no attribute '_launch_tui'` (or exit code from `no_args_is_help`).

- [ ] **Step 3: Write minimal implementation**

In `noc_cli/cli.py`, change the app construction (remove `no_args_is_help=True`):

```python
app = typer.Typer(
    name="noc-cli",
    help="Read-only NOC triage assistant for Carbyne APEX NG911/E911.",
    invoke_without_command=True,
)
```

Replace the `main` callback (lines 55-65) with one that launches the TUI when no subcommand ran:

```python
@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Read-only NOC triage assistant. Run with no command to open the inbox."""
    if ctx.invoked_subcommand is None:
        _launch_tui()
```

Add `_launch_tui()` by extracting the body of the current `watch` command (lines 531-574) verbatim into a module-level function (it builds the client, connects the store, runs `WatchApp`). Keep the lazy imports inside it:

```python
def _launch_tui(view: str = "", assignee: str = "", interval: int = 60) -> None:
    from noc_cli import store
    from noc_cli.config import db_path
    from noc_cli.tui.watch_app import WatchApp
    from noc_cli.watch.notify import build_notifier
    from noc_cli.watch.state import WatchState
    from noc_cli.zendesk import ZendeskClient, ZendeskError

    cfg = load_config()
    if view:
        cfg = cfg.model_copy(update={"watch_view": view})
    if assignee:
        cfg = cfg.model_copy(update={"watch_assignee": assignee})
    if not cfg.watch_view:
        typer.secho(
            "Error: no view configured. Run `noc-cli setup`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        client = ZendeskClient(cfg)
    except ZendeskError as exc:
        typer.secho(f"Zendesk error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    conn = store.connect(db_path())
    try:
        ws = WatchState(conn)
        notifier = build_notifier(cfg.notify)
        WatchApp(
            config=cfg,
            client=client,
            watch_state=ws,
            notifier=notifier,
            poll_interval=interval,
        ).run()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_bare_invocation_launches_tui -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/cli.py tests/test_cli.py
git commit -m "feat(cli): bare noc-cli launches the TUI"
```

---

### Task 2: Hide + deprecate `investigate` / `watch` / `scout`

Implements spec §4.1 (deprecated/hidden compatibility aliases — the user's edit) and §2 (keep aliases one release, hidden from `--help`, warn + delegate).

**Files:**
- Modify: `noc_cli/cli.py` — `@app.command()` decorators for `investigate`, `watch`, `scout` → `@app.command(hidden=True, deprecated=True)`; add a deprecation warning at the top of each body.
- Test: `tests/test_cli.py` (update two existing tests + add one)

- [ ] **Step 1: Write the failing test**

In `tests/test_cli.py`, replace `test_help_lists_config_in_full_command_surface` with:

```python
def test_help_hides_deprecated_aliases_keeps_setup_doctor_config():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("setup", "doctor", "config"):
        assert command in result.stdout
    # investigate/watch/scout are hidden compatibility aliases now.
    for command in ("investigate", "watch", "scout"):
        assert command not in result.stdout


def test_watch_alias_warns_then_delegates(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.setattr("noc_cli.cli._launch_tui", lambda **kw: None)
    result = runner.invoke(app, ["watch"])
    assert result.exit_code == 0, result.output
    assert "deprecated" in result.output.lower()
    assert "noc-cli" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_help_hides_deprecated_aliases_keeps_setup_doctor_config tests/test_cli.py::test_watch_alias_warns_then_delegates -v`
Expected: FAIL — `investigate` still in `--help`; `watch` body still runs the TUI without a warning.

- [ ] **Step 3: Write minimal implementation**

Add a helper near the top of `cli.py`:

```python
def _warn_deprecated(old: str, new: str) -> None:
    typer.secho(
        f"`noc-cli {old}` is deprecated and will be removed in a future release. "
        f"Use {new} instead.",
        fg=typer.colors.YELLOW,
        err=True,
    )
```

Change the `watch` command to delegate to `_launch_tui` (its body becomes a thin alias):

```python
@app.command(hidden=True, deprecated=True)
def watch(
    view: str = typer.Option("", "--view"),
    assignee: str = typer.Option("", "--assignee"),
    interval: int = typer.Option(60, "--interval", min=1),
) -> None:
    """Deprecated alias — bare `noc-cli` now opens the inbox."""
    _warn_deprecated("watch", "bare `noc-cli`")
    _launch_tui(view=view, assignee=assignee, interval=interval)
```

For `investigate` (line 165) and `scout` (line 459), add `hidden=True, deprecated=True` to the decorator and `_warn_deprecated(...)` as the first line of each body:

```python
@app.command(hidden=True, deprecated=True)
def investigate(...) -> None:
    """Deprecated alias — use `/investigate` inside the TUI."""
    _warn_deprecated("investigate", "`/investigate` inside the TUI")
    # ... existing body unchanged ...
```

```python
@app.command(hidden=True, deprecated=True)
def scout(...) -> None:
    """Deprecated alias — use `/scout` inside the TUI."""
    _warn_deprecated("scout", "`/scout` inside the TUI")
    # ... existing body unchanged ...
```

Update the existing `test_watch_command_exposes_flags` and `test_watch_command_rejects_invalid_interval`: they still pass (`--help` on a hidden command still renders flags; `--interval 0` still rejected by `min=1`). No change needed — verify in Step 4.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py tests/test_cli_scout.py -v`
Expected: PASS — the updated `test_cli.py` help/alias tests, and PR #6's `tests/test_cli_scout.py` unchanged (a hidden command is still invokable; the deprecation warning is additive and those tests assert with substrings).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/cli.py tests/test_cli.py
git commit -m "feat(cli): demote investigate/watch/scout to hidden deprecated aliases"
```

---

## Phase 2 — Splash + version header

### Task 3: Show the version in the TUI header

Implements spec §3, §4.2 (version in header). `_update_banner` in `watch_app.py:480-495` builds `"noc-cli watch · my tickets · …"`; change the leading two parts to `"noc-cli v{__version__}"`.

**Files:**
- Modify: `noc_cli/tui/watch_app.py:480-495` (`_update_banner`)
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_watch_app.py`:

```python
async def test_banner_shows_version(db_conn, tmp_path):
    from noc_cli import __version__

    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        banner = _text(app.query_one("#banner"))
    assert f"v{__version__}" in banner
    assert "my tickets" in banner
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watch_app.py::test_banner_shows_version -v`
Expected: FAIL — banner contains `"noc-cli watch"`, not `"v0.1.0"`.

- [ ] **Step 3: Write minimal implementation**

In `watch_app.py`, add the import near the top (with the other `noc_cli` imports):

```python
from noc_cli import __version__
```

In `_update_banner`, change the `parts` list head from:

```python
        parts = [
            "noc-cli watch",
            "my tickets",
```

to:

```python
        parts = [
            f"noc-cli v{__version__}",
            "my tickets",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_watch_app.py::test_banner_shows_version -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): show version in the watch header"
```

---

### Task 4: Splash overlay on mount, dissolve on first poll

Implements spec §4.2. A full-screen `#splash` Static is shown from `on_mount`, then removed the first time `on_poll_complete` runs.

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — `_CSS` (add `#splash` + `layers`), `compose` (yield splash), `__init__` (`_splash_dismissed` flag), `on_poll_complete` (dissolve)
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_splash_shows_then_dissolves_on_first_poll(db_conn, tmp_path):
    from noc_cli import __version__

    app = _make_app(_make_config(tmp_path), _FakeClient(), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        splash = app.query("#splash")
        assert len(splash) == 1
        assert f"v{__version__}" in _text(splash.first())
        await _poll(app, pilot)
        assert len(app.query("#splash")) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watch_app.py::test_splash_shows_then_dissolves_on_first_poll -v`
Expected: FAIL — no `#splash` widget.

- [ ] **Step 3: Write minimal implementation**

In `_CSS`, change the `Screen` rule to declare layers and add a splash rule:

```css
Screen { layout: vertical; layers: base overlay; }
#splash {
    layer: overlay;
    width: 100%;
    height: 100%;
    content-align: center middle;
    text-align: center;
    background: $surface;
    color: $text;
}
```

In `compose`, add the splash as the last child (so it overlays), using the existing branding:

```python
    def compose(self) -> ComposeResult:
        yield Static("", id="banner", markup=False)
        yield Static("", id="notification", markup=False)
        with Horizontal(id="body"):
            yield TicketList(id="ticket-list")
            with Vertical(id="detail-pane"):
                yield Static("", id="detail-header", markup=False)
                yield Static("", id="detail-tablabel", markup=False)
                with DetailPane(id="detail"):
                    yield Static("", id="detail-content", markup=False)
        yield Footer()
        yield Static(self._splash_text(), id="splash", markup=False)
```

Add the splash text helper and a dismissed flag. Add to `__init__` (with the other instance attrs):

```python
        self._splash_dismissed = False
```

Add the method:

```python
    def _splash_text(self) -> str:
        from noc_cli import __version__
        from noc_cli.branding import TAGLINE

        return f"noc-cli\n\n{TAGLINE}\nv{__version__}\n\n⠹ Loading my tickets …"
```

In `on_poll_complete`, dissolve the splash once, as the first action of the handler:

```python
    def on_poll_complete(self, message: PollComplete) -> None:
        if not self._splash_dismissed:
            self._splash_dismissed = True
            for node in self.query("#splash"):
                node.remove()
        self._polling = False
        # ... rest of the existing handler unchanged ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_watch_app.py::test_splash_shows_then_dissolves_on_first_poll -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): cold-start splash that dissolves on first poll"
```

---

## Phase 3 — Input-first model, router, in-process investigate

### Task 5: Extract `run_investigation` into `noc_cli/investigate.py`

Implements spec §4.4. Move the pipeline out of `cli.py::_run_investigate` (lines 238-456) into a reusable async function that emits progress via an `on_line` callback instead of a `PhaseTracker`/`Console`. The emitted strings MUST match `watch.inbox.detect_phase` substrings (`"Scaffold ready"`, `"fetched"`, `"Evidence gathered"`, `"PII redacted"`, `"History seeded"`, `"Agent completed"`, `"Report rendered"`, `"complete"`+`"Ticket #"`).

**Files:**
- Create: `noc_cli/investigate.py`
- Modify: `noc_cli/cli.py` — `_run_investigate` (delegate to the new module)
- Test: `tests/test_investigate_module.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_investigate_module.py`:

```python
from pathlib import Path

import pytest

from noc_cli.config import Config

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _cfg(tmp_path) -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="a@x.com",
        zendesk_api_token="tok",
        tickets_root=tmp_path,
        owner="enrique",
        watch_view="555",
    )


async def test_run_investigation_no_agent_emits_phase_lines(tmp_path):
    from noc_cli.investigate import run_investigation
    from noc_cli.watch.inbox import detect_phase

    lines: list[str] = []
    root = await run_investigation(
        ticket_id=4242,
        config=_cfg(tmp_path),
        tickets_root=tmp_path,
        owner="enrique",
        no_agent=True,
        on_line=lines.append,
    )
    assert root == tmp_path / "4242"
    detected = {detect_phase(line) for line in lines}
    # The no-agent path scaffolds, gathers, and redacts.
    assert "Scaffold ready" in detected
    assert "Evidence gathered" in detected
    assert "PII redacted" in detected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_investigate_module.py -v`
Expected: FAIL — `ModuleNotFoundError: noc_cli.investigate`.

- [ ] **Step 3: Write minimal implementation**

Create `noc_cli/investigate.py`. This is `cli.py::_run_investigate` with `console`/`tracker` replaced by `on_line`, returning `folder.root`, and raising `InvestigationError` instead of `typer.Exit`:

```python
"""In-process investigate pipeline, callable from the CLI and the TUI worker."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from noc_cli.config import Config


class InvestigationError(RuntimeError):
    """Raised when the pipeline cannot complete (soft-lock, agent failure)."""


def _noop(_line: str) -> None:
    pass


async def run_investigation(
    *,
    ticket_id: int,
    config: Config,
    tickets_root: Path,
    owner: str,
    initial_hypothesis: str = "",
    extra_files: Optional[list[Path]] = None,
    pastes: Optional[list] = None,
    force: bool = False,
    fixture: Optional[Path] = None,
    no_agent: bool = False,
    verbose: bool = False,
    on_line: Callable[[str], None] | None = None,
) -> Path:
    """Run the L3 investigate pipeline. Emits human-readable progress strings via
    on_line (matching watch.inbox.detect_phase). Returns the ticket folder root.
    Raises InvestigationError on soft-lock conflict or agent failure."""
    emit = on_line or _noop
    extra_files = extra_files or []
    pastes = pastes or []

    from noc_cli.evidence import (
        PasteInput,
        _looks_like_text,
        gather_evidence,
        write_ticket_source,
    )
    from noc_cli.memory import InvestigationRecord, MemoryStore, append_investigation
    from noc_cli.scaffold import SoftLockConflict, preflight_soft_lock, scaffold_ticket

    tickets_root = Path(os.environ.get("NOC_TICKETS_ROOT", str(tickets_root)))

    # Scaffold + soft-lock
    folder = scaffold_ticket(tickets_root, ticket_id)
    try:
        preflight_soft_lock(folder, owner=owner, force=force)
    except SoftLockConflict as exc:
        raise InvestigationError(f"soft-lock conflict: {exc}") from exc
    emit(f"Scaffold ready: {folder.root}")

    # Fetch (skipped in --no-agent and --fixture)
    attachments: list[dict] = []
    zd_client = None
    if fixture is None and not no_agent:
        try:
            from noc_cli.zendesk import ZendeskClient

            zd_client = ZendeskClient(config)
            ticket = zd_client.get_ticket(ticket_id)
            comments = zd_client.get_comments(ticket_id)
            write_ticket_source(folder, ticket, comments)
            for comment in comments:
                for att in getattr(comment, "attachments", []) or []:
                    attachments.append(att.model_dump())
            emit(f"Ticket #{ticket_id} fetched")
        except Exception as exc:  # fetch failure is non-fatal
            emit(f"Zendesk fetch failed: {exc}")

    # Gather
    paste_inputs: list = []
    for p in pastes:
        if isinstance(p, PasteInput):
            paste_inputs.append(p)
        elif "=" in p:
            label, _, text = p.partition("=")
            paste_inputs.append(PasteInput(label=label.strip(), text=text))
        else:
            paste_inputs.append(PasteInput(label="paste", text=p))
    gather_evidence(
        folder=folder,
        zendesk_attachments=attachments,
        extra_files=extra_files,
        pastes=paste_inputs,
        zendesk_client=zd_client,
    )
    emit("Evidence gathered")

    # Redact (best-effort)
    from noc_cli.redact import redact, residual_pii_warning

    for log_file in folder.logs.iterdir():
        if log_file.is_file() and _looks_like_text(log_file.name):
            try:
                text = log_file.read_text(encoding="utf-8", errors="replace")
                redacted, counts = redact(text)
                log_file.write_text(redacted, encoding="utf-8")
                warning = residual_pii_warning(redacted, counts)
                if warning and verbose:
                    emit(f"{log_file.name}: {warning}")
            except Exception:
                pass
    emit("PII redacted")

    if no_agent:
        emit(f"Ticket #{ticket_id} complete (no-agent) — {folder.root}")
        return folder.root

    # History
    from noc_cli.config import db_path

    mem_db = Path(os.environ.get("NOC_DB_PATH", str(db_path())))
    mem_md = tickets_root / "MEMORY.md"
    mem_store = MemoryStore(db_path=mem_db, memory_md_path=mem_md)
    mem_store.init()

    history_context = ""
    if fixture is None:
        from noc_cli.history import seed_history
        from noc_cli.zendesk import ZendeskClient

        zd_for_history = ZendeskClient(config)
        symptom_tag = initial_hypothesis or "[unclassified]"
        candidates = seed_history(
            symptom_tag, zendesk_client=zd_for_history, memory_store=mem_store
        )
        history_context = "\n".join(
            f"- Ticket #{c.ticket_id}: {c.subject} (source: {c.source})"
            for c in candidates[:10]
        )
        emit(f"History seeded: {len(candidates)} candidate(s)")
    else:
        emit("History seeded (fixture mode)")

    # Agent (or fixture replay)
    from noc_cli.models import Handoff

    handoff: Optional[Handoff] = None
    transcript = []
    if fixture is not None:
        import json

        handoff_path = fixture / "handoff_good.json"
        if not handoff_path.exists():
            raise InvestigationError(f"fixture {handoff_path} not found")
        handoff = Handoff.model_validate(json.loads(handoff_path.read_text()))
    else:
        from noc_cli.agent.prompt import build_system_prompt
        from noc_cli.agent.runner import run_agent
        from noc_cli.rubric import load_rubric

        rubric = load_rubric()
        system_prompt = build_system_prompt(rubric.core)
        runner_result = await run_agent(
            ticket_id=ticket_id,
            folder=folder,
            system_prompt=system_prompt,
            history_context=history_context,
            initial_hypothesis=initial_hypothesis,
        )
        transcript = runner_result.transcript
        handoff = runner_result.handoff
        if handoff is None:
            try:
                from noc_cli.render import render_reasoning

                render_reasoning(transcript, None, folder)
            except Exception:
                pass
            raise InvestigationError(
                f"agent failed after 2 attempts; raw stashed to {runner_result.stash_path}"
            )
    emit("Agent completed")

    # Render
    from noc_cli.render import (
        consulted_runbook_slugs,
        render_handoff,
        render_reasoning,
        validation_warnings,
    )

    if initial_hypothesis:
        handoff = handoff.model_copy(
            deep=True,
            update={
                "intake": handoff.intake.model_copy(
                    update={"initial_hypothesis": initial_hypothesis}
                )
            },
        )
    consulted = consulted_runbook_slugs(transcript)
    warnings = validation_warnings(handoff, consulted, folder=folder)
    render_handoff(
        handoff, folder, owner=owner,
        consulted_runbooks=consulted, validator_warnings=warnings,
    )
    try:
        render_reasoning(transcript, handoff, folder)
    except Exception:
        pass
    emit("Report rendered")

    # Memory append
    append_investigation(
        mem_store,
        InvestigationRecord(
            ticket_id=str(ticket_id),
            symptom_tag=handoff.fork_packet.symptom_tag,
            fork_letter=handoff.fork_packet.fork_letter.value,
            confidence=handoff.fork_packet.confidence.value,
            one_line_fingerprint=handoff.intake.one_line_fingerprint,
            summary=handoff.fork_packet.reasoning[:300],
            related_zendesk=handoff.fork_packet.related_zendesk,
            rubric_version=handoff.rubric_version,
        ),
    )
    emit(f"Ticket #{ticket_id} complete — {folder.root}")
    return folder.root
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_investigate_module.py -v`
Expected: PASS.

- [ ] **Step 5: Refactor the CLI command to delegate, then commit**

Replace `cli.py::_run_investigate` (the whole async function) with a thin wrapper so the existing `investigate` alias still works, using a Rich console for `on_line`:

```python
async def _run_investigate(
    ticket_id, extra_files, pastes, force, fixture, no_agent, initial_hypothesis, verbose
):
    from rich.console import Console

    from noc_cli.investigate import InvestigationError, run_investigation

    import os

    console = Console()
    cfg = load_config()
    owner = os.environ.get("NOC_OWNER", getattr(cfg, "owner", ""))

    try:
        root = await run_investigation(
            ticket_id=ticket_id,
            config=cfg,
            tickets_root=Path(cfg.tickets_root),
            owner=owner,
            initial_hypothesis=initial_hypothesis,
            extra_files=extra_files,
            pastes=pastes,
            force=force,
            fixture=fixture,
            no_agent=no_agent,
            verbose=verbose,
            on_line=lambda line: console.print(f"[green]✓[/green] {line}"),
        )
    except InvestigationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"\n[bold green]Report:[/bold green] {root}")
```

(Remove the now-dead `_handoff_with_initial_hypothesis` helper if nothing else references it — grep first: `uv run grep -rn _handoff_with_initial_hypothesis noc_cli tests`.)

Run the investigate CLI tests to confirm no regression: `uv run pytest tests/test_cli_investigate.py -v`
Expected: PASS (they exercise the same pipeline via the alias).

```bash
git add noc_cli/investigate.py noc_cli/cli.py tests/test_investigate_module.py
git commit -m "refactor(investigate): extract run_investigation with on_line progress callback"
```

---

### Task 6: Command router (`noc_cli/tui/command.py`)

Implements spec §4.3 (parse `/`-command vs freeform). Pure, no Textual import.

**Files:**
- Create: `noc_cli/tui/command.py`
- Test: `tests/test_tui_command.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui_command.py`:

```python
from noc_cli.tui.command import KNOWN_COMMANDS, parse_input


def test_slash_command_with_args():
    p = parse_input("/investigate 45747")
    assert p.is_command is True
    assert p.name == "investigate"
    assert p.args == "45747"


def test_slash_command_no_args_lowercased():
    p = parse_input("/Refresh")
    assert p.is_command is True
    assert p.name == "refresh"
    assert p.args == ""


def test_freeform_text_is_not_a_command():
    p = parse_input("why is this call stuck?")
    assert p.is_command is False
    assert p.name == ""
    assert p.args == "why is this call stuck?"


def test_blank_input_is_freeform_empty():
    p = parse_input("   ")
    assert p.is_command is False
    assert p.args == ""


def test_known_commands_cover_the_spec_set():
    assert {"investigate", "scout", "doctor", "help", "refresh",
            "copy", "open", "quit", "file", "paste", "revise", "retry"} <= set(KNOWN_COMMANDS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_command.py -v`
Expected: FAIL — `ModuleNotFoundError: noc_cli.tui.command`.

- [ ] **Step 3: Write minimal implementation**

Create `noc_cli/tui/command.py`:

```python
"""Pure parser for the input-first command box (no Textual import)."""
from __future__ import annotations

from dataclasses import dataclass

# Command name -> one-line help, shown by /help and used to validate input.
KNOWN_COMMANDS: dict[str, str] = {
    "investigate": "investigate the selected (or given) ticket",
    "scout": "open Backlog Scout",
    "doctor": "run health checks",
    "help": "list commands",
    "refresh": "poll now",
    "copy": "copy current detail",
    "open": "open the ticket in the browser",
    "quit": "quit",
    "file": "(chat) attach a local file as evidence",
    "paste": "(chat) attach inline text as evidence (label=body)",
    "revise": "(chat) re-run the pipeline with new evidence",
    "retry": "(chat) re-send the last analyst turn",
}


@dataclass
class ParsedCommand:
    raw: str
    is_command: bool
    name: str
    args: str


def parse_input(text: str) -> ParsedCommand:
    """Classify a submitted box string. Leading '/' => command; else freeform."""
    stripped = text.strip()
    if stripped.startswith("/"):
        body = stripped[1:].lstrip()
        name, _, args = body.partition(" ")
        return ParsedCommand(raw=text, is_command=True, name=name.lower(), args=args.strip())
    return ParsedCommand(raw=text, is_command=False, name="", args=stripped)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tui_command.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/command.py tests/test_tui_command.py
git commit -m "feat(tui): pure command-input parser"
```

---

### Task 7: Mount `CommandInput`, rebuild bindings input-first, wire the router

Implements spec §4.3 (input-first model, dispatch table). The box always holds focus; bare letters are no longer bindings; submitting routes through `parse_input`.

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — imports, `_CSS` (`#command`), `BINDINGS`, `compose`, `on_mount` (focus the box), add `on_input_submitted`, `_dispatch_command`, `action_interrupt`, `action_scroll_detail_*`, `_show_help`, `_stub_chat`
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_command_box_is_focused_and_routes_slash_refresh(db_conn, tmp_path):
    from textual.widgets import Input

    first = [_ticket(601)]
    second = [_ticket(602)]
    app = _make_app(_make_config(tmp_path), _FakeClient([first, second]), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        assert app.focused is box
        box.value = "/refresh"
        await box.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        ids = [r.ticket_id for r in app.query_one("#ticket-list").rows]
    assert ids == [602]
    assert box.value == ""  # cleared after submit


async def test_slash_open_routes_to_browser(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(909)]]), WatchState(db_conn))
    with patch("webbrowser.open") as mock_open:
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            box = app.query_one("#command", Input)
            box.value = "/open"
            await box.action_submit()
            await pilot.pause()
    mock_open.assert_called_once_with("https://carbyne.zendesk.com/agent/tickets/909")


async def test_help_lists_commands_in_detail(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "/help"
        await box.action_submit()
        await pilot.pause()
    assert "/investigate" in app.current_detail_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watch_app.py::test_command_box_is_focused_and_routes_slash_refresh -v`
Expected: FAIL — no `#command` widget.

- [ ] **Step 3: Write minimal implementation**

Add imports to `watch_app.py`:

```python
from textual.widgets import Footer, Input, Static
from noc_cli.tui.command import KNOWN_COMMANDS, parse_input
```

Add to `_CSS`:

```css
#command {
    dock: bottom;
    height: 3;
    border: round $accent;
}
```

Replace `BINDINGS` with the input-first set (arrows priority so they win over the Input; no bare letters):

```python
    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("tab", "next_detail_file", "Next view", show=True, priority=True),
        Binding("shift+tab", "previous_detail_file", "Prev view", show=True, priority=True),
        Binding("escape", "interrupt", "Interrupt", show=True, priority=True),
        Binding("pageup", "scroll_detail_up", "Scroll up", show=False, priority=True),
        Binding("pagedown", "scroll_detail_down", "Scroll down", show=False, priority=True),
        Binding("ctrl+c", "quit", "Quit", show=True, priority=True),
    ]
```

In `compose`, add the input box right before the `Footer()`:

```python
        yield Input(placeholder="ask about the selected ticket, or /investigate /scout /help…", id="command")
        yield Footer()
```

In `on_mount`, focus the box instead of the ticket list:

```python
    def on_mount(self) -> None:
        self._update_banner()
        self._refresh_detail()
        self.query_one("#command", Input).focus()
        if self._poll_interval < 9999:
            self.set_interval(self._poll_interval, self.action_poll_now)
            self.action_poll_now()
        self.set_interval(0.1, self._tick_spinner)
```

Add the submit handler and dispatcher:

```python
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command":
            return
        text = event.value
        event.input.value = ""
        parsed = parse_input(text)
        if not parsed.is_command and not parsed.args:
            return
        self._dispatch_command(parsed)

    def _dispatch_command(self, parsed) -> None:
        if not parsed.is_command:
            self._stub_chat(parsed.args)  # replaced in Phase 4
            return
        name = parsed.name
        if name == "refresh":
            self.action_poll_now()
        elif name == "open":
            self.action_open_ticket()
        elif name == "copy":
            self.action_copy_current()
        elif name == "quit":
            self.exit()  # App.exit() is the sync-safe quit (action_quit is a coroutine)
        elif name == "help":
            self._show_help()
        elif name == "investigate":
            self.action_investigate()
        elif name == "doctor":
            self._run_doctor()
        elif name == "scout":
            # PR #6 shipped the engine as the (now hidden/deprecated) CLI command;
            # an in-TUI Scout panel is a future task (interactive-feat.md §9).
            self._set_notification("Scout runs from the CLI for now: `noc-cli scout`")
        else:
            self._set_notification(f"Unknown command /{name}; try /help")
```

Add the helpers `action_interrupt` (placeholder until Phase 4 wires chat interrupt), the scroll actions, `_show_help`, `_run_doctor`, and the chat stub:

```python
    def action_interrupt(self) -> None:
        # Phase 4 wires this to chat interrupt; for now clear the box.
        try:
            self.query_one("#command", Input).value = ""
        except NoMatches:
            pass

    def action_scroll_detail_up(self) -> None:
        self.query_one("#detail", DetailPane).scroll_page_up()

    def action_scroll_detail_down(self) -> None:
        self.query_one("#detail", DetailPane).scroll_page_down()

    def _show_help(self) -> None:
        lines = ["Commands:"]
        for cmd, desc in KNOWN_COMMANDS.items():
            lines.append(f"  /{cmd:<12} {desc}")
        lines.append("")
        lines.append("Anything without a leading / is a chat turn about the selected ticket.")
        self._set_detail_text("\n".join(lines))

    def _run_doctor(self) -> None:
        from noc_cli.doctor import run_checks

        results = run_checks(self._config)
        lines = ["Doctor:"]
        for r in results:
            mark = "✓" if r.ok else "✗"
            lines.append(f"  {mark} {r.name}: {r.detail}")
        self._set_detail_text("\n".join(lines))

    def _stub_chat(self, text: str) -> None:
        self._set_notification("Chat lands next — press /investigate for now.")
```

Note: `_run_doctor` assumes `run_checks` returns objects with `.ok`, `.name`, `.detail`. Verify against `noc_cli/doctor.py` before writing — if the field names differ, adjust this helper (this is the only place that reads them). Run: `uv run grep -n "class\|\.ok\|name\|detail" noc_cli/doctor.py | head`.

Also delete the now-unused single-key handlers' bindings only (keep the `action_*` methods — they're still called by the dispatcher): `action_cursor_up/down`, `action_next/previous_detail_file`, `action_investigate`, `action_poll_now`, `action_copy_current`, `action_open_ticket` all stay. Remove `action_focus_detail` and `action_summary` bindings (the `enter`/`escape`-to-summary behaviors are replaced); keep `action_summary` as a method (used after investigate). 

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_watch_app.py::test_command_box_is_focused_and_routes_slash_refresh tests/test_watch_app.py::test_slash_open_routes_to_browser tests/test_watch_app.py::test_help_lists_commands_in_detail -v`
Expected: PASS.

- [ ] **Step 5: Update the now-obsolete key tests, then commit**

The old single-key tests (`test_r_key_refreshes_live_queue`, `test_o_opens_zendesk_ticket_url`, `test_y_copies_current_summary_or_activity`, `test_enter_focuses_detail_pane`) press bare keys that are no longer bindings. Rewrite each to drive the box instead (set `box.value` + `await box.action_submit()`), or delete the ones fully covered by the three new tests above (`r`/`o` are covered; keep a rewritten `copy` test):

```python
async def test_slash_copy_copies_current_detail(db_conn, tmp_path):
    from textual.widgets import Input
    from noc_cli.tui.watch_app import WatchApp

    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(1001, subject="No ANI")]]), WatchState(db_conn))
    with patch.object(WatchApp, "copy_to_clipboard", autospec=True) as mock_copy:
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            box = app.query_one("#command", Input)
            box.value = "/copy"
            await box.action_submit()
            await pilot.pause()
    assert mock_copy.call_count == 1
    assert "Ticket: ZD-1001" in mock_copy.call_args[0][1]
```

Delete `test_enter_focuses_detail_pane`, `test_r_key_refreshes_live_queue`, `test_o_opens_zendesk_ticket_url`, `test_y_copies_current_summary_or_activity`.

Also, `escape` is now **interrupt**, not "back to Summary". In `test_tab_cycles_to_file_and_escape_returns_to_summary`, drop the `escape`→Summary half (keep the `tab`→`# Intake for 707` assertion); rename it `test_tab_cycles_to_file`. `test_i_*` are replaced in Task 8.

Run the whole file: `uv run pytest tests/test_watch_app.py -v` — Expected: PASS (the `tab`/`shift+tab` view-cycle tests still pass — those bindings remain; only the bare-letter and `escape`/`enter` ones changed).

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): input-first command box with slash-command router"
```

---

### Task 8: In-process investigate worker (replace the subprocess)

Implements spec §4.4. Swap `_run_investigate` (subprocess) for a thread worker that runs `run_investigation(... on_line=...)`, feeding the existing `_investigate_on_line`.

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — `_run_investigate` worker, `_investigate_on_line` (drop ANSI/CR scrubbing), `on_unmount` (drop proc handling), remove `_investigate_proc`/`_strip_ansi`/`subprocess`/`sys` if now unused
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing test**

Replace `test_i_launches_investigate_for_selected_ticket` and `test_i_is_single_flight_while_running` with router-driven, in-process versions:

```python
async def test_investigate_runs_in_process_and_streams_phases(db_conn, tmp_path):
    from textual.widgets import Input

    captured = {}

    async def fake_run_investigation(*, ticket_id, on_line=None, **kw):
        captured["ticket_id"] = ticket_id
        on_line("Scaffold ready: /x")
        on_line("Evidence gathered")
        return tmp_path / str(ticket_id)

    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(808)]]), WatchState(db_conn))
    with patch("noc_cli.investigate.run_investigation", side_effect=fake_run_investigation):
        async with app.run_test(size=(120, 40)) as pilot:
            await _poll(app, pilot)
            box = app.query_one("#command", Input)
            box.value = "/investigate"
            await box.action_submit()
            for _ in range(30):
                await pilot.pause()
                if app._investigating_id is None:
                    break
    assert captured["ticket_id"] == 808
    assert app._investigating_id is None


async def test_investigate_is_single_flight(db_conn, tmp_path):
    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(818)]]), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._investigating_id = 818  # simulate in-flight
        app.action_investigate()
        await pilot.pause()
        assert "already running" in app.query_one("#notification").content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watch_app.py::test_investigate_runs_in_process_and_streams_phases -v`
Expected: FAIL — the worker still calls `subprocess.Popen`, so `run_investigation` is never invoked.

- [ ] **Step 3: Write minimal implementation**

Replace the `_run_investigate` worker:

```python
    @work(thread=True, exclusive=False)
    def _run_investigate(self, ticket_id: int) -> None:
        """Run the investigate pipeline in-process (thread worker). Streams
        progress lines into the detail pane via _investigate_on_line."""
        import asyncio

        from noc_cli.investigate import InvestigationError, run_investigation

        owner = os.environ.get("NOC_OWNER", getattr(self._config, "owner", ""))

        def emit(line: str) -> None:
            self.app.call_from_thread(self._investigate_on_line, ticket_id, line)

        try:
            asyncio.run(
                run_investigation(
                    ticket_id=ticket_id,
                    config=self._config,
                    tickets_root=self._tickets_root(),
                    owner=owner,
                    on_line=emit,
                )
            )
        except InvestigationError:
            self.app.call_from_thread(self._investigate_finished, ticket_id, 1)
            return
        except Exception as exc:  # pragma: no cover
            self.app.call_from_thread(self._investigate_failed, ticket_id, str(exc))
            return
        self.app.call_from_thread(self._investigate_finished, ticket_id, 0)
```

Simplify `_investigate_on_line` (the line is already clean — no subprocess CR/ANSI):

```python
    def _investigate_on_line(self, ticket_id: int, line: str) -> None:
        if self._investigating_id != ticket_id:
            return
        self._investigate_lines.append(line)
        label = detect_phase(line)
        if label is not None and label in self._investigate_phases:
            self._investigate_phases[label] = True
        if self._selected_is_investigating():
            self._refresh_detail()
```

Remove `on_unmount`'s subprocess termination (no child proc now) — replace its body with `pass` or delete the method. Remove the unused `import subprocess`, `import sys`, `_strip_ansi`, `_ANSI_RE`, and the `self._investigate_proc` attribute (grep to confirm no remaining refs: `uv run grep -n "_investigate_proc\|_strip_ansi\|subprocess\|^import sys" noc_cli/tui/watch_app.py`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_watch_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): run investigate in-process, drop the subprocess spawn"
```

---

### Task 9: Add the Chat view to the detail-pane view list

Implements spec §4.6 (Chat joins the cyclable views). For this task Chat renders an empty-state line; Phase 4 fills it.

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — `_DETAIL_MODES`, `_TAB_TAGLINES`, `_refresh_detail` (Chat branch), `action_next/previous_detail_file` (reachable on any row, not just triaged)
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_tab_reaches_chat_view(db_conn, tmp_path):
    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(700, subject="No ALI")]]), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        await pilot.press("tab")  # Summary -> Chat
        await pilot.pause()
        label = _text(app.query_one("#detail-tablabel"))
        detail = app.current_detail_text
    assert "Chat" in label
    assert "about this ticket" in detail.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watch_app.py::test_tab_reaches_chat_view -v`
Expected: FAIL — `tab` on a non-triaged row is a no-op today (guarded on `row.summary`), and there is no Chat view.

- [ ] **Step 3: Write minimal implementation**

Change `_DETAIL_MODES` to insert Chat after Summary:

```python
_DETAIL_MODES = [
    "Summary",
    "Chat",
    "INTAKE.md",
    "EVIDENCE_PREFLIGHT.md",
    "FORK_PACKET.md",
    "DRAFTS.md",
    "STATE.md",
]
```

Add a Chat tagline to `_TAB_TAGLINES`:

```python
    "Chat": "about this ticket",
```

In `_refresh_detail`, add a Chat branch after the Summary (index 0) branch and before the file-tab branch. Chat is index 1:

```python
        if _DETAIL_MODES[self._detail_index] == "Chat":
            self._set_detail_text(self._render_chat_panel())  # Phase 4 fills this
            self._update_tablabel()
            return
```

Add the placeholder renderer (Phase 4 replaces it):

```python
    def _render_chat_panel(self) -> str:
        row = self.selected_row
        if row is None:
            return "No ticket selected."
        return f"Chat · about ZD-{row.ticket_id}\n\nType a message in the box to start."
```

Make `action_next_detail_file`/`action_previous_detail_file` work on any selected row (remove the `row.summary is None` guard so Summary↔Chat cycling works pre-triage); the file-tab read path in `_refresh_detail` already guards on `row.summary` and falls back to Summary, so non-triaged rows that land on a file tab will reset:

```python
    def action_next_detail_file(self) -> None:
        if self.selected_row is None:
            return
        self._detail_index = (self._detail_index + 1) % len(_DETAIL_MODES)
        self._refresh_detail()

    def action_previous_detail_file(self) -> None:
        if self.selected_row is None:
            return
        self._detail_index = (self._detail_index - 1) % len(_DETAIL_MODES)
        self._refresh_detail()
```

Update `_update_tablabel`: the `_detail_index == 0` branch stays for Summary; add a Chat case before the file-tab default:

```python
        if _DETAIL_MODES[self._detail_index] == "Chat":
            label.update("Chat — about this ticket")
            return
```

Adjust the file-tab read in `_refresh_detail`: the block that does `self._read_detail_file(row.summary, _DETAIL_MODES[self._detail_index])` must skip when the current mode is "Chat" (handled above) — already covered since Chat returns early. For non-triaged rows landing on a file tab, the existing `if row.summary is not None:` guard falls through to `self._detail_index = 0; self._refresh_detail()`, which is correct.

- [ ] **Step 4: Run test to verify it passes**

Inserting "Chat" at index 1 shifts every file tab by one, so existing tests that press `tab` once to reach a file now land on Chat. Update all three before running:
- `test_tab_cycles_to_file` (renamed in Task 7): press `tab` **twice** to reach `# Intake for 707`.
- `test_tablabel_describes_current_file_tab`: press `tab` **twice** to reach the `INTAKE.md` label (one press now shows the `Chat` label — optionally assert that too).
- `test_shift_tab_cycles_back_from_file_to_summary`: press `tab` **twice** to reach INTAKE.md, then `shift+tab` twice back to Summary.

Run: `uv run pytest tests/test_watch_app.py::test_tab_reaches_chat_view -v && uv run pytest tests/test_watch_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): add Chat to the detail-pane view cycle"
```

---

## Phase 4 — Chat subsystem

### Task 10: `ChatSession` — per-ticket client, persistence, redaction

Implements spec §4.5 (session model, persistence, redaction). Mirrors `runner.py`'s injection pattern: a `client_factory` is injected in tests; production builds a `ClaudeSDKClient`.

**Files:**
- Create: `noc_cli/tui/chat.py`
- Test: `tests/test_tui_chat.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui_chat.py`:

```python
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeClient:
    """Stands in for ClaudeSDKClient: async-context, query, receive_response."""

    def __init__(self, reply="Held in queue; ALI link timed out."):
        self._reply = reply
        self.interrupted = False

    async def connect(self):
        return self

    async def disconnect(self):
        return None

    async def query(self, prompt):
        self._last = prompt

    async def receive_response(self):
        class _Msg:
            def __init__(self, result):
                self.result = result
        yield _Msg(self._reply)

    async def interrupt(self):
        self.interrupted = True


async def test_send_persists_turns_and_returns_reply(tmp_path):
    from noc_cli.tui.chat import ChatSession

    folder = tmp_path / "45747"
    folder.mkdir()
    fake = _FakeClient()
    session = ChatSession(
        ticket_id=45747,
        folder=folder,
        client_factory=lambda: fake,
    )

    out = []
    async for line in session.send("call me at 555-123-4567 — why stuck?"):
        out.append(line)

    assert any("Held in queue" in line for line in out)
    convo = (folder / "CONVERSATION.jsonl").read_text().splitlines()
    assert len(convo) == 2  # one analyst turn + one agent turn
    analyst = json.loads(convo[0])
    assert analyst["role"] == "you"
    assert "<PHONE>" in analyst["text"]  # PII redacted at the boundary
    assert "555-123-4567" not in analyst["text"]
    agent = json.loads(convo[1])
    assert agent["role"] == "agent"
    assert "CONVERSATION.md" in [p.name for p in folder.iterdir()]


async def test_interrupt_delegates_to_client(tmp_path):
    from noc_cli.tui.chat import ChatSession

    folder = tmp_path / "1"
    folder.mkdir()
    fake = _FakeClient()
    session = ChatSession(ticket_id=1, folder=folder, client_factory=lambda: fake)
    async for _ in session.send("hi"):
        pass
    await session.interrupt()
    assert fake.interrupted is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_chat.py -v`
Expected: FAIL — `ModuleNotFoundError: noc_cli.tui.chat`.

- [ ] **Step 3: Write minimal implementation**

Create `noc_cli/tui/chat.py`:

```python
"""Per-ticket chat session over ClaudeSDKClient (spec §4.5).

Mirrors agent/runner.py: the SDK client is built by an injectable factory so
tests pass a fake; production lazily builds a real ClaudeSDKClient.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from noc_cli.redact import redact


@dataclass
class ChatTurn:
    role: str  # "you" | "agent"
    text: str
    ts: str


class ChatSession:
    """A resumable conversation about one ticket, persisted to the ticket folder."""

    def __init__(
        self,
        *,
        ticket_id: int,
        folder: Path,
        client_factory: Callable,
        redact_fn: Callable[[str], tuple[str, object]] = redact,
    ) -> None:
        self._ticket_id = ticket_id
        self._folder = Path(folder)
        self._client_factory = client_factory
        self._redact = redact_fn
        self._client = None
        self._turns: list[ChatTurn] = []

    @property
    def transcript(self) -> list[ChatTurn]:
        return list(self._turns)

    async def _ensure_client(self):
        if self._client is None:
            client = self._client_factory()
            await client.connect()
            self._client = client
        return self._client

    async def send(self, text: str) -> AsyncIterator[str]:
        """Send one analyst turn; yield agent output lines. Persists both turns."""
        redacted, _counts = self._redact(text)
        self._append(ChatTurn(role="you", text=redacted, ts=_now()))
        yield f"you ❯ {redacted}"

        client = await self._ensure_client()
        await client.query(redacted)
        reply = ""
        async for message in client.receive_response():
            result = getattr(message, "result", None)
            if result is not None:
                reply = str(result)
        for line in (reply or "(no response)").splitlines() or ["(no response)"]:
            yield f"◆ {line}"
        self._append(ChatTurn(role="agent", text=reply, ts=_now()))

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    def _append(self, turn: ChatTurn) -> None:
        self._turns.append(turn)
        with (self._folder / "CONVERSATION.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn)) + "\n")
        self._render_md()

    def _render_md(self) -> None:
        lines = [f"# Conversation — ZD-{self._ticket_id}", ""]
        for turn in self._turns:
            who = "You" if turn.role == "you" else "Agent"
            lines += [f"**{who}** · {turn.ts}", "", turn.text, ""]
        (self._folder / "CONVERSATION.md").write_text("\n".join(lines), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_sdk_client_factory(folder: Path) -> Callable:
    """Production factory: a ClaudeSDKClient bound to the ticket sandbox with the
    read-only hooks. Verify the ClaudeSDKClient(options=...) kwarg against the
    installed claude-agent-sdk 0.2.88 before first live use."""

    def _factory():
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # noqa: PLC0415

        from noc_cli.agent.harness import build_hooks  # noqa: PLC0415

        hooks = build_hooks(sandbox_root=folder, events_path=folder / "events.jsonl")
        options = ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "LS"],
            permission_mode="bypassPermissions",
            cwd=str(folder),
            hooks=hooks,
        )
        return ClaudeSDKClient(options=options)

    return _factory
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tui_chat.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/chat.py tests/test_tui_chat.py
git commit -m "feat(tui): per-ticket ChatSession with persistence and redaction"
```

---

### Task 11: Wire freeform input → chat turn → Chat view (scaffold-on-first-chat)

Implements spec §4.5/§4.6. Freeform box text starts/continues a chat about the selected ticket, scaffolding the folder on first use, switching the right pane to Chat, and streaming the reply.

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — `__init__` (chat state), `_dispatch_command` (freeform → `_submit_chat_turn`), add `_submit_chat_turn`, `_chat_worker`, `_chat_on_line`, replace `_render_chat_panel`
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_freeform_input_starts_chat_and_renders(db_conn, tmp_path):
    from textual.widgets import Input

    class _FakeChatClient:
        async def connect(self): return self
        async def disconnect(self): return None
        async def query(self, prompt): self.p = prompt
        async def receive_response(self):
            class M:
                result = "Held in queue; ALI link timed out."
            yield M()
        async def interrupt(self): pass

    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(45747, subject="stuck")]]), WatchState(db_conn))
    app._chat_client_factory = lambda folder: (lambda: _FakeChatClient())  # inject
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "why is this stuck?"
        await box.action_submit()
        for _ in range(30):
            await pilot.pause()
            if "Held in queue" in app.current_detail_text:
                break
    assert "you ❯ why is this stuck?" in app.current_detail_text
    assert "Held in queue" in app.current_detail_text
    assert (tmp_path / "45747" / "CONVERSATION.jsonl").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watch_app.py::test_freeform_input_starts_chat_and_renders -v`
Expected: FAIL — freeform input hits `_stub_chat`, no chat session.

- [ ] **Step 3: Write minimal implementation**

Add to `__init__`:

```python
        self._chat_sessions: dict[int, "ChatSession"] = {}
        self._chat_lines: dict[int, list[str]] = {}
        self._chatting_id: int | None = None
        # Injectable: ticket folder -> client_factory. Overridden in tests.
        from noc_cli.tui.chat import build_sdk_client_factory
        self._chat_client_factory = build_sdk_client_factory
```

Add the import at the top of the file:

```python
from noc_cli.tui.chat import ChatSession
```

Replace `_dispatch_command`'s freeform branch:

```python
        if not parsed.is_command:
            self._submit_chat_turn(parsed.args)
            return
```

Add the chat plumbing:

```python
    def _submit_chat_turn(self, text: str) -> None:
        row = self.selected_row
        if row is None:
            self._set_notification("Select a ticket to chat about.")
            return
        ticket_id = row.ticket_id
        # Switch the right pane to the Chat view.
        self._detail_index = _DETAIL_MODES.index("Chat")
        self._chat_lines.setdefault(ticket_id, [])
        self._run_chat(ticket_id, text)

    def _ensure_chat_session(self, ticket_id: int) -> "ChatSession":
        session = self._chat_sessions.get(ticket_id)
        if session is None:
            from noc_cli.scaffold import scaffold_ticket

            folder = scaffold_ticket(self._tickets_root(), ticket_id)
            session = ChatSession(
                ticket_id=ticket_id,
                folder=folder.root,
                client_factory=self._chat_client_factory(folder.root),
            )
            self._chat_sessions[ticket_id] = session
        return session

    @work(thread=False, exclusive=False)
    async def _run_chat(self, ticket_id: int, text: str) -> None:
        self._chatting_id = ticket_id
        self._refresh_detail()
        session = self._ensure_chat_session(ticket_id)
        try:
            async for line in session.send(text):
                self._chat_on_line(ticket_id, line)
        except Exception as exc:  # pragma: no cover
            self._chat_on_line(ticket_id, f"✗ chat error: {exc}")
        finally:
            self._chatting_id = None
            self._refresh_detail()

    def _chat_on_line(self, ticket_id: int, line: str) -> None:
        self._chat_lines.setdefault(ticket_id, []).append(line)
        row = self.selected_row
        if row is not None and row.ticket_id == ticket_id and _DETAIL_MODES[self._detail_index] == "Chat":
            self._refresh_detail()
```

Replace `_render_chat_panel`:

```python
    def _render_chat_panel(self) -> str:
        row = self.selected_row
        if row is None:
            return "No ticket selected."
        lines = [f"Chat · about ZD-{row.ticket_id}", ""]
        body = self._chat_lines.get(row.ticket_id, [])
        if not body:
            return "\n".join(lines + ["Type a message in the box to start."])
        lines.extend(body)
        if self._chatting_id == row.ticket_id:
            frame = _BRAILLE[self._spinner_frame]
            lines.append(f"{frame} …")
        return "\n".join(lines)
```

Add a spinner tick for chat in `_tick_spinner` (so the "…" animates) — extend the `busy` condition:

```python
        busy = self._polling or self._investigating_id is not None or self._chatting_id is not None
```

and refresh the detail when chatting:

```python
            if self._selected_is_investigating() or (
                self._chatting_id is not None
                and self.selected_row is not None
                and self.selected_row.ticket_id == self._chatting_id
            ):
                self._refresh_detail()
```

The test injects `app._chat_client_factory = lambda folder: (lambda: _FakeChatClient())`, matching the `build_sdk_client_factory(folder) -> factory` shape.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_watch_app.py::test_freeform_input_starts_chat_and_renders -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): freeform input drives a per-ticket chat session"
```

---

### Task 12: `esc` interrupts the running chat turn

Implements spec §4.5 (interrupt). Wire `action_interrupt` to the active session's `interrupt()`.

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — `action_interrupt`
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_escape_interrupts_active_chat(db_conn, tmp_path):
    from textual.widgets import Input

    interrupted = {"flag": False}

    class _SlowClient:
        async def connect(self): return self
        async def disconnect(self): return None
        async def query(self, prompt): pass
        async def receive_response(self):
            class M:
                result = "partial"
            yield M()
        async def interrupt(self): interrupted["flag"] = True

    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(500)]]), WatchState(db_conn))
    app._chat_client_factory = lambda folder: (lambda: _SlowClient())
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "explain"
        await box.action_submit()
        await pilot.pause()
        # Force a live session, then interrupt.
        session = app._ensure_chat_session(500)
        await session._ensure_client()
        await pilot.press("escape")
        for _ in range(20):
            await pilot.pause()
            if interrupted["flag"]:
                break
    assert interrupted["flag"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watch_app.py::test_escape_interrupts_active_chat -v`
Expected: FAIL — `action_interrupt` only clears the box.

- [ ] **Step 3: Write minimal implementation**

Replace `action_interrupt`:

```python
    def action_interrupt(self) -> None:
        target = self._chatting_id
        if target is None:
            target = self.selected_row.ticket_id if self.selected_row else None
        session = self._chat_sessions.get(target) if target is not None else None
        if session is not None:
            self._interrupt_chat(session)
            self._set_notification("Interrupting…")
            return
        # Idle: clear the box.
        try:
            self.query_one("#command", Input).value = ""
        except NoMatches:
            pass

    @work(thread=False, exclusive=False)
    async def _interrupt_chat(self, session: "ChatSession") -> None:
        try:
            await session.interrupt()
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_watch_app.py::test_escape_interrupts_active_chat -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): esc interrupts the active chat turn"
```

---

### Task 13: In-chat commands `/file`, `/paste`, `/revise`, `/retry`

Implements spec §4.5 (in-chat commands). `/file` and `/paste` attach evidence into the selected ticket's folder; `/revise` re-runs the pipeline; `/retry` re-sends the last analyst turn.

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — `_dispatch_command` (add `file`/`paste`/`revise`/`retry`), add `_attach_file`, `_attach_paste`, `_chat_retry`; reuse `action_investigate` for `/revise`
- Modify: `noc_cli/tui/chat.py` — `ChatSession.last_user_turn` property (for `/retry`)
- Test: `tests/test_watch_app.py`, `tests/test_tui_chat.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_watch_app.py`:

```python
async def test_slash_file_attaches_evidence(db_conn, tmp_path):
    from textual.widgets import Input

    src = tmp_path / "pcap-excerpt.txt"
    src.write_text("SIP 200 OK\n")
    app = _make_app(_make_config(tmp_path), _FakeClient([[_ticket(45747)]]), WatchState(db_conn))
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = f"/file {src}"
        await box.action_submit()
        await pilot.pause()
    assert (tmp_path / "45747" / "logs" / "pcap-excerpt.txt").exists()
    assert "attached" in app.query_one("#notification").content.lower()
```

In `tests/test_tui_chat.py`:

```python
async def test_last_user_turn_tracks_redacted_input(tmp_path):
    from noc_cli.tui.chat import ChatSession

    folder = tmp_path / "9"
    folder.mkdir()

    class _C:
        async def connect(self): return self
        async def disconnect(self): return None
        async def query(self, p): pass
        async def receive_response(self):
            class M: result = "ok"
            yield M()
        async def interrupt(self): pass

    s = ChatSession(ticket_id=9, folder=folder, client_factory=lambda: _C())
    async for _ in s.send("first question"):
        pass
    assert s.last_user_turn == "first question"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watch_app.py::test_slash_file_attaches_evidence tests/test_tui_chat.py::test_last_user_turn_tracks_redacted_input -v`
Expected: FAIL — `/file` unknown; `last_user_turn` missing.

- [ ] **Step 3: Write minimal implementation**

In `chat.py`, add the property:

```python
    @property
    def last_user_turn(self) -> str:
        for turn in reversed(self._turns):
            if turn.role == "you":
                return turn.text
        return ""
```

In `watch_app.py` `_dispatch_command`, add cases before the `else`:

```python
        elif name == "file":
            self._attach_file(parsed.args)
        elif name == "paste":
            self._attach_paste(parsed.args)
        elif name == "revise":
            self.action_investigate()  # re-runs the structured pipeline
        elif name == "retry":
            self._chat_retry()
```

Add the handlers:

```python
    def _attach_file(self, path_arg: str) -> None:
        row = self.selected_row
        if row is None or not path_arg.strip():
            self._set_notification("Usage: /file <path> (with a ticket selected)")
            return
        from noc_cli.evidence import gather_evidence
        from noc_cli.scaffold import scaffold_ticket

        src = Path(path_arg.strip()).expanduser()
        if not src.exists():
            self._set_notification(f"File not found: {src}")
            return
        folder = scaffold_ticket(self._tickets_root(), row.ticket_id)
        gather_evidence(
            folder=folder, zendesk_attachments=[], extra_files=[src], pastes=[]
        )
        self._set_notification(f"Attached {src.name} as evidence.")

    def _attach_paste(self, arg: str) -> None:
        row = self.selected_row
        if row is None or "=" not in arg:
            self._set_notification("Usage: /paste label=body (with a ticket selected)")
            return
        from noc_cli.evidence import PasteInput, gather_evidence
        from noc_cli.scaffold import scaffold_ticket

        label, _, body = arg.partition("=")
        folder = scaffold_ticket(self._tickets_root(), row.ticket_id)
        gather_evidence(
            folder=folder, zendesk_attachments=[], extra_files=[],
            pastes=[PasteInput(label=label.strip(), text=body)],
        )
        self._set_notification(f"Attached paste '{label.strip()}' as evidence.")

    def _chat_retry(self) -> None:
        row = self.selected_row
        session = self._chat_sessions.get(row.ticket_id) if row else None
        if session is None or not session.last_user_turn:
            self._set_notification("Nothing to retry.")
            return
        self._submit_chat_turn(session.last_user_turn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_watch_app.py::test_slash_file_attaches_evidence tests/test_tui_chat.py::test_last_user_turn_tracks_redacted_input -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/watch_app.py noc_cli/tui/chat.py tests/test_watch_app.py tests/test_tui_chat.py
git commit -m "feat(tui): in-chat /file /paste /revise /retry commands"
```

---

## Final verification

- [ ] **Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS. Pay attention to `tests/test_cli.py`, `tests/test_cli_investigate.py`, `tests/test_watch_app.py`, and the four new test files.

- [ ] **Manual smoke (optional, needs creds)**

Run: `uv run noc-cli` → splash → panes; type `/help` → command list; type a question → Chat view streams; `esc` interrupts; `/refresh`, `/open` work; `noc-cli watch` prints a deprecation warning then opens the TUI.

- [ ] **Update the spec status line**

In `docs/superpowers/specs/2026-06-05-interactive-tui-shell-design.md`, change `Status:` to `IMPLEMENTED`. Commit.

---

## Self-review notes (for the implementer)

- **Spec coverage:** §3 splash (Task 4) + version header (Task 3); §4.1 CLI surface (Tasks 1–2); §4.2 splash (Task 4); §4.3 input model + router (Tasks 6–7); §4.4 investigate-as-worker (Tasks 5, 8); §4.5 chat (Tasks 10–13); §4.6 view model (Task 9); §7 error/edge cases (config-missing guard in Task 1, empty-queue/no-selection in Tasks 11/13, unknown command in Task 7); §8 safety (read-only hooks reused via `build_sdk_client_factory`, Task 10); §9 testing (each task is TDD).
- **Two spec items intentionally light:** the cold-start "spinning up session…" line is approximated by the chat spinner (Task 11) — if you want the literal first-turn-only text, special-case it in `_render_chat_panel` when `self._chat_lines[id]` has exactly the one analyst line. `/doctor` rendering (Task 7) assumes `run_checks` result fields — verify against `noc_cli/doctor.py` and adjust `_run_doctor` only.
- **Type consistency:** `run_investigation(*, ticket_id, config, tickets_root, owner, on_line=...)` is used identically in `cli.py` (Task 5) and the TUI worker (Task 8). `ChatSession(ticket_id, folder, client_factory, redact_fn=redact)` and the `client_factory()` → object-with-`connect/query/receive_response/interrupt` contract are identical across `chat.py`, the TUI wiring (Task 11), and all chat tests. `parse_input` → `ParsedCommand(raw, is_command, name, args)` is consistent across Tasks 6–7, 13.
