# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the five high-level MCP operations in openviking/server/mcp_ops.py.

Each operation is exercised three ways where it matters: the success path, the
argument/semantic REJECT path (typed error, never an empty success), and the MCP
wire path where a business error must become ``CallToolResult(isError=True)``
with the preserved error code. Output bounds are asserted so a future change
cannot turn one call into a corpus dump.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.core.skill_loader import SkillLoader
from openviking.server import mcp_ops
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import FindResult
from openviking_cli.session.user_id import UserIdentifier

CTX = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)


@pytest.fixture(autouse=True)
def _identity_and_service(service):
    """Wire the real in-process service (fake embedder/VLM) and the identity ctx."""
    set_service(service)
    token = mcp_endpoint._mcp_ctx.set(CTX)
    yield
    mcp_endpoint._mcp_ctx.reset(token)
    set_service(None)


def _entries(uris):
    return [
        SimpleNamespace(uri=uri, category="memory", score=0.9 - index / 1000)
        for index, uri in enumerate(uris)
    ]


class _Assembled:
    def __init__(self, entries=None, rendered="", digest="", stats=None):
        self.entries = entries or []
        self.rendered = rendered
        self.digest = digest
        self.stats = stats if stats is not None else {"used_tokens": 12}


async def _wire_call(name: str, arguments: dict) -> dict:
    """Call a tool through the MCP request handler (the real wire path)."""
    handler = mcp_endpoint.mcp._mcp_server.request_handlers[CallToolRequest]
    request = CallToolRequest(params=CallToolRequestParams(name=name, arguments=arguments))
    return (await handler(request)).root.model_dump(by_alias=True, exclude_none=True)


# ---------------------------------------------------------------------------
# contextPack
# ---------------------------------------------------------------------------


async def test_context_pack_returns_injection_block_with_provenance(monkeypatch):
    captured = {}
    kept = "viking://user/test_user/memories/preferences/style.md"
    dropped = "viking://user/test_user/memories/events/2026/01/01/launch.md"

    async def fake_assemble(*, service, ctx, params):
        captured["params"] = params
        return _Assembled(
            entries=_entries([kept, dropped, kept]),
            rendered="[style] Be concise.",
        )

    monkeypatch.setattr(mcp_ops, "assemble_context", fake_assemble)

    result = await mcp_endpoint.context_pack(task="write a concise status update", maxTokens=512)

    assert result.content[0].text == "[style] Be concise."
    payload = result.structuredContent
    assert payload["maxTokens"] == 512
    assert payload["mode"] == "context"
    assert payload["usedTokens"] == 12
    assert [item["uri"] for item in payload["provenance"]] == [kept, dropped]
    assert payload["truncated"] is False
    assert "contextChars" in payload
    assert "notice" not in payload
    params = captured["params"]
    assert params.query == "write a concise status update"
    assert params.max_tokens == 512
    assert params.session_id is None
    assert params.query_expansion == "off"
    assert params.rewrite is False


async def test_context_pack_bounds_provenance(monkeypatch):
    uris = [f"viking://user/test_user/memories/events/e-{index}.md" for index in range(150)]

    async def fake_assemble(*, service, ctx, params):
        return _Assembled(entries=_entries(uris), rendered="block")

    monkeypatch.setattr(mcp_ops, "assemble_context", fake_assemble)

    result = await mcp_endpoint.context_pack(task="anything", maxTokens=999)
    payload = result.structuredContent
    assert len(payload["provenance"]) == mcp_ops.MAX_CONTEXT_PROVENANCE_URIS
    assert payload["truncated"] is True
    assert payload["entryCount"] == 150


async def test_context_pack_reports_empty_result_as_notice_not_failure(monkeypatch):
    async def fake_assemble(*, service, ctx, params):
        return _Assembled(entries=[], rendered="", digest="")

    monkeypatch.setattr(mcp_ops, "assemble_context", fake_assemble)

    result = await mcp_endpoint.context_pack(task="unknown topic", maxTokens=128)
    assert result.content[0].text == "No matching context found."
    assert result.structuredContent["notice"] == "No matching context found."
    assert result.structuredContent["provenance"] == []


