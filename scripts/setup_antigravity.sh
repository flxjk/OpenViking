#!/usr/bin/env bash
# Install and wire the Google Antigravity CLI + SDK to the local OpenViking MCP.
#
# What it does:
#   1. Installs the `agy` CLI if missing.
#   2. Installs the `google-antigravity` Python SDK into the project venv.
#   3. Registers the OpenViking MCP server with `agy`.
#   4. Verifies the Gemini API key and the OpenViking MCP endpoint.
#
# Usage:
#   scripts/setup_antigravity.sh
#   OV_MCP_URL=http://127.0.0.1:1933/mcp scripts/setup_antigravity.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGY_BIN="${AGY_BIN:-$HOME/.local/bin/agy}"
OV_MCP_URL="${OV_MCP_URL:-http://127.0.0.1:1933/mcp}"
AGY_MCP_NAME="${AGY_MCP_NAME:-openviking}"
VENV_PY="${VENV_PY:-$REPO_ROOT/.venv/bin/python}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }

# 1. agy CLI
if [[ -x "$AGY_BIN" ]]; then
  say "agy already installed: $("$AGY_BIN" --version 2>/dev/null || echo unknown)"
else
  say "installing agy CLI"
  curl -fsSL https://antigravity.google/cli/install.sh | bash
fi

# 2. Python SDK
if [[ -x "$VENV_PY" ]]; then
  say "installing google-antigravity into $VENV_PY"
  UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" uv pip install --python "$VENV_PY" google-antigravity
else
  warn "no venv at $VENV_PY; install the SDK manually: pip install google-antigravity"
fi

# 3. OpenViking MCP registration
if "$AGY_BIN" mcp list 2>/dev/null | awk '{print $1}' | grep -qx "$AGY_MCP_NAME"; then
  say "MCP server '$AGY_MCP_NAME' already registered"
else
  say "registering MCP server '$AGY_MCP_NAME' -> $OV_MCP_URL"
  "$AGY_BIN" mcp add "$AGY_MCP_NAME" "$OV_MCP_URL"
fi

# 4. Verification
say "checking Gemini API key"
if [[ -n "${GEMINI_API_KEY:-}" ]]; then
  echo "    GEMINI_API_KEY is set (${#GEMINI_API_KEY} chars)"
else
  warn "GEMINI_API_KEY is not set in this shell; the SDK reads it from the environment"
fi

say "checking OpenViking MCP endpoint"
if curl -fsS -o /dev/null -X POST "$OV_MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"setup","version":"1"}}}'; then
  echo "    OpenViking MCP is reachable"
else
  warn "OpenViking MCP is not reachable at $OV_MCP_URL"
fi

say "done"
echo "    CLI agent:  scripts/agy_agent.sh \"What is my local AI stack?\""
echo "    SDK agent:  $VENV_PY scripts/antigravity_agent.py \"What is my local AI stack?\""
