# SDK Tuning — Model Selection + Caching Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize per-surface model selection (pinned defaults + `NOC_MODEL_<SURFACE>` env override + investigate fallback + per-surface effort) across investigate / chat / scout, and add best-effort usage/caching observability written to a JSONL event log.

**Architecture:** A new `noc_cli/model_profiles.py` registry owns the four model IDs and resolves overrides (process env → data-dir `.env` → pinned default), tracking the `source`. A new `noc_cli/usage.py` appends a structured usage line from the terminal `ResultMessage`. Each surface reads its profile and sets `model`/`fallback_model`/`effort` on its existing `ClaudeAgentOptions`; the registry centralizes *model settings only* (not the differing option/tool/client shapes). Usage logging is threaded via an `on_result` callback in `runner._drain` and scout's `final_result`, and inline in chat.

**Tech Stack:** Python 3.10+, `uv`, `claude-agent-sdk` 0.2.88 (`ClaudeAgentOptions.{model,fallback_model,effort}`, `ResultMessage.{usage,total_cost_usd,model_usage,num_turns,session_id}`), `python-dotenv`, pytest + anyio.

**Spec:** `docs/superpowers/specs/2026-06-06-sdk-tuning-model-selection-design.md`

---

## Plan-level decisions (resolving spec gaps surfaced during grounding)

- **Scout usage destination:** Scout's pipeline runs in a *temp* workspace (`run_scout_report` uses `Path(tmp)`), so a workspace `events.jsonl` would be discarded. Scout therefore logs usage to a **stable** path: `data_dir() / "usage.jsonl"`. Investigate and chat log to their per-ticket `events.jsonl` (cost attributable to a ticket). `log_usage` is path-agnostic — callers choose.
- **Consistent callback seam:** both `runner._drain` and `scout.final_result` gain an optional `on_result(message)` callback, invoked with the terminal `ResultMessage`. This mirrors one pattern across surfaces.
- **Resolution timing:** `profile_for()` resolves env at call time. Surfaces call it when building options (per CLI invocation, env is set before that), so overrides apply. Scout's `SCREEN`/`SYNTHESIS` resolve at module import — fine for a fresh CLI process. Tests exercise `profile_for()` directly.

## File structure

| File | Responsibility |
|---|---|
| `noc_cli/model_profiles.py` *(new)* | `ModelProfile(model, fallback_model=None, effort="medium", source="default", override_var=None)`; `_DEFAULTS` (4 profiles); `profile_for(surface) -> ModelProfile`. |
| `noc_cli/usage.py` *(new)* | `log_usage(events_path, *, surface, profile, result_message, attempt=None)` — best-effort JSONL append including the profile source and override variable. |
| `noc_cli/agent/runner.py` *(modify)* | Set model/fallback/effort from `profile_for("investigate")`; `_drain(gen, on_result=...)`; log usage per attempt. |
| `noc_cli/tui/chat.py` *(modify)* | Set model/effort from `profile_for("chat")`; log usage in the same abort-safe `finally` path that persists the agent turn. |
| `noc_cli/scout/profiles.py` *(modify)* | `SCREEN`/`SYNTHESIS` source `model`+`effort` from `profile_for`. |
| `noc_cli/scout/llm_io.py` *(modify)* | `final_result(query_gen, *, on_result=None)`. |
| `noc_cli/scout/screen.py`, `synthesize.py` *(modify)* | Thread `on_result` → `log_usage(data_dir()/"usage.jsonl", …)`. |

---

### Task 1: `model_profiles.py` registry + `profile_for`

