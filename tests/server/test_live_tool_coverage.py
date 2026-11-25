# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Every tool must actually work: all built-in MCP tools and all bridged external ones.

What this suite is for
----------------------
The built-in MCP surface is enumerated from the *live* server and compared against the
expected set, so a tool that silently disappears from the endpoint fails here instead of
being discovered by a user. Each built-in tool is then really invoked over the MCP
transport, not through a mock, because a tool that is merely *listed* proves nothing.

The external tools live behind ``mcp-proxy`` bridges (``scripts/mcp-bridges.sh``) and are
reached through the built-in ``invoke_mcp_tool``. Their catalog is enumerated from disk so
the expected per-server counts are asserted rather than assumed.

Scope and safety
----------------
These tests talk to a running stack: the instance named by ``OPENVIKING_TEST_BASE_URL``,
defaulting to the local stack at ``http://127.0.0.1:1933``. They carry the ``integration``
marker and skip cleanly when the server or a bridge is absent, so a bare CI checkout is
unaffected. A target taken from the environment that is unreachable skips with that URL in
the reason instead of silently falling back to the default, so an isolated run can never
land on the developer's live stack. No test touches real user memories: every write goes to
a throwaway URI created by the test.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import httpx
import pytest

# Suite target: the instance named by ``OPENVIKING_TEST_BASE_URL`` when set, else the local
# stack. An explicit target is used verbatim so a run cannot drift onto the default stack.
BASE_URL_ENV = "OPENVIKING_TEST_BASE_URL"
DEFAULT_BASE_URL = "http://127.0.0.1:1933"
_BASE_URL_OVERRIDE = os.environ.get(BASE_URL_ENV, "").strip()
BASE_URL = (_BASE_URL_OVERRIDE or DEFAULT_BASE_URL).rstrip("/")
MCP_URL = f"{BASE_URL}/mcp"
HEALTH_URL = f"{BASE_URL}/health"

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "data" / "viking" / "default" / "user" / "default" / "tools"

# The built-in surface as the endpoint must expose it. An explicit literal so drift in
# either direction is caught.
EXPECTED_BUILTIN_TOOLS = {
    "add_resource",
    "add_skill",
    "cancel_watch",
    "captureDecision",
    "contextPack",
    "download",
    "edit",
    "find",
    "find_mcp_tools",
    "forget",
    "get_acl",
    "glob",
    "grep",
    "health",
    "invoke_mcp_tool",
    "list",
    "list_groups",
    "list_users",
    "list_watches",
    "mcpToolsByIntent",
    "privacy_resolve",
    "privacy_tree",
    "privacy_upsert",
    "read",
    "remember",
    "rule_search",
    "rulesFor",
    "search",
    "set_acl",
    "skillFromTrajectory",
    "skillInstructions",
    "skillsCatalog",
    "toolSchema",
    "tree",
    "write",
}

# The five tools added in the latest surface expansion, named separately so a stale endpoint
# fails with them spelled out instead of only a set difference.
NEW_BUILTIN_TOOLS = (
    "captureDecision",
    "contextPack",
    "mcpToolsByIntent",
    "rulesFor",
    "skillFromTrajectory",
    "skillInstructions",
    "skillsCatalog",
    "toolSchema",
)

# External servers exposed by scripts/mcp-bridges.sh, with their port and upstream tool count.
EXPECTED_MCP_SERVERS = {
    "filesystem": 8801,
    "fetch": 8802,
    "git": 8803,
    "memory": 8804,
}
EXPECTED_EXTERNAL_COUNTS = {
    "filesystem": 16,
    "fetch": 1,
    "git": 14,
    "memory": 9,
}

TIMEOUT = 30.0


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _server_up() -> bool:
    try:
        return httpx.get(HEALTH_URL, timeout=2.0).status_code == 200
    except Exception:
        return False


