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
        return ParsedCommand(
            raw=text, is_command=True, name=name.lower(), args=args.strip()
        )
    return ParsedCommand(raw=text, is_command=False, name="", args=stripped)