**Files:** Create `noc_cli/model_profiles.py`; Test `tests/test_model_profiles.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_profiles.py`:
```python
import pytest

from noc_cli.model_profiles import ModelProfile, profile_for


def test_pinned_defaults():
    inv = profile_for("investigate")
    assert (inv.model, inv.fallback_model, inv.effort, inv.source) == (
        "claude-opus-4-8", "claude-sonnet-4-6", "high", "default",
    )
    assert profile_for("chat").model == "claude-sonnet-4-6"
    assert profile_for("scout_screen").model == "claude-haiku-4-5"
    assert profile_for("scout_synth").model == "claude-opus-4-8"


def test_process_env_override_wins(monkeypatch):
    monkeypatch.setenv("NOC_MODEL_INVESTIGATE", "claude-sonnet-4-6")
    p = profile_for("investigate")
    assert p.model == "claude-sonnet-4-6"
    assert p.source == "env"
    assert p.fallback_model == "claude-sonnet-4-6"  # fallback unchanged


def test_blank_override_is_ignored(monkeypatch):
    monkeypatch.setenv("NOC_MODEL_CHAT", "   ")
    p = profile_for("chat")
    assert p.model == "claude-sonnet-4-6"
    assert p.source == "default"


def test_dotenv_override_is_used_when_process_env_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.delenv("NOC_MODEL_CHAT", raising=False)
    (tmp_path / ".env").write_text("NOC_MODEL_CHAT=claude-haiku-4-5\n", encoding="utf-8")

    p = profile_for("chat")

    assert p.model == "claude-haiku-4-5"
    assert p.source == "dotenv"
    assert p.override_var == "NOC_MODEL_CHAT"


def test_process_env_beats_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("NOC_MODEL_CHAT=claude-haiku-4-5\n", encoding="utf-8")
    monkeypatch.setenv("NOC_MODEL_CHAT", "claude-sonnet-4-6")

    p = profile_for("chat")

    assert p.model == "claude-sonnet-4-6"
    assert p.source == "env"


def test_unknown_surface_raises():
    with pytest.raises(KeyError):
        profile_for("nope")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_model_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.model_profiles'`.

- [ ] **Step 3: Implement**

Create `noc_cli/model_profiles.py`:
```python
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
    source: str = "default"  # "default" | "env" | "dotenv"
    override_var: str | None = None


_DEFAULTS: dict[str, ModelProfile] = {
    "investigate": ModelProfile("claude-opus-4-8", "claude-sonnet-4-6", "high"),
    "chat": ModelProfile("claude-sonnet-4-6", None, "medium"),
    "scout_screen": ModelProfile("claude-haiku-4-5", None, "medium"),
    "scout_synth": ModelProfile("claude-opus-4-8", None, "high"),
}


def _env_var(surface: str) -> str:
    return f"NOC_MODEL_{surface.upper()}"


def profile_for(surface: str) -> ModelProfile:
    """Resolve a surface's model profile: process env → data-dir .env → default.

    Raises KeyError for an unknown surface. Blank/whitespace overrides are
    ignored. The resolved model + source are logged so an override is visible
    even if no run completes.
    """
    base = _DEFAULTS[surface]  # KeyError on unknown surface — intentional
    var = _env_var(surface)

    override, source = None, "default"
    proc = os.environ.get(var)
    if proc and proc.strip():
        override, source = proc.strip(), "env"
    else:
        try:
            from noc_cli.config import config_path  # noqa: PLC0415
            from dotenv import dotenv_values  # noqa: PLC0415

            file_val = dotenv_values(config_path()).get(var)
            if file_val and file_val.strip():
                override, source = file_val.strip(), "dotenv"
        except Exception:  # noqa: BLE001 — resolution must never crash option-build
            pass

    resolved = replace(base, model=override or base.model, source=source, override_var=var)
    logger.info("model profile %s -> %s (source=%s)", surface, resolved.model, resolved.source)
    return resolved
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_model_profiles.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/model_profiles.py tests/test_model_profiles.py
git commit -m "feat(agent): central model profile registry with env override"
```

---

### Task 2: `usage.py` best-effort usage logger

