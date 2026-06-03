from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Bash: patterns that are unconditionally destructive ────────────────────

_DESTRUCTIVE_BASH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\b"),                    # rm (any form)
    re.compile(r"\bmv\b"),                    # mv (may exfil)
    re.compile(r"\bcp\b"),                    # cp (may exfil out of sandbox)
    re.compile(r"\bchmod\b"),                 # permission change
    re.compile(r"\bchown\b"),
    re.compile(r"\bcurl\b"),                  # network write
    re.compile(r"\bwget\b"),
    re.compile(r"\bpip\s+install\b"),         # package mutation
    re.compile(r"\buv\s+add\b"),
    re.compile(r"\bnpm\s+install\b"),
    re.compile(r">(?!=)"),                    # stdout redirect (> or >>)
]

# Zendesk MCP tool names that are writes (exact prefix match is intentional)
_ZENDESK_WRITE_PREFIXES: tuple[str, ...] = (
    "mcp__zendesk__create_",
    "mcp__zendesk__update_",
    "mcp__zendesk__delete_",
    "mcp__zendesk__add_comment",
    "mcp__zendesk__close_",
    "mcp__zendesk__set_",
)

# Tools that write files — we check path containment for these
_WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})

# Tools that are unconditionally read-only and never blocked
_ALWAYS_ALLOWED_TOOLS = frozenset({"Read", "Glob", "Grep", "LS"})


def _deny(event: dict, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": event["hook_event_name"],
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _allow() -> dict:
    return {}


def _is_inside_sandbox(path_str: str, sandbox_root: Path) -> bool:
    """Return True iff path_str resolves to a path inside sandbox_root."""
    try:
        candidate = Path(path_str).resolve()
        resolved_sandbox = sandbox_root.resolve()
        candidate.relative_to(resolved_sandbox)
        return True
    except (ValueError, OSError):
        # relative_to() raises ValueError when outside; resolve() may raise
        # OSError on some paths. Fail closed (treat as outside the sandbox).
        return False


def make_pre_tool_use(sandbox_root: Path):
    """Return a PreToolUse hook callback enforcing the read-only sandbox."""

    async def pre_tool_use(input_data: dict[str, Any], tool_use_id, context) -> dict:
        tool_name: str = input_data.get("tool_name", "")
        tool_input: dict = input_data.get("tool_input", {})

        # Always-allowed read tools: pass through immediately
        if tool_name in _ALWAYS_ALLOWED_TOOLS:
            return _allow()

        # Zendesk write MCP calls: deny
        tl = tool_name.lower()
        for prefix in _ZENDESK_WRITE_PREFIXES:
            if tl.startswith(prefix):
                return _deny(input_data, f"Zendesk write tool {tool_name!r} is not permitted")

        # File-writing tools: must resolve inside sandbox
        if tool_name in _WRITE_TOOLS:
            file_path = tool_input.get("file_path", "")
            if not file_path:
                return _deny(input_data, "Write/Edit called with empty file_path")
            if not _is_inside_sandbox(file_path, sandbox_root):
                return _deny(
                    input_data,
                    f"Write/Edit path {file_path!r} is outside the ticket sandbox "
                    f"({sandbox_root}). Only paths inside Tickets/<id>/ are permitted.",
                )
            return _allow()

        # Bash: check for destructive patterns
        if tool_name == "Bash":
            command: str = tool_input.get("command", "")
            for pat in _DESTRUCTIVE_BASH_PATTERNS:
                if pat.search(command):
                    return _deny(
                        input_data,
                        f"Bash command contains a disallowed pattern ({pat.pattern!r}): "
                        f"{command[:120]!r}",
                    )
            return _allow()

        # All other tools: allow (permission_mode + allowed_tools in runner.py
        # handle the final gate; harness only blocks the above)
        return _allow()

    return pre_tool_use


def make_post_tool_use(events_path: Path):
    """Return a PostToolUse hook that appends every tool call to events.jsonl."""

    async def post_tool_use(input_data: dict[str, Any], tool_use_id, context) -> dict:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool_name": input_data.get("tool_name"),
            "tool_input": input_data.get("tool_input"),
            "tool_output_snippet": str(input_data.get("tool_output", ""))[:500],
            "tool_use_id": tool_use_id,
        }
        with events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return {}

    return post_tool_use


def build_hooks(
    sandbox_root: Path,
    events_path: Path,
) -> dict[str, list]:
    """Build the hooks dict for ClaudeAgentOptions.

    Returns:
      {
        "PreToolUse":  [HookMatcher(hooks=[pre_tool_use_callback])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use_callback])],
      }

    Import HookMatcher lazily so this module can be tested without the SDK
    being importable (the test suite calls make_pre_tool_use / make_post_tool_use
    directly rather than via the HookMatcher wrapper).
    """
    from claude_agent_sdk import HookMatcher  # noqa: PLC0415

    pre = make_pre_tool_use(sandbox_root=sandbox_root)
    post = make_post_tool_use(events_path=events_path)

    return {
        "PreToolUse": [HookMatcher(hooks=[pre])],
        "PostToolUse": [HookMatcher(hooks=[post])],
    }