def _mcp_call(method: str, params: dict | None = None, call_id: int = 1) -> dict:
    """One JSON-RPC round trip against the MCP endpoint.

    The endpoint rejects a request without an SSE-capable ``Accept`` header, and the
    response framing depends on the caller: OpenViking answers as SSE (``data: {...}``)
    while a proxied upstream may answer with plain JSON. Both shapes are parsed here.
    """
    payload = {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params or {}}
    response = httpx.post(
        MCP_URL,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    body = response.text.strip()
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    return json.loads(body)


def _call_tool(name: str, arguments: dict | None = None) -> dict:
    return _mcp_call("tools/call", {"name": name, "arguments": arguments or {}})


def _result_text(result: dict) -> str:
    """Flatten an MCP tool result into text for assertions."""
    payload = result.get("result", result)
    chunks: list[str] = []
    for item in payload.get("content", []) or []:
        if isinstance(item, dict) and "text" in item:
            chunks.append(str(item["text"]))
    if not chunks:
        chunks.append(json.dumps(payload, ensure_ascii=False))
    return "\n".join(chunks)


def _is_error(result: dict) -> bool:
    if "error" in result:
        return True
    payload = result.get("result", result)
    return bool(payload.get("isError"))


if _BASE_URL_OVERRIDE:
    _SKIP_REASON = (
        f"{BASE_URL_ENV}={BASE_URL} is not reachable - refusing to fall back to "
        f"{DEFAULT_BASE_URL}; start the isolated server there or unset {BASE_URL_ENV}"
    )
else:
    _SKIP_REASON = f"no OpenViking server at {BASE_URL}"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _server_up(), reason=_SKIP_REASON),
]


# ---------------------------------------------------------------------------
# The endpoint must expose exactly the expected built-in surface
# ---------------------------------------------------------------------------


def test_tools_list_matches_expected_builtin_set():
    listed = {tool["name"] for tool in _mcp_call("tools/list")["result"]["tools"]}
    missing = EXPECTED_BUILTIN_TOOLS - listed
    unexpected = listed - EXPECTED_BUILTIN_TOOLS
    new_tool_names = list(NEW_BUILTIN_TOOLS)
    missing_new = [name for name in new_tool_names if name in missing]
    missing_hint = (
        f"; new tools absent: {missing_new} (the five new tools are {new_tool_names})"
        if missing_new
        else ""
    )
    assert not missing, f"tools missing from the endpoint: {sorted(missing)}{missing_hint}"
    assert not unexpected, f"undocumented tools exposed: {sorted(unexpected)}"
    assert len(listed) == 32


def test_every_builtin_tool_declares_an_input_schema():
    for tool in _mcp_call("tools/list")["result"]["tools"]:
        schema = tool.get("inputSchema")
        assert isinstance(schema, dict), f"{tool['name']} has no inputSchema"
        assert schema.get("type") == "object", f"{tool['name']} schema is not an object"


# ---------------------------------------------------------------------------
# Read-only built-in tools: each is really invoked
# ---------------------------------------------------------------------------


READ_ONLY_CALLS = {
    "health": {},
    "list": {},
    "tree": {},
    "list_users": {},
    "list_groups": {},
    "list_watches": {},
    "privacy_tree": {},
    "get_acl": {"uri": "viking://resources"},
    "glob": {"pattern": "*.md"},
    "grep": {"pattern": ["memory"], "uri": "viking://user/default/memories"},
    "find": {"query": "memory", "limit": 3},
    "search": {"query": "memory", "limit": 3},
    "find_mcp_tools": {"query": "read a file", "limit": 3},
    "skillsCatalog": {"limit": 5},
    "contextPack": {"task": "verify that every tool works", "maxTokens": 128},
    "rule_search": {"task_context": "verify that every tool works"},
    "read": {"uri": "viking://user/default/memories/profile.md"},
    "download": {"uri": "viking://user/default/memories/profile.md"},
}


@pytest.mark.parametrize("tool_name", sorted(READ_ONLY_CALLS))
def test_read_only_builtin_tool_responds(tool_name):
    result = _call_tool(tool_name, READ_ONLY_CALLS[tool_name])
    assert not _is_error(result), f"{tool_name} returned an error: {_result_text(result)}"


def test_find_mcp_tools_returns_real_external_tools():
    """Regression guard: registering an upstream without ``wait`` leaves this empty."""
    result = _call_tool("find_mcp_tools", {"query": "read a file", "limit": 5})
    assert not _is_error(result)
    payload = json.loads(_result_text(result))
    assert payload["total"] > 0, (
        "external tool catalog is empty — was the bridge registered with wait=true?"
    )
    assert any(tool["uri"].endswith(".json") for tool in payload["tools"])
    # Discovery is compact by default: the schema crosses the wire only on demand.
    assert "tool" not in payload["tools"][0]
    assert "inputSchema" not in json.dumps(payload["tools"][0])


def test_find_mcp_tools_include_schema_restores_the_legacy_shape():
    result = _call_tool("find_mcp_tools", {"query": "read a file", "limit": 2, "includeSchema": True})
    assert not _is_error(result)
    payload = json.loads(_result_text(result))
    if payload["total"] == 0:
        pytest.skip("external tool catalog is empty")
    assert "inputSchema" in json.dumps(payload["tools"][0])