async def test_context_pack_runs_the_real_assembler_against_an_empty_store(service, monkeypatch):
    async def empty_find(**kwargs):
        del kwargs
        return FindResult(memories=[], resources=[], skills=[])

    monkeypatch.setattr(service.search, "find", empty_find)

    result = await mcp_endpoint.context_pack(
        task="nothing has been stored in this account yet", maxTokens=128
    )

    payload = result.structuredContent
    assert payload["mode"] == "context"
    assert payload["maxTokens"] == 128
    assert payload["provenance"] == []
    assert payload["notice"] == "No matching context found."
    assert result.content[0].text == "No matching context found."


@pytest.mark.parametrize("budget", [0, -1, 32_001, True, "256"])
async def test_context_pack_rejects_invalid_token_budget(budget):
    with pytest.raises(InvalidArgumentError, match="maxTokens"):
        await mcp_endpoint.context_pack(task="anything", maxTokens=budget)


@pytest.mark.parametrize("task", ["", "   ", None, 42])
async def test_context_pack_rejects_empty_or_non_string_task(task):
    with pytest.raises(InvalidArgumentError, match="task"):
        await mcp_endpoint.context_pack(task=task, maxTokens=128)


async def test_context_pack_wire_error_carries_code():
    result = await _wire_call("contextPack", {"task": "anything", "maxTokens": 0})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "INVALID_ARGUMENT"
    assert result["structuredContent"]["error"]["details"]["field"] == "maxTokens"


# ---------------------------------------------------------------------------
# rulesFor
# ---------------------------------------------------------------------------


async def test_rules_for_returns_rendered_rules_and_provenance(service, monkeypatch):
    rule_uri = "viking://user/test_user/memories/rules/confirm-delete.md"
    search = AsyncMock(
        return_value={
            "rules": [
                {
                    "atomic_id": "confirm-delete",
                    "uri": rule_uri,
                    "probability": 0.91,
                    "content": "# Confirm before deleting",
                }
            ],
            "rendered": "Rule: confirm before deleting files.",
        }
    )
    monkeypatch.setattr(service.rules, "search_rules", search)

    result = await mcp_endpoint.rules_for(task="delete a generated file", ruleProbabilityThreshold=0.5)

    assert result.content[0].text == "Rule: confirm before deleting files."
    payload = result.structuredContent
    assert payload["ruleCount"] == 1
    assert payload["ruleProbabilityThreshold"] == 0.5
    assert payload["provenance"] == [
        {"uri": rule_uri, "atomicId": "confirm-delete", "probability": 0.91}
    ]
    request = search.await_args.args[0]
    assert request.task_context == "delete a generated file"
    assert request.rule_probability_threshold == 0.5


async def test_rules_for_raises_typed_configuration_error_when_empty(service, monkeypatch):
    monkeypatch.setattr(
        service.rules,
        "search_rules",
        AsyncMock(return_value={"rules": [], "rendered": "No active compiled Rule..."}),
    )

    with pytest.raises(mcp_ops.RuleCorpusNotCompiledError) as excinfo:
        await mcp_endpoint.rules_for(task="delete a generated file")

    assert excinfo.value.code == "RULE_CORPUS_NOT_COMPILED"
    assert excinfo.value.details["requiredAction"] == "compile_rules"
    assert excinfo.value.details["ruleProbabilityThreshold"] == 0.65


async def test_rules_for_wire_error_keeps_the_configuration_code(service, monkeypatch):
    monkeypatch.setattr(
        service.rules,
        "search_rules",
        AsyncMock(return_value={"rules": [], "rendered": "No active compiled Rule..."}),
    )

    result = await _wire_call("rulesFor", {"task": "delete a generated file"})

    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "RULE_CORPUS_NOT_COMPILED"


async def test_rules_for_bounds_rendered_text(service, monkeypatch):
    monkeypatch.setattr(
        service.rules,
        "search_rules",
        AsyncMock(
            return_value={
                "rules": [{"atomic_id": "big", "uri": "viking://x/big.md", "probability": 0.9}],
                "rendered": "R" * (mcp_ops.MAX_RULE_TEXT_CHARS + 500),
            }
        ),
    )

    result = await mcp_endpoint.rules_for(task="anything")
    text = result.content[0].text
    assert result.structuredContent["truncated"] is True
    assert len(text) > mcp_ops.MAX_RULE_TEXT_CHARS
    assert "[truncated at" in text
    assert result.structuredContent["renderedChars"] == mcp_ops.MAX_RULE_TEXT_CHARS + 500


