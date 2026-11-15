#!/usr/bin/env bash
# Run local stdio -> Streamable HTTP bridges for external MCP servers.
#
# Why this exists
# ---------------
# OpenViking can only consume **Streamable HTTP** MCP upstreams: its request model
# (`openviking/server/routers/mcps.py`, AddMCPRequest) accepts nothing but
# {name, url, headers}, with extra="forbid" -- there is no stdio transport and no
# command/args field.  See docs/en/guides/24-live-context-arrangement.md:
# "Only Streamable HTTP MCP servers are supported."
#
# The useful community MCP servers (filesystem, fetch, git, memory) are stdio-only
# programs.  `mcp-proxy` (PyPI) wraps one stdio server and re-exposes it over
# Streamable HTTP, which is exactly the transport OpenViking needs.
#
# Servers exposed (none require an API key)
# -----------------------------------------
#   filesystem  8801  npx @modelcontextprotocol/server-filesystem   (npm)
#   fetch       8802  uvx mcp-server-fetch                          (PyPI)
#   git         8803  uvx mcp-server-git --repository <repo>        (PyPI)
#   memory      8804  npx @modelcontextprotocol/server-memory       (npm)
#
# NOTE: the npm packages literally named `mcp-server-fetch` / `mcp-server-git` are
# squatted stubs -- the real ones live on PyPI and must be run with uvx.  Conversely
# `@modelcontextprotocol/server-*` are npm-only.  Do not swap these runners.
#
# Usage
# -----
#   scripts/mcp-bridges.sh start     # start every bridge (idempotent)
#   scripts/mcp-bridges.sh stop      # stop every bridge
#   scripts/mcp-bridges.sh status    # pid + health of each bridge
#   scripts/mcp-bridges.sh restart
#
# Supervision caveat
# ------------------
# start detaches each bridge (setsid/nohup), so the bridges are NOT children of a
# live supervisor and will not be restarted if one dies. They survive this script
# exiting, but nothing revives them. For long-lived use, run start from a process
# you keep alive, or wrap it in launchd/systemd. Use status to detect a dead
# bridge and restart to bring the set back up.
#
# Port probing uses bash /dev/tcp and degrades to lsof/ss/netstat; lsof is absent
# from a stock macOS base install, so it is not required.
#   scripts/mcp-bridges.sh verify    # real MCP initialize + tools/list over HTTP
#   scripts/mcp-bridges.sh urls      # print the URLs to register in OpenViking
#   scripts/mcp-bridges.sh call fetch fetch '{"url":"https://example.com"}'
#
# Configuration (env overrides)
# -----------------------------
#   MCP_BRIDGE_ROOT       dir for logs/pids/state (default: <repo>/.run/mcp-bridges)
#   MCP_FS_ROOT           root exposed by the filesystem server (default: this repo)
#   MCP_GIT_REPO          repository exposed by the git server (default: this repo)
#   MCP_BRIDGE_STATELESS  1 = stateless Streamable HTTP (default 1)
#
# All state is repo-local; this script never writes outside $MCP_BRIDGE_ROOT plus
# the uv/npm caches it redirects there.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ROOT="${MCP_BRIDGE_ROOT:-$REPO_ROOT/.run/mcp-bridges}"

# --- roots must be real directories, or the tools are useless -----------------
MCP_FS_ROOT="${MCP_FS_ROOT:-$REPO_ROOT}"
MCP_GIT_REPO="${MCP_GIT_REPO:-$REPO_ROOT}"
STATELESS="${MCP_BRIDGE_STATELESS:-1}"

# --- tools ---------------------------------------------------------------------
# uv/npm caches are redirected under $RUN_ROOT: the sandbox forbids ~/.cache/uv and
# ~/.npm, and mcp-proxy does NOT propagate the parent environment to named servers,
# so these paths must be passed explicitly to every child process below.
UV_BIN_DIR="$RUN_ROOT/uv-bin"
UV_CACHE_DIR="$RUN_ROOT/uv-cache"
UV_TOOL_DIR="$RUN_ROOT/uv-tools"
NPM_CACHE_DIR="$RUN_ROOT/npm-cache"
TMP_DIR="$RUN_ROOT/tmp"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# Explicit bridge table -- bash arrays keep the argv intact (no word-splitting bugs).
BRIDGE_NAMES=(filesystem fetch git memory)
BRIDGE_PORTS=(8801 8802 8803 8804)
BRIDGE_URLS=()      # filled by resolve_commands
BRIDGE_CMDS=()      # full argv string per bridge

resolve_commands() {
  BRIDGE_CMDS=(
    "npx -y @modelcontextprotocol/server-filesystem $MCP_FS_ROOT"
    "uvx mcp-server-fetch"
    "uvx mcp-server-git --repository $MCP_GIT_REPO"
    "npx -y @modelcontextprotocol/server-memory"
  )
  BRIDGE_URLS=()
  local i
  for i in "${!BRIDGE_NAMES[@]}"; do
    BRIDGE_URLS+=("http://127.0.0.1:${BRIDGE_PORTS[$i]}/mcp")
  done
}

