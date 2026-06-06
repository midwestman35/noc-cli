from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace

logger = logging.getLogger("noc_cli.model_profiles")


@dataclass(frozen=True)
class ModelProfile:
    model: str
    fallback_model: str | None = None
    effort: str = "medium"
    source: str = "default"
    override_var: str | None = None


_DEFAULTS: dict[str, ModelProfile] = {
    "investigate": ModelProfile(
        model="claude-opus-4-8",
        fallback_model="claude-sonnet-4-6",
        effort="high",
    ),
    "chat": ModelProfile(model="claude-sonnet-4-6", effort="medium"),
    "scout_screen": ModelProfile(model="claude-haiku-4-5", effort="medium"),
    "scout_synth": ModelProfile(model="claude-opus-4-8", effort="high"),
    "grounding": ModelProfile("claude-haiku-4-5", None, "medium"),
}


def _override_var(surface: str) -> str:
    return f"NOC_MODEL_{surface.upper()}"


def _nonblank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _dotenv_override(var: str) -> str | None:
    try:
        from dotenv import dotenv_values

        from noc_cli.config import config_path

        value = dotenv_values(config_path()).get(var)
    except Exception:
        return None
    return _nonblank(value)


def profile_for(surface: str) -> ModelProfile:
    base = _DEFAULTS[surface]
    var = _override_var(surface)
    source = "default"
    model = base.model

    env_model = _nonblank(os.environ.get(var))
    if env_model is not None:
        model = env_model
        source = "env"
    else:
        dotenv_model = _dotenv_override(var)
        if dotenv_model is not None:
            model = dotenv_model
            source = "dotenv"

    profile = replace(base, model=model, source=source, override_var=var)
    logger.info(
        "resolved model profile surface=%s model=%s source=%s",
        surface,
        profile.model,
        profile.source,
    )
    return profile
