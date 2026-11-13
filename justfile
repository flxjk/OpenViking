# ═══════════════════════════════════════════════════════════════════════════
# OpenViking local stack — every launch in one place.
#
#   just              list every recipe
#   just up           start OpenViking + DSH web
#   just status       show every component
#   just down         stop everything
#
# Everything binds to loopback. No recipe reaches a telemetry endpoint.
# ═══════════════════════════════════════════════════════════════════════════

set shell := ["bash", "-euo", "pipefail", "-c"]

repo      := justfile_directory()
venv      := repo / ".venv"
py        := venv / "bin" / "python"
ov_server := venv / "bin" / "openviking-server"
ov_cli    := venv / "bin" / "ov"
ov_conf   := repo / "ov.conf"
run_dir   := repo / ".run"

dsh_dir   := env("DSH_DIR", home_directory() / "git" / "deepseek-harness")
rhex_dir  := env("RHEX_DIR", home_directory() / "git" / "rhex")

ov_host   := env("OV_HOST", "127.0.0.1")
ov_port   := env("OV_PORT", "1933")
dsh_port  := env("DSH_PORT", "3090")
ov_url    := "http://" + ov_host + ":" + ov_port
dsh_url   := "http://" + ov_host + ":" + dsh_port

bot_host  := env("BOT_HOST", "127.0.0.1")
bot_port  := env("BOT_PORT", "18790")
bot_url   := "http://" + bot_host + ":" + bot_port
bot_bin   := venv / "bin" / "vikingbot"

ollama_url  := env("OLLAMA_URL", "http://127.0.0.1:11434")
studio_dir  := env("STUDIO_DIR", home_directory() / "DeepSeek-Reasonix-studio")
gemini_srv  := studio_dir / "scripts" / "gemini-api-server.mjs"
research_srv := studio_dir / "research-mcp" / "src" / "server.mjs"

# ── default ────────────────────────────────────────────────────────────────

# List every recipe.
default:
    @just --list

# ── setup ──────────────────────────────────────────────────────────────────

# Create the venv and install Python dependencies.
[group('setup')]
setup:
    uv sync

# Build native extensions (ragfs + C++).
[group('setup')]
build:
    make build

# Build the Rust CLI (`ov`).
[group('setup')]
build-cli:
    make build-cli

# Build the Web Studio SPA.
[group('setup')]
build-studio:
    make build-studio

# Check every prerequisite this justfile relies on.
[group('setup')]
doctor:
    #!/usr/bin/env bash
    set -uo pipefail
    ok=0
    check() {
      local label="$1" cmd="$2"
      if eval "$cmd" >/dev/null 2>&1; then
        printf '  ok    %s\n' "$label"
      else
        printf '  MISS  %s\n' "$label"
        ok=1
      fi
    }
    echo "prerequisites:"
    check "just"            "just --version"
    check "uv"              "uv --version"
    check "node"            "node --version"
    check "python venv"     "test -x {{py}}"
    check "openviking-server" "test -x {{ov_server}}"
    check "ov.conf"         "test -f {{ov_conf}}"
    check "dsh checkout"    "test -d {{dsh_dir}}"
    check "rhex checkout"   "test -d {{rhex_dir}}"
    check "agy CLI"         "test -x $HOME/.local/bin/agy"
    exit "$ok"

# ── stack ──────────────────────────────────────────────────────────────────

# Start the whole local stack (OpenViking + DSH web + VikingBot).
[group('stack')]
up: ov-start dsh-start bot-start
    @echo
    @just status

# Stop the whole local stack.
[group('stack')]
down: bot-stop dsh-stop ov-stop

