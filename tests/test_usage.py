from __future__ import annotations

import json

from noc_cli.model_profiles import profile_for
from noc_cli.usage import log_usage


class _FakeResult:
    def __init__(self, **kwargs: object) -> None:
        self.usage = kwargs.get(
            "usage",
            {"input_tokens": 10, "cache_read_input_tokens": 7},
        )
        self.total_cost_usd = kwargs.get("total_cost_usd", 0.01)
        self.model_usage = kwargs.get(
            "model_usage",
            {"claude-opus-4-8": {"input_tokens": 10}},
        )
        self.num_turns = kwargs.get("num_turns", 2)
        self.session_id = kwargs.get("session_id", "sess-1")


def test_log_usage_writes_parseable_line(tmp_path):
    events_path = tmp_path / "events.jsonl"

    log_usage(
        events_path,
        surface="investigate",
        profile=profile_for("investigate"),
        result_message=_FakeResult(),
        attempt=1,
    )

    line = json.loads(events_path.read_text(encoding="utf-8").strip())
    assert line["type"] == "usage"
    assert line["surface"] == "investigate"
    assert line["attempt"] == 1
    assert line["model"] == "claude-opus-4-8"
    assert line["fallback_model"] == "claude-sonnet-4-6"
    assert line["effort"] == "high"
    assert line["profile_source"] == "default"
    assert line["profile_override_var"] == "NOC_MODEL_INVESTIGATE"
    assert line["usage"]["cache_read_input_tokens"] == 7
    assert line["model_usage"]["claude-opus-4-8"]["input_tokens"] == 10
    assert line["total_cost_usd"] == 0.01
    assert line["num_turns"] == 2
    assert line["session_id"] == "sess-1"


def test_log_usage_is_best_effort_on_missing_fields(tmp_path):
    events_path = tmp_path / "events.jsonl"

    class Bare:
        pass

    log_usage(
        events_path,
        surface="chat",
        profile=profile_for("chat"),
        result_message=Bare(),
    )

    line = json.loads(events_path.read_text(encoding="utf-8").strip())
    assert line["surface"] == "chat"
    assert line["profile_override_var"] == "NOC_MODEL_CHAT"
    assert "attempt" not in line
    assert "usage" not in line
    assert "total_cost_usd" not in line


def test_log_usage_coerces_object_shaped_usage_payloads(tmp_path):
    events_path = tmp_path / "events.jsonl"

    class UsagePayload:
        def model_dump(self, *, mode: str = "python") -> dict[str, int | str]:
            return {
                "mode": mode,
                "input_tokens": 11,
                "cache_creation_input_tokens": 5,
            }

    class ModelUsagePayload:
        def to_dict(self) -> dict[str, int]:
            return {"output_tokens": 3}

    log_usage(
        events_path,
        surface="investigate",
        profile=profile_for("investigate"),
        result_message=_FakeResult(
            usage=UsagePayload(),
            model_usage={"claude-opus-4-8": ModelUsagePayload()},
        ),
    )

    line = json.loads(events_path.read_text(encoding="utf-8").strip())
    assert line["usage"]["mode"] == "json"
    assert line["usage"]["input_tokens"] == 11
    assert line["usage"]["cache_creation_input_tokens"] == 5
    assert line["model_usage"]["claude-opus-4-8"]["output_tokens"] == 3


def test_log_usage_swallows_unwritable_path(tmp_path):
    log_usage(
        tmp_path,
        surface="chat",
        profile=profile_for("chat"),
        result_message=_FakeResult(),
    )
