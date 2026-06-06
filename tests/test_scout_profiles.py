import importlib

from noc_cli.scout.profiles import SCREEN, SYNTHESIS, build_options


def test_profile_model_and_effort():
    assert SCREEN.model == "claude-haiku-4-5"
    assert SCREEN.effort == "medium"
    assert SCREEN.max_turns == 12
    assert "Read" in SCREEN.allowed_tools
    assert "Bash" not in SCREEN.allowed_tools
    assert "Write" not in SCREEN.allowed_tools
    assert SYNTHESIS.model == "claude-opus-4-8"
    assert SYNTHESIS.effort == "high"
    assert SYNTHESIS.max_turns == 6
    assert SYNTHESIS.allowed_tools == ()


def test_scout_profiles_source_model_and_effort_from_registry(monkeypatch):
    monkeypatch.setenv("NOC_MODEL_SCOUT_SCREEN", "claude-test-screen")
    monkeypatch.setenv("NOC_MODEL_SCOUT_SYNTH", "claude-test-synth")

    import noc_cli.scout.profiles as profiles

    reloaded = importlib.reload(profiles)

    assert reloaded.SCREEN.model == "claude-test-screen"
    assert reloaded.SCREEN.effort == "medium"
    assert reloaded.SYNTHESIS.model == "claude-test-synth"
    assert reloaded.SYNTHESIS.effort == "high"

    monkeypatch.delenv("NOC_MODEL_SCOUT_SCREEN")
    monkeypatch.delenv("NOC_MODEL_SCOUT_SYNTH")
    importlib.reload(profiles)


def test_build_options_passes_model_and_effort():
    captured = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    build_options(
        SYNTHESIS,
        system_prompt="sp",
        cwd="/tmp/ws",
        hooks={"x": 1},
        options_cls=FakeOptions,
    )

    assert captured["model"] == "claude-opus-4-8"
    assert captured["effort"] == "high"
    assert captured["max_turns"] == 6
    assert captured["permission_mode"] == "bypassPermissions"
    assert captured["cwd"] == "/tmp/ws"
    assert captured["hooks"] == {"x": 1}
    assert captured["allowed_tools"] == []
