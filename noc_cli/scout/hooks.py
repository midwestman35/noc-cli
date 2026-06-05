from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def build_scout_options(
    workspace: Path, *, options_cls=None
) -> tuple[Callable, Callable]:
    """Return ``(screen_options_factory, synth_options_factory)`` sharing one
    read-only hook sandbox rooted at *workspace*.

    Both factories produce fresh ``ClaudeAgentOptions`` (cold-start each call),
    but the hook sandbox is built once and shared. This is the single place the
    Scout agent profiles are wired to the ``build_hooks`` read-only sandbox, so
    ``screen`` and ``runner`` no longer duplicate it. ``options_cls`` is injected
    in tests to avoid importing the SDK.
    """
    from noc_cli.agent.harness import build_hooks  # noqa: PLC0415
    from noc_cli.scout.profiles import SCREEN, SYNTHESIS, build_options  # noqa: PLC0415
    from noc_cli.scout.screen import SCREEN_SYSTEM_PROMPT  # noqa: PLC0415
    from noc_cli.scout.synthesize import SYNTHESIS_SYSTEM_PROMPT  # noqa: PLC0415

    hooks = build_hooks(
        sandbox_root=workspace,
        events_path=workspace / "events.jsonl",
        restrict_read_tools=True,
    )

    def screen_options_factory():
        return build_options(
            SCREEN,
            system_prompt=SCREEN_SYSTEM_PROMPT,
            cwd=workspace,
            hooks=hooks,
            options_cls=options_cls,
        )

    def synth_options_factory():
        return build_options(
            SYNTHESIS,
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
            cwd=workspace,
            hooks=hooks,
            options_cls=options_cls,
        )

    return screen_options_factory, synth_options_factory
