import asyncio
from pathlib import Path

from noc_cli.agent.runner import RunnerResult, TranscriptEntry, run_agent
from noc_cli.models import ForkLetter
from noc_cli.scaffold import scaffold_ticket

FIXTURES = Path(__file__).parent / "fixtures"


def _run(coro):
    return asyncio.run(coro)


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _make_fake_query(result_json: str):
    """Return an async-generator query fn that yields one fake ResultMessage."""

    async def fake_query(*, prompt, options):
        class FakeResult:
            result = result_json
            is_error = False
            subtype = "success"

        yield FakeResult()

    return fake_query


def _make_multi_turn_query(result_json: str):
    """Yield assistant reasoning, a tool call, then the final result."""

    class _Text:
        def __init__(self, text):
            self.text = text

    class _ToolUse:
        def __init__(self, name, inp):
            self.name = name
            self.input = inp

    class _ToolResult:
        def __init__(self, content):
            self.content = content

    class _Assistant:
        def __init__(self, content):
            self.content = content

    class _Result:
        def __init__(self, result):
            self.result = result
            self.is_error = False

    async def fake_query(*, prompt, options):
        yield _Assistant([_Text("Reading the ticket and the low-audio runbook.")])
        yield _Assistant([_ToolUse("Read", {"file_path": "runbooks/low-audio.md"})])
        yield _Assistant([_ToolResult("low-audio runbook content " + ("x" * 1200))])
        yield _Result(result_json)

    return fake_query


