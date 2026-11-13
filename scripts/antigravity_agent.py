#!/usr/bin/env python3
"""Run a Google Antigravity SDK agent wired to the local OpenViking MCP server.

The agent uses the Gemini API key from ``GEMINI_API_KEY`` (or ``--api-key``) and
talks to OpenViking over its streamable-HTTP MCP endpoint, so every answer can
be grounded in the user's own memory.

Examples:
    python scripts/antigravity_agent.py "What is my local AI stack?"
    python scripts/antigravity_agent.py --model gemini-3.8-flash-low "List my skills"
    python scripts/antigravity_agent.py --no-mcp "What is 2+2?"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

DEFAULT_MCP_URL = "http://127.0.0.1:1933/mcp"
DEFAULT_INSTRUCTIONS = (
    "You are a local AI stack assistant. Use the openviking MCP tools "
    "(find, search, read, list) to answer from the user's own memory. "
    "Cite the viking:// URI you used. Be terse."
)


def _load_api_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    # Fall back to the shell profile the Antigravity installer wrote to.
    for profile in (".zshrc", ".bashrc", ".profile"):
        path = Path.home() / profile
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("export GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(
        "GEMINI_API_KEY is not set. Export it or pass --api-key."
    )


def _build_config(args, api_key: str):
    from google.antigravity import LocalAgentConfig
    from google.antigravity.types import McpStreamableHttpServer

    kwargs = {
        "system_instructions": args.instructions,
    }
    if args.model:
        kwargs["model"] = args.model
    if not args.no_mcp:
        kwargs["mcp_servers"] = [
            McpStreamableHttpServer(name="openviking", url=args.mcp_url)
        ]
    return LocalAgentConfig(**kwargs)


async def _run(args, api_key: str) -> int:
    os.environ["GEMINI_API_KEY"] = api_key
    from google.antigravity import Agent

    config = _build_config(args, api_key)
    async with Agent(config) as agent:
        response = await agent.chat(args.prompt)
        print(await response.text())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Prompt to send to the agent")
    parser.add_argument("--model", default=None, help="Antigravity model id")
    parser.add_argument("--api-key", default=None, help="Gemini API key override")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL, help="OpenViking MCP URL")
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Do not attach the OpenViking MCP server",
    )
    parser.add_argument(
        "--instructions",
        default=DEFAULT_INSTRUCTIONS,
        help="System instructions for the agent",
    )
    args = parser.parse_args()

    api_key = _load_api_key(args.api_key)
    return asyncio.run(_run(args, api_key))


if __name__ == "__main__":
    sys.exit(main())
