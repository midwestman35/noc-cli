from noc_cli.scout.profiles import SCREEN, SYNTHESIS, build_options


def test_profile_model_and_effort():
    assert SCREEN.model == "claude-haiku-4-5"
    assert SCREEN.effort == "medium"
    assert "Read" in SCREEN.allowed_tools
    assert "Bash" not in SCREEN.allowed_tools
    assert "Write" not in SCREEN.allowed_tools
    assert SYNTHESIS.model == "claude-opus-4-8"
    assert SYNTHESIS.effort == "high"
    assert SYNTHESIS.allowed_tools == ()


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
    assert captured["permission_mode"] == "bypassPermissions"
    assert captured["cwd"] == "/tmp/ws"
    assert captured["hooks"] == {"x": 1}
    assert captured["allowed_tools"] == []
