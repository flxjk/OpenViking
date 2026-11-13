#!/usr/bin/env python3
"""Run an Antigravity SDK agent with OpenViking long-term memory over MCP.

The agent talks to the local OpenViking server's streamable-HTTP MCP endpoint,
so it can search, read, and write memories while it works.

Usage:
    python scripts/antigravity_openviking_agent.py "your task"
    OPENVIKING_MCP_URL=http://127.0.0.1:1933/mcp python scripts/antigravity_openviking_agent.py "list my memories"
"""

from __future__ import annotations

import asyncio
import os
import sys

from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.types import McpStreamableHttpServer

DEFAULT_MCP_URL = "http://127.0.0.1:1933/mcp"
DEFAULT_PROMPT = "List my OpenViking memories under viking://user/default/memories."

SYSTEM_INSTRUCTIONS = (
    "You are an Antigravity agent with access to OpenViking long-term memory. "
    "Use the openviking MCP tools (find, search, read, write, remember) to recall "
    "relevant context before acting and to persist durable facts after acting."
)


def build_config() -> LocalAgentConfig:
    """Build an agent config wired to the local OpenViking MCP server."""
    mcp_url = os.environ.get("OPENVIKING_MCP_URL", DEFAULT_MCP_URL)
    return LocalAgentConfig(
        system_instructions=SYSTEM_INSTRUCTIONS,
        mcp_servers=[McpStreamableHttpServer(name="openviking", url=mcp_url)],
        workspaces=[os.getcwd()],
    )


async def run(prompt: str) -> int:
    """Run one agent turn and print the final text."""
    async with Agent(build_config()) as agent:
        response = await agent.chat(prompt)
        print(await response.text())
    return 0


def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip() or DEFAULT_PROMPT
    return asyncio.run(run(prompt))


if __name__ == "__main__":
    raise SystemExit(main())