@pytest.mark.parametrize("threshold", [1.01, -0.01, True, "0.5", None])
async def test_rules_for_rejects_invalid_threshold(threshold):
    with pytest.raises(InvalidArgumentError, match="ruleProbabilityThreshold"):
        await mcp_endpoint.rules_for(task="anything", ruleProbabilityThreshold=threshold)


@pytest.mark.parametrize("task", ["", "   ", None])
async def test_rules_for_rejects_empty_task(task):
    with pytest.raises(InvalidArgumentError, match="task"):
        await mcp_endpoint.rules_for(task=task)


# ---------------------------------------------------------------------------
# captureDecision
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self):
        self.messages = []

    async def add_message_async(self, role, parts, peer_id=None, created_at=None):
        self.messages.append((role, parts))


def _mock_extraction(service, monkeypatch, *, task_id="task-decision-1", **result):
    session = _FakeSession()
    commit = AsyncMock(return_value={"task_id": task_id, "status": "accepted", **result})
    monkeypatch.setattr(service.sessions, "open", AsyncMock(return_value=session))
    monkeypatch.setattr(service.sessions, "commit_async", commit)
    return session, commit


async def test_capture_decision_writes_memory_uri_and_uses_remember_path(
    service, monkeypatch
):
    session, commit = _mock_extraction(service, monkeypatch)

    result = await mcp_endpoint.capture_decision(
        summary="Adopt digest-addressed decision records.",
        evidence=["viking://user/test_user/memories/events/decision.md"],
    )

    payload = result.structuredContent
    memory_uri = payload["memoryUri"]
    assert payload["created"] is True
    assert payload["digest"] == mcp_ops._decision_digest(
        "Adopt digest-addressed decision records.",
        ["viking://user/test_user/memories/events/decision.md"],
    )
    assert memory_uri.startswith("viking://user/test_user/memories/events/decisions/")
    assert memory_uri.endswith(".md")
    assert payload["extraction"]["taskId"] == "task-decision-1"
    assert payload["extraction"]["sessionId"].startswith("mcp-decision-")

    stored = await service.fs.read(memory_uri, ctx=CTX)
    assert "Adopt digest-addressed decision records." in stored
    assert payload["digest"] in stored
    assert len(session.messages) == 1
    assert session.messages[0][0] == "user"
    assert "Decision worth persisting." in session.messages[0][1][0].text
    assert commit.await_args.args[0] == payload["extraction"]["sessionId"]


async def test_capture_decision_is_idempotent_by_content_digest(service, monkeypatch):
    _, commit = _mock_extraction(service, monkeypatch)
    arguments = {
        "summary": "The queue stays process-local.",
        "evidence": ["viking://user/test_user/memories/events/q.md"],
    }

    first = await mcp_endpoint.capture_decision(**arguments)
    second = await mcp_endpoint.capture_decision(**arguments)

    assert first.structuredContent["created"] is True
    assert second.structuredContent["created"] is False
    assert second.structuredContent["idempotent"] is True
    assert second.structuredContent["memoryUri"] == first.structuredContent["memoryUri"]
    assert second.structuredContent["extraction"] is None
    assert first.structuredContent["digest"] == second.structuredContent["digest"]
    commit.assert_awaited_once()


async def test_capture_decision_digest_changes_with_content(service, monkeypatch):
    _mock_extraction(service, monkeypatch)

    first = await mcp_endpoint.capture_decision(summary="One", evidence=["a"])
    second = await mcp_endpoint.capture_decision(summary="Two", evidence=["a"])

    assert first.structuredContent["digest"] != second.structuredContent["digest"]
    assert first.structuredContent["memoryUri"] != second.structuredContent["memoryUri"]


async def test_capture_decision_fails_typed_when_extraction_accepts_nothing(
    service, monkeypatch
):
    _mock_extraction(service, monkeypatch, task_id=None, status="skipped", reason="no_messages")

    with pytest.raises(mcp_ops.MemoryExtractionUnavailableError) as excinfo:
        await mcp_endpoint.capture_decision(summary="A decision", evidence=["e"])

    assert excinfo.value.code == "MEMORY_EXTRACTION_UNAVAILABLE"
    assert excinfo.value.details["reason"] == "no_messages"


