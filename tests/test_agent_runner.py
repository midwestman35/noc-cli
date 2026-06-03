import asyncio
from pathlib import Path

from noc_cli.agent.runner import RunnerResult, run_agent
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