**Files:** Create `noc_cli/usage.py`; Test `tests/test_usage.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_usage.py`:
```python
import json

from noc_cli.model_profiles import profile_for
from noc_cli.usage import log_usage


class _FakeResult:
    def __init__(self, **kw):
        self.usage = kw.get("usage", {"input_tokens": 10, "cache_read_input_tokens": 7})
        self.total_cost_usd = kw.get("total_cost_usd", 0.01)
        self.model_usage = kw.get("model_usage", {})
        self.num_turns = kw.get("num_turns", 2)
        self.session_id = kw.get("session_id", "sess-1")


def test_log_usage_writes_parseable_line(tmp_path):
    p = tmp_path / "events.jsonl"
    log_usage(p, surface="investigate", profile=profile_for("investigate"),
              result_message=_FakeResult(), attempt=1)
    line = json.loads(p.read_text().strip())
    assert line["type"] == "usage"
    assert line["surface"] == "investigate"
    assert line["attempt"] == 1
    assert line["model"] == "claude-opus-4-8"
    assert line["fallback_model"] == "claude-sonnet-4-6"
    assert line["effort"] == "high"
    assert line["profile_source"] == "default"
    assert line["profile_override_var"] == "NOC_MODEL_INVESTIGATE"
    assert line["usage"]["cache_read_input_tokens"] == 7
    assert line["total_cost_usd"] == 0.01


def test_log_usage_is_best_effort_on_missing_fields(tmp_path):
    p = tmp_path / "events.jsonl"

    class Bare:  # no usage / cost attrs at all
        pass

    log_usage(p, surface="chat", profile=profile_for("chat"), result_message=Bare())
    line = json.loads(p.read_text().strip())
    assert line["surface"] == "chat"
    assert "attempt" not in line  # omitted when None


def test_log_usage_swallows_unwritable_path(tmp_path):
    # directory path (not a file) -> write fails -> must not raise
    log_usage(tmp_path, surface="chat", profile=profile_for("chat"),
              result_message=_FakeResult())
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_usage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.usage'`.

- [ ] **Step 3: Implement**

Create `noc_cli/usage.py`:
```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from noc_cli.model_profiles import ModelProfile


def _jsonable(value: Any) -> Any:
    """Coerce SDK usage objects to JSON-safe values; return {} on failure."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    for attr in ("model_dump", "to_dict", "__dict__"):
        got = getattr(value, attr, None)
        if callable(got):
            try:
                return got()
            except Exception:  # noqa: BLE001
                continue
        if attr == "__dict__" and isinstance(got, dict):
            return dict(got)
    return None


def log_usage(
    events_path: Path,
    *,
    surface: str,
    profile: ModelProfile,
    result_message: Any,
    attempt: int | None = None,
) -> None:
    """Append one best-effort {"type":"usage", ...} line. Never raises."""
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
        for field in ("total_cost_usd", "num_turns", "session_id"):
            val = getattr(result_message, field, None)
            if val is not None:
                entry[field] = val
        usage = _jsonable(getattr(result_message, "usage", None))
        if usage is not None:
            entry["usage"] = usage
        model_usage = _jsonable(getattr(result_message, "model_usage", None))
        if model_usage is not None:
            entry["model_usage"] = model_usage

        with Path(events_path).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001 — observability must never break a run
        pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_usage.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/usage.py tests/test_usage.py
git commit -m "feat(agent): best-effort usage/caching observability logger"
```

---

### Task 3: Wire investigate (runner.py) — model + per-attempt usage

**Files:** Modify `noc_cli/agent/runner.py`; Test `tests/test_agent_runner.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_runner.py`:
```python
def test_investigate_sets_model_profile_and_logs_usage(tmp_path):
    from noc_cli.scaffold import scaffold_ticket

    folder = scaffold_ticket(tmp_path, 7001)
    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options

        class R:
            result = '{"totally":"wrong"}'  # forces 2 attempts → 2 usage lines
            is_error = False
            usage = {"input_tokens": 5, "cache_read_input_tokens": 3}
            total_cost_usd = 0.002
            num_turns = 1
            session_id = "s"

        yield R()

    _run(
        run_agent(
            ticket_id=7001, folder=folder, system_prompt="t", history_context="",
            _query_fn=fake_query,
        )
    )
    opts = captured["options"]
    assert opts.model == "claude-opus-4-8"
    assert opts.fallback_model == "claude-sonnet-4-6"
    assert opts.effort == "high"
    usage_lines = [
        l for l in (folder.root / "events.jsonl").read_text().splitlines()
        if '"type": "usage"' in l or '"type":"usage"' in l
    ]
    assert len(usage_lines) == 2  # one per attempt
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent_runner.py -k investigate_sets_model -v`
Expected: FAIL — `opts.model` is `None` (no profile wired) / no usage lines.