# Show every component status.
[group('stack')]
status:
    #!/usr/bin/env bash
    set -uo pipefail
    probe() {
      local code
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null || echo 000)"
      if [ "$code" != "000" ]; then printf '  UP    %-14s %s\n' "$2" "$1"
      else printf '  DOWN  %-14s %s\n' "$2" "$1"; fi
    }
    # A process check for stdio services. `pgrep` excludes its own process, so
    # this cannot self-match when the recipe body is run from a temp file.
    # (An `awk`-over-`ps` variant does self-match its own command line -- do not
    # swap this back without a negative control.)
    proc() {
      if pgrep -f "$1" >/dev/null 2>&1; then printf '  UP    %-14s %s\n' "$2" "$3"
      else printf '  DOWN  %-14s %s\n' "$2" "$3"; fi
    }
    echo "servers"
    probe "{{ov_url}}/health" "OpenViking" "{{ov_url}}"
    probe "{{dsh_url}}" "DSH web" "{{dsh_url}}"
    probe "{{ov_url}}/gateway/v1/models" "OV gateway" "{{ov_url}}/gateway"
    probe "{{bot_url}}/health" "VikingBot" "{{bot_url}}"
    probe "{{ollama_url}}/api/tags" "Ollama" "{{ollama_url}}"
    echo "mcp servers (spawned by DSH)"
    proc "gemini-api-server.mjs" "gemini-api" "stdio"
    proc "research-mcp/src/server.mjs" "rhex-research" "stdio"
    # ACP clients are stdio servers spawned on demand; probe the handshake instead.
    acp() {
      local label="$1" note="$2"
      shift 2
      if "{{py}}" "{{repo}}/scripts/acp_probe.py" "$@" >/dev/null 2>&1; then
        printf '  UP    %-14s %s\n' "$label" "$note"
      else
        printf '  DOWN  %-14s %s\n' "$label" "$note"
      fi
    }
    echo "clients"
    acp "reasonix" "ACP ready" "/Users/gj/.local/bin/reasonix" acp
    acp "agy" "ACP ready" "/Users/gj/.local/bin/agy" acp --disable-telemetry

# Deep functional probe of every component (servers, MCP servers, ACP clients).
[group('stack')]
verify:
    "{{py}}" "{{repo}}/scripts/stack_verify.py"

# Exercise every client path end to end.
[group('stack')]
health: _ov-lang
    #!/usr/bin/env bash
    set -uo pipefail
    pass=0; fail=0
    check() {
      local name="$1"; shift
      if out="$("$@" 2>&1)" && printf '%s' "$out" | grep -qiE "$PROBE_RE"; then
        printf '  OK    %s\n' "$name"; pass=$((pass+1))
      else
        printf '  FAIL  %s — %s\n' "$name" "$(printf '%s' "$out" | tail -1 | cut -c1-90)"; fail=$((fail+1))
      fi
    }
    PROBE_RE='OK|tokens|dimensions|SUCCESS|models'

    check "gemini CLI generate"   node "{{studio_dir}}/scripts/gemini-genai.mjs" --effort minimal "Reply with exactly one word: OK"
    check "gemini CLI models"     node "{{studio_dir}}/scripts/gemini-genai.mjs" models
    check "gemini CLI count"      node "{{studio_dir}}/scripts/gemini-genai.mjs" count "hello world"
    check "gemini CLI embed"      node "{{studio_dir}}/scripts/gemini-genai.mjs" embed "hello world"
    check "antigravity SDK"       "{{py}}" "{{studio_dir}}/scripts/antigravity-agent.py" "Reply with exactly one word: OK"
    check "agy CLI"               "{{studio_dir}}/scripts/agy-agent.sh" "Reply with exactly one word: OK"
    check "gemini MCP server"     bash -c 'printf "%s\n" "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}" | node "{{gemini_srv}}"'

    echo
    echo "$pass ok, $fail failed"
    [ "$fail" -eq 0 ]

# Tail both service logs.
[group('stack')]
logs:
    #!/usr/bin/env bash
    set -uo pipefail
    mkdir -p "{{run_dir}}"
    touch "{{run_dir}}/ov.log" "{{run_dir}}/dsh.log"
    tail -n 40 -f "{{run_dir}}/ov.log" "{{run_dir}}/dsh.log"

