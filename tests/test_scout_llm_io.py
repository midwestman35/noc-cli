import asyncio

from noc_cli.scout.llm_io import extract_json, final_result


def _run(coro):
    return asyncio.run(coro)


def test_extract_json_unwraps_fenced_block():
    raw = 'prose\n```json\n{"a": 1}\n```\ntrailing'
    assert extract_json(raw) == '{"a": 1}'


def test_extract_json_falls_back_to_brace_span():
    raw = 'here is the object {"a": 1, "b": 2} and more text'
    assert extract_json(raw) == '{"a": 1, "b": 2}'


def test_extract_json_returns_stripped_when_no_object():
    assert extract_json("  not json  ") == "not json"


def test_final_result_keeps_last_result_text():
    class _Msg:
        def __init__(self, result=None):
            self.result = result

    async def gen():
        yield _Msg()  # no result attr value
        yield _Msg("first")
        yield _Msg("final")

    assert _run(final_result(gen())) == "final"


def test_final_result_invokes_callback_with_terminal_message():
    class _Msg:
        def __init__(self, result=None):
            self.result = result

    first = _Msg("first")
    terminal = _Msg("final")
    seen = []

    async def gen():
        yield _Msg()
        yield first
        yield terminal

    assert _run(final_result(gen(), on_result=seen.append)) == "final"
    assert seen == [terminal]


def test_final_result_preserves_behavior_without_callback():
    class _Msg:
        def __init__(self, result=None):
            self.result = result

    async def gen():
        yield _Msg("first")
        yield _Msg("final")

    assert _run(final_result(gen())) == "final"