bridge_index() {
  local want="$1" i
  for i in "${!BRIDGE_NAMES[@]}"; do
    [[ "${BRIDGE_NAMES[$i]}" == "$want" ]] && { echo "$i"; return 0; }
  done
  return 1
}

ensure_dirs() {
  mkdir -p "$RUN_ROOT" "$UV_CACHE_DIR" "$UV_TOOL_DIR" "$UV_BIN_DIR" "$NPM_CACHE_DIR" "$TMP_DIR"
}

# mcp-proxy is installed as a uv tool into $UV_BIN_DIR (not the system python).
mcp_proxy_bin() {
  local bin="$UV_BIN_DIR/mcp-proxy"
  if [[ ! -x "$bin" ]]; then
    if [[ -x "$UV_BIN_DIR/mcp-reverse-proxy" && ! -x "$bin" ]]; then :; fi
    say "installing mcp-proxy as a uv tool" >&2
    UV_CACHE_DIR="$UV_CACHE_DIR" UV_TOOL_DIR="$UV_TOOL_DIR" UV_TOOL_BIN_DIR="$UV_BIN_DIR" \
      TMPDIR="$TMP_DIR" uv tool install --quiet mcp-proxy >&2
  fi
  [[ -x "$bin" ]] || die "mcp-proxy not found at $bin"
  echo "$bin"
}

# Environment every child (mcp-proxy, and the stdio servers it spawns) must see.
child_env_args() {
  printf '%s\n' \
    -e UV_CACHE_DIR "$UV_CACHE_DIR" \
    -e UV_TOOL_DIR "$UV_TOOL_DIR" \
    -e TMPDIR "$TMP_DIR" \
    -e npm_config_cache "$NPM_CACHE_DIR" \
    -e NPM_CONFIG_CACHE "$NPM_CACHE_DIR" \
    -e MEMORY_FILE_PATH "$RUN_ROOT/memory.jsonl" \
    -e PATH "$UV_BIN_DIR:$PATH"
}