- [ ] **Step 3: Implement**

In `noc_cli/agent/runner.py`:

(a) Add imports near the top (module level):
```python
from noc_cli.model_profiles import profile_for
from noc_cli.usage import log_usage
```

(b) Change `_drain` to accept an `on_result` callback and invoke it on the terminal message:
```python
async def _drain(query_gen, on_result=None) -> tuple[str, list[TranscriptEntry]]:
    """Drain the agent stream and keep both final result and intermediate turns."""
    raw = ""
    transcript: list[TranscriptEntry] = []
    async for message in query_gen:
        result_text = getattr(message, "result", None)
        if result_text is not None:
            raw = result_text
            if on_result is not None:
                on_result(message)
            transcript.append(
                TranscriptEntry(
                    kind="result",
                    text=str(result_text)[:4000],
                    raw_text=str(result_text),
                )
            )
            continue
```
(Leave the rest of `_drain` unchanged.)

(c) In `run_agent`, resolve the profile once (after `events_path` is set) and set options:
```python
    events_path = folder.root / "events.jsonl"
    hooks = build_hooks(sandbox_root=folder.root, events_path=events_path)
    profile = profile_for("investigate")
```
In `_make_options`, add the three fields:
```python
        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=ALLOWED_TOOLS + extra_tools,
            permission_mode="bypassPermissions",
            max_turns=MAX_TURNS,
            cwd=str(folder.root),
            hooks=hooks,
            mcp_servers=mcp_servers,
            model=profile.model,
            fallback_model=profile.fallback_model,
            effort=profile.effort,
        )
```

(d) Pass per-attempt usage callbacks to the two `_drain` calls:
```python
    gen1 = _query_fn(prompt=full_prompt, options=_make_options())
    raw1, transcript1 = await _drain(
        gen1,
        on_result=lambda m: log_usage(
            events_path, surface="investigate", profile=profile, result_message=m, attempt=1
        ),
    )
```
and for the retry:
```python
    gen2 = _query_fn(prompt=correction_prompt, options=_make_options())
    raw2, transcript2 = await _drain(
        gen2,
        on_result=lambda m: log_usage(
            events_path, surface="investigate", profile=profile, result_message=m, attempt=2
        ),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: PASS — new test + all existing runner tests (the `on_result` default is `None`, so existing tests are unaffected).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/agent/runner.py tests/test_agent_runner.py
git commit -m "feat(agent): investigate runs on Opus profile + per-attempt usage logging"
```

---

### Task 4: Scout profiles source from the registry

**Files:** Modify `noc_cli/scout/profiles.py`; Test `tests/test_scout_profiles.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scout_profiles.py`:
```python
from noc_cli.scout.profiles import SCREEN, SYNTHESIS


def test_scout_profiles_source_models_from_registry():
    # behavior unchanged after centralization
    assert SCREEN.model == "claude-haiku-4-5"
    assert SCREEN.effort == "medium"
    assert SYNTHESIS.model == "claude-opus-4-8"
    assert SYNTHESIS.effort == "high"
```

- [ ] **Step 2: Run to verify it fails (or passes trivially)**

Run: `uv run pytest tests/test_scout_profiles.py -k source_models -v`
Expected: PASS already on values, but proceed to wire the source so the IDs are no longer duplicated (the point of the task is removing duplication, verified by the import below in Step 3).

- [ ] **Step 3: Implement**

