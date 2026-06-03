import asyncio
import json

from noc_cli.agent.harness import (
    build_hooks,
    make_post_tool_use,
    make_pre_tool_use,
)


def _run(coro):
    """Run an async hook callback to completion (dependency-free)."""
    return asyncio.run(coro)


def _pre_event(tool_name: str, tool_input: dict) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def _post_event(tool_name: str, tool_input: dict, output: str = "") -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": output,
    }


async def _call(hook, event: dict) -> dict:
    return await hook(event, None, None)


# ── pre-tool-use: writes outside sandbox ────────────────────────────────────


def test_write_outside_sandbox_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)
    result = _run(_call(pre, _pre_event("Write", {"file_path": "/etc/passwd"})))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_edit_outside_sandbox_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)
    result = _run(_call(pre, _pre_event("Edit", {"file_path": str(tmp_path / "outside.txt")})))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_inside_sandbox_is_allowed(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)
    result = _run(_call(pre, _pre_event("Write", {"file_path": str(sandbox / "analysis" / "notes.md")})))
    assert result == {} or result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"


def test_read_anywhere_is_allowed(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)
    result = _run(_call(pre, _pre_event("Read", {"file_path": "/some/log/file.log"})))
    spec = result.get("hookSpecificOutput", {})
    assert spec.get("permissionDecision", "allow") != "deny"


# ── pre-tool-use: destructive Bash ──────────────────────────────────────────


def test_bash_rm_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)
    result = _run(_call(pre, _pre_event("Bash", {"command": "rm -rf /tmp/foo"})))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_bash_redirect_outside_sandbox_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)
    result = _run(_call(pre, _pre_event("Bash", {"command": "cat logs/foo.log > /tmp/exfil.txt"})))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_bash_grep_is_allowed(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)
    result = _run(_call(pre, _pre_event("Bash", {"command": "grep -r 'SIP INVITE' logs/"})))
    spec = result.get("hookSpecificOutput", {})
    assert spec.get("permissionDecision", "allow") != "deny"


def test_zendesk_write_mcp_is_denied(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    pre = make_pre_tool_use(sandbox_root=sandbox)
    result = _run(_call(pre, _pre_event("mcp__zendesk__create_ticket", {"subject": "x"})))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── post-tool-use: events.jsonl ─────────────────────────────────────────────


def test_post_tool_use_appends_to_events_jsonl(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    events_path = sandbox / "events.jsonl"
    post = make_post_tool_use(events_path=events_path)
    _run(_call(post, _post_event("Read", {"file_path": "logs/k.log"}, "line1\nline2")))
    _run(_call(post, _post_event("Bash", {"command": "grep INVITE logs/k.log"}, "match")))
    lines = events_path.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool_name"] == "Read"
    second = json.loads(lines[1])
    assert second["tool_name"] == "Bash"


def test_build_hooks_returns_correct_structure(tmp_path):
    sandbox = tmp_path / "Tickets" / "18432"
    sandbox.mkdir(parents=True)
    hooks = build_hooks(sandbox_root=sandbox, events_path=sandbox / "events.jsonl")
    assert "PreToolUse" in hooks
    assert "PostToolUse" in hooks
    assert len(hooks["PreToolUse"]) >= 1
    assert len(hooks["PostToolUse"]) >= 1