port_free() {
  local port="$1"
  # lsof is not part of a stock macOS base install. Probe with bash's own
  # /dev/tcp first, then fall back to lsof/ss/netstat when available. An
  # unknown probe must not be reported as a free port.
  if ! (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    return 0
  fi
  exec 3>&- 2>/dev/null || true
  if command -v lsof >/dev/null 2>&1; then
    ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi
  if command -v ss >/dev/null 2>&1; then
    ! ss -ltn 2>/dev/null | grep -q ":$port "
    return
  fi
  if command -v netstat >/dev/null 2>&1; then
    ! netstat -an 2>/dev/null | grep -q "[.:]$port .*LISTEN"
    return
  fi
  return 1
}

pid_file() { echo "$RUN_ROOT/$1.pid"; }

is_running() {
  local pf; pf="$(pid_file "$1")"
  [[ -f "$pf" ]] || return 1
  local pid; pid="$(cat "$pf" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

start_one() {
  local idx="$1"
  local name="${BRIDGE_NAMES[$idx]}" port="${BRIDGE_PORTS[$idx]}" cmd="${BRIDGE_CMDS[$idx]}"

  if is_running "$name"; then
    say "$name already running (pid $(cat "$(pid_file "$name")")) on port $port"
    return 0
  fi
  port_free "$port" || die "port $port ($name) is already in use by another process"

  local bin; bin="$(mcp_proxy_bin)"
  local log="$RUN_ROOT/$name.log"
  local -a envargs=()
  while IFS= read -r a; do envargs+=("$a"); done < <(child_env_args)

  # Default-server (positional) form: mcp-proxy applies -e/--cwd to it.
  # --named-server + URL paths do NOT receive -e, which breaks uvx/npx children.
  say "starting $name on port $port -> $cmd"
  # shellcheck disable=SC2086  # deliberate word-split of the command string
  nohup "$bin" --port "$port" --host 127.0.0.1 \
    ${STATELESS:+--stateless} \
    "${envargs[@]}" \
    -- $cmd >"$log" 2>&1 &

  local pid=$!
  echo "$pid" >"$(pid_file "$name")"
  sleep 0.4
  if ! kill -0 "$pid" 2>/dev/null; then
    warn "$name died immediately; last log lines:"
    tail -n 15 "$log" >&2 || true
    rm -f "$(pid_file "$name")"
    return 1
  fi
  echo "    pid $pid, log $log"
}

wait_ready() {
  local idx="$1" tries="${2:-45}"
  local name="${BRIDGE_NAMES[$idx]}" port="${BRIDGE_PORTS[$idx]}"
  local i
  for ((i = 0; i < tries; i++)); do
    if curl -fsS --max-time 2 -o /dev/null \
      -X POST "http://127.0.0.1:$port/mcp" \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' \
      2>/dev/null; then
      echo "    $name ready"
      return 0
    fi
    sleep 1
  done
  warn "$name did not answer on port $port within ${tries}s (see $RUN_ROOT/$name.log)"
  return 1
}

cmd_start() {
  ensure_dirs
  [[ -d "$MCP_FS_ROOT" ]] || die "MCP_FS_ROOT does not exist: $MCP_FS_ROOT"
  [[ -d "$MCP_GIT_REPO" ]] || die "MCP_GIT_REPO does not exist: $MCP_GIT_REPO"
  resolve_commands
  local i
  for i in "${!BRIDGE_NAMES[@]}"; do start_one "$i"; done
  say "waiting for bridges to become ready"
  local rc=0
  for i in "${!BRIDGE_NAMES[@]}"; do wait_ready "$i" || rc=1; done
  return $rc
}

cmd_stop() {
  local i pf pid
  for i in "${!BRIDGE_NAMES[@]}"; do
    pf="$(pid_file "${BRIDGE_NAMES[$i]}")"
    if [[ -f "$pf" ]]; then
      pid="$(cat "$pf" 2>/dev/null || true)"
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        say "stopping ${BRIDGE_NAMES[$i]} (pid $pid)"
        kill "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.3
        done
        kill -0 "$pid" 2>/dev/null && { warn "force killing $pid"; kill -9 "$pid" 2>/dev/null || true; }
      fi
      rm -f "$pf"
    fi
  done
  say "stopped"
}

cmd_status() {
  local i
  printf '%-12s %-6s %-9s %s\n' NAME PORT STATE URL
  for i in "${!BRIDGE_NAMES[@]}"; do
    local state="down"
    is_running "${BRIDGE_NAMES[$i]}" && state="up"
    printf '%-12s %-6s %-9s %s\n' "${BRIDGE_NAMES[$i]}" "${BRIDGE_PORTS[$i]}" "$state" "${BRIDGE_URLS[$i]}"
  done
}

# Responses may be plain JSON or SSE (`data: {...}`).  Normalise to JSON first,
# then pull the tool names out of the tools/list result.
normalise_body() {
  local body="$1"
  if printf '%s' "$body" | grep -q '^data: '; then
    printf '%s' "$body" | sed -n 's/^data: //p'
  else
    printf '%s' "$body"
  fi
}

# Print comma-separated tool names from a tools/list response.
extract_tools() {
  normalise_body "$1" \
    | grep -o '"name":"[^"]*"' \
    | sed 's/"name":"//; s/"$//' \
    | paste -sd, - 2>/dev/null || true
}

cmd_verify() {
  local i rc=0
  for i in "${!BRIDGE_NAMES[@]}"; do
    local name="${BRIDGE_NAMES[$i]}" port="${BRIDGE_PORTS[$i]}"
    echo "=== $name (port $port) ==="
    local init
    init="$(curl -sS --max-time 15 -X POST "http://127.0.0.1:$port/mcp" \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}' 2>&1)"
    if [[ -z "$init" ]]; then warn "$name: no response to initialize"; rc=1; continue; fi
    echo "    initialize: $(normalise_body "$init" | grep -o '"serverInfo":{[^}]*}' | head -1)"

    local body
    body="$(curl -sS --max-time 20 -X POST "http://127.0.0.1:$port/mcp" \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' 2>&1)"
    local tools; tools="$(extract_tools "$body")"
    if [[ -z "$tools" ]]; then
      warn "$name: tools/list returned no tools"
      printf '    raw: %s\n' "$(printf '%s' "$body" | head -c 300)"
      rc=1
    else
      echo "    tools: $tools"
      echo "    count: $(printf '%s' "$tools" | tr ',' '\n' | grep -c .)"
    fi
  done
  return $rc
}

cmd_urls() {
  resolve_commands
  local i
  for i in "${!BRIDGE_NAMES[@]}"; do
    printf '%-12s %s\n' "${BRIDGE_NAMES[$i]}" "${BRIDGE_URLS[$i]}"
  done
}

# Invoke one tool:  mcp-bridges.sh call <bridge> <tool> '<json-arguments>'
cmd_call() {
  local name="${1:-}" tool="${2:-}" args="${3:-{\}}"
  [[ -n "$name" && -n "$tool" ]] || die "usage: $0 call <bridge> <tool> ['<json-args>']"
  local idx; idx="$(bridge_index "$name")" || die "unknown bridge: $name"
  local port="${BRIDGE_PORTS[$idx]}"
  local body
  body="$(curl -sS --max-time 30 -X POST "http://127.0.0.1:$port/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":9,\"method\":\"tools/call\",\"params\":{\"name\":\"$tool\",\"arguments\":$args}}" 2>&1)"
  normalise_body "$body"
}

main() {
  resolve_commands
  case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    status)  cmd_status ;;
    verify)  cmd_verify ;;
    urls)    cmd_urls ;;
    call)    shift; cmd_call "$@" ;;
    ""|help|-h|--help)
      sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      ;;
    *) die "unknown command: $1 (try: start|stop|restart|status|verify|urls|call)" ;;
  esac
}

main "$@"
