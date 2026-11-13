#!/usr/bin/env python3
"""Functional verification of the whole local stack.

Probes every server, MCP server and ACP client with a real request instead of
a port check, so a listener that cannot answer still fails.

Usage:
    python scripts/stack_verify.py
    OV_GW_MODEL=gemini-3.8-flash python scripts/stack_verify.py

Exit code 0 when every probe passes, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

OV = os.environ.get("OV_URL", "http://127.0.0.1:1933")
DSH = os.environ.get("DSH_URL", "http://127.0.0.1:3090")
BOT = os.environ.get("BOT_URL", "http://127.0.0.1:18790")
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
GW_MODEL = os.environ.get("OV_GW_MODEL", "gemini-3.8-flash")
AGY = os.path.expanduser("~/.local/bin/agy")
REASONIX = os.path.expanduser("~/.local/bin/reasonix")
KEY_FILE = pathlib.Path.home() / ".openviking" / "gateway.key"

MCP_INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "stack-verify", "version": "1.0"},
        },
    }
)
ACP_INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": 1, "clientCapabilities": {}},
    }
)

results: list[tuple[bool, str, str]] = []


def record(ok: bool, name: str, detail: str) -> None:
    results.append((ok, name, detail))


def http_json(url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 40):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check_http(name: str, url: str) -> None:
    try:
        http_json(url)
        record(True, name, url)
    except Exception as error:
        record(False, name, f"{url} ({type(error).__name__})")


def check_gateway() -> None:
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        record(False, "gateway key", f"missing {KEY_FILE}")
        return
    headers = {"Authorization": f"Bearer {key}"}
    try:
        models = [m["id"] for m in http_json(f"{OV}/gateway/v1/models", headers=headers)["data"]]
        record(True, "gateway models", ", ".join(models))
    except Exception as error:
        record(False, "gateway models", type(error).__name__)
        return
    try:
        body = {"model": GW_MODEL, "messages": [{"role": "user", "content": "Reply with exactly: OK"}]}
        reply = http_json(
            f"{OV}/gateway/v1/chat/completions",
            payload=body,
            headers={**headers, "Content-Type": "application/json"},
        )
        record(True, "gateway chat", f"{GW_MODEL} -> {reply['choices'][0]['message']['content'][:40]}")
    except Exception as error:
        record(False, "gateway chat", f"{GW_MODEL} ({type(error).__name__})")


def check_openviking_mcp() -> None:
    """The streamable-HTTP MCP endpoint answers with SSE frames, not raw JSON."""
    try:
        body = json.loads(MCP_INITIALIZE)
        request = urllib.request.Request(
            f"{OV}/mcp",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
        payload = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                break
        if payload is None:
            payload = json.loads(raw)
        info = payload.get("result", {}).get("serverInfo", {})
        record(True, "OpenViking MCP", f"{info.get('name', 'ok')} {info.get('version', '')}".strip())
    except Exception as error:
        record(False, "OpenViking MCP", type(error).__name__)


def check_stdio(name: str, command: list[str], cwd: str, request: str) -> None:
    try:
        completed = subprocess.run(
            command, cwd=cwd, input=request + "\n", capture_output=True, text=True, timeout=40
        )
        line = next((l for l in completed.stdout.splitlines() if '"result"' in l), "")
        if not line:
            record(False, name, "no result")
            return
        info = json.loads(line)["result"].get("serverInfo") or json.loads(line)["result"].get("agentInfo", {})
        record(True, name, f"{info.get('name', 'ok')} {info.get('version', '')}".strip())
    except Exception as error:
        record(False, name, type(error).__name__)


def check_agy_mcp() -> None:
    try:
        completed = subprocess.run([AGY, "mcp", "list"], capture_output=True, text=True, timeout=30)
        line = next((l for l in completed.stdout.splitlines() if l.startswith("openviking")), "")
        if line:
            record(True, "agy -> OpenViking MCP", " ".join(line.split()[2:]))
        else:
            record(False, "agy -> OpenViking MCP", "not registered")
    except Exception as error:
        record(False, "agy -> OpenViking MCP", type(error).__name__)


def main() -> int:
    studio = os.environ.get("REASONIX_STUDIO", "/Users/gj/DeepSeek-Reasonix-studio")

    check_http("OpenViking", f"{OV}/health")
    check_http("VikingBot", f"{BOT}/health")
    check_http("Ollama", f"{OLLAMA}/api/tags")
    try:
        urllib.request.urlopen(DSH, timeout=5)
        record(True, "DSH web", DSH)
    except urllib.error.HTTPError as error:
        record(error.code in (200, 401), "DSH web", f"{DSH} (HTTP {error.code})")
    except Exception as error:
        record(False, "DSH web", type(error).__name__)

    check_gateway()
    check_openviking_mcp()
    check_stdio(
        "MCP gemini-api",
        ["node", f"{studio}/scripts/gemini-api-server.mjs"],
        studio,
        MCP_INITIALIZE,
    )
    check_stdio(
        "MCP rhex-research",
        ["node", f"{studio}/research-mcp/src/server.mjs"],
        f"{studio}/research-mcp",
        MCP_INITIALIZE,
    )
    check_stdio("ACP reasonix", [REASONIX, "acp"], os.getcwd(), ACP_INITIALIZE)
    check_stdio("ACP agy", [AGY, "acp", "--disable-telemetry"], os.getcwd(), ACP_INITIALIZE)
    check_agy_mcp()

    width = max(len(name) for _, name, _ in results)
    failed = 0
    for ok, name, detail in results:
        if not ok:
            failed += 1
        print(f"  {'UP  ' if ok else 'DOWN'}  {name:<{width}}  {detail}")
    print()
    print(f"{len(results) - failed}/{len(results)} probes passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