@pytest.mark.parametrize(
    "summary,evidence",
    [
        ("", ["e"]),
        ("   ", ["e"]),
        (None, ["e"]),
        ("decision", "not-a-list"),
        ("decision", [""]),
        ("decision", [123]),
        ("decision", [f"e{index}" for index in range(51)]),
        ("decision", ["x" * 2_001]),
    ],
)
async def test_capture_decision_rejects_invalid_arguments(summary, evidence):
    with pytest.raises(InvalidArgumentError):
        await mcp_endpoint.capture_decision(summary=summary, evidence=evidence)


# ---------------------------------------------------------------------------
# mcpToolsByIntent
# ---------------------------------------------------------------------------


def _find_hit(
    uri="viking://user/test_user/tools/weather/current.json",
    *,
    description="Get the current weather",
    property_count=3,
):
    properties = {f"p{index}": {"type": "string"} for index in range(property_count)}
    return {
        "uri": uri,
        "server": "weather",
        "score": 0.88,
        "tool": {
            "name": "current",
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": ["city"],
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
    }


async def test_mcp_tools_by_intent_returns_bounded_descriptors(service, monkeypatch):
    find = AsyncMock(return_value={"tools": [_find_hit(description="d" * 900)], "total": 1})
    monkeypatch.setattr(service.mcps, "find", find)

    result = await mcp_endpoint.mcp_tools_by_intent(intent="get the weather", limit=5)

    payload = result.structuredContent
    assert payload["intent"] == "get the weather"
    assert payload["total"] == 1
    descriptor = payload["tools"][0]
    assert descriptor["uri"].endswith("current.json")
    assert descriptor["name"] == "current"
    assert descriptor["readOnlyHint"] is True
    assert descriptor["required"] == ["city"]
    assert len(descriptor["description"]) <= mcp_ops.MAX_MCP_TOOL_DESCRIPTION_CHARS + 3
    assert "inputProperties" in descriptor
    assert "inputSchema" not in descriptor
    find.assert_awaited_once_with("get the weather", 5, CTX, include_definition=True)


async def test_mcp_tools_by_intent_caps_property_names(service, monkeypatch):
    monkeypatch.setattr(
        service.mcps,
        "find",
        AsyncMock(return_value={"tools": [_find_hit(property_count=80)], "total": 1}),
    )

    result = await mcp_endpoint.mcp_tools_by_intent(intent="anything")
    descriptor = result.structuredContent["tools"][0]
    assert len(descriptor["inputProperties"]) == mcp_ops.MAX_MCP_TOOL_PROPERTIES


async def test_mcp_tools_by_intent_invokes_a_selected_tool(service, monkeypatch):
    hit = _find_hit()
    monkeypatch.setattr(service.mcps, "find", AsyncMock(return_value={"tools": [hit], "total": 1}))
    invoke = AsyncMock(
        return_value={
            "content": [{"type": "text", "text": "23C and sunny"}],
            "isError": False,
            "structuredContent": {"temperature": 23},
        }
    )
    monkeypatch.setattr(service.mcps, "invoke", invoke)

    result = await mcp_endpoint.mcp_tools_by_intent(
        intent="get the weather",
        invoke=True,
        toolUri=hit["uri"],
        arguments={"city": "Beijing"},
    )

    invoked = result.structuredContent["invoked"]
    assert invoked["toolUri"] == hit["uri"]
    assert invoked["text"] == "23C and sunny"
    assert invoked["isError"] is False
    assert invoked["structuredContent"] == {"temperature": 23}
    invoke.assert_awaited_once_with(hit["uri"], {"city": "Beijing"}, CTX)


async def test_mcp_tools_by_intent_refuses_invoking_an_unselected_tool(service, monkeypatch):
    monkeypatch.setattr(
        service.mcps,
        "find",
        AsyncMock(return_value={"tools": [_find_hit()], "total": 1}),
    )
    invoke = AsyncMock()
    monkeypatch.setattr(service.mcps, "invoke", invoke)

    with pytest.raises(InvalidArgumentError, match="toolUri"):
        await mcp_endpoint.mcp_tools_by_intent(
            intent="get the weather",
            invoke=True,
            toolUri="viking://user/test_user/tools/other/secret.json",
        )
    invoke.assert_not_awaited()


async def test_mcp_tools_by_intent_bounds_invocation_output(service, monkeypatch):
    hit = _find_hit()
    monkeypatch.setattr(service.mcps, "find", AsyncMock(return_value={"tools": [hit], "total": 1}))
    monkeypatch.setattr(
        service.mcps,
        "invoke",
        AsyncMock(
            return_value={
                "content": [
                    {"type": "text", "text": "T" * 9_000},
                    {"type": "image", "data": "AAAA", "mimeType": "image/png"},
                ],
                "structuredContent": {"blob": "S" * 5_000},
            }
        ),
    )

    result = await mcp_endpoint.mcp_tools_by_intent(
        intent="get the weather", invoke=True, toolUri=hit["uri"]
    )
    invoked = result.structuredContent["invoked"]
    assert len(invoked["text"]) == mcp_ops.MAX_MCP_INVOKE_TEXT_CHARS
    assert invoked["textTruncated"] is True
    assert invoked["nonTextBlocks"] == 1
    assert invoked["structuredContent"] is None
    assert invoked["structuredTruncated"] is True


@pytest.mark.parametrize("intent", ["", "   ", None, 7])
async def test_mcp_tools_by_intent_refuses_empty_intent(intent):
    with pytest.raises(InvalidArgumentError, match="intent"):
        await mcp_endpoint.mcp_tools_by_intent(intent=intent)


@pytest.mark.parametrize("limit", [0, -1, 51, True, "5"])
async def test_mcp_tools_by_intent_rejects_invalid_limit(limit):
    with pytest.raises(InvalidArgumentError, match="limit"):
        await mcp_endpoint.mcp_tools_by_intent(intent="anything", limit=limit)


async def test_mcp_tools_by_intent_raises_typed_error_for_an_unmatched_intent(
    service, monkeypatch
):
    monkeypatch.setattr(
        service.mcps, "find", AsyncMock(return_value={"tools": [], "total": 0})
    )

    with pytest.raises(mcp_ops.McpToolCatalogEmptyError) as excinfo:
        await mcp_endpoint.mcp_tools_by_intent(intent="fly a kite to Mars")

    assert excinfo.value.code == "MCP_TOOL_CATALOG_EMPTY"
    assert excinfo.value.details["requiredAction"] == "register_mcp_servers"


async def test_mcp_tools_by_intent_wire_error_keeps_catalog_code(service, monkeypatch):
    monkeypatch.setattr(
        service.mcps, "find", AsyncMock(return_value={"tools": [], "total": 0})
    )

    result = await _wire_call("mcpToolsByIntent", {"intent": "fly a kite to Mars"})

    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "MCP_TOOL_CATALOG_EMPTY"


@pytest.mark.parametrize(
    "invoke,arguments",
    [("yes", None), (False, ["not", "an", "object"])],
)
async def test_mcp_tools_by_intent_rejects_invalid_invoke_flags(invoke, arguments):
    with pytest.raises(InvalidArgumentError):
        await mcp_endpoint.mcp_tools_by_intent(
            intent="anything", invoke=invoke, arguments=arguments
        )


# ---------------------------------------------------------------------------
# skillFromTrajectory
# ---------------------------------------------------------------------------


async def test_skill_from_trajectory_creates_and_verifies_a_skill(service):
    citation = "viking://user/test_user/memories/experiences/login-retry.md"

    result = await mcp_endpoint.skill_from_trajectory(
        title="Fix flaky login retry",
        steps=[
            "Reproduce the timeout with the recorded fixture.",
            "Retry only idempotent requests and assert the call count.",
        ],
        citations=[citation],
    )

    payload = result.structuredContent
    assert payload["verified"] is True
    assert payload["name"] == "fix-flaky-login-retry"
    assert payload["description"] == "Fix flaky login retry"
    assert payload["steps"] == 2
    assert payload["citations"] == 1
    assert payload["skillUri"].endswith("/fix-flaky-login-retry/SKILL.md")
    assert result.content[0].text.startswith("Skill 'fix-flaky-login-retry' created and verified")

    stored = await service.fs.read(payload["skillUri"], ctx=CTX)
    parsed = SkillLoader.parse(stored)
    assert parsed["name"] == "fix-flaky-login-retry"
    assert parsed["description"] == "Fix flaky login retry"
    assert "Reproduce the timeout" in parsed["content"]
    assert citation in parsed["content"]


async def test_skill_from_trajectory_raises_when_readback_does_not_match(service, monkeypatch):
    monkeypatch.setattr(
        service.fs,
        "read",
        AsyncMock(return_value="---\nname: someone-else\ndescription: Different\n---\nBody"),
    )

    with pytest.raises(mcp_ops.SkillVerificationError) as excinfo:
        await mcp_endpoint.skill_from_trajectory(
            title="A valid title", steps=["one step"]
        )
    assert excinfo.value.code == "SKILL_VERIFICATION_FAILED"


async def test_skill_from_trajectory_output_is_bounded(service):
    long_step = "S" * mcp_ops.MAX_SKILL_STEP_CHARS
    result = await mcp_endpoint.skill_from_trajectory(
        title="Bounded output skill",
        steps=[long_step] * 10,
        citations=["viking://user/test_user/memories/experiences/e.md"],
    )
    encoded = json.dumps(result.structuredContent)
    assert len(encoded) < 4_000
    assert result.structuredContent["steps"] == 10
    assert long_step not in encoded


@pytest.mark.parametrize(
    "title,steps,citations",
    [
        ("", ["step"], []),
        ("   ", ["step"], []),
        ("!!!", ["step"], []),
        (None, ["step"], []),
        ("title", [], []),
        ("title", "not-a-list", []),
        ("title", [""], []),
        ("title", [123], []),
        ("title", ["step"], "not-a-list"),
        ("title", ["step"], [""]),
        ("title", ["step"], [f"c{index}" for index in range(101)]),
        ("t" * (mcp_ops.MAX_SKILL_TITLE_CHARS + 1), ["step"], []),
        ("title", ["s"] * (mcp_ops.MAX_SKILL_STEPS + 1), []),
    ],
)
async def test_skill_from_trajectory_rejects_malformed_input(title, steps, citations):
    with pytest.raises(InvalidArgumentError):
        await mcp_endpoint.skill_from_trajectory(title=title, steps=steps, citations=citations)


# ---------------------------------------------------------------------------
# registration surface
# ---------------------------------------------------------------------------


async def test_new_operations_are_registered_with_annotations():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}

    assert {
        "contextPack",
        "rulesFor",
        "captureDecision",
        "mcpToolsByIntent",
        "skillFromTrajectory",
    } <= set(tools)

    for name in ("contextPack", "rulesFor"):
        assert tools[name].annotations.readOnlyHint is True, name
        assert tools[name].annotations.destructiveHint is False, name
    # These write, or can invoke an upstream tool, so they are not read-only.
    for name in ("captureDecision", "skillFromTrajectory", "mcpToolsByIntent"):
        assert tools[name].annotations.readOnlyHint is False, name
        assert tools[name].annotations.destructiveHint is True, name
    assert tools["mcpToolsByIntent"].annotations.openWorldHint is True