In `noc_cli/scout/profiles.py`, replace the hardcoded model strings in `SCREEN`/`SYNTHESIS` with registry lookups (keep all other fields). Resolve each profile once so model and effort come from the same env snapshot:
```python
from noc_cli.model_profiles import profile_for  # add to imports

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scout_profiles.py -v`
Expected: PASS (all, including existing scout-profile tests).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/profiles.py tests/test_scout_profiles.py
git commit -m "refactor(scout): source SCREEN/SYNTHESIS models from central registry"
```

---

### Task 5: `final_result` gains an `on_result` callback

**Files:** Modify `noc_cli/scout/llm_io.py`; Test `tests/test_scout_llm_io.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scout_llm_io.py`:
```python
import anyio

from noc_cli.scout.llm_io import final_result


def test_final_result_invokes_on_result_with_terminal_message():
    seen = []

    class Msg:
        def __init__(self, result=None):
            self.result = result

    async def gen():
        yield Msg(result=None)         # non-terminal
        yield Msg(result='{"ok":1}')    # terminal

    async def run():
        return await final_result(gen(), on_result=seen.append)

    raw = anyio.run(run)
    assert raw == '{"ok":1}'
    assert len(seen) == 1
    assert seen[0].result == '{"ok":1}'


def test_final_result_without_callback_still_returns_text():
    class Msg:
        result = '{"ok":2}'

    async def gen():
        yield Msg()

    assert anyio.run(lambda: final_result(gen())) == '{"ok":2}'
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scout_llm_io.py -k on_result -v`
Expected: FAIL — `final_result() got an unexpected keyword argument 'on_result'`.

- [ ] **Step 3: Implement**

In `noc_cli/scout/llm_io.py`, update `final_result`:
```python
async def final_result(query_gen, *, on_result=None) -> str:
    """Drain a query stream, keeping only the terminal ResultMessage text.

    If ``on_result`` is given, it is called with the terminal message (the one
    carrying ``result``) for best-effort usage logging. Return value unchanged.
    """
    raw = ""
    async for message in query_gen:
        text = getattr(message, "result", None)
        if text is not None:
            raw = text
            if on_result is not None:
                on_result(message)
    return raw
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scout_llm_io.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/llm_io.py tests/test_scout_llm_io.py
git commit -m "feat(scout): final_result optional on_result callback for usage logging"
```

---

### Task 6: Scout screen/synthesize log usage to the stable usage log

**Files:** Modify `noc_cli/scout/screen.py`, `noc_cli/scout/synthesize.py`; Test `tests/test_scout_synthesize.py`, `tests/test_scout_screen.py`.

**Context:** `screen_ticket` and `synthesize` each call `final_result(query_fn(prompt=..., options=options_factory()))`. Add a usage callback that writes to `data_dir() / "usage.jsonl"` with the matching surface + profile. Read each file first to match its exact call site.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scout_synthesize.py`:
```python
import json

from noc_cli.scout.synthesize import synthesize


def test_synthesize_logs_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))  # redirect data_dir() to tmp

    async def fake_query(*, prompt, options):
        class R:
            result = (
                '{"ranked":[{"ticket_id":1,"rank":1,"rationale":"x",'
                '"runbook_id":"low-audio","runbook_match_confidence":0.4,'
                '"missing_evidence":[]}]}'
            )
            usage = {"input_tokens": 1, "cache_read_input_tokens": 0}
            total_cost_usd = 0.0
            num_turns = 1
            session_id = "s"

        yield R()

    report = _run(
        synthesize(
            REPORTS,
            query_fn=fake_query,
            now=NOW,
            options_factory=lambda: None,
        )
    )

    assert report.ranked
    usage_log = tmp_path / "usage.jsonl"
    assert usage_log.exists()
    line = json.loads(usage_log.read_text().splitlines()[-1])
    assert line["surface"] == "scout_synth"
    assert line["model"] == "claude-opus-4-8"
```

The contract to assert is: a `scout_synth` usage line lands in `data_dir()/"usage.jsonl"`. Confirm `NOC_HOME` redirects `data_dir()` (it does — `config.data_dir()` honors `NOC_HOME`).

