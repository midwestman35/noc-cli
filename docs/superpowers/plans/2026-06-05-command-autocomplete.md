# Command Autocomplete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive slash-command palette to the TUI command box — typing `/` shows a filter-as-you-type menu of `KNOWN_COMMANDS` with descriptions; `↑`/`↓` highlight, `Tab` completes, `Enter` runs, `Esc` dismisses.

**Architecture:** A pure `match_commands` matcher in `noc_cli/tui/command.py` (no Textual) decides what to suggest and doubles as the show/hide rule. `WatchApp` renders a `#autocomplete` `Static` on the existing `overlay` layer, driven by `on_input_changed`, and gates the existing key handlers behind an `_ac_open` flag so menu-closed behavior is byte-for-byte unchanged.

**Tech Stack:** Python 3.10+, Textual ≥ 0.60 (`Input`, `Static`, CSS layers, `App.run_test`/`Pilot`), pytest + `pytest.mark.anyio` (asyncio backend). Ruff is enforced via a format-on-edit hook; keep imports sorted and lines formatted.

---

## Spec

Implements `docs/superpowers/specs/2026-06-05-command-autocomplete-design.md`. Read it once for context.

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `noc_cli/tui/command.py` | Modify | Add `CommandMatch` dataclass + `match_commands(text)` pure function beside `parse_input`. |
| `noc_cli/tui/watch_app.py` | Modify | Add 3 `_ac_*` state fields, the `#autocomplete` widget in `compose`, its CSS, `on_input_changed`, render/move/complete/close helpers, and one-line guards in 5 existing handlers. |
| `tests/test_tui_command.py` | Modify | Unit tests for `match_commands`. |
| `tests/test_watch_app.py` | Modify | App-level (`run_test`/`pilot`) tests for the panel + key routing. |

No new files: the matcher belongs with the existing parser, and the panel belongs with the app that owns the command box.

## Conventions for every task

- Run the full suite with `python -m pytest -q` from the repo root; run single tests with `python -m pytest tests/<file>::<test> -v`.
- `tests/test_watch_app.py` already provides `_make_config`, `_make_app`, `_FakeClient`, `_poll`, `_text`, the `db_conn` and `anyio_backend` fixtures, and `pytestmark = pytest.mark.anyio`. Reuse them — do not redefine.
- Stage only the files named in each task's commit step (`git add <paths>`). Never `git add -A` — there is unrelated tooling in the tree.
- Commit messages end with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Task 1: Pure `match_commands` matcher

**Files:**
- Modify: `noc_cli/tui/command.py` (append after `parse_input`, currently ends line 41; add `import re` near top)
- Test: `tests/test_tui_command.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_tui_command.py`, change the import line at the top from
`from noc_cli.tui.command import KNOWN_COMMANDS, parse_input` to:

```python
from noc_cli.tui.command import (
    KNOWN_COMMANDS,
    CommandMatch,
    match_commands,
    parse_input,
)
```

Append these tests:

```python
def test_match_commands_slash_lists_all_in_insertion_order():
    matches = match_commands("/")
    assert [m.name for m in matches] == list(KNOWN_COMMANDS)


def test_match_commands_prefix_filters_to_one():
    assert [m.name for m in match_commands("/in")] == ["investigate"]


def test_match_commands_is_case_insensitive():
    assert [m.name for m in match_commands("/IN")] == ["investigate"]


def test_match_commands_no_prefix_match_is_empty():
    assert match_commands("/zzz") == []


def test_match_commands_space_means_arguments_not_menu():
    assert match_commands("/file foo") == []


def test_match_commands_freeform_and_blank_are_empty():
    assert match_commands("why is this call stuck?") == []
    assert match_commands("") == []


def test_command_match_carries_name_and_description():
    [match] = match_commands("/doctor")
    assert match == CommandMatch("doctor", KNOWN_COMMANDS["doctor"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_tui_command.py -q`
Expected: FAIL — `ImportError: cannot import name 'CommandMatch'` (or `match_commands`).