def test_run_agent_parses_good_handoff(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    fake_q = _make_fake_query(_load_fixture("handoff_good.json"))
    result = _run(
        run_agent(
            ticket_id=18432, folder=folder, system_prompt="test",
            history_context="", _query_fn=fake_q,
        )
    )
    assert isinstance(result, RunnerResult)
    assert result.handoff is not None
    assert result.handoff.fork_packet.fork_letter is ForkLetter.B
    assert result.stash_path is None
    assert result.attempts == 1


def test_run_agent_parses_inconclusive_handoff(tmp_path):
    folder = scaffold_ticket(tmp_path, 18433)
    fake_q = _make_fake_query(_load_fixture("handoff_inconclusive.json"))
    result = _run(
        run_agent(
            ticket_id=18433, folder=folder, system_prompt="test",
            history_context="", _query_fn=fake_q,
        )
    )
    assert result.handoff is not None
    assert result.handoff.fork_packet.fork_letter is ForkLetter.D


def test_run_agent_retries_on_bad_json_then_stashes(tmp_path):
    folder = scaffold_ticket(tmp_path, 18434)
    fake_q = _make_fake_query(_load_fixture("handoff_bad.json"))
    result = _run(
        run_agent(
            ticket_id=18434, folder=folder, system_prompt="test",
            history_context="", _query_fn=fake_q,
        )
    )
    assert result.handoff is None
    assert result.stash_path is not None
    assert result.stash_path.exists()
    assert len(result.stash_path.read_text()) > 0
    assert result.attempts == 2
    content = result.stash_path.read_text()
    assert "# Attempt 1" in content
    assert "# Attempt 2" in content


def test_stash_written_to_debug_subdir(tmp_path):
    folder = scaffold_ticket(tmp_path, 18435)
    fake_q = _make_fake_query('{"totally": "wrong"}')
    result = _run(
        run_agent(
            ticket_id=18435, folder=folder, system_prompt="test",
            history_context="", _query_fn=fake_q,
        )
    )
    assert result.stash_path is not None
    assert ".debug" in str(result.stash_path)


def test_retry_uses_correction_prompt(tmp_path):
    folder = scaffold_ticket(tmp_path, 18436)
    prompts_seen: list[str] = []

    async def capturing_query(*, prompt, options):
        prompts_seen.append(prompt)

        class R:
            result = '{"still": "bad"}'
            is_error = False

        yield R()

    result = _run(
        run_agent(
            ticket_id=18436, folder=folder, system_prompt="test",
            history_context="", _query_fn=capturing_query,
        )
    )
    assert result.handoff is None
    assert len(prompts_seen) == 2  # initial attempt + one retry
    assert "could not be parsed" in prompts_seen[1]  # 2nd call is the correction prompt
    assert "Triage ticket #18436" in prompts_seen[0]  # 1st call is the initial prompt


def test_run_agent_captures_transcript(tmp_path):
    folder = scaffold_ticket(tmp_path, 18440)
    query = _make_multi_turn_query(_load_fixture("handoff_good.json"))
    result = _run(
        run_agent(
            ticket_id=18440,
            folder=folder,
            system_prompt="test",
            history_context="",
            _query_fn=query,
        )
    )

    assert result.handoff is not None
    assert all(isinstance(entry, TranscriptEntry) for entry in result.transcript)
    kinds = [entry.kind for entry in result.transcript]
    assert "reasoning" in kinds
    assert "tool" in kinds
    assert "tool_result" in kinds
    tool = next(entry for entry in result.transcript if entry.kind == "tool")
    assert tool.tool_name == "Read"
    assert "low-audio" in tool.tool_args
    tool_result = next(entry for entry in result.transcript if entry.kind == "tool_result")
    assert "low-audio runbook content" in tool_result.text
    assert len(tool_result.text) <= 1000


def test_successful_run_stashes_debug_transcript(tmp_path):
    folder = scaffold_ticket(tmp_path, 18439)
    query = _make_multi_turn_query(_load_fixture("handoff_good.json"))
    result = _run(
        run_agent(
            ticket_id=18439,
            folder=folder,
            system_prompt="test",
            history_context="",
            _query_fn=query,
        )
    )

    assert result.handoff is not None
    transcripts = list((folder.root / ".debug").glob("transcript-*.jsonl"))
    assert transcripts
    content = transcripts[0].read_text(encoding="utf-8")
    assert "low-audio" in content
    assert "Reading the ticket" in content


def test_debug_transcript_keeps_untruncated_result_and_tool_result(tmp_path):
    folder = scaffold_ticket(tmp_path, 18438)
    marker = "FULL_RESULT_MARKER_" + ("r" * 4500)
    tool_marker = "FULL_TOOL_MARKER_" + ("t" * 1200)

    class _ToolResult:
        def __init__(self, content):
            self.content = content

    class _Assistant:
        def __init__(self, content):
            self.content = content

    class _Result:
        result = _load_fixture("handoff_good.json") + marker
        is_error = False

    async def query(*, prompt, options):
        yield _Assistant([_ToolResult(tool_marker)])
        yield _Result()

    result = _run(
        run_agent(
            ticket_id=18438,
            folder=folder,
            system_prompt="test",
            history_context="",
            _query_fn=query,
        )
    )

    assert result.handoff is not None
    transcript_path = next((folder.root / ".debug").glob("transcript-*.jsonl"))
    content = transcript_path.read_text(encoding="utf-8")
    assert marker in content
    assert tool_marker in content


def test_run_agent_weaves_initial_hypothesis_into_prompt(tmp_path):
    folder = scaffold_ticket(tmp_path, 18441)
    prompts_seen: list[str] = []

    async def capturing_query(*, prompt, options):
        prompts_seen.append(prompt)

        class R:
            result = _load_fixture("handoff_good.json")
            is_error = False

        yield R()

    _run(
        run_agent(
            ticket_id=18441,
            folder=folder,
            system_prompt="test",
            history_context="",
            initial_hypothesis="[low audio]",
            _query_fn=capturing_query,
        )
    )

    assert "Triage ticket #18441" in prompts_seen[0]
    assert "[low audio]" in prompts_seen[0]
    assert "starting point, not a verdict" in prompts_seen[0]


def test_transcript_stashed_on_double_failure(tmp_path):
    folder = scaffold_ticket(tmp_path, 18442)
    query = _make_multi_turn_query('{"totally": "wrong"}')
    result = _run(
        run_agent(
            ticket_id=18442,
            folder=folder,
            system_prompt="test",
            history_context="",
            _query_fn=query,
        )
    )

    assert result.handoff is None
    stash_dir = folder.root / ".debug"
    transcripts = list(stash_dir.glob("transcript-*.jsonl"))
    assert transcripts
    content = transcripts[0].read_text(encoding="utf-8").strip()
    assert content
    assert "low-audio" in content


def test_run_agent_passes_mcp_servers_and_allowed_tools(tmp_path):
    from noc_cli.config import Config
    from noc_cli.memory import MemoryStore

    folder = scaffold_ticket(tmp_path, 761)
    store = MemoryStore(db_path=tmp_path / "m.db", memory_md_path=tmp_path / "MEMORY.md")
    store.init()
    config = Config(zendesk_subdomain="acme", zendesk_email="a@b.co", zendesk_api_token="tok")

    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        if False:
            yield  # make this an async generator that yields nothing

    _run(
        run_agent(
            ticket_id=761, folder=folder, system_prompt="sys", history_context="",
            config=config, memory_store=store, _query_fn=fake_query,
        )
    )
    opts = captured["options"]
    assert set(opts.mcp_servers) == {"zendesk", "history"}
    for name in (
        "mcp__zendesk__get_ticket", "mcp__zendesk__get_comments",
        "mcp__zendesk__search", "mcp__history__search_history",
    ):
        assert name in opts.allowed_tools