Add a matching assertion to `tests/test_scout_screen.py`:
```python
import json


def test_screen_ticket_logs_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))

    async def fake_query(*, prompt, options):
        class R:
            result = GOOD
            usage = {"input_tokens": 1, "cache_read_input_tokens": 0}
            total_cost_usd = 0.0
            num_turns = 1
            session_id = "s"

        yield R()

    candidate = Candidate(ticket_id=42, subject="audio dropouts")
    report = _run(
        screen_ticket(
            candidate,
            runbooks_dir=tmp_path / "runbooks",
            query_fn=fake_query,
            options_factory=lambda: None,
        )
    )

    assert report is not None
    usage_log = tmp_path / "usage.jsonl"
    line = json.loads(usage_log.read_text().splitlines()[-1])
    assert line["surface"] == "scout_screen"
    assert line["model"] == "claude-haiku-4-5"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scout_synthesize.py -k logs_usage -v`
Expected: FAIL — no `usage.jsonl` written.

- [ ] **Step 3: Implement**

In `noc_cli/scout/synthesize.py`, at the `final_result(...)` call (≈ line 81), thread the callback:
```python
from noc_cli.config import data_dir  # add to imports
from noc_cli.model_profiles import profile_for  # add to imports
from noc_cli.usage import log_usage  # add to imports

# ... inside synthesize(), replacing the existing final_result call:
    _profile = profile_for("scout_synth")
    raw = await final_result(
        query_fn(prompt=prompt, options=options_factory()),
        on_result=lambda m: log_usage(
            data_dir() / "usage.jsonl",
            surface="scout_synth", profile=_profile, result_message=m,
        ),
    )
```

In `noc_cli/scout/screen.py`, at its `final_result(...)` call (≈ line 59), do the same with `surface="scout_screen"`:
```python
from noc_cli.config import data_dir  # add to imports
from noc_cli.model_profiles import profile_for  # add to imports
from noc_cli.usage import log_usage  # add to imports

    _profile = profile_for("scout_screen")
    raw = await final_result(
        query_fn(prompt=prompt, options=options_factory()),
        on_result=lambda m: log_usage(
            data_dir() / "usage.jsonl",
            surface="scout_screen", profile=_profile, result_message=m,
        ),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scout_synthesize.py tests/test_scout_screen.py -v`
Expected: PASS — new usage test + all existing scout screen/synthesize tests (the callback is additive; JSON parsing/return unchanged).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/scout/screen.py noc_cli/scout/synthesize.py tests/test_scout_synthesize.py tests/test_scout_screen.py
git commit -m "feat(scout): log screen/synth usage to data-dir usage.jsonl"
```

---

### Task 7: Wire chat (chat.py) — model + usage, preserving abort-persistence

**Files:** Modify `noc_cli/tui/chat.py`; Test `tests/test_tui_chat.py`.

**Context:** `build_sdk_client_factory` builds the chat `ClaudeAgentOptions` (currently no model). The `ChatSession.send` generator drains the SDK client's `receive_response()`, persists the agent turn (even if the consumer aborts — there is an existing abort-persistence test), and yields reply lines. **Read `noc_cli/tui/chat.py` and the abort-persistence test in `tests/test_tui_chat.py` first** to place the changes precisely.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tui_chat.py`:
```python
def test_chat_options_use_sonnet_profile(tmp_path, monkeypatch):
    from noc_cli.tui.chat import build_sdk_client_factory

    # build_sdk_client_factory(folder) returns a no-arg factory producing a
    # ClaudeSDKClient; capture the options it was constructed with.
    captured = {}

    class FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

    import claude_agent_sdk

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)
    build_sdk_client_factory(tmp_path)()

    assert captured["options"].model == "claude-sonnet-4-6"
    assert captured["options"].effort == "medium"
```