- [ ] **Step 3: Implement the matcher**

In `noc_cli/tui/command.py`, add `import re` so the import block reads:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
```

Append after `parse_input` (end of file):

```python
@dataclass
class CommandMatch:
    name: str
    description: str


# A bare command fragment: a leading slash then non-space chars, nothing else.
# The first space (arguments) or any non-slash text (a chat turn) fails to match.
_FRAGMENT_RE = re.compile(r"^/(\S*)$")


def match_commands(text: str) -> list[CommandMatch]:
    """Slash-command suggestions for the box. Empty list => close the menu.

    Case-insensitive prefix match on the command name, preserving
    ``KNOWN_COMMANDS`` insertion order. Returns ``[]`` for anything that is not
    a bare ``/fragment`` (arguments started, freeform chat, or empty).
    """
    match = _FRAGMENT_RE.match(text.lstrip())
    if match is None:
        return []
    fragment = match.group(1).lower()
    return [
        CommandMatch(name, description)
        for name, description in KNOWN_COMMANDS.items()
        if name.startswith(fragment)
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_tui_command.py -q`
Expected: PASS (all, including the pre-existing `parse_input` tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/command.py tests/test_tui_command.py
git commit -m "feat(tui): match_commands matcher for command autocomplete

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Autocomplete panel that filters as you type

Adds the `#autocomplete` widget, its CSS, the three state fields, the `on_input_changed` driver, and the render/close helpers. No key routing yet — the menu only appears, filters, and hides.

**Files:**
- Modify: `noc_cli/tui/watch_app.py`
  - import line (currently line 22)
  - `_CSS` (insert before the closing `"""` at line 137)
  - `__init__` (after `self._splash_dismissed = False`, line 443)
  - `compose` (after `yield Footer()`, line 472)
  - add new methods `on_input_changed`, `_render_autocomplete`, `_ac_close`
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_app.py`:

```python
async def test_autocomplete_opens_on_slash(db_conn, tmp_path):
    from textual.widgets import Input, Static

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "/"
        await pilot.pause()
        panel = app.query_one("#autocomplete", Static)
        assert app._ac_open is True
        assert panel.display is True
        assert "/investigate" in _text(panel)


async def test_autocomplete_filters_by_prefix(db_conn, tmp_path):
    from textual.widgets import Input, Static

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "/in"
        await pilot.pause()
        assert [m.name for m in app._ac_matches] == ["investigate"]
        assert "/investigate" in _text(app.query_one("#autocomplete", Static))


async def test_autocomplete_hidden_on_space_and_no_match(db_conn, tmp_path):
    from textual.widgets import Input, Static

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        panel = app.query_one("#autocomplete", Static)
        box.value = "/file foo"
        await pilot.pause()
        assert app._ac_open is False
        assert panel.display is False
        box.value = "/zzz"
        await pilot.pause()
        assert app._ac_open is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_watch_app.py -k autocomplete -q`
Expected: FAIL — `AttributeError: 'WatchApp' object has no attribute '_ac_open'` (or `NoMatches` querying `#autocomplete`).

- [ ] **Step 3: Implement the panel, state, driver, and render**

In `noc_cli/tui/watch_app.py`:

(a) Extend the command import (line 22) to include the new names:

```python
from noc_cli.tui.command import (
    KNOWN_COMMANDS,
    CommandMatch,
    ParsedCommand,
    match_commands,
    parse_input,
)
```

(b) Add CSS for the panel. Insert this block immediately before the closing `"""` of `_CSS` (the `#command` rule ends at line 136; add after it, before line 137):

```css
#autocomplete {
    layer: overlay;
    dock: bottom;
    offset: 0 -4;
    width: 100%;
    height: auto;
    background: $surface;
    border: round $accent;
    padding: 0 1;
    display: none;
}
```

(`offset: 0 -4` lifts the bottom-docked panel above the 3-row `#command` box and the 1-row `Footer`, so it floats just above the box without reflowing the two panes.)

(c) Add the three state fields in `__init__`, immediately after `self._splash_dismissed = False` (line 443):

```python
        # Command-palette autocomplete: matches for the current `/fragment`,
        # the highlighted row, and whether the menu is currently shown.
        self._ac_matches: list[CommandMatch] = []
        self._ac_index: int = 0
        self._ac_open: bool = False
```

(d) In `compose`, add the panel on the overlay layer. Insert it after `yield Footer()` and before the splash line (between lines 472 and 473):

```python
        yield Static("", id="autocomplete", markup=False)
```

(e) Add the driver and helpers as new methods. Place them right after `on_mount` (after line 482):

```python
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "command":
            return
        # Recompute on every keystroke; reset the highlight to the best match.
        self._ac_matches = match_commands(event.value)
        self._ac_open = bool(self._ac_matches)
        self._ac_index = 0
        self._render_autocomplete()

    def _render_autocomplete(self) -> None:
        try:
            panel = self.query_one("#autocomplete", Static)
        except NoMatches:
            return
        if not self._ac_open or not self._ac_matches:
            panel.display = False
            return
        panel.display = True
        text = Text()
        for index, match in enumerate(self._ac_matches):
            selected = index == self._ac_index
            line = Text()
            line.append("▸ " if selected else "  ")
            line.append(f"/{match.name}".ljust(14))
            line.append(" ")
            line.append(match.description, style="dim")
            line.append("\n")
            if selected:
                line.stylize("on grey23")
            text.append_text(line)
        text.append("↑↓ select · Tab complete · Enter run · Esc dismiss", style="dim")
        panel.update(text)

    def _ac_close(self) -> None:
        self._ac_open = False
        self._ac_matches = []
        self._render_autocomplete()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_watch_app.py -k autocomplete -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS (no regressions).

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): autocomplete panel that filters as you type

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Arrow-key navigation of the palette

`↑`/`↓` move the highlight while the menu is open and leave the ticket cursor alone; with the menu closed they still navigate tickets.

**Files:**
- Modify: `noc_cli/tui/watch_app.py`
  - `action_cursor_up` (line 850), `action_cursor_down` (line 857)
  - add `_ac_move` helper
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_app.py`:

```python
async def test_arrows_move_highlight_not_ticket_cursor(db_conn, tmp_path):
    from textual.widgets import Input

    from noc_cli.tui.watch_app import TicketList

    app = _make_app(
        _make_config(tmp_path),
        _FakeClient([[_ticket(1), _ticket(2)]]),
        WatchState(db_conn),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        ticket_list = app.query_one("#ticket-list", TicketList)
        cursor_before = ticket_list.cursor_index
        box = app.query_one("#command", Input)
        box.value = "/"  # all commands => more than one match
        await pilot.pause()
        assert app._ac_index == 0
        await pilot.press("down")
        await pilot.pause()
        assert app._ac_index == 1
        await pilot.press("up")
        await pilot.pause()
        assert app._ac_index == 0
        assert ticket_list.cursor_index == cursor_before


async def test_arrows_navigate_tickets_when_menu_closed(db_conn, tmp_path):
    app = _make_app(
        _make_config(tmp_path),
        _FakeClient([[_ticket(1), _ticket(2)]]),
        WatchState(db_conn),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        assert app._ac_open is False
        before = app.selected_row.ticket_id
        await pilot.press("down")
        await pilot.pause()
        assert app.selected_row.ticket_id != before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_watch_app.py -k "highlight or menu_closed" -q`
Expected: FAIL — `test_arrows_move_highlight_not_ticket_cursor` fails because `down` moves the ticket cursor and `_ac_index` stays 0. (`test_arrows_navigate_tickets_when_menu_closed` already passes — that's the regression guard.)

- [ ] **Step 3: Implement the guard and helper**

In `noc_cli/tui/watch_app.py`, add the guard as the first lines of `action_cursor_up` and `action_cursor_down`:

```python
    def action_cursor_up(self) -> None:
        if self._ac_open:
            self._ac_move(-1)
            return
        moved = self.query_one("#ticket-list", TicketList).move_up()
        if moved:
            self._detail_index = 0
            self._acknowledge_selection()
            self._refresh_detail()

    def action_cursor_down(self) -> None:
        if self._ac_open:
            self._ac_move(1)
            return
        moved = self.query_one("#ticket-list", TicketList).move_down()
        if moved:
            self._detail_index = 0
            self._acknowledge_selection()
            self._refresh_detail()
```

Add the helper next to `_ac_close`:

```python
    def _ac_move(self, delta: int) -> None:
        if not self._ac_matches:
            return
        self._ac_index = max(
            0, min(self._ac_index + delta, len(self._ac_matches) - 1)
        )
        self._render_autocomplete()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_watch_app.py -k "highlight or menu_closed" -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS.

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): arrow-key navigation for the command palette

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `Tab` completes; `shift+tab` is swallowed

`Tab` completes the highlighted command into the box as `/{name} ` (closing the menu); `shift+tab` does nothing while the menu is open (no background detail-view cycling).

**Files:**
- Modify: `noc_cli/tui/watch_app.py`
  - `action_next_detail_file` (line 1079), `action_previous_detail_file` (line 1086)
  - add `_ac_complete` helper
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_app.py`:

```python
async def test_tab_completes_highlighted_command(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "/in"
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert box.value == "/investigate "
        assert app._ac_open is False


async def test_shift_tab_swallowed_while_menu_open(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        assert app._detail_index == 0
        box = app.query_one("#command", Input)
        box.value = "/"
        await pilot.pause()
        await pilot.press("shift+tab")
        await pilot.pause()
        # Detail view did NOT cycle, and the menu is still open.
        assert app._detail_index == 0
        assert app._ac_open is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_watch_app.py -k "tab" -q`
Expected: FAIL — `tab` cycles the detail view (so `box.value` is unchanged, not `/investigate `) and `shift+tab` cycles `_detail_index` to `len(_DETAIL_MODES) - 1`.

- [ ] **Step 3: Implement the guards and helper**

In `noc_cli/tui/watch_app.py`, add the guards as the first lines of the two handlers:

```python
    def action_next_detail_file(self) -> None:
        if self._ac_open:
            self._ac_complete()
            return
        row = self.selected_row
        if row is None:
            return
        self._detail_index = (self._detail_index + 1) % len(_DETAIL_MODES)
        self._refresh_detail()

    def action_previous_detail_file(self) -> None:
        if self._ac_open:
            return  # swallow shift+tab while the palette is open
        row = self.selected_row
        if row is None:
            return
        self._detail_index = (self._detail_index - 1) % len(_DETAIL_MODES)
        self._refresh_detail()
```

Add the helper next to `_ac_move`:

```python
    def _ac_complete(self) -> None:
        if not self._ac_matches:
            return
        index = max(0, min(self._ac_index, len(self._ac_matches) - 1))
        name = self._ac_matches[index].name
        box = self.query_one("#command", Input)
        box.value = f"/{name} "  # trailing space => match_commands returns []
        box.cursor_position = len(box.value)
        self._ac_close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_watch_app.py -k "tab" -q`
Expected: PASS (both new tests; the existing `tab`/`shift+tab` view-cycling tests still pass because they run with the menu closed).

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS.

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): Tab-completion in the command palette

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `Enter` runs the highlight; `Esc` dismisses

`Enter` while the menu is open runs the highlighted command through the existing dispatcher (ignoring the literal box text) and clears the box; `Esc` closes the menu without interrupting anything.

**Files:**
- Modify: `noc_cli/tui/watch_app.py`
  - `on_input_submitted` (line 1115)
  - `action_interrupt` (line 1203)
- Test: `tests/test_watch_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_app.py`:

```python
async def test_enter_runs_highlighted_command(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "/h"  # only /help matches "h"; help is the highlight
        await pilot.pause()
        assert app._ac_open is True
        await pilot.press("enter")
        await pilot.pause()
        # Enter ran the HIGHLIGHTED /help (not the literal "/h"): help text
        # rendered into the detail pane, and the box was cleared.
        assert "/investigate" in app.current_detail_text
        assert box.value == ""
        assert app._ac_open is False


async def test_escape_dismisses_menu_without_interrupting(db_conn, tmp_path):
    from textual.widgets import Input

    app = _make_app(
        _make_config(tmp_path), _FakeClient([[_ticket(1)]]), WatchState(db_conn)
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _poll(app, pilot)
        box = app.query_one("#command", Input)
        box.value = "/in"
        await pilot.pause()
        assert app._ac_open is True
        await pilot.press("escape")
        await pilot.pause()
        assert app._ac_open is False
        # Dismiss leaves the typed text in place (unlike interrupt, which
        # clears the box), and starts/interrupts no chat session.
        assert box.value == "/in"
        assert app._chat_sessions == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_watch_app.py -k "enter_runs or escape_dismisses" -q`
Expected: FAIL (both).
- `test_enter_runs_highlighted_command`: without the guard, `Enter` submits the literal `/h`, which the dispatcher rejects via its `else` branch as a "Unknown command /h" notification — so the help text never reaches the detail pane and `"/investigate" in app.current_detail_text` is False.
- `test_escape_dismisses_menu_without_interrupting`: without the guard, `escape` falls through to `action_interrupt`, whose idle branch *clears* the box, so `box.value == "/in"` fails (the box becomes `""`).

- [ ] **Step 3: Implement the two guards**

In `noc_cli/tui/watch_app.py`, add the menu branch to `on_input_submitted`:

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
        parsed = parse_input(text)
        if not parsed.is_command and not parsed.args:
            return
        self._dispatch_command(parsed)
```

Add the guard as the first lines of `action_interrupt`:

```python
    def action_interrupt(self) -> None:
        if self._ac_open:
            self._ac_close()
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_watch_app.py -k "enter_runs or escape_dismisses" -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS (full suite green, 0 skipped beyond the pre-existing baseline).

```bash
git add noc_cli/tui/watch_app.py tests/test_watch_app.py
git commit -m "feat(tui): Enter-runs and Esc-dismiss for the command palette

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Plan self-review

**Spec coverage:**

| Spec section | Task |
|--------------|------|
| Matcher (`match_commands` + `CommandMatch`, `^/(\S*)$`, case-insensitive prefix, insertion order) | Task 1 |
| Show/hide rules (open iff non-empty; recompute on keystroke; reset highlight) | Task 2 (`on_input_changed`) |
| Rendering & layout (overlay panel, `▸`+`on grey23`, hint line, no reflow) | Task 2 |
| State fields `_ac_open` / `_ac_index` / `_ac_matches` | Task 2 |
| Key routing — `↑`/`↓` | Task 3 |
| Key routing — `Tab` complete, `shift+tab` swallow | Task 4 |
| Key routing — `Enter` run, `Esc` dismiss | Task 5 |
| "Pick and go" `Enter` via existing dispatcher | Task 5 |
| Show-all (no context-gating), no scrolling | Tasks 1–2 (matcher returns all matches; render lists all) |
| Menu-closed behavior unchanged | Tasks 3–5 (guards return early only when `_ac_open`); regression test in Task 3 |

No gaps.

**Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step shows complete code; every run step shows the command and expected result. The `offset: 0 -4` value is concrete with a stated rationale (3-row box + 1-row footer).

**Type/name consistency:** `CommandMatch(name, description)`, `match_commands(text) -> list[CommandMatch]`, and the helpers `_ac_move`/`_ac_complete`/`_ac_close`/`_render_autocomplete`, fields `_ac_open`/`_ac_index`/`_ac_matches`, and widget id `#autocomplete` are spelled identically across Tasks 1–5 and match the spec. `on_input_changed`/`on_input_submitted` use `Input.Changed`/`Input.Submitted`, both already imported. Tab routes through `action_next_detail_file`, shift+tab through `action_previous_detail_file`, esc through `action_interrupt`, up/down through `action_cursor_up`/`action_cursor_down` — all verified against the current `BINDINGS`.
