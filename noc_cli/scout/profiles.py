from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SCREEN_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "LS")


@dataclass(frozen=True)
class Profile:
    """A frozen per-role agent profile."""

    model: str
    effort: str
    max_turns: int
    allowed_tools: tuple[str, ...]


SCREEN = Profile(
    model="claude-haiku-4-5",
    effort="medium",
    max_turns=12,
    allowed_tools=SCREEN_TOOLS,
)

SYNTHESIS = Profile(
    model="claude-opus-4-8",
    effort="high",
    max_turns=6,
    allowed_tools=(),
)


def build_options(
    profile: Profile,
    *,
    system_prompt: str,
    cwd: Path | str,
    hooks=None,
    options_cls=None,
):
    """Build ClaudeAgentOptions for a profile; options_cls is injected in tests."""
    if options_cls is None:
        from claude_agent_sdk import ClaudeAgentOptions as options_cls  # noqa: PLC0415

    kwargs = {
        "system_prompt": system_prompt,
        "allowed_tools": list(profile.allowed_tools),
        "permission_mode": "bypassPermissions",
        "max_turns": profile.max_turns,
        "model": profile.model,
        "effort": profile.effort,
        "cwd": str(cwd),
    }
    if hooks is not None:
        kwargs["hooks"] = hooks
    return options_cls(**kwargs)
