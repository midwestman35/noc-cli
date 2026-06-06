# Command Autocomplete for the TUI Command Box — Design

**Date:** 2026-06-05
**Status:** Design approved; implementation plan to follow.
**Branch:** `feat/command-autocomplete` (based on `chore/adopt-ruff`)

## Goal

When the user types a leading `/` in the TUI command box, show an interactive
dropdown of matching slash-commands with their one-line descriptions — the
codex/claude-style command palette — so commands are discoverable and fast to
run without memorizing them.

## Background

The interactive TUI shell (shipped in v0.5.0) routes the always-focused
`#command` box two ways: a leading `/` is a command (`parse_input` → `ParsedCommand`
→ `_dispatch_command`), anything else is a freeform chat turn about the selected
ticket. The set of commands lives in `KNOWN_COMMANDS` (`noc_cli/tui/command.py`)
as `name → one-line description`. Today the only way to learn the commands is
`/help`, which dumps the list into the detail pane. This feature surfaces the
same information inline, as a live, navigable picker, the moment a `/` is typed.

## Scope

**In scope:** an interactive, filter-as-you-type menu over `KNOWN_COMMANDS`;
keyboard navigation (`↑`/`↓`), completion (`Tab`), run (`Enter`), and dismissal
(`Esc`); a floating panel anchored above the command box.

**Out of scope (future work):** fuzzy/substring matching (this is prefix-only),
argument autocomplete (e.g. file-path completion after `/file`), and an in-TUI
Scout panel.

## Architecture

Three pieces, each following an existing pattern in the codebase:

| Piece | File | Responsibility |
|-------|------|----------------|
| **Matcher** (pure) | `noc_cli/tui/command.py` | Given the box text, return which commands to suggest. No Textual import — lives beside `parse_input`. |
| **Panel + routing** | `noc_cli/tui/watch_app.py` | One `#autocomplete` `Static` on the existing `overlay` layer, three state fields, an `on_input_changed` driver, and one-line guards in the existing key handlers. |
| **Styling** | `_CSS` in `noc_cli/tui/watch_app.py` | Anchors the panel just above the `#command` box. |

The design keeps the hard part — *what matches* — as a pure function (mirroring
how `parse_input` already lives in `command.py`), so it tests without Textual.
The `Input` keeps focus the entire time; the menu is a passive display the app
repaints. This sidesteps the focus-split problem of a separately-focusable list
widget and means typing never breaks.

## The matcher (pure, fully unit-testable)

Added to `noc_cli/tui/command.py`:

```python
@dataclass
class CommandMatch:
    name: str
    description: str


_FRAGMENT_RE = re.compile(r"^/(\S*)$")  # slash + non-space chars, nothing else


def match_commands(text: str) -> list[CommandMatch]:
    """Suggestions for the command box. An empty list means the menu is closed.

    A suggestion is offered only while the text is a bare ``/fragment`` — a
    leading slash followed by zero or more non-space characters. The first space
    (the user has begun typing arguments) or any non-slash text (a freeform chat
    turn) yields an empty list.
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

The regex `^/(\S*)$` is both the filter and the hide rule: the moment a space
appears or the text is not a pure slash-fragment, it returns `[]`, which the app
reads as "close the menu." Matching is case-insensitive prefix matching on the
command name. Ordering is inherited from `KNOWN_COMMANDS` insertion order
(top-level commands first, the four `(chat)` commands last) — no re-sorting.

## Show / hide rules

The menu is **open if and only if `match_commands(value)` is non-empty.** It is
recomputed on every keystroke by `on_input_changed`, which also resets the
highlight to the top match (index `0`). It closes when the text becomes empty,
becomes non-slash (a chat turn), gains a space (arguments), or matches nothing —
**and** on `Tab` (complete), `Enter` (run), or `Esc` (dismiss).

## Key routing

Each intercept is a one-line guard at the top of an *existing* handler. When the
menu is closed, every key behaves exactly as it does today — the regression
surface is limited to "menu is open."

| Key | Menu **open** | Menu **closed** (unchanged) |
|-----|---------------|------------------------------|
| `↑` / `↓` | move the highlight (clamped); ticket cursor untouched | `action_cursor_up` / `action_cursor_down` → ticket nav |
| `Tab` | complete the highlight → box becomes `/{name} `, menu closes | `action_next_detail_file` → cycle detail view |
| `Enter` | **run** the highlighted command via the existing dispatcher; clear the box | `on_input_submitted` → submit chat/command |
| `Esc` | close the menu (no interrupt) | `action_interrupt` |
| `shift+tab` | swallowed (no background view-cycling while picking) | `action_previous_detail_file` |
| `pageup` / `pagedown` / `ctrl+c` | unaffected | unaffected |

## Completion & run semantics ("pick and go")

- **`Enter`** ignores the literal box text and runs the **highlighted** match.
  Typing `/in` then `Enter` runs `/investigate`. It routes through the existing
  `_dispatch_command(parse_input(f"/{name}"))`, so each command behaves
  identically to typing it out, and the usage-hint guards still apply — e.g.
  running `/file` with no path replies "Usage: /file <path>" rather than erroring.
- **`Tab`** completes the highlighted command into the box as `/{name} ` (with a
  trailing space) and leaves focus in the box so the user can type arguments. The
  trailing space immediately makes `match_commands` return `[]`, closing the menu.

This split means no-argument commands (`/help`, `/doctor`, `/refresh`, `/quit`,
`/copy`, `/open`) are a fast two-keystroke affair, while argument-taking commands
(`/file`, `/paste`) get `Tab` to fill the name and pause for input.

## Rendering & layout

`#autocomplete` is a `Static` on the existing `overlay` layer, bottom-docked with
an upward offset so it floats *just above* the command box (clearing the 3-row
command box and the 1-row footer) — so the two panes do **not** reflow when it
appears or disappears. It is hidden via `display: none` until there are matches.

