"""Start the real server as an MCP client would, and talk to it over stdio."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from arma3_wiki_mcp import __version__

from .conftest import write_wiki

ROOT = Path(__file__).resolve().parents[1]


async def call_tools(data: Path, command: list[str]) -> tuple[list[str], str]:
    params = StdioServerParameters(
        command=command[0], args=command[1:], env={**os.environ, "ARMA3_WIKI_DATA": str(data)}
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = [t.name for t in (await session.list_tools()).tools]
        result = await session.call_tool("lookup_command", {"name": "setdamage"})
        return tools, result.content[0].text


def run(coro, timeout: float):
    return asyncio.run(asyncio.wait_for(coro, timeout))


@pytest.fixture
def cached_wiki(tmp_path: Path) -> Path:
    data = write_wiki(tmp_path / "wiki")
    (data / ".last_refresh").touch()  # fresh, so the server makes no network calls
    return data


@pytest.mark.parametrize(
    "command",
    [[sys.executable, "-m", "arma3_wiki_mcp"], [sys.executable, str(ROOT / "server.py")]],
    ids=["package", "checkout-shim"],
)
def test_stdio_round_trip(cached_wiki, command):
    tools, text = run(call_tools(cached_wiki, command), 60)
    assert {"lookup_command", "lookup_event", "search", "refresh_wiki"} <= set(tools)
    assert "obj setDamage value" in text


def test_version_flag():
    out = subprocess.run([sys.executable, "-m", "arma3_wiki_mcp", "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == f"arma3-wiki-mcp {__version__}"


@pytest.mark.network
def test_real_wiki_download(tmp_path):
    """Download the real data from GitHub and check a well-known command."""
    data = tmp_path / "wiki"
    tools, text = run(call_tools(data, [sys.executable, "-m", "arma3_wiki_mcp"]), 600)
    assert "lookup_command" in tools
    assert "setDamage" in text and "syntax:" in text
    assert len(list((data / "commands").glob("*.yml"))) > 2000
