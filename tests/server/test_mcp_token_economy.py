# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Zero-token discovery: deferred schemas, skill catalog, bounded instructions.

The MCP surface pays for discovery before an agent can act, so these tests pin
the cheap shapes: discovery never carries an upstream schema unless asked, the
schema fetch is a separate bounded call, the skill catalog carries no bodies, and
skill instructions are clamped and refuse path traversal. ContextPack savings
are asserted against the assembler arithmetic, not a decorative number.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server import mcp_ops
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier

CTX = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)


@pytest.fixture(autouse=True)
def _identity_and_service(service):
    set_service(service)
    token = mcp_endpoint._mcp_ctx.set(CTX)
    yield
    mcp_endpoint._mcp_ctx.reset(token)
    set_service(None)


async def _wire_call(name: str, arguments: dict) -> dict:
    handler = mcp_endpoint.mcp._mcp_server.request_handlers[CallToolRequest]
    request = CallToolRequest(params=CallToolRequestParams(name=name, arguments=arguments))
    return (await handler(request)).root.model_dump(by_alias=True, exclude_none=True)


# -- find_mcp_tools ---------------------------------------------------------


async def test_find_mcp_tools_defaults_to_compact_descriptors(monkeypatch, service):
    captured = {}

    async def fake_find(query, limit, ctx, *, include_definition=False, target_uri=None):
        captured["include_definition"] = include_definition
        return {
            "tools": [
                {
                    "uri": "viking://user/default/tools/fs/read.json",
                    "server": "filesystem",
                    "name": "read_file",
                    "description": "Read one file from the mounted tree.",
                    "score": 0.91,
                    "revision": "r1",
                }
            ],
            "total": 1,
        }

    monkeypatch.setattr(service.mcps, "find", fake_find)
    payload = await _wire_call("find_mcp_tools", {"query": "read a file"})
    data = json.loads(payload["content"][0]["text"])
    assert captured["include_definition"] is False
    assert data["includeSchema"] is False
    assert data["tools"][0]["name"] == "read_file"
    assert "tool" not in data["tools"][0]
    assert "inputSchema" not in json.dumps(data)


async def test_find_mcp_tools_include_schema_is_the_legacy_shape(monkeypatch, service):
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    async def fake_find(query, limit, ctx, *, include_definition=False, target_uri=None):
        assert include_definition is True
        return {
            "tools": [
                {
                    "uri": "viking://user/default/tools/fs/read.json",
                    "server": "filesystem",
                    "name": "read_file",
                    "description": "Read one file.",
                    "score": 0.9,
                    "revision": "r1",
                    "tool": {"name": "read_file", "description": "Read one file.", "inputSchema": schema},
                }
            ],
            "total": 1,
        }

    monkeypatch.setattr(service.mcps, "find", fake_find)
    payload = await _wire_call("find_mcp_tools", {"query": "file", "includeSchema": True})
    data = json.loads(payload["content"][0]["text"])
    assert data["tools"][0]["tool"]["inputSchema"] == schema


# -- toolSchema -------------------------------------------------------------


async def test_tool_schema_returns_only_the_requested_definition(monkeypatch, service):
    definition = {
        "uri": "viking://user/default/tools/fs/read.json",
        "server": "filesystem",
        "name": "read_file",
        "description": "Read one file.",
        "inputSchema": {"type": "object"},
        "revision": "r1",
    }
    service.mcps.definition = AsyncMock(return_value=definition)
    result = await mcp_ops.tool_schema(
        service, CTX, tool_uri="viking://user/default/tools/fs/read.json"
    )
    assert result["payload"]["inputSchema"] == {"type": "object"}
    assert result["payload"]["truncated"] is False


async def test_tool_schema_refuses_a_non_catalogued_uri(service):
    with pytest.raises(InvalidArgumentError):
        await mcp_ops.tool_schema(service, CTX, tool_uri="https://example.com/tool")


