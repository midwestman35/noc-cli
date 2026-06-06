from noc_cli.scout.hooks import build_scout_options
from noc_cli.scout.profiles import SCREEN_TOOLS


class _FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_build_scout_options_returns_screen_and_synth_factories(tmp_path):
    screen_factory, synth_factory = build_scout_options(
        tmp_path, options_cls=_FakeOptions
    )

    screen = screen_factory()
    synth = synth_factory()

    # Both confined to the same workspace cwd and same read-only hook sandbox.
    assert screen.kwargs["cwd"] == str(tmp_path)
    assert synth.kwargs["cwd"] == str(tmp_path)
    assert "hooks" in screen.kwargs
    assert screen.kwargs["hooks"] is synth.kwargs["hooks"]

    # Per-role profiles preserved: screening reads, synthesis has no tools.
    assert screen.kwargs["allowed_tools"] == list(SCREEN_TOOLS)
    assert synth.kwargs["allowed_tools"] == []
    assert screen.kwargs["model"] == "claude-haiku-4-5"
    assert synth.kwargs["model"] == "claude-opus-4-8"


def test_build_scout_options_builds_hooks_once(tmp_path, monkeypatch):
    import noc_cli.agent.harness as harness

    calls: list[dict] = []
    real = harness.build_hooks

    def recording(sandbox_root, events_path, *, restrict_read_tools=False):
        calls.append(
            {"sandbox_root": sandbox_root, "restrict_read_tools": restrict_read_tools}
        )
        return real(sandbox_root, events_path, restrict_read_tools=restrict_read_tools)

    monkeypatch.setattr(harness, "build_hooks", recording)

    build_scout_options(tmp_path, options_cls=_FakeOptions)

    assert len(calls) == 1
    assert calls[0]["sandbox_root"] == tmp_path
    assert calls[0]["restrict_read_tools"] is True