async def test_new_operation_schemas_are_portable_and_discoverable():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}

    context_props = tools["contextPack"].inputSchema["properties"]
    assert context_props["task"]["type"] == "string"
    assert context_props["maxTokens"]["type"] == "integer"
    assert tools["contextPack"].inputSchema["required"] == ["task"]

    assert set(tools["rulesFor"].inputSchema["properties"]) == {
        "task",
        "ruleProbabilityThreshold",
    }
    assert tools["rulesFor"].inputSchema["properties"]["ruleProbabilityThreshold"]["type"] == (
        "number"
    )

    assert set(tools["captureDecision"].inputSchema["required"]) == {"summary", "evidence"}
    assert tools["captureDecision"].inputSchema["properties"]["evidence"]["type"] == "array"

    assert "intent" in tools["mcpToolsByIntent"].inputSchema["required"]
    assert tools["mcpToolsByIntent"].inputSchema["properties"]["invoke"]["type"] == "boolean"

    skill_schema = tools["skillFromTrajectory"].inputSchema
    assert set(skill_schema["required"]) == {"title", "steps"}
    assert skill_schema["properties"]["citations"]["type"] == "array"


async def test_remember_still_uses_the_shared_extraction_path(service, monkeypatch):
    from openviking.server.mcp_endpoint import StoreMessage, remember

    session = _FakeSession()
    commit = AsyncMock(return_value={"task_id": "task-remember-1", "status": "accepted"})
    monkeypatch.setattr(service.sessions, "open", AsyncMock(return_value=session))
    monkeypatch.setattr(service.sessions, "commit_async", commit)

    result = await remember(messages=[StoreMessage(role="user", content="Remember this")])

    assert "Submitted 1 message(s) for memory extraction" in result
    assert "session mcp-store-" in result
    assert "task_id=task-remember-1" in result
    assert len(session.messages) == 1
    assert commit.await_count == 1