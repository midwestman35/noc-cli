"""Per-ticket chat session over ClaudeSDKClient (spec §4.5).

Mirrors agent/runner.py: the SDK client is built by an injectable factory so
tests pass a fake; production lazily builds a real ClaudeSDKClient.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from noc_cli.redact import redact


@dataclass
class ChatTurn:
    role: str  # "you" | "agent"
    text: str
    ts: str


class ChatSession:
    """A resumable conversation about one ticket, persisted to the ticket folder."""

    def __init__(
        self,
        *,
        ticket_id: int,
        folder: Path,
        client_factory: Callable,
        redact_fn: Callable[[str], tuple[str, object]] = redact,
    ) -> None:
        self._ticket_id = ticket_id
        self._folder = Path(folder)
        self._client_factory = client_factory
        self._redact = redact_fn
        self._client = None
        self._turns: list[ChatTurn] = []

    @property
    def transcript(self) -> list[ChatTurn]:
        return list(self._turns)

    @property
    def last_user_turn(self) -> str:
        for turn in reversed(self._turns):
            if turn.role == "you":
                return turn.text
        return ""

    async def _ensure_client(self):
        if self._client is None:
            client = self._client_factory()
            await client.connect()
            self._client = client
        return self._client

    async def send(self, text: str) -> AsyncGenerator[str, None]:
        """Send one analyst turn; yield agent output lines. Persists both turns."""
        from noc_cli.model_profiles import profile_for  # noqa: PLC0415
        from noc_cli.usage import log_usage  # noqa: PLC0415

        redacted, _counts = self._redact(text)
        self._append(ChatTurn(role="you", text=redacted, ts=_now()))
        yield f"you ❯ {redacted}"

        client = await self._ensure_client()
        await client.query(redacted)
        reply = ""
        terminal_message = None
        async for message in client.receive_response():
            result = getattr(message, "result", None)
            if result is not None:
                reply = str(result)
                terminal_message = message
        try:
            lines = (reply or "(no response)").splitlines()
            for line in lines or ["(no response)"]:
                yield f"◆ {line}"
        finally:
            self._append(ChatTurn(role="agent", text=reply, ts=_now()))
            if terminal_message is not None:
                log_usage(
                    self._folder / "events.jsonl",
                    surface="chat",
                    profile=profile_for("chat"),
                    result_message=terminal_message,
                )

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    def _append(self, turn: ChatTurn) -> None:
        self._turns.append(turn)
        with (self._folder / "CONVERSATION.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn)) + "\n")
        self._render_md()

    def _render_md(self) -> None:
        # Derived read artefact — CONVERSATION.jsonl is the source of truth.
        lines = [f"# Conversation — ZD-{self._ticket_id}", ""]
        for turn in self._turns:
            who = "You" if turn.role == "you" else "Agent"
            lines += [f"**{who}** · {turn.ts}", "", turn.text, ""]
        (self._folder / "CONVERSATION.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_sdk_client_factory(folder: Path) -> Callable:
    """Production factory: a ClaudeSDKClient bound to the ticket sandbox with the
    read-only hooks. The returned callable takes no args and returns a client."""

    def _factory():
        from claude_agent_sdk import (  # noqa: PLC0415
            ClaudeAgentOptions,
            ClaudeSDKClient,
        )

        from noc_cli.agent.harness import build_hooks  # noqa: PLC0415
        from noc_cli.model_profiles import profile_for  # noqa: PLC0415

        _profile = profile_for("chat")
        hooks = build_hooks(sandbox_root=folder, events_path=folder / "events.jsonl")
        options = ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "LS"],
            permission_mode="bypassPermissions",
            cwd=str(folder),
            hooks=hooks,
            model=_profile.model,
            effort=_profile.effort,
        )
        return ClaudeSDKClient(options=options)

    return _factory