Also add (or extend an existing test) to assert a `surface="chat"` usage line is written after a turn — reuse the file's existing fake-response harness — and **confirm the existing abort-persistence test still passes unchanged**.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_tui_chat.py -k sonnet_profile -v`
Expected: FAIL — `options.model` is `None`.

- [ ] **Step 3: Implement**

In `noc_cli/tui/chat.py`:

(a) Set the chat profile on the options in `build_sdk_client_factory`:
```python
        from noc_cli.model_profiles import profile_for  # noqa: PLC0415

        _profile = profile_for("chat")
        options = ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "LS"],
            permission_mode="bypassPermissions",
            cwd=str(folder),
            hooks=hooks,
            model=_profile.model,
            effort=_profile.effort,
        )
```

(b) In `ChatSession.send`, capture the terminal message during the drain (the message with a `result`/usage payload) into a local, and log usage in the same `finally` block that persists the agent turn. This preserves the existing abort-safe behavior because closing the generator after the first yielded agent line still runs `finally`.
```python
        from noc_cli.model_profiles import profile_for  # noqa: PLC0415
        from noc_cli.usage import log_usage  # noqa: PLC0415

        finally:
            self._append(ChatTurn(role="agent", text=reply, ts=_now()))
            if terminal_message is not None:
                log_usage(
                    self._folder / "events.jsonl",
                    surface="chat",
                    profile=profile_for("chat"),
                    result_message=terminal_message,
                )
```
**Implementer note:** chat is per-ticket, so log to the ticket's `events.jsonl` (the chat session already knows its folder — use that path; do not use `data_dir()` here). Keep the existing reply-yield loop inside the `try`; the shown `finally` replaces the current one-line append-only `finally`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_tui_chat.py -v`
Expected: PASS — new tests + the existing abort-persistence test, unchanged.

- [ ] **Step 5: Commit**

```bash
git add noc_cli/tui/chat.py tests/test_tui_chat.py
git commit -m "feat(tui): chat runs on Sonnet profile + usage logging (abort-safe)"
```

---

### Task 8: Full-suite regression + ruff

**Files:** none (verification).

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass (prior count + the new tests).

- [ ] **Step 2: Lint/format (ruff is on this branch's base)**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: `All checks passed!` and all files formatted. If `ruff check` flags anything in the new files, fix at the source (or run `uv run ruff check --fix .` for autofixable items) and re-run the suite.

- [ ] **Step 3: Commit any lint fixes**

```bash
git add -A && git commit -m "style(ruff): tidy SDK-tuning modules" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- Central registry, pinned + env (process→.env) + source + blank-ignored + unknown-raises → Task 1 ✔
- `log_usage` rich line (surface/attempt/model/fallback/effort/profile_source/profile_override_var/usage/model_usage/cost/turns/session), best-effort → Task 2 ✔
- Investigate Opus + fallback + effort + per-attempt usage → Task 3 ✔
- Scout models centralized, behavior unchanged → Task 4 ✔
- `final_result` on_result seam (return unchanged) → Task 5 ✔
- Scout screen/synth usage (stable `data_dir()/usage.jsonl`, since workspace is temp) → Task 6 ✔
- Chat Sonnet + effort + abort-safe usage → Task 7 ✔
- No live model calls in tests; full regression → Task 8 ✔
- Out-of-scope (thinking/budget/scout-model-change/prompt-content) → not touched ✔

**Placeholder scan:** No placeholder helper calls remain. Task 7 names the exact lazy-import monkeypatch target and the abort-safe `finally` placement. All production-code steps show complete code.

**Type/name consistency:** `ModelProfile(model, fallback_model, effort, source, override_var)` and `profile_for(surface)` are used identically in Tasks 1–7. `log_usage(events_path, *, surface, profile, result_message, attempt=None)` matches every call site (runner attempt=1/2; scout/chat omit attempt). `final_result(query_gen, *, on_result=None)` matches Tasks 5/6. Surface keys (`investigate`, `chat`, `scout_screen`, `scout_synth`) match the registry, the env-var names, and the usage `surface` field throughout.