Content is a Rich `Text`, reusing the ticket list's visual language: a `▸ ` marker
plus an `on grey23` highlight on the selected row. One row per match — `/{name}`
padded to a fixed column, then the dimmed description — followed by a dim hint line:

```
┌─ commands ───────────────────────────────────────────────┐
│▸ /investigate  investigate the selected (or given) ticket │
└───────────────────────────────────────────────────────────┘
╭───────────────────────────────────────────────────────────╮
│ /in▌                                                        │
╰───────────────────────────────────────────────────────────╯
 ↑↓ select · Tab complete · Enter run · Esc dismiss
```

(`investigate` is the only command starting with `in`, so `/in` shows one row.)

## State

Three fields on `WatchApp`:

- `_ac_open: bool` — is the menu currently shown.
- `_ac_index: int` — highlighted row, clamped to the match list.
- `_ac_matches: list[CommandMatch]` — the current suggestions.

## Behavior decisions

- **Show *all* matching commands, with no context-gating** — including the four
  `(chat)` commands (`/file`, `/paste`, `/revise`, `/retry`). This mirrors
  `/help`, their descriptions already carry the `(chat)` hint, and context-hiding
  would add invisible state and "why did it vanish?" confusion.
- **No scrolling.** `KNOWN_COMMANDS` is a bounded set (12 today); the full list
  always fits on screen.
- **The menu only ever concerns the `/`-command path.** Freeform chat text never
  triggers it (`match_commands` returns `[]` for any non-slash input).

## Edge cases & error handling

- `match_commands` performs only string operations and never raises.
- The menu can only be open when `_ac_matches` is non-empty; `Tab`/`Enter`
  handlers still clamp `_ac_index` defensively before indexing.
- Programmatically setting `Input.value` during completion posts an
  `Input.Changed` message; the completion helper also closes the menu
  synchronously so state is deterministic for tests that assert immediately.
- The splash cannot collide with the menu: at mount the box is empty, so
  `match_commands("")` is `[]`.
- Chat/investigate are unaffected: the menu reads only the box's slash-fragment
  state and never touches chat sessions or the read-only chat tool whitelist.

## Testing plan

**Pure unit tests (`tests/test_tui_command.py`):**

- `match_commands("/")` → all of `KNOWN_COMMANDS`, in insertion order.
- `match_commands("/in")` → `[investigate]` (the only `in*` command).
- `match_commands("/IN")` → case-insensitive, same as `/in`.
- `match_commands("/zzz")` → `[]` (no prefix match).
- `match_commands("/file foo")` → `[]` (space → arguments).
- `match_commands("hello")` and `match_commands("")` → `[]`.
- `CommandMatch.name` / `.description` equal the corresponding `KNOWN_COMMANDS`
  entry.

**App-level tests (`tests/test_watch_app.py`, via `run_test` / `pilot`):**

- Typing `/` opens the panel (`#autocomplete` visible, contains `/investigate`).
- Typing `/in` filters to `investigate` and highlights it.
- `↓` / `↑` move `_ac_index` **and leave the ticket cursor unchanged** (the key
  regression guard).
- `Tab` fills the box with `/investigate ` and closes the menu (`_ac_open` False).
- `Enter` on a highlighted `/help` renders the help text into the detail pane and
  clears the box (chosen because `/help` renders synchronously, no SDK).
- `Esc` closes the menu without interrupting (no chat session is started).
- With the menu closed (no leading `/`), `↑` / `↓` still navigate tickets.
- Typing a space, or a no-match fragment, hides the panel.

## Invariants preserved

- The read-only chat agent contract is untouched: this feature never grants or
  invokes any tool; it only renders a menu and routes keystrokes.
- Menu-closed behavior is byte-for-byte the existing behavior; the new code path
  is reachable only while `_ac_open` is true.
