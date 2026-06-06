from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from noc_cli.model_profiles import ModelProfile

_UNSAFE = object()


def _json_safe(value: Any) -> Any | object:
    if value is None:
        return None
    if isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        safe: dict[Any, Any] = {}
        for key, item in value.items():
            safe_item = _json_safe(item)
            if safe_item is not _UNSAFE:
                safe[key] = safe_item
        return safe
    if isinstance(value, list | tuple):
        safe_items = []
        for item in value:
            safe_item = _json_safe(item)
            if safe_item is not _UNSAFE:
                safe_items.append(safe_item)
        return safe_items

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe(model_dump(mode="json"))
        except TypeError:
            try:
                return _json_safe(model_dump())
            except Exception:
                return _UNSAFE
        except Exception:
            return _UNSAFE

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _json_safe(to_dict())
        except Exception:
            return _UNSAFE

    try:
        object_dict = vars(value)
    except TypeError:
        object_dict = None
    if object_dict is not None:
        public_values = {
            key: item for key, item in object_dict.items() if not key.startswith("_")
        }
        return _json_safe(public_values)

    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return _UNSAFE
    return value


def _message_attr(result_message: object, name: str) -> Any:
    try:
        return getattr(result_message, name)
    except Exception:
        return None


def log_usage(
    events_path: str | Path,
    *,
    surface: str,
    profile: ModelProfile,
    result_message: object,
    attempt: int | None = None,
) -> None:
    try:
        entry: dict[str, Any] = {
            "type": "usage",
            "surface": surface,
            "model": profile.model,
            "fallback_model": profile.fallback_model,
            "effort": profile.effort,
            "profile_source": profile.source,
            "profile_override_var": profile.override_var,
        }
        if attempt is not None:
            entry["attempt"] = attempt

        for name in ("total_cost_usd", "num_turns", "session_id"):
            value = _json_safe(_message_attr(result_message, name))
            if value is not None and value is not _UNSAFE:
                entry[name] = value

        for name in ("usage", "model_usage"):
            value = _json_safe(_message_attr(result_message, name))
            if value is not None and value is not _UNSAFE:
                entry[name] = value

        path = Path(events_path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        return