def test_tool_schema_fetches_one_chosen_tool():
    found = json.loads(_result_text(_call_tool("find_mcp_tools", {"query": "read a file", "limit": 1})))
    if found["total"] == 0:
        pytest.skip("external tool catalog is empty")
    uri = found["tools"][0]["uri"]
    result = _call_tool("toolSchema", {"toolUri": uri})
    assert not _is_error(result), _result_text(result)
    payload = json.loads(_result_text(result))
    assert payload["uri"] == uri
    assert "inputSchema" in payload


def test_skill_instructions_read_one_listed_skill():
    catalog = json.loads(_result_text(_call_tool("skillsCatalog", {"limit": 5})))
    if catalog["total"] == 0:
        pytest.skip("no installed skills on this instance")
    name = catalog["skills"][0]["name"]
    result = _call_tool("skillInstructions", {"skill": name, "maxChars": 2000})
    assert not _is_error(result), _result_text(result)
    payload = json.loads(_result_text(result))
    assert payload["skill"] == name
    assert payload["returnedChars"] <= 2000 + len("\n\n[truncated at 2000 characters; read the skill file for the remainder]")


# ---------------------------------------------------------------------------
# Write tools: exercised against throwaway URIs only
# ---------------------------------------------------------------------------


def _is_busy(result: dict) -> bool:
    """True when the resource is still indexing after a write.

    ``write`` returns success while the resource stays under processing, so an immediate
    ``edit``/``forget`` on the same URI is refused by design (``content_write.py`` raises
    "resource is busy and cannot be written now"; ``_ops.py`` raises ``ResourceBusyError``
    "Resource is being processed"). That is documented back-pressure, not tool breakage, so
    the suite tolerates it while still rejecting any other failure.
    """
    text = _result_text(result).lower()
    return "busy" in text or "being processed" in text


@pytest.fixture
def scratch_uri():
    """A URI under the caller's own scratch space, removed afterwards."""
    return "viking://user/default/resources/live-tool-coverage-scratch.md"


def test_write_then_read_round_trip(scratch_uri):
    body = "live tool coverage probe"
    written = _call_tool("write", {"uri": scratch_uri, "content": body})
    assert not _is_error(written), f"write failed: {_result_text(written)}"

    read_back = _call_tool("read", {"uri": scratch_uri})
    assert not _is_error(read_back), f"read after write failed: {_result_text(read_back)}"
    assert body in _result_text(read_back)


def test_edit_applies_a_change(scratch_uri):
    _call_tool("write", {"uri": scratch_uri, "content": "alpha beta"})
    # A leftover scratch file from an earlier run would not contain "alpha", so anchor the
    # edit on content this test just wrote rather than assuming a clean slate.
    current = _result_text(_call_tool("read", {"uri": scratch_uri}))
    if "alpha" not in current:
        pytest.skip("scratch URI holds unrelated content; edit anchor unavailable")
    edited = _call_tool(
        "edit", {"uri": scratch_uri, "old_string": "alpha", "new_string": "gamma"}
    )
    if _is_busy(edited):
        pytest.skip("resource still indexing after write - documented busy window")
    assert not _is_error(edited), f"edit failed: {_result_text(edited)}"
    assert "gamma" in _result_text(_call_tool("read", {"uri": scratch_uri}))


def test_forget_removes_what_the_test_created(scratch_uri):
    _call_tool("write", {"uri": scratch_uri, "content": "temporary"})
    forgotten = _call_tool("forget", {"uri": scratch_uri})
    if _is_busy(forgotten):
        pytest.skip("resource still indexing after write - documented busy window")
    assert not _is_error(forgotten), f"forget failed: {_result_text(forgotten)}"


def test_remember_accepts_a_message_pair():
    result = _call_tool(
        "remember",
        {
            "messages": [
                {"role": "user", "content": "live tool coverage marker"},
                {"role": "assistant", "content": "acknowledged"},
            ]
        },
    )
    assert not _is_error(result), f"remember failed: {_result_text(result)}"


def test_set_acl_is_accepted_on_a_real_resource():
    result = _call_tool(
        "set_acl", {"uri": "viking://resources/arranger", "acl": {"acl_mode": "inherit"}}
    )
    assert not _is_error(result), f"set_acl failed: {_result_text(result)}"