# ── openviking ─────────────────────────────────────────────────────────────

# Write ov.conf from OPENROUTER_API_KEY / GEMINI_API_KEY (FORCE=1 to overwrite).
[group('openviking')]
ov-config-init:
    "{{py}}" scripts/ov_config_init.py

# Ensure the CLI has a display language (default: en).
[private]
_ov-lang:
    #!/usr/bin/env bash
    set -euo pipefail
    settings="$HOME/.openviking/ovcli.settings.conf"
    if [ -f "$settings" ] && grep -q '"language"' "$settings"; then exit 0; fi
    "{{ov_cli}}" language en >/dev/null

# Set the CLI display language.
[group('openviking')]
ov-language lang="en":
    "{{ov_cli}}" language "{{lang}}"

# Start the OpenViking server on 127.0.0.1:1933 (background, pidfile in .run/).
[group('openviking')]
ov-start:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "{{run_dir}}"
    if [ -f "{{run_dir}}/ov.pid" ] && kill -0 "$(cat "{{run_dir}}/ov.pid")" 2>/dev/null; then
      echo "OpenViking already running (pid $(cat "{{run_dir}}/ov.pid"))"
      exit 0
    fi
    if curl -fsS "{{ov_url}}/health" >/dev/null 2>&1; then
      echo "OpenViking already listening on {{ov_url}} (not started by just)"
      exit 0
    fi
    if [ ! -f "{{ov_conf}}" ]; then
      echo "missing {{ov_conf}} — run: just ov-config-init" >&2
      exit 1
    fi
    nohup "{{ov_server}}" --config "{{ov_conf}}" > "{{run_dir}}/ov.log" 2>&1 &
    echo $! > "{{run_dir}}/ov.pid"
    for _ in $(seq 1 90); do
      if curl -fsS "{{ov_url}}/health" >/dev/null 2>&1; then
        echo "OpenViking up at {{ov_url}} (pid $(cat "{{run_dir}}/ov.pid"))"
        exit 0
      fi
      sleep 1
    done
    echo "OpenViking did not become healthy in 90s" >&2
    tail -30 "{{run_dir}}/ov.log" >&2
    exit 1

# Stop the OpenViking server.
[group('openviking')]
ov-stop:
    #!/usr/bin/env bash
    set -uo pipefail
    pid=""
    if [ -f "{{run_dir}}/ov.pid" ]; then pid="$(cat "{{run_dir}}/ov.pid")"; fi
    if [ -z "$pid" ]; then
      pid="$(lsof -nP -iTCP:{{ov_port}} -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
    fi
    if [ -z "$pid" ]; then
      echo "OpenViking not running"
      rm -f "{{run_dir}}/ov.pid"
      exit 0
    fi
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "{{run_dir}}/ov.pid"
    echo "OpenViking stopped (pid $pid)"

# Restart the OpenViking server.
[group('openviking')]
ov-restart: ov-stop ov-start

# ── vikingbot ──────────────────────────────────────────────────────────────

# Start the VikingBot gateway (Personal AI Assistant on 127.0.0.1:18790).
[group('bot')]
bot-start:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "{{run_dir}}"
    if [ -f "{{run_dir}}/bot.pid" ] && kill -0 "$(cat "{{run_dir}}/bot.pid")" 2>/dev/null; then
      echo "VikingBot already running (pid $(cat "{{run_dir}}/bot.pid"))"
      exit 0
    fi
    if curl -fsS "{{bot_url}}/health" >/dev/null 2>&1; then
      echo "VikingBot already listening on {{bot_url}} (not started by just)"
      exit 0
    fi
    nohup "{{bot_bin}}" gateway --port {{bot_port}} --host {{bot_host}} --config "{{ov_conf}}" \
      > "{{run_dir}}/bot.log" 2>&1 &
    echo $! > "{{run_dir}}/bot.pid"
    for _ in $(seq 1 45); do
      if curl -fsS "{{bot_url}}/health" >/dev/null 2>&1; then
        echo "VikingBot up at {{bot_url}} (pid $(cat "{{run_dir}}/bot.pid"))"
        exit 0
      fi
      sleep 1
    done
    echo "VikingBot did not become healthy in 45s" >&2
    tail -30 "{{run_dir}}/bot.log" >&2
    exit 1

