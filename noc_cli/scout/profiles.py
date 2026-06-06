from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from noc_cli.model_profiles import profile_for

SCREEN_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "LS")


@dataclass(frozen=True)
class Profile:
    """A frozen per-role agent profile."""

    model: str
    effort: str
    max_turns: int
    allowed_tools: tuple[str, ...]


_SCREEN_PROFILE = profile_for("scout_screen")
_SYNTHESIS_PROFILE = profile_for("scout_synth")

SCREEN = Profile(
    model=_SCREEN_PROFILE.model,
    effort=_SCREEN_PROFILE.effort,
    max_turns=12,
    allowed_tools=SCREEN_TOOLS,
)

SYNTHESIS = Profile(
    model=_SYNTHESIS_PROFILE.model,
    effort=_SYNTHESIS_PROFILE.effort,
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
        # bypassPermissions skips interactive prompts; the read-only `build_hooks`
        # sandbox (passed in via `hooks`) is the real gate, denying writes and
        # out-of-workspace access before any permission check runs.
        "permission_mode": "bypassPermissions",
        "max_turns": profile.max_turns,
        "model": profile.model,
        "effort": profile.effort,
        "cwd": str(cwd),
    }
    if hooks is not None:
        kwargs["hooks"] = hooks
    return options_cls(**kwargs)
