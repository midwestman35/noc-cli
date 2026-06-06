from __future__ import annotations

import pytest

from noc_cli.model_profiles import ModelProfile, profile_for


def test_pinned_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    for surface in ("INVESTIGATE", "CHAT", "SCOUT_SCREEN", "SCOUT_SYNTH"):
        monkeypatch.delenv(f"NOC_MODEL_{surface}", raising=False)

    inv = profile_for("investigate")

    assert isinstance(inv, ModelProfile)
    assert (inv.model, inv.fallback_model, inv.effort, inv.source) == (
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "high",
        "default",
    )
    assert profile_for("chat").model == "claude-sonnet-4-6"
    assert profile_for("scout_screen").model == "claude-haiku-4-5"
    assert profile_for("scout_synth").model == "claude-opus-4-8"


def test_process_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.setenv("NOC_MODEL_INVESTIGATE", "claude-sonnet-4-6")

    p = profile_for("investigate")

    assert p.model == "claude-sonnet-4-6"
    assert p.source == "env"
    assert p.fallback_model == "claude-sonnet-4-6"
    assert p.override_var == "NOC_MODEL_INVESTIGATE"


def test_blank_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.setenv("NOC_MODEL_CHAT", "   ")

    p = profile_for("chat")

    assert p.model == "claude-sonnet-4-6"
    assert p.source == "default"


def test_dotenv_override_is_used_when_process_env_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.delenv("NOC_MODEL_CHAT", raising=False)
    (tmp_path / ".env").write_text(
        "NOC_MODEL_CHAT=claude-haiku-4-5\n",
        encoding="utf-8",
    )

    p = profile_for("chat")

    assert p.model == "claude-haiku-4-5"
    assert p.source == "dotenv"
    assert p.override_var == "NOC_MODEL_CHAT"


def test_process_env_beats_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "NOC_MODEL_CHAT=claude-haiku-4-5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NOC_MODEL_CHAT", "claude-sonnet-4-6")

    p = profile_for("chat")

    assert p.model == "claude-sonnet-4-6"
    assert p.source == "env"


def test_unknown_surface_raises():
    with pytest.raises(KeyError):
        profile_for("nope")


def test_grounding_profile_is_haiku():
    p = profile_for("grounding")
    assert p.model == "claude-haiku-4-5"
    assert p.effort == "medium"
    assert p.source == "default"
