# In-TUI Backlog Scout Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `/scout` in the watch TUI to run the existing Backlog Scout engine in a worker and render the ranked report as a detail-pane takeover, and add `/take <id>` to claim a candidate (Enter-confirm → assignee write → in-TUI investigation), replacing the "runs from the CLI" stub.

**Architecture:** Thin orchestration in `WatchApp` over the existing, tested engine seams in `noc_cli/scout/commands.py` — no new engine logic. The detail-pane takeover mirrors the live-investigation panel; `/take` reuses `preflight_current_ticket` / `resolve_owner_id` / `make_writer().assign_ticket` with a TUI confirm and the TUI's own in-process investigate. Engine functions are imported lazily inside methods and called as `commands.<fn>` so tests patch `noc_cli.scout.commands.*` exactly like `tests/test_cli_scout.py`.

**Tech Stack:** Python 3.10+, Textual ≥ 0.60 (`@work(thread=True)` workers, `App.run_test`/`Pilot`), pytest + `pytest.mark.anyio`. Backlog Scout engine (PR #6), command palette (PR #8).

---

## Spec

Implements `docs/superpowers/specs/2026-06-06-scout-tui-panel-design.md`. Read it once for context.

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `noc_cli/tui/command.py` | Modify | `KNOWN_COMMANDS`: add `take`; restore `scout` description (panel now exists). |
| `noc_cli/tui/watch_app.py` | Modify | Scout state, `/scout` worker + render + takeover gate, `/take` propose/confirm workers, Enter/Esc precedence, the post-take investigate handoff, and a `TicketList.select_ticket` helper. |
| `tests/test_watch_app.py` | Modify | TUI-level tests patching `noc_cli.scout.commands.*`. |

No new files: the panel is orchestration that belongs with the TUI it lives in; the engine it calls is unchanged.

## Conventions for every task

- **Work in the worktree** `/Users/envelazquez/Documents/noc-cli-scout` (branch `feat/scout-tui-panel`). Prefix shell commands with `cd /Users/envelazquez/Documents/noc-cli-scout && …`. Verify `git branch --show-current` is `feat/scout-tui-panel` before any commit — the main checkout is on a different branch.
- **One-time setup:** `uv sync --directory /Users/envelazquez/Documents/noc-cli-scout` (creates the worktree's `.venv`).
- **Running tests** (a shell proxy mangles `python -m pytest`): run pytest inside `python -c`, from the worktree, so imports resolve to the worktree source:
  `cd /Users/envelazquez/Documents/noc-cli-scout && .venv/bin/python -c "import sys,pytest; sys.exit(pytest.main(['tests/test_watch_app.py','-k','scout or take','-q','-p','no:cacheprovider']))"`
  Full suite: replace the args with `['-q','-p','no:cacheprovider']`.
- **Lazy imports:** import engine functions *inside* the methods that use them (`from noc_cli.scout import commands`), matching the existing `_run_investigate`/`_run_doctor` pattern. This also sidesteps the ruff format-on-edit hook stripping a not-yet-used top-level import.
- Stage only the files named in each task (`git add <paths>`); never `git add -A`.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- `tests/test_watch_app.py` already provides `_make_config`, `_make_app`, `_FakeClient`, `_poll`, `_text`, the `db_conn`/`anyio_backend` fixtures, and `pytestmark = pytest.mark.anyio`. Reuse them.

---

## Task 1: `/scout` runs the engine and renders the report (takeover)

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — `__init__` (after `self._splash_dismissed = False`); `_tick_spinner`; `_refresh_detail` (after `_update_detail_header()`); `_update_tablabel` (top); `_dispatch_command` (the `scout` branch); add `action_scout`, `_run_scout`, `_scout_finished`, `_scout_failed`, `_render_scout_panel`.
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_app.py`:

```python
async def test_scout_renders_ranked_report(db_conn, tmp_path):
    from unittest.mock import patch

    from textual.widgets import Input

    from noc_cli.scout.models import RankedCandidate, ScoutReport

    report = ScoutReport(
        ranked=[
            RankedCandidate(
                ticket_id=5012,
                rank=1,
                rationale="repeated egress drop",
                runbook_id="low-audio",
                runbook_match_confidence=0.8,
            )
        ],
        candidates_screened=1,
        reports_parsed=1,
    )
    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        with patch(
            "noc_cli.scout.commands.run_scout_report", return_value=report
        ) as run:
            box = app.query_one("#command", Input)
            box.value = "/scout"
            await box.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
    assert run.call_count == 1
    assert app._scout_active is True
    assert "#5012" in app.current_detail_text
    assert "low-audio" in app.current_detail_text


async def test_scout_single_flight(db_conn, tmp_path):
    from unittest.mock import patch

    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._scouting = True  # a run is already in flight
        with patch("noc_cli.scout.commands.run_scout_report") as run:
            box = app.query_one("#command", Input)
            box.value = "/scout"
            await box.action_submit()
            await pilot.pause()
        run.assert_not_called()


async def test_scout_engine_error_shows_in_panel(db_conn, tmp_path):
    from unittest.mock import patch

    from textual.widgets import Input

    from noc_cli.zendesk import ZendeskError

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        with patch(
            "noc_cli.scout.commands.run_scout_report",
            side_effect=ZendeskError("auth failed"),
        ):
            box = app.query_one("#command", Input)
            box.value = "/scout"
            await box.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
    assert "Scout failed" in app.current_detail_text
    assert "auth failed" in app.current_detail_text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/envelazquez/Documents/noc-cli-scout && .venv/bin/python -c "import sys,pytest; sys.exit(pytest.main(['tests/test_watch_app.py','-k','scout','-q','-p','no:cacheprovider']))"`
Expected: FAIL — `AttributeError: 'WatchApp' object has no attribute '_scout_active'` (and the `/scout` dispatch still shows the CLI-notice notification rather than rendering a report).

- [ ] **Step 3: Implement**

(a) State — in `__init__`, immediately after `self._splash_dismissed = False`:

```python
        # Backlog Scout panel: the report-run lifecycle, the latest report/error,
        # the candidate awaiting an Enter-confirm (+ its resolved owner id), and
        # the id to investigate once the post-take poll surfaces it. All defined
        # here so the render/confirm/handoff paths never reference a missing field.
        self._scout_active: bool = False
        self._scouting: bool = False
        self._scout_report = None  # ScoutReport | None
        self._scout_error: str | None = None
        self._pending_take: int | None = None
        self._pending_take_owner: int | None = None
        self._investigate_after_poll: int | None = None
```

(b) `_tick_spinner` — replace the whole method so scouting animates + repaints:

```python
    def _tick_spinner(self) -> None:
        busy = (
            self._polling
            or self._investigating_id is not None
            or self._chatting_id is not None
            or self._scouting
        )
        if busy:
            self._spinner_frame = (self._spinner_frame + 1) % len(_BRAILLE)
            if self._polling:
                self._update_banner()
            if (
                self._scouting
                or self._selected_is_investigating()
                or (
                    self._chatting_id is not None
                    and self.selected_row is not None
                    and self.selected_row.ticket_id == self._chatting_id
                )
            ):
                self._refresh_detail()
```

(c) `_refresh_detail` — add the scout takeover gate immediately after `self._update_detail_header()` (before the `_selected_is_investigating()` gate):

```python
        if self._scout_active:
            self._set_detail_text(self._render_scout_panel())
            self._update_tablabel()
            return
```

(d) `_update_tablabel` — add at the very top, before the `_selected_is_investigating()` check:

```python
        if self._scout_active:
            label.update("Backlog Scout — Tier-1 backlog")
            return
```

(e) `_dispatch_command` — replace the `scout` branch stub:

```python
        elif name == "scout":
            self.action_scout()
```

(f) New methods — add after `_render_investigate_panel` (near the other render/worker helpers):

```python
    def action_scout(self) -> None:
        if self._scouting:
            self._set_notification("Scout is already running.")
            return
        self._scout_active = True
        self._scouting = True
        self._scout_report = None
        self._scout_error = None
        self._refresh_detail()
        self._run_scout()

    @work(thread=True, exclusive=False)
    def _run_scout(self) -> None:
        """Run the read-only Scout pipeline in a thread worker (it is blocking
        and agent-backed). Results return via call_from_thread."""
        from noc_cli.scout import commands

        try:
            report = commands.run_scout_report(
                self._config, top_k=8, min_staleness_days=7
            )
        except Exception as exc:
            self.app.call_from_thread(self._scout_failed, str(exc))
            return
        self.app.call_from_thread(self._scout_finished, report)

    def _scout_finished(self, report) -> None:
        self._scouting = False
        self._scout_report = report
        self._scout_error = None
        self._refresh_detail()

    def _scout_failed(self, msg: str) -> None:
        self._scouting = False
        self._scout_error = msg
        self._refresh_detail()

    def _render_scout_panel(self) -> str:
        from noc_cli.scout.render import render_scout_report

        if self._scouting:
            frame = _BRAILLE[self._spinner_frame]
            return f"{frame} Scouting the Tier-1 backlog …"
        if self._scout_error is not None:
            return (
                f"✗ Scout failed ({self._scout_error}).\n\n"
                "Type /scout to retry, or check the terminal for details."
            )
        if self._scout_report is None:
            return "No scout report yet."
        lines = [render_scout_report(self._scout_report)]
        if self._pending_take is not None:
            lines.append("")
            lines.append(
                f"▶ Assign #{self._pending_take} to you and investigate? "
                "Enter to confirm · Esc to cancel"
            )
        else:
            if self._scout_report.dropped:
                lines.append("")
                lines.append(
                    f"Note: {self._scout_report.dropped} of "
                    f"{self._scout_report.candidates_screened} screens could not "
                    "be parsed and were dropped."
                )
            lines.append("")
            lines.append("/take <id> to claim + investigate · Esc to leave")
        return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the `-k scout` command from Step 2. Expected: PASS (3 tests). Then run the FULL suite; expected: no regressions.

- [ ] **Step 5: Commit**

```bash
cd /Users/envelazquez/Documents/noc-cli-scout
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(scout): /scout runs the engine and renders the report in the TUI

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `/take <id>` proposes (preflight + owner)

**Files:**
- Modify: `noc_cli/tui/command.py` — `KNOWN_COMMANDS`.
- Modify: `noc_cli/tui/watch_app.py` — `_dispatch_command` (add `take`); add `action_take`, `_run_take_proposal`, `_take_proposed`.
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_app.py`:

```python
async def test_take_eligible_proposes_confirmation(db_conn, tmp_path):
    from unittest.mock import patch

    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._scout_active = True  # in scout mode
        with patch(
            "noc_cli.scout.commands.preflight_current_ticket", return_value=None
        ), patch("noc_cli.scout.commands.resolve_owner_id", return_value=7):
            box = app.query_one("#command", Input)
            box.value = "/take 5012"
            await box.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
    assert app._pending_take == 5012
    assert app._pending_take_owner == 7
    assert "Assign #5012" in app.current_detail_text


async def test_take_ineligible_notifies_and_does_not_propose(db_conn, tmp_path):
    from unittest.mock import patch

    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        with patch(
            "noc_cli.scout.commands.preflight_current_ticket",
            return_value="already assigned",
        ), patch("noc_cli.scout.commands.resolve_owner_id") as owner:
            box = app.query_one("#command", Input)
            box.value = "/take 5012"
            await box.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
        owner.assert_not_called()
    assert app._pending_take is None
    assert "already assigned" in app.query_one("#notification").content


async def test_take_unresolved_owner_notifies(db_conn, tmp_path):
    from unittest.mock import patch

    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        with patch(
            "noc_cli.scout.commands.preflight_current_ticket", return_value=None
        ), patch("noc_cli.scout.commands.resolve_owner_id", return_value=None):
            box = app.query_one("#command", Input)
            box.value = "/take 5012"
            await box.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
    assert app._pending_take is None
    assert "Zendesk user id" in app.query_one("#notification").content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/envelazquez/Documents/noc-cli-scout && .venv/bin/python -c "import sys,pytest; sys.exit(pytest.main(['tests/test_watch_app.py','-k','take','-q','-p','no:cacheprovider']))"`
Expected: FAIL — `/take` is an unknown command (`_dispatch_command` hits the `else` "Unknown command" branch), so `_pending_take` stays `None` and nothing is proposed.

- [ ] **Step 3: Implement**

(a) `noc_cli/tui/command.py` — in `KNOWN_COMMANDS`, restore the `scout` description and add `take` at the end of the dict:

```python
    "scout": "open Backlog Scout",
```
and after the `"retry": …` entry (last line of the dict):
```python
    "take": "(scout) claim + investigate a candidate",
```

(b) `noc_cli/tui/watch_app.py` — in `_dispatch_command`, add a branch (e.g. after the `scout` branch):

```python
        elif name == "take":
            self.action_take(parsed.args)
```

(c) New methods — add after `action_scout` / the scout helpers:

```python
    def action_take(self, arg: str) -> None:
        raw = arg.strip()
        if not raw.isdigit():
            self._set_notification("Usage: /take <ticket id>")
            return
        if self._pending_take is not None:
            self._set_notification("A take is already awaiting confirmation.")
            return
        self._run_take_proposal(int(raw))

    @work(thread=True, exclusive=False)
    def _run_take_proposal(self, tid: int) -> None:
        """Preflight + resolve owner off the UI thread, then propose."""
        from noc_cli.scout import commands

        reason = commands.preflight_current_ticket(
            self._config, ticket_id=tid, min_staleness_days=7
        )
        if reason is not None:
            self.app.call_from_thread(
                self._set_notification, f"Can't take #{tid}: {reason}"
            )
            return
        owner_id = commands.resolve_owner_id(self._config)
        if owner_id is None:
            self.app.call_from_thread(
                self._set_notification,
                "Could not resolve your Zendesk user id — set NOC_OWNER / "
                "NOC_WATCH_ASSIGNEE.",
            )
            return
        self.app.call_from_thread(self._take_proposed, tid, owner_id)

    def _take_proposed(self, tid: int, owner_id: int) -> None:
        self._pending_take = tid
        self._pending_take_owner = owner_id
        self._refresh_detail()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the `-k take` command from Step 2. Expected: PASS (3 tests). Run the FULL suite; no regressions (the `command.py` description change touches no test — `test_tui_command.py` asserts only on names).

- [ ] **Step 5: Commit**

```bash
cd /Users/envelazquez/Documents/noc-cli-scout
git add noc_cli/tui/command.py noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(scout): /take <id> proposes a claim (preflight + owner)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Enter-confirm / Esc-cancel → assign the ticket

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — `on_input_submitted` (empty-box confirm branch); `action_interrupt` (precedence); add `_confirm_take`, `_take_aborted`, `_take_assigned`.
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_app.py`:

```python
async def test_enter_confirms_take_and_assigns(db_conn, tmp_path):
    from unittest.mock import MagicMock, patch

    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    writer = MagicMock()
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._scout_active = True
        app._pending_take = 5012
        app._pending_take_owner = 7
        with patch(
            "noc_cli.scout.commands.preflight_current_ticket", return_value=None
        ), patch("noc_cli.scout.commands.make_writer", return_value=writer):
            box = app.query_one("#command", Input)
            box.value = ""
            await box.action_submit()  # bare Enter confirms
            await app.workers.wait_for_complete()
            await pilot.pause()
    writer.assign_ticket.assert_called_once_with(5012, 7)
    assert app._pending_take is None


async def test_take_rechecks_before_assigning(db_conn, tmp_path):
    from unittest.mock import MagicMock, patch

    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    writer = MagicMock()
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._pending_take = 5012
        app._pending_take_owner = 7
        with patch(
            "noc_cli.scout.commands.preflight_current_ticket",
            return_value="already assigned",
        ), patch("noc_cli.scout.commands.make_writer", return_value=writer):
            box = app.query_one("#command", Input)
            box.value = ""
            await box.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
    writer.assign_ticket.assert_not_called()
    assert app._pending_take is None


async def test_escape_cancels_pending_take(db_conn, tmp_path):
    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._scout_active = True
        app._pending_take = 5012
        app._pending_take_owner = 7
        await pilot.press("escape")
        await pilot.pause()
    assert app._pending_take is None
    assert app._scout_active is True  # still in scout mode, just no pending take


async def test_take_surfaces_write_error(db_conn, tmp_path):
    from unittest.mock import MagicMock, patch

    from textual.widgets import Input

    from noc_cli.scout.writer import ZendeskWriteError

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    writer = MagicMock()
    writer.assign_ticket.side_effect = ZendeskWriteError("auth failed on assign")
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        app._pending_take = 5012
        app._pending_take_owner = 7
        with patch(
            "noc_cli.scout.commands.preflight_current_ticket", return_value=None
        ), patch("noc_cli.scout.commands.make_writer", return_value=writer):
            box = app.query_one("#command", Input)
            box.value = ""
            await box.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
    assert app._pending_take is None
    assert "auth failed on assign" in app.query_one("#notification").content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/envelazquez/Documents/noc-cli-scout && .venv/bin/python -c "import sys,pytest; sys.exit(pytest.main(['tests/test_watch_app.py','-k','confirm or rechecks or cancels or write_error','-q','-p','no:cacheprovider']))"`
Expected: FAIL — a bare Enter with `_pending_take` set does nothing (no `_confirm_take`), so `assign_ticket` is never called; `escape` falls through to the chat-interrupt/clear-box branch and leaves `_pending_take` set.

- [ ] **Step 3: Implement**

(a) `on_input_submitted` — replace the body so an empty submit with a pending take confirms it. The full method becomes:

```python
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command":
            return
        if self._ac_open and self._ac_matches:
            # Run the highlighted command, ignoring the partial box text.
            index = max(0, min(self._ac_index, len(self._ac_matches) - 1))
            name = self._ac_matches[index].name
            event.input.value = ""
            self._ac_close()
            self._dispatch_command(parse_input(f"/{name}"))
            return
        text = event.value
        event.input.value = ""
        if not text.strip():
            if self._pending_take is not None:
                self._confirm_take()
            return
        parsed = parse_input(text)
        if not parsed.is_command and not parsed.args:
            return
        self._dispatch_command(parsed)
```

(b) `action_interrupt` — add the pending-take cancel and scout-leave branches after the menu-close branch:

```python
    def action_interrupt(self) -> None:
        if self._ac_open:
            self._ac_close()
            return
        if self._pending_take is not None:
            self._pending_take = None
            self._pending_take_owner = None
            self._set_notification("Take cancelled.")
            self._refresh_detail()
            return
        if self._scout_active:
            self._scout_active = False
            self._refresh_detail()
            return
        target = self._chatting_id
        if target is None:
            target = self.selected_row.ticket_id if self.selected_row else None
        session = self._chat_sessions.get(target) if target is not None else None
        if session is not None:
            self._interrupt_chat(session)
            self._set_notification("Interrupting…")
            return
        # Idle (no chat session to interrupt): clear the box.
        try:
            self.query_one("#command", Input).value = ""
        except NoMatches:
            pass
```

(c) New methods — add near `_take_proposed`:

```python
    @work(thread=True, exclusive=False)
    def _confirm_take(self) -> None:
        """Re-preflight (TOCTOU) then write the assignee. Off the UI thread."""
        from noc_cli.scout import commands
        from noc_cli.scout.writer import ZendeskWriteError

        tid = self._pending_take
        owner_id = self._pending_take_owner
        if tid is None or owner_id is None:
            return
        reason = commands.preflight_current_ticket(
            self._config, ticket_id=tid, min_staleness_days=7
        )
        if reason is not None:
            self.app.call_from_thread(self._take_aborted, f"Can't take #{tid}: {reason}")
            return
        try:
            commands.make_writer(self._config).assign_ticket(tid, owner_id)
        except ZendeskWriteError as exc:
            self.app.call_from_thread(self._take_aborted, f"Zendesk error: {exc}")
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.app.call_from_thread(self._take_aborted, str(exc))
            return
        self.app.call_from_thread(self._take_assigned, tid)

    def _take_aborted(self, reason: str) -> None:
        self._pending_take = None
        self._pending_take_owner = None
        self._set_notification(reason)
        self._refresh_detail()

    def _take_assigned(self, tid: int) -> None:
        self._pending_take = None
        self._pending_take_owner = None
        self._scout_active = False
        self._set_notification(f"Assigned #{tid} to you.")
        self.action_poll_now()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the `-k` command from Step 2. Expected: PASS (4 tests). Run the FULL suite; no regressions (the `on_input_submitted` refactor preserves the menu and normal-dispatch paths; verify the existing `test_*command*` / autocomplete tests still pass).

- [ ] **Step 5: Commit**

```bash
cd /Users/envelazquez/Documents/noc-cli-scout
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(scout): Enter-confirm / Esc-cancel a take; assign the ticket

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Auto-investigate the claimed ticket after the take

**Files:**
- Modify: `noc_cli/tui/watch_app.py` — add `TicketList.select_ticket`; extract `_begin_investigate` from `action_investigate`; extend `_take_assigned`; hook `on_poll_complete`.
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_app.py`:

```python
async def test_select_ticket_moves_cursor(db_conn, tmp_path):
    from noc_cli.tui.watch_app import TicketList

    app = _make_app(
        _make_config(tmp_path),
        _FakeClient([[_ticket(1), _ticket(2), _ticket(3)]]),
        WatchState(db_conn),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        tlist = app.query_one("#ticket-list", TicketList)
        assert tlist.select_ticket(3) is True
        assert tlist.selected_ticket_id == 3
        assert tlist.select_ticket(999) is False


async def test_take_handoff_selects_and_investigates_after_poll(db_conn, tmp_path):
    from unittest.mock import patch

    from noc_cli.tui.watch_app import TicketList

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(5012)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        with patch.object(app, "_run_investigate") as run_inv:
            app._take_assigned(5012)  # clears scout, sets handoff, triggers poll
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert app._scout_active is False
        assert app._investigate_after_poll is None
        assert app._investigating_id == 5012
        run_inv.assert_called_once_with(5012)
        assert app.query_one("#ticket-list", TicketList).selected_ticket_id == 5012
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/envelazquez/Documents/noc-cli-scout && .venv/bin/python -c "import sys,pytest; sys.exit(pytest.main(['tests/test_watch_app.py','-k','select_ticket or handoff','-q','-p','no:cacheprovider']))"`
Expected: FAIL — `TicketList` has no `select_ticket`; `_take_assigned` does not set `_investigate_after_poll`, and `on_poll_complete` does not start an investigation, so `_investigating_id` stays `None`.

- [ ] **Step 3: Implement**

(a) `TicketList` — add the cursor-by-id helper (near `move_up`/`move_down`):

```python
    def select_ticket(self, ticket_id: int) -> bool:
        """Move the cursor to the row with this id. Returns True if found."""
        for index, row in enumerate(self._rows):
            if row.ticket_id == ticket_id:
                self.cursor_index = index
                self._render_rows()
                return True
        return False
```

(b) `action_investigate` — extract the setup into `_begin_investigate(tid)` so a specific id can be investigated (not just the selected row):

```python
    def action_investigate(self) -> None:
        row = self.selected_row
        if row is None:
            return
        self._begin_investigate(row.ticket_id)

    def _begin_investigate(self, tid: int) -> None:
        if self._investigating_id is not None:
            self._set_notification("An investigation is already running.")
            return
        self._investigating_id = tid
        self._investigate_lines = []
        self._investigate_phases = {label: False for _sub, label in INVESTIGATE_PHASES}
        self._investigate_error = None
        # Looking at it now clears any pending badge.
        self._unread_ids.discard(tid)
        self._refresh_detail()
        self._run_investigate(tid)
```

(c) `_take_assigned` — set the handoff and update the notice:

```python
    def _take_assigned(self, tid: int) -> None:
        self._pending_take = None
        self._pending_take_owner = None
        self._scout_active = False
        self._set_notification(f"Assigned #{tid} to you — investigating…")
        self._investigate_after_poll = tid
        self.action_poll_now()
```

(d) `on_poll_complete` — at the very end (after `self._schedule_pulse_clear()`), consume the handoff:

```python
        if self._investigate_after_poll is not None:
            tid = self._investigate_after_poll
            self._investigate_after_poll = None
            # Select the now-assigned ticket so the investigate takeover renders
            # live; if the watch view has not surfaced it yet, investigate by id
            # anyway (it still runs and lands under "Recently worked").
            self.query_one("#ticket-list", TicketList).select_ticket(tid)
            self._detail_index = 0
            self._begin_investigate(tid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the `-k select_ticket or handoff` command from Step 2. Expected: PASS (2 tests). Run the FULL suite; expected: no regressions (the `action_investigate` refactor preserves its behavior — the guard moved into `_begin_investigate`).

- [ ] **Step 5: Commit**

```bash
cd /Users/envelazquez/Documents/noc-cli-scout
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(scout): auto-investigate the claimed ticket after a take

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Plan self-review

**Spec coverage:**

| Spec section | Task |
|--------------|------|
| `/scout` worker + spinner + render + `dropped` note + engine-error panel | Task 1 |
| Detail-pane takeover gate + scout tab label + single-flight | Task 1 |
| `/take <id>` preflight + owner resolve + proposal | Task 2 |
| `/take` in `KNOWN_COMMANDS`; `scout` description restored | Task 2 |
| Enter-confirm (empty box + pending) | Task 3 |
| TOCTOU re-preflight before the write | Task 3 |
| Assignee-only write via `assign_ticket`; `ZendeskWriteError` surfaced | Task 3 |
| Esc precedence (cancel take → leave scout → chat/clear) | Task 3 |
| Take → investigate handoff (poll → select → investigate, with fallback) | Task 4 |
| Read-only safety invariant (only write = `assign_ticket`) | Tasks 1–4 (no other write introduced) |

No gaps. Out-of-scope items (auto-eval on poll, graduated autonomy, persisted `SCOUT.md`, metric loop, in-panel arrow-select, full-screen mode, tunable params) are not implemented by any task — correct.

**Placeholder scan:** No TBD/TODO/"handle errors". Every code step shows complete code; every run step gives the exact command + expected result.

**Type/name consistency:** State fields `_scout_active`/`_scouting`/`_scout_report`/`_scout_error`/`_pending_take`/`_pending_take_owner`/`_investigate_after_poll`, and methods `action_scout`/`_run_scout`/`_scout_finished`/`_scout_failed`/`_render_scout_panel`/`action_take`/`_run_take_proposal`/`_take_proposed`/`_confirm_take`/`_take_aborted`/`_take_assigned`/`_begin_investigate`/`TicketList.select_ticket` are spelled identically across tasks and match the spec. Engine calls use `commands.run_scout_report` / `preflight_current_ticket` / `resolve_owner_id` / `make_writer` (lazy `from noc_cli.scout import commands`) and `render_scout_report` (from `noc_cli.scout.render`), matching `commands.py`. `_render_scout_panel` (Task 1) reads `_pending_take`, which is defined in the Task 1 `__init__` block — no forward reference.
