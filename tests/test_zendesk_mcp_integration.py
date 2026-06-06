import sys

import anyio
import pytest

pytestmark = pytest.mark.integration


def test_spawned_server_lists_three_read_tools():
    """Spawn the stdio server as a subprocess and list its tools over MCP.

    Never touches live Zendesk (we only list tools; no creds needed to enumerate).
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "noc_cli.mcp.zendesk_server"],
        env={"ZENDESK_SUBDOMAIN": "x", "ZENDESK_EMAIL": "x@x.co", "ZENDESK_API_TOKEN": "x"},
    )

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return sorted(t.name for t in result.tools)

    names = anyio.run(_run)
    assert names == ["get_comments", "get_ticket", "search"]