# Stop the VikingBot gateway.
[group('bot')]
bot-stop:
    #!/usr/bin/env bash
    set -uo pipefail
    pid=""
    if [ -f "{{run_dir}}/bot.pid" ]; then pid="$(cat "{{run_dir}}/bot.pid")"; fi
    if [ -z "$pid" ]; then
      pid="$(lsof -nP -iTCP:{{bot_port}} -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
    fi
    if [ -z "$pid" ]; then
      echo "VikingBot not running"
      rm -f "{{run_dir}}/bot.pid"
      exit 0
    fi
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "{{run_dir}}/bot.pid"
    echo "VikingBot stopped (pid $pid)"

# Probe the VikingBot gateway.
[group('bot')]
bot-status:
    @curl -s -m 4 -o /dev/null -w 'VikingBot {{bot_url}} -> HTTP %{http_code}\n' "{{bot_url}}/health" || echo "VikingBot {{bot_url}} -> DOWN"

# Tail the VikingBot log.
[group('bot')]
bot-logs:
    @mkdir -p "{{run_dir}}" && touch "{{run_dir}}/bot.log" && tail -n 40 -f "{{run_dir}}/bot.log"

# Show server health and component status.
[group('openviking')]
ov-status:
    #!/usr/bin/env bash
    set -uo pipefail
    if curl -fsS "{{ov_url}}/health" >/dev/null 2>&1; then
      echo "OpenViking  UP  {{ov_url}}"
      curl -fsS "{{ov_url}}/health" | head -c 600
      echo
    else
      echo "OpenViking  DOWN  {{ov_url}}"
      exit 1
    fi
    "{{ov_cli}}" status || true

# Quick health probe.
[group('openviking')]
ov-health:
    @curl -fsS "{{ov_url}}/health" && echo

# Tail the server log.
[group('openviking')]
ov-logs:
    @mkdir -p "{{run_dir}}" && touch "{{run_dir}}/ov.log" && tail -n 80 -f "{{run_dir}}/ov.log"

# Remove a stale data-directory lock left by a killed server.
[group('openviking')]
ov-unlock:
    #!/usr/bin/env bash
    set -euo pipefail
    lock="{{repo}}/data/.openviking.lock"
    if [ -f "$lock" ]; then
      rm -f "$lock"
      echo "removed $lock"
    else
      echo "no stale lock"
    fi

# Drop the vector collection (needed after an embedding-dimension change).
[group('openviking')]
ov-reset-vectordb:
    #!/usr/bin/env bash
    set -euo pipefail
    target="{{repo}}/data/vectordb/context"
    if [ -d "$target" ]; then
      rm -rf "$target"
      echo "removed $target"
    else
      echo "no vector collection at $target"
    fi

# Semantic retrieval.
[group('openviking')]
ov-find query: _ov-lang
    "{{ov_cli}}" find "{{query}}"

# Read a resource by URI.
[group('openviking')]
ov-read uri: _ov-lang
    "{{ov_cli}}" read "{{uri}}"

# Interactive file explorer.
[group('openviking')]
ov-tui: _ov-lang
    "{{ov_cli}}" tui

# ── dsh ────────────────────────────────────────────────────────────────────

