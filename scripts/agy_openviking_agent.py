#!/usr/bin/env python3
"""Run the Antigravity CLI (`agy`) as an agent with OpenViking memory.

This path uses the `agy` CLI's OAuth login, so it does not need a Gemini API
key. The OpenViking MCP server is registered in `agy` once with:

    agy mcp add --type http openviking http://127.0.0.1:1933/mcp

Usage:
    python scripts/agy_openviking_agent.py "your task"
    python scripts/agy_openviking_agent.py --model gemini-3.8-flash-high "your task"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

AGY = os.environ.get("AGY_BIN") or shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
OPENVIKING_HEALTH = os.environ.get("OPENVIKING_HEALTH_URL", "http://127.0.0.1:1933/health")
DEFAULT_PROMPT = "List my OpenViking memories under viking://user/default/memories."


def openviking_healthy() -> bool:
    """Return True when the local OpenViking server answers its health probe."""
    try:
        with urllib.request.urlopen(OPENVIKING_HEALTH, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("healthy"))
    except Exception:
        return False


def run_agent(prompt: str, model: str | None, timeout: str) -> int:
    """Run one non-interactive `agy` turn and stream its output."""
    command = [AGY, "-p", prompt, "--print-timeout", timeout]
    if model:
        command.extend(["--model", model])
    completed = subprocess.run(command, text=True)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="*", help="Task for the agent")
    parser.add_argument("--model", default=None, help="Antigravity model id, e.g. gemini-3.8-flash-high")
    parser.add_argument("--timeout", default="300s", help="Print-mode timeout, e.g. 300s")
    args = parser.parse_args()

    if not openviking_healthy():
        print(
            f"OpenViking is not healthy at {OPENVIKING_HEALTH}. "
            "Start it with: openviking-server --config /tmp/ov-run.conf",
            file=sys.stderr,
        )
        return 2

    prompt = " ".join(args.prompt).strip() or DEFAULT_PROMPT
    return run_agent(prompt, args.model, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
