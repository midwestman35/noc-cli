import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeClient:
    """Stands in for ClaudeSDKClient: async-context, query, receive_response."""

    def __init__(self, reply="Held in queue; ALI link timed out."):
        self._reply = reply
        self.interrupted = False

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def query(self, prompt):
        self._last = prompt

    async def receive_response(self):
        class _Msg:
            def __init__(self, result):
                self.result = result

        yield _Msg(self._reply)

    async def interrupt(self):
        self.interrupted = True


async def test_send_persists_turns_and_returns_reply(tmp_path):
    from noc_cli.tui.chat import ChatSession

    folder = tmp_path / "45747"
    folder.mkdir()
    fake = _FakeClient()
    session = ChatSession(
        ticket_id=45747,
        folder=folder,
        client_factory=lambda: fake,
    )

    out = []
    async for line in session.send("call me at 555-123-4567 — why stuck?"):
        out.append(line)

    assert any("Held in queue" in line for line in out)
    convo = (folder / "CONVERSATION.jsonl").read_text().splitlines()
    assert len(convo) == 2  # one analyst turn + one agent turn
    analyst = json.loads(convo[0])
    assert analyst["role"] == "you"
    assert "<PHONE>" in analyst["text"]  # PII redacted at the boundary
    assert "555-123-4567" not in analyst["text"]
    agent = json.loads(convo[1])
    assert agent["role"] == "agent"
    assert "CONVERSATION.md" in [p.name for p in folder.iterdir()]


async def test_interrupt_delegates_to_client(tmp_path):
    from noc_cli.tui.chat import ChatSession

    folder = tmp_path / "1"
    folder.mkdir()
    fake = _FakeClient()
    session = ChatSession(ticket_id=1, folder=folder, client_factory=lambda: fake)
    async for _ in session.send("hi"):
        pass
    await session.interrupt()
    assert fake.interrupted is True


async def test_agent_turn_persisted_even_if_consumer_aborts(tmp_path):
    import json
    from noc_cli.tui.chat import ChatSession

    folder = tmp_path / "555"
    folder.mkdir()
    session = ChatSession(
        ticket_id=555, folder=folder, client_factory=lambda: _FakeClient()
    )

    gen = session.send("hi")
    await gen.__anext__()  # analyst echo ("you ❯ hi")
    await gen.__anext__()  # first agent line ("◆ …")
    await gen.aclose()  # consumer aborts mid-stream → GeneratorExit

    convo = (folder / "CONVERSATION.jsonl").read_text().splitlines()
    roles = [json.loads(line)["role"] for line in convo]
    assert roles == ["you", "agent"]  # both turns persisted despite the abort


async def test_last_user_turn_tracks_redacted_input(tmp_path):
    from noc_cli.tui.chat import ChatSession

    folder = tmp_path / "9"
    folder.mkdir()

    class _C:
        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def query(self, p):
            pass

        async def receive_response(self):
            class M:
                result = "ok"

            yield M()

        async def interrupt(self):
            pass

    s = ChatSession(ticket_id=9, folder=folder, client_factory=lambda: _C())
    async for _ in s.send("first question"):
        pass
    assert s.last_user_turn == "first question"
