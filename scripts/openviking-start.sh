#!/usr/bin/env bash
# openviking-start — start the whole OpenViking local development stack.
#
# One command, default ports, everything on loopback:
#
#   OpenViking API + VikingBot   http://127.0.0.1:1933   (/health)
#   DeepSeek Harness Web         http://127.0.0.1:3090
#   Web Studio                   http://127.0.0.1:3000
#   React library docs           http://127.0.0.1:3002/ui/
#   OpenViking product docs      http://127.0.0.1:5173
#   OpenWebUI tools              http://127.0.0.1:8765
#
# The stack runs in the foreground and supervises every child, so Ctrl+C stops
# all of them. Builds and watchers are started by scripts/dev-all.mjs; this
# wrapper only locates the checkout and guarantees a working npm/node.
#
# Usage:
#   openviking-start               start the full stack
#   openviking-start --plan        print the start plan, start nothing
#   openviking-start --help        show the launcher's own help
#
# Environment:
#   OV_DIR      OpenViking checkout (default: this script's checkout)
#   OV_HOST     bind host (default 127.0.0.1)
#   OV_PORT     OpenViking API port (default 1933)
#   BOT_PORT    VikingBot port (default 18790)
#   DSH_DIR     DeepSeek Harness checkout (default ~/git/deepseek-harness)
#   DSH_PORT    DSH Web port (default 3090)
#   DOCS_PORT   product docs port (default 5173)
#   OPENWEBUI_PORT  OpenWebUI tools port (default 8765)

set -euo pipefail

# Resolve the checkout: prefer an explicit OV_DIR, else the repo this script
# lives in, else the conventional location.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ -n "${OV_DIR:-}" ]]; then
  OV_DIR="$OV_DIR"
elif [[ -f "$DEFAULT_DIR/scripts/dev-all.mjs" ]]; then
  OV_DIR="$DEFAULT_DIR"
else
  OV_DIR="$HOME/git/OpenViking-main"
fi

if [[ ! -d "$OV_DIR" ]]; then
  echo "openviking-start: OpenViking checkout not found at $OV_DIR" >&2
  echo "Set OV_DIR to the correct path, e.g. OV_DIR=/path/to/OpenViking openviking-start" >&2
  exit 1
fi

if [[ ! -f "$OV_DIR/scripts/dev-all.mjs" ]]; then
  echo "openviking-start: $OV_DIR/scripts/dev-all.mjs is missing" >&2
  echo "The checkout may be outdated; run: git -C \"$OV_DIR\" fetch origin dev && git -C \"$OV_DIR\" checkout dev && git -C \"$OV_DIR\" pull" >&2
  exit 1
fi

cd "$OV_DIR"

# npm from mise/nvm is not guaranteed on a non-interactive PATH; fall back to the
# known shim directories only when npm is genuinely absent.
if ! command -v npm >/dev/null 2>&1; then
  for candidate in "$HOME/.local/share/mise/shims" "/opt/homebrew/bin" "/usr/local/bin"; do
    if [[ -x "$candidate/npm" ]]; then
      PATH="$candidate:$PATH"
      break
    fi
  done
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "openviking-start: npm not found on PATH" >&2
  exit 1
fi

# The supervisor needs a usable Python venv for the OpenViking server.
if [[ ! -x "$OV_DIR/.venv/bin/openviking-server" ]]; then
  echo "openviking-start: $OV_DIR/.venv/bin/openviking-server not found" >&2
  echo "Create the environment and build first: just setup && just build" >&2
  exit 1
fi

if [[ ! -f "$OV_DIR/ov.conf" ]]; then
  echo "openviking-start: $OV_DIR/ov.conf not found" >&2
  echo "Generate it first: just ov-config-init" >&2
  exit 1
fi

exec npm run dev -- "$@"