# Start the DeepSeek Harness Web GUI on 127.0.0.1:3090 (background).
[group('dsh')]
dsh-start:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "{{run_dir}}"
    if [ -f "{{run_dir}}/dsh.pid" ] && kill -0 "$(cat "{{run_dir}}/dsh.pid")" 2>/dev/null; then
      echo "DSH already running (pid $(cat "{{run_dir}}/dsh.pid"))"
      exit 0
    fi
    if [ -n "$(lsof -nP -iTCP:{{dsh_port}} -sTCP:LISTEN -t 2>/dev/null || true)" ]; then
      echo "DSH already listening on {{dsh_url}} (not started by just)"
      exit 0
    fi
    cd "{{dsh_dir}}"
    nohup node --import tsx/esm apps/cli/src/bin.ts web > "{{run_dir}}/dsh.log" 2>&1 &
    echo $! > "{{run_dir}}/dsh.pid"
    for _ in $(seq 1 120); do
      if grep -q "^dsh web: " "{{run_dir}}/dsh.log" 2>/dev/null; then
        grep "^dsh web: " "{{run_dir}}/dsh.log" | tail -1
        exit 0
      fi
      sleep 1
    done
    echo "DSH did not announce a URL in 120s" >&2
    tail -30 "{{run_dir}}/dsh.log" >&2
    exit 1

# Stop the DSH web server.
[group('dsh')]
dsh-stop:
    #!/usr/bin/env bash
    set -uo pipefail
    pid=""
    if [ -f "{{run_dir}}/dsh.pid" ]; then pid="$(cat "{{run_dir}}/dsh.pid")"; fi
    if [ -z "$pid" ]; then
      pid="$(lsof -nP -iTCP:{{dsh_port}} -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
    fi
    if [ -z "$pid" ]; then
      echo "DSH not running"
      rm -f "{{run_dir}}/dsh.pid"
      exit 0
    fi
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "{{run_dir}}/dsh.pid"
    echo "DSH stopped (pid $pid)"

# Show the token-authenticated DSH URL.
[group('dsh')]
dsh-url:
    #!/usr/bin/env bash
    set -uo pipefail
    if [ -f "{{run_dir}}/dsh.log" ] && grep -q "^dsh web: " "{{run_dir}}/dsh.log"; then
      grep "^dsh web: " "{{run_dir}}/dsh.log" | tail -1
      exit 0
    fi
    if [ -n "$(lsof -nP -iTCP:{{dsh_port}} -sTCP:LISTEN -t 2>/dev/null || true)" ]; then
      echo "{{dsh_url}}"
      echo "(started outside just: the launch token is in-memory; an existing browser cookie authenticates)"
      exit 0
    fi
    echo "DSH is not running — run: just dsh-start"

# Tail the DSH log.
[group('dsh')]
dsh-logs:
    @mkdir -p "{{run_dir}}" && touch "{{run_dir}}/dsh.log" && tail -n 80 -f "{{run_dir}}/dsh.log"

# Probe the DSH web port.
[group('dsh')]
dsh-status:
    #!/usr/bin/env bash
    set -uo pipefail
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "{{dsh_url}}" 2>/dev/null || echo 000)"
    if [ "$code" != "000" ]; then
      echo "DSH  UP  {{dsh_url}}  (http $code)"
    else
      echo "DSH  DOWN  {{dsh_url}}"
      exit 1
    fi

# ── gateway ────────────────────────────────────────────────────────────────

# List models exposed by the embedded gateway (needs OV_GW_KEY).
[group('gateway')]
gw-models:
    #!/usr/bin/env bash
    set -euo pipefail
    key="${OV_GW_KEY:-}"
    if [ -z "$key" ] && [ -f "$HOME/.openviking/gateway.key" ]; then
      key="$(cat "$HOME/.openviking/gateway.key")"
    fi
    if [ -z "$key" ]; then
      echo "set OV_GW_KEY (Studio -> Gateway -> Keys) or write ~/.openviking/gateway.key" >&2
      exit 1
    fi
    curl -fsS "{{ov_url}}/gateway/v1/models" -H "Authorization: Bearer $key"

