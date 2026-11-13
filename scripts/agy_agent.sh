#!/usr/bin/env bash
# Run the Antigravity CLI (`agy`) as an agent wired to the local OpenViking MCP.
#
# Usage:
#   scripts/agy_agent.sh "What is my local AI stack?"
#   scripts/agy_agent.sh --model gemini-3.8-flash-low "List my skills"
#   AGY_MODEL=gemini-3.8-flash-high scripts/agy_agent.sh "Audit the repo"
#
# Environment:
#   AGY_BIN        Path to the agy binary (default: ~/.local/bin/agy)
#   AGY_MODEL      Model id (default: gemini-3.8-flash-low)
#   OV_MCP_URL     OpenViking MCP URL (default: http://127.0.0.1:1933/mcp)
#   AGY_MCP_NAME   MCP server name registered in agy (default: openviking)
set -euo pipefail

AGY_BIN="${AGY_BIN:-$HOME/.local/bin/agy}"
AGY_MODEL="${AGY_MODEL:-gemini-3.8-flash-low}"
OV_MCP_URL="${OV_MCP_URL:-http://127.0.0.1:1933/mcp}"
AGY_MCP_NAME="${AGY_MCP_NAME:-openviking}"

if [[ ! -x "$AGY_BIN" ]]; then
  echo "agy not found at $AGY_BIN" >&2
  echo "Install it with: curl -fsSL https://antigravity.google/cli/install.sh | bash" >&2
  exit 1
fi

# Register the OpenViking MCP server once; `agy mcp add` is idempotent.
if ! "$AGY_BIN" mcp list 2>/dev/null | awk '{print $1}' | grep -qx "$AGY_MCP_NAME"; then
  "$AGY_BIN" mcp add "$AGY_MCP_NAME" "$OV_MCP_URL" >/dev/null
fi

exec "$AGY_BIN" -p "$*" \
  --model "$AGY_MODEL" \
  --dangerously-skip-permissions