async def test_tool_schema_bounds_an_oversized_schema(monkeypatch, service):
    service.mcps.definition = AsyncMock(
        return_value={
            "uri": "viking://user/default/tools/big/tool.json",
            "server": "big",
            "name": "huge",
            "description": "huge schema",
            "inputSchema": {"properties": {f"k{index}": {"type": "string"} for index in range(2000)}},
        }
    )
    result = await mcp_ops.tool_schema(service, CTX, tool_uri="viking://user/default/tools/big/tool.json")
    assert result["payload"]["truncated"] is True
    assert result["payload"]["inputSchema"]["truncated"] is True


# -- skillsCatalog / skillInstructions --------------------------------------


async def test_skill_catalog_is_compact_and_clamped(monkeypatch, service):
    long_description = "x" * 500
    rows = [
        {
            "name": "pdf-processing",
            "description": long_description,
            "root_uri": "viking://user/test_user/skills/pdf-processing",
        },
        {
            "name": "web-standards-audit",
            "description": "Audit web standards.",
            "root_uri": "viking://agent/skills/web-standards-audit",
        },
    ]

    async def fake_catalog(*args, **kwargs):
        return rows

    import openviking.server.routers.skills as skills_router

    monkeypatch.setattr(skills_router, "list_skill_catalog", fake_catalog)
    result = await mcp_ops.skill_catalog(service, CTX, limit=10, target_uri=None)
    first = result["payload"]["skills"][0]
    assert first["name"] == "pdf-processing"
    assert len(first["description"]) <= mcp_ops.MAX_SKILL_CATALOG_DESCRIPTION_CHARS + 3
    assert first["uri"].startswith("viking://")
    assert "content" not in json.dumps(result["payload"])


async def test_skill_instructions_refuse_traversal_and_clamp_the_body(monkeypatch, service):
    fake = SimpleNamespace(
        viking_fs=SimpleNamespace(exists=AsyncMock(return_value=True)),
        fs=SimpleNamespace(read=AsyncMock(return_value="a" * 50_000)),
    )

    result = await mcp_ops.skill_instructions(fake, CTX, skill="pdf-processing", max_chars=1000)
    assert result["payload"]["truncated"] is True
    assert result["payload"]["returnedChars"] <= mcp_ops.MAX_SKILL_INSTRUCTIONS_CHARS
    assert "[truncated at 1000 characters" in result["text"]

    with pytest.raises(InvalidArgumentError):
        await mcp_ops.skill_instructions(
            fake, CTX, skill="pdf-processing", reference="../../etc/passwd"
        )


async def test_skill_instructions_unknown_skill_is_not_found(service):
    fake = SimpleNamespace(
        viking_fs=SimpleNamespace(exists=AsyncMock(return_value=False)),
        fs=SimpleNamespace(read=AsyncMock(return_value=None)),
    )
    with pytest.raises(NotFoundError):
        await mcp_ops.skill_instructions(fake, CTX, skill="missing-skill")


# -- contextPack savings ----------------------------------------------------


async def test_context_pack_reports_exact_savings(monkeypatch, service):
    class _Assembled:
        entries = [SimpleNamespace(uri="viking://user/test_user/memories/a.md", category="memory", score=0.9)]
        rendered = "packed body"
        digest = ""
        stats = {
            "used_tokens": 40,
            "candidate_tokens": 100,
            "tokens_saved": 60,
            "dropped_tokens": 60,
            "deduped_tokens": 0,
        }

    async def fake_assemble(*, service, ctx, params):
        return _Assembled()

    monkeypatch.setattr(mcp_ops, "assemble_context", fake_assemble)
    result = await mcp_ops.context_pack(service, CTX, task="summarize", max_tokens=500)
    savings = result["payload"]["savings"]
    assert savings["candidateTokens"] == 100
    assert savings["usedTokens"] == 40
    assert savings["tokensSaved"] == 60
    assert savings["candidateTokens"] - savings["usedTokens"] == savings["tokensSaved"]