# Send one chat completion through the gateway (needs OV_GW_KEY).
[group('gateway')]
gw-chat prompt:
    #!/usr/bin/env bash
    set -euo pipefail
    key="${OV_GW_KEY:-}"
    if [ -z "$key" ] && [ -f "$HOME/.openviking/gateway.key" ]; then
      key="$(cat "$HOME/.openviking/gateway.key")"
    fi
    if [ -z "$key" ]; then
      echo "set OV_GW_KEY (Studio -> Gateway -> Keys) or write ~/.openviking/gateway.key" >&2
      exit 1
    fi
    body="$("{{py}}" -c 'import json,os,sys; print(json.dumps({"model": os.environ.get("OV_GW_MODEL","deepseek/deepseek-chat"), "messages": [{"role":"user","content": sys.argv[1]}]}))' "{{prompt}}")"
    curl -fsS "{{ov_url}}/gateway/v1/chat/completions" \
      -H "content-type: application/json" \
      -H "Authorization: Bearer $key" \
      -d "$body"

# Probe the gateway port.
[group('gateway')]
gw-status:
    #!/usr/bin/env bash
    set -uo pipefail
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "{{ov_url}}/gateway/v1/models" 2>/dev/null || echo 000)"
    if [ "$code" != "000" ]; then
      echo "gateway  UP  {{ov_url}}/gateway  (http $code)"
    else
      echo "gateway  DOWN  {{ov_url}}/gateway"
      exit 1
    fi

# Live protocol/cache acceptance (needs OV_GW_TEST_* credentials).
[group('gateway')]
gw-acceptance protocol="chat":
    "{{py}}" scripts/gateway_acceptance.py --protocol "{{protocol}}"

# ── mcp ────────────────────────────────────────────────────────────────────

# List MCP servers registered with OpenViking.
[group('mcp')]
mcp-list: _ov-lang
    "{{ov_cli}}" mcps list

# Find an MCP tool by description.
[group('mcp')]
mcp-find query: _ov-lang
    "{{ov_cli}}" mcps find "{{query}}"

# Show one MCP server and its tool catalog.
[group('mcp')]
mcp-show name: _ov-lang
    "{{ov_cli}}" mcps show "{{name}}"

# ── agents ─────────────────────────────────────────────────────────────────

# Install the Antigravity CLI + SDK and register the OpenViking MCP.
[group('agents')]
agy-setup:
    scripts/setup_antigravity.sh

# Run the Antigravity CLI against OpenViking memory.
[group('agents')]
agy prompt:
    scripts/agy_agent.sh "{{prompt}}"

# Run the Antigravity ACP server (telemetry disabled).
[group('agents')]
agy-acp:
    @$HOME/.local/bin/agy acp --disable-telemetry

# Run the Antigravity Python SDK agent against OpenViking memory.
[group('agents')]
antigravity prompt:
    "{{py}}" scripts/antigravity_agent.py "{{prompt}}"

# Run the Gemini 4 agent (Carbon/Argon) against OpenViking memory.
[group('agents')]
gemini prompt:
    node scripts/gemini_agent.mjs "{{prompt}}"

# ── telemetry ──────────────────────────────────────────────────────────────

# Prove that no outbound telemetry ships in the tree.
[group('telemetry')]
telemetry-audit:
    "{{py}}" scripts/telemetry_audit.py

# Dry-run the telemetry removal migration.
[group('telemetry')]
telemetry-remove:
    "{{py}}" scripts/remove_telemetry.py --dry-run

# Apply the telemetry removal migration.
[group('telemetry')]
telemetry-remove-apply:
    "{{py}}" scripts/remove_telemetry.py

# ── tests ──────────────────────────────────────────────────────────────────

# Run the full test suite.
[group('tests')]
test:
    "{{py}}" -m pytest

# Run the suite and stop at the first failure.
[group('tests')]
test-fast:
    "{{py}}" -m pytest -x -q

# Run one test file or node id.
[group('tests')]
test-file path:
    "{{py}}" -m pytest "{{path}}" -x -q