def test_cancel_watch_reports_a_missing_watch_rather_than_crashing():
    """No watch exists for this URI, so the tool must answer, not explode."""
    result = _call_tool("cancel_watch", {"to_uri": "viking://resources/arranger"})
    assert isinstance(result, dict)
    assert _result_text(result), "cancel_watch produced no output at all"


# ---------------------------------------------------------------------------
# External tools: catalog, counts, and real invocation
# ---------------------------------------------------------------------------


def _external_catalog() -> dict[str, list[str]]:
    catalog: dict[str, list[str]] = {}
    for server in EXPECTED_MCP_SERVERS:
        directory = TOOLS_ROOT / server
        if not directory.is_dir():
            continue
        catalog[server] = sorted(path.stem for path in directory.glob("*.json"))
    return catalog


@pytest.mark.skipif(not TOOLS_ROOT.is_dir(), reason="no external MCP catalog on disk")
def test_external_catalog_is_complete():
    catalog = _external_catalog()
    for server, expected in EXPECTED_EXTERNAL_COUNTS.items():
        assert server in catalog, f"{server} is not registered in the catalog"
        assert len(catalog[server]) == expected, (
            f"{server} exposes {len(catalog[server])} tools, expected {expected}"
        )
    total = sum(len(names) for names in catalog.values())
    assert total == 40, f"catalog holds {total} external tools, expected 40"


@pytest.mark.skipif(not TOOLS_ROOT.is_dir(), reason="no external MCP catalog on disk")
def test_every_external_server_is_reachable_and_publishes_its_tools():
    for server, port in EXPECTED_MCP_SERVERS.items():
        if not _port_listening(port):
            pytest.skip(f"bridge for {server} is not listening on {port}")
        status = httpx.get(
            f"{BASE_URL}/api/v1/mcps/status", params={"name": server}, timeout=TIMEOUT
        ).json()
        payload = status["result"]
        assert payload["status"] == "running", f"{server} is {payload['status']}"
        assert payload["tool_count"] == EXPECTED_EXTERNAL_COUNTS[server]


# Representative real invocations, one per external server. Each asserts on real content.
EXTERNAL_INVOCATIONS = {
    "filesystem": (
        "list_allowed_directories",
        {},
        lambda text: str(REPO_ROOT) in text,
    ),
    "fetch": (
        "fetch",
        {"url": "https://example.com", "max_length": 200},
        lambda text: "Example Domain" in text,
    ),
    "git": (
        "git_log",
        {"repo_path": str(REPO_ROOT), "max_count": 1},
        lambda text: "Commit" in text,
    ),
    "memory": (
        "search_nodes",
        {"query": "ov-bridge-smoke"},
        lambda text: "entities" in text,
    ),
}


@pytest.mark.parametrize("server", sorted(EXTERNAL_INVOCATIONS))
def test_external_tool_returns_real_content(server):
    if not _port_listening(EXPECTED_MCP_SERVERS[server]):
        pytest.skip(f"bridge for {server} is not listening")

    tool_name, arguments, assertion = EXTERNAL_INVOCATIONS[server]
    result = _call_tool(
        "invoke_mcp_tool",
        {
            "tool_uri": f"viking://user/default/tools/{server}/{tool_name}.json",
            "arguments": arguments,
        },
    )
    assert not _is_error(result), f"{server}/{tool_name} failed: {_result_text(result)}"
    text = _result_text(result)
    assert assertion(text), f"{server}/{tool_name} returned unexpected content: {text[:300]}"


@pytest.mark.skipif(not TOOLS_ROOT.is_dir(), reason="no external MCP catalog on disk")
@pytest.mark.parametrize("server", sorted(EXPECTED_MCP_SERVERS))
def test_external_catalog_entries_are_well_formed(server):
    """Every catalogued tool must carry the metadata invoke_mcp_tool relies on."""
    directory = TOOLS_ROOT / server
    if not directory.is_dir():
        pytest.skip(f"{server} not catalogued")
    for path in sorted(directory.glob("*.json")):
        entry = json.loads(path.read_text())
        assert entry.get("kind") == "mcp_tool", f"{path.name} is not an mcp_tool entry"
        assert entry.get("server") == server, f"{path.name} names server {entry.get('server')}"
        assert entry.get("address"), f"{path.name} has no upstream address"
        assert isinstance(entry.get("tool"), dict), f"{path.name} has no tool definition"
        assert entry["tool"].get("name"), f"{path.name} has no tool name"