# Lint Python sources.
[group('tests')]
lint:
    uvx ruff check openviking openviking_cli openviking_gateway bot scripts

# Format Python sources.
[group('tests')]
fmt:
    uvx ruff format openviking openviking_cli openviking_gateway bot scripts

# ── sdk ────────────────────────────────────────────────────────────────────

# Start a throwaway OpenViking server for mutation probing and sandbox tests.
[group('sdk')]
sdk-sandbox-up port="1944" workspace="/tmp/ov-sandbox/data" root_key="":
    #!/usr/bin/env bash
    set -euo pipefail
    if curl -fsS "http://127.0.0.1:{{port}}/health" >/dev/null 2>&1; then
      echo "sandbox already running on {{port}}"
      exit 0
    fi
    mkdir -p "{{workspace}}"
    args=(--out /tmp/ov-sandbox.conf --port "{{port}}" --workspace "{{workspace}}")
    if [ -n "{{root_key}}" ]; then
      args+=(--root-key "{{root_key}}")
    fi
    "{{py}}" "{{repo}}/scripts/ov_sandbox.py" "${args[@]}"
    nohup "{{ov_server}}" --config /tmp/ov-sandbox.conf > /tmp/ov-sandbox.log 2>&1 &
    echo $! > /tmp/ov-sandbox.pid
    for _ in $(seq 1 90); do
      if curl -fsS "http://127.0.0.1:{{port}}/health" >/dev/null 2>&1; then
        echo "sandbox up on {{port}} (pid $(cat /tmp/ov-sandbox.pid), workspace {{workspace}})"
        exit 0
      fi
      sleep 1
    done
    echo "sandbox did not become healthy in 90s" >&2
    tail -30 /tmp/ov-sandbox.log >&2
    exit 1

# Stop the sandbox server and wipe its workspace.
[group('sdk')]
sdk-sandbox-down port="1944" workspace="/tmp/ov-sandbox/data":
    #!/usr/bin/env bash
    set -uo pipefail
    # Only ever kill a process that is provably this sandbox: the pidfile is
    # shared across runs, and a recycled pid must not take the live server down.
    is_sandbox() {
      local pid="$1"
      [ -n "$pid" ] || return 1
      ps -p "$pid" -o command= 2>/dev/null | grep -q "ov-sandbox.conf"
    }
    if [ -f /tmp/ov-sandbox.pid ]; then
      pid="$(cat /tmp/ov-sandbox.pid)"
      if is_sandbox "$pid"; then kill "$pid" 2>/dev/null || true; fi
      rm -f /tmp/ov-sandbox.pid
    fi
    pid="$(lsof -nP -iTCP:{{port}} -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
    if is_sandbox "$pid"; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 30); do
        lsof -nP -iTCP:{{port}} -sTCP:LISTEN -t >/dev/null 2>&1 || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -rf "{{workspace}}"
    echo "sandbox stopped on {{port}}, workspace removed"

# Regenerate the TypeScript SDK surface from the live OpenAPI spec.
[group('sdk')]
sdk-generate:
    cd "{{repo}}/sdk/typescript" && npm run generate

# Infer mutation response types: dev pass for account scope, root pass for admin.
[group('sdk')]
sdk-generate-mutations port="1944" workspace="/tmp/ov-sandbox/data":
    #!/usr/bin/env bash
    set -euo pipefail
    state=/tmp/ov-mutation-types.json
    rm -f "$state"
    sdk="{{repo}}/sdk/typescript"

    just sdk-sandbox-down "{{port}}" "{{workspace}}" >/dev/null
    just sdk-sandbox-up "{{port}}" "{{workspace}}" >/dev/null
    ( cd "$sdk" && python3 scripts/probe_mutations.py --url "http://127.0.0.1:{{port}}" \
        --spec /tmp/ov-openapi.json --scope account --state "$state" )

    just sdk-sandbox-down "{{port}}" "{{workspace}}" >/dev/null
    key="ovroot_probe_$(openssl rand -hex 16)"
    just sdk-sandbox-up "{{port}}" "{{workspace}}" "$key" >/dev/null
    ( cd "$sdk" && python3 scripts/probe_mutations.py --url "http://127.0.0.1:{{port}}" \
        --spec /tmp/ov-openapi.json --scope admin --api-key "$key" --state "$state" )

    just sdk-sandbox-down "{{port}}" "{{workspace}}" >/dev/null
    python3 "$sdk/scripts/generate.py" --spec /tmp/ov-openapi.json

# Typecheck the TypeScript SDK.
[group('sdk')]
sdk-typecheck:
    cd "{{repo}}/sdk/typescript" && npm run typecheck

# Run the TypeScript SDK tests.
[group('sdk')]
sdk-test:
    cd "{{repo}}/sdk/typescript" && npm test

# Run the SDK examples against the live stack.
[group('sdk')]
sdk-examples query="local AI stack":
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{repo}}/sdk/typescript"
    npm run build >/dev/null
    echo "── 01-connect"
    node examples/01-connect.mjs
    echo "── 02-memory"
    node examples/02-memory.mjs "{{query}}"
    echo "── 03-observe"
    node examples/03-observe.mjs
    echo "── 04-agent"
    node examples/04-agent.mjs "what is my {{query}}?"

# Run the SDK tests including the sandbox lifecycle suite.
[group('sdk')]
sdk-test-sandbox port="1944" workspace="/tmp/ov-sandbox/data":
    #!/usr/bin/env bash
    set -euo pipefail
    started=0
    if ! curl -fsS "http://127.0.0.1:{{port}}/health" >/dev/null 2>&1; then
      just sdk-sandbox-up "{{port}}" "{{workspace}}"
      started=1
    fi
    status=0
    ( cd "{{repo}}/sdk/typescript" && npm test ) || status=$?
    if [ "$started" = "1" ]; then
      just sdk-sandbox-down "{{port}}" "{{workspace}}" >/dev/null
    fi
    exit "$status"

# Build the TypeScript SDK bundles.
[group('sdk')]
sdk-build:
    cd "{{repo}}/sdk/typescript" && npm run build

# Generate, typecheck, test and build the TypeScript SDK.
[group('sdk')]
sdk-verify: sdk-generate sdk-typecheck sdk-test sdk-build

# ── rhex ───────────────────────────────────────────────────────────────────

# Show rhex branch, target tip and dirty paths.
[group('rhex')]
rhex-status:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{rhex_dir}}"
    RHEX_GITOPS_AUTHORIZED=1 RHEX_GITOPS_ACTOR="${RHEX_GITOPS_ACTOR:-lead}" \
      bin/gitops inspect --json

# Run the compile truth gate.
[group('rhex')]
rhex-gate:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{rhex_dir}}"
    bin/compile-truth-gate

# Re-record the compile/tsgo baselines.
[group('rhex')]
rhex-record-baseline:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{rhex_dir}}"
    bin/compile-truth-gate --record-baseline

# Commit and push to origin/dev. Set RHEX_INTEGRITY_BYPASS=1 to skip the gate.
[group('rhex')]
rhex-push message *paths:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{rhex_dir}}"
    if [ -n "${RHEX_INTEGRITY_BYPASS:-}" ]; then
      echo "integrity gate: BYPASSED (RHEX_INTEGRITY_BYPASS=$RHEX_INTEGRITY_BYPASS)"
    else
      echo "integrity gate: enforced (set RHEX_INTEGRITY_BYPASS=1 to bypass)"
    fi
    args=()
    paths="{{paths}}"
    if [ -n "$paths" ]; then
      for p in $paths; do args+=(--path "$p"); done
    fi
    RHEX_GITOPS_AUTHORIZED=1 \
    RHEX_GITOPS_ACTOR="${RHEX_GITOPS_ACTOR:-lead}" \
      bin/gitops run "${args[@]}" --message "{{message}}"
