# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""High-level MCP operations that compose OpenViking's existing kernels.

Five operations make the server genuinely useful to an agent in one call each:

* :func:`context_pack` runs the context assembler in ``mode="context"`` and
  returns an injection-ready, token-budgeted block with provenance URIs.
* :func:`rules_for` wraps :meth:`RuleService.search_rules` and treats an empty
  result as a typed configuration failure, because "no rules" means the rule
  corpus was never compiled for this account.
* :func:`capture_decision` persists one decision as a durable memory record and
  submits the same text through the existing ``remember`` extraction path. It is
  idempotent by content digest.
* :func:`mcp_tools_by_intent` wraps ``find_mcp_tools`` with bounded descriptors
  and an optional, guarded ``invoke_mcp_tool`` passthrough.
* :func:`skill_from_trajectory` writes a SKILL.md through the skill store and
  verifies it by reading the stored file back.

Every operation validates its arguments and raises a typed ``OpenVikingError``;
none of them dump a corpus. The MCP tool registrations live in
``openviking.server.mcp_endpoint`` and only adapt these functions to the wire.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import yaml

from openviking.core.namespace import canonical_user_root
from openviking.core.skill_loader import SkillLoader
from openviking.retrieve.context_assembler import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_TOKENS,
    AssembleParams,
    assemble_context,
)
from openviking.server.identity import RequestContext
from openviking.service.rule_service import RuleSearchRequest
from openviking.utils.skill_processor import (
    MAX_SKILL_NAME_LENGTH,
    SkillProcessor,
    validate_skill_name,
)
from openviking_cli.exceptions import FailedPreconditionError, InvalidArgumentError, NotFoundError

# -- bounds -----------------------------------------------------------------
#
# Every bound below is a hard ceiling on what one call may return. They are
# deliberately generous enough to stay useful and small enough that a single
# tool result can never become a corpus dump.

MIN_CONTEXT_PACK_TOKENS = 1
MAX_CONTEXT_PACK_TOKENS = 32_000
MAX_CONTEXT_PROVENANCE_URIS = 100
MAX_CONTEXT_TASK_CHARS = 64_000

MAX_RULE_TASK_CHARS = 64_000
MAX_RULE_TEXT_CHARS = 40_000
MAX_RULE_PROVENANCE = 100

MAX_DECISION_SUMMARY_CHARS = 4_000
MAX_DECISION_EVIDENCE_ITEMS = 50
MAX_DECISION_EVIDENCE_ITEM_CHARS = 2_000
MAX_DECISION_EVIDENCE_CHARS = 12_000

MAX_MCP_INTENT_CHARS = 2_000
MAX_MCP_TOOL_LIMIT = 50
MAX_MCP_TOOL_DESCRIPTION_CHARS = 300
MAX_MCP_TOOL_PROPERTIES = 60
MAX_MCP_TOOL_REQUIRED = 40
MAX_MCP_INVOKE_TEXT_CHARS = 8_000
MAX_MCP_INVOKE_STRUCTURED_CHARS = 4_000
MAX_MCP_SCHEMA_CHARS = 20_000

MAX_SKILL_CATALOG_ENTRIES = 200
MAX_SKILL_CATALOG_DESCRIPTION_CHARS = 160
MAX_SKILL_INSTRUCTIONS_CHARS = 12_000

MAX_SKILL_TITLE_CHARS = 200
MAX_SKILL_STEPS = 100
MAX_SKILL_STEP_CHARS = 2_000
MAX_SKILL_CITATIONS = 100
MAX_SKILL_CITATION_CHARS = 500
MAX_SKILL_READBACK_CHARS = 20_000
MAX_SKILL_DESCRIPTION_CHARS = 500

DECISIONS_MEMORY_DIR = "memories/events/decisions"


# -- typed failures ---------------------------------------------------------


class RuleCorpusNotCompiledError(FailedPreconditionError):
    """No active compiled Rule matched, so the rule corpus is not usable.

    This is a configuration failure rather than an empty success: an agent that
    reads "no rules apply" as a valid answer will act without the constraints the
    account believes it has.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.code = "RULE_CORPUS_NOT_COMPILED"


class MemoryExtractionUnavailableError(FailedPreconditionError):
    """The remember extraction path accepted no work, so nothing was captured."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.code = "MEMORY_EXTRACTION_UNAVAILABLE"


class SkillVerificationError(FailedPreconditionError):
    """A skill was written but reading it back did not confirm its frontmatter."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.code = "SKILL_VERIFICATION_FAILED"


class McpToolCatalogEmptyError(FailedPreconditionError):
    """No MCP tool matched the intent, so the external tool catalog is unusable.

    An empty tool list is a registration/configuration signal. Returning it as a
    successful search would let an agent conclude that no capability exists when
    the catalog was simply never mounted.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.code = "MCP_TOOL_CATALOG_EMPTY"


# -- argument validation ----------------------------------------------------


def _require_text(
    value: Any,
    *,
    field: str,
    max_chars: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise InvalidArgumentError(
            f"'{field}' must be a string",
            details={"field": field, "expected": "string"},
        )
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise InvalidArgumentError(
            f"'{field}' must not be empty",
            details={"field": field},
        )
    if len(normalized) > max_chars:
        raise InvalidArgumentError(
            f"'{field}' must not exceed {max_chars} characters",
            details={"field": field, "max_chars": max_chars, "actual": len(normalized)},
        )
    return normalized


def _require_str_list(
    value: Any,
    *,
    field: str,
    max_items: int,
    max_item_chars: int,
    total_chars: Optional[int] = None,
) -> List[str]:
    if not isinstance(value, list):
        raise InvalidArgumentError(
            f"'{field}' must be a list of strings",
            details={"field": field, "expected": "list"},
        )
    if len(value) > max_items:
        raise InvalidArgumentError(
            f"'{field}' must not exceed {max_items} items",
            details={"field": field, "max_items": max_items, "actual": len(value)},
        )
    normalized: List[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise InvalidArgumentError(
                f"'{field}[{index}]' must be a non-empty string",
                details={"field": f"{field}[{index}]"},
            )
        text = item.strip()
        if len(text) > max_item_chars:
            raise InvalidArgumentError(
                f"'{field}[{index}]' must not exceed {max_item_chars} characters",
                details={
                    "field": f"{field}[{index}]",
                    "max_chars": max_item_chars,
                    "actual": len(text),
                },
            )
        normalized.append(text)
    if total_chars is not None and sum(len(item) for item in normalized) > total_chars:
        raise InvalidArgumentError(
            f"'{field}' exceeds the combined {total_chars}-character limit",
            details={"field": field, "max_total_chars": total_chars},
        )
    return normalized


def _require_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidArgumentError(
            f"'{field}' must be an integer",
            details={"field": field, "expected": "integer"},
        )
    if value < minimum or value > maximum:
        raise InvalidArgumentError(
            f"'{field}' must be between {minimum} and {maximum}",
            details={"field": field, "min": minimum, "max": maximum, "actual": value},
        )
    return value


def _require_probability(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidArgumentError(
            f"'{field}' must be a number between 0.0 and 1.0",
            details={"field": field, "expected": "number"},
        )
    probability = float(value)
    if probability < 0.0 or probability > 1.0:
        raise InvalidArgumentError(
            f"'{field}' must be between 0.0 and 1.0",
            details={"field": field, "min": 0.0, "max": 1.0, "actual": probability},
        )
    return probability


# -- contextPack ------------------------------------------------------------


async def context_pack(
    service: Any,
    ctx: RequestContext,
    *,
    task: Any,
    max_tokens: Any = DEFAULT_MAX_TOKENS,
) -> Dict[str, Any]:
    """Assemble one injection-ready context block for ``task``.

    The block is produced by the same assembler ``search(mode="context")`` uses,
    so quotas, tier filling and token accounting match the retrieval API.
    ``max_tokens`` must be within 1..32000; anything else is a typed
    ``INVALID_ARGUMENT`` failure rather than a silently clamped budget.
    """
    task_text = _require_text(task, field="task", max_chars=MAX_CONTEXT_TASK_CHARS)
    budget = _require_int(
        max_tokens,
        field="maxTokens",
        minimum=MIN_CONTEXT_PACK_TOKENS,
        maximum=MAX_CONTEXT_PACK_TOKENS,
    )

    params = AssembleParams(
        query=task_text,
        search_type="semantic",
        limit=DEFAULT_LIMIT,
        # No session and rewrite="off": a one-call pack must not depend on a
        # planner model or on the caller having a session.
        session_id=None,
        query_expansion="off",
        max_tokens=budget,
        rewrite=False,
        render=True,
    )
    result = await assemble_context(service=service, ctx=ctx, params=params)
    block = (result.digest or "").strip() or (result.rendered or "").strip()

    provenance: List[Dict[str, Any]] = []
    seen: set[str] = set()
    truncated = False
    for entry in result.entries:
        uri = str(getattr(entry, "uri", "") or "")
        if not uri or uri in seen:
            continue
        if len(provenance) >= MAX_CONTEXT_PROVENANCE_URIS:
            truncated = True
            break
        seen.add(uri)
        provenance.append(
            {
                "uri": uri,
                "category": str(getattr(entry, "category", "") or ""),
                "score": float(getattr(entry, "score", 0.0) or 0.0),
            }
        )

    stats = result.stats if isinstance(result.stats, dict) else {}
    used_tokens = stats.get("used_tokens")
    candidate_tokens = stats.get("candidate_tokens")
    tokens_saved = stats.get("tokens_saved")
    savings: Optional[Dict[str, Any]] = None
    if isinstance(candidate_tokens, int) and isinstance(used_tokens, int):
        ratio = (used_tokens / candidate_tokens) if candidate_tokens > 0 else 0.0
        savings = {
            "candidateTokens": candidate_tokens,
            "usedTokens": used_tokens,
            "tokensSaved": tokens_saved if isinstance(tokens_saved, int) else 0,
            "droppedTokens": stats.get("dropped_tokens", 0),
            "dedupedTokens": stats.get("deduped_tokens", 0),
            "ratio": round(ratio, 4),
        }
    payload: Dict[str, Any] = {
        "task": task_text,
        "mode": "context",
        "maxTokens": budget,
        "entryCount": len(result.entries),
        "usedTokens": used_tokens if isinstance(used_tokens, int) else None,
        "savings": savings,
        "provenance": provenance,
        "truncated": truncated,
    }
    if not block:
        payload["notice"] = "No matching context found."
    else:
        payload["contextChars"] = len(block)
    return {"payload": payload, "text": block or payload["notice"]}


# -- rulesFor ---------------------------------------------------------------


def _bounded_rule_text(rendered: str) -> tuple[str, bool]:
    if len(rendered) <= MAX_RULE_TEXT_CHARS:
        return rendered, False
    marker = (
        f"\n\n[truncated at {MAX_RULE_TEXT_CHARS} characters; "
        "read the provenance URIs for the remaining Rules]"
    )
    return rendered[:MAX_RULE_TEXT_CHARS] + marker, True


async def rules_for(
    service: Any,
    ctx: RequestContext,
    *,
    task: Any,
    rule_probability_threshold: Any = 0.65,
) -> Dict[str, Any]:
    """Return the active compiled Rules that apply to ``task``.

    Zero matches raise :class:`RuleCorpusNotCompiledError`. An empty rule set is
    indistinguishable from "no rule applies" to a reader, so returning it as a
    success would silently remove every constraint the account believes it has.
    """
    task_text = _require_text(task, field="task", max_chars=MAX_RULE_TASK_CHARS)
    threshold = _require_probability(
        rule_probability_threshold, field="ruleProbabilityThreshold"
    )

    result = await service.rules.search_rules(
        RuleSearchRequest(
            task_context=task_text,
            rule_probability_threshold=threshold,
        ),
        ctx=ctx,
    )
    rules = result.get("rules") if isinstance(result, dict) else None
    rules = rules if isinstance(rules, list) else []
    if not rules:
        raise RuleCorpusNotCompiledError(
            "No active compiled Rule matched the task, so the rule corpus is not "
            "usable for this account. Compile rules before relying on rulesFor.",
            details={
                "ruleProbabilityThreshold": threshold,
                "taskContextChars": len(task_text),
                "requiredAction": "compile_rules",
            },
        )

    rendered = result.get("rendered") if isinstance(result, dict) else None
    rendered = rendered if isinstance(rendered, str) else ""
    if not rendered.strip():
        # Defensive: the service always renders today, but a future change must
        # not turn a matched rule set into an empty success.
        rendered = json.dumps(
            [
                {
                    "atomic_id": rule.get("atomic_id"),
                    "uri": rule.get("uri"),
                    "content": rule.get("content"),
                }
                for rule in rules
                if isinstance(rule, dict)
            ],
            ensure_ascii=False,
        )
    text, truncated = _bounded_rule_text(rendered)

    provenance: List[Dict[str, Any]] = []
    for rule in rules[:MAX_RULE_PROVENANCE]:
        if not isinstance(rule, dict):
            continue
        provenance.append(
            {
                "uri": rule.get("uri"),
                "atomicId": rule.get("atomic_id"),
                "probability": rule.get("probability"),
            }
        )
    payload = {
        "task": task_text,
        "ruleProbabilityThreshold": threshold,
        "ruleCount": len(rules),
        "renderedChars": len(rendered),
        "truncated": truncated,
        "provenance": provenance,
    }
    return {"payload": payload, "text": text}


# -- captureDecision --------------------------------------------------------


def _decision_digest(summary: str, evidence: Iterable[str]) -> str:
    canonical = json.dumps(
        {"summary": summary, "evidence": list(evidence)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decision_document(summary: str, evidence: List[str], *, digest: str) -> str:
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Decision",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Evidence",
        "",
    ]
    lines.extend(f"- {item}" for item in evidence)
    if not evidence:
        lines.append("(no evidence supplied)")
    lines.extend(
        [
            "",
            "---",
            "",
            f"- digest: {digest}",
            f"- captured_at: {captured_at}",
            "- source: captureDecision",
            "",
        ]
    )
    return "\n".join(lines)


async def submit_memory_extraction(
    service: Any,
    ctx: RequestContext,
    messages: Iterable[tuple[str, str]],
    *,
    session_prefix: str = "mcp-store",
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit messages to the same memory-extraction path the remember tool uses.

    Returns ``session_id``, ``task_id`` (``None`` when nothing was committed) and
    ``reason``. This is the single extraction entry point shared by ``remember``
    and ``captureDecision`` so the two tools cannot drift apart.
    """
    from openviking.message.part import TextPart

    sid = session_id or f"{session_prefix}-{uuid.uuid4().hex[:12]}"
    session = await service.sessions.open(sid, ctx, auto_create=True)
    added = 0
    for role, content in messages:
        if not content:
            continue
        add_async = getattr(session, "add_message_async", None)
        if callable(add_async):
            await add_async(role, [TextPart(text=content)])
        else:
            session.add_message(role, [TextPart(text=content)])
        added += 1
    result = await service.sessions.commit_async(sid, ctx)
    return {
        "session_id": sid,
        "added": added,
        "task_id": result.get("task_id"),
        "reason": result.get("reason") or result.get("status"),
    }


async def capture_decision(
    service: Any,
    ctx: RequestContext,
    *,
    summary: Any,
    evidence: Any,
) -> Dict[str, Any]:
    """Persist one decision durably and request memory extraction for it.

    The durable record lives at a digest-derived URI, so the same content always
    produces the same URI and a repeat call returns ``created=false`` without
    submitting the extraction again. The text is also handed to the existing
    remember extraction path so the extractor can consolidate the decision with
    the rest of the account's memories.
    """
    summary_text = _require_text(
        summary, field="summary", max_chars=MAX_DECISION_SUMMARY_CHARS
    )
    evidence_items = _require_str_list(
        evidence,
        field="evidence",
        max_items=MAX_DECISION_EVIDENCE_ITEMS,
        max_item_chars=MAX_DECISION_EVIDENCE_ITEM_CHARS,
        total_chars=MAX_DECISION_EVIDENCE_CHARS,
    )

    digest = _decision_digest(summary_text, evidence_items)
    memory_uri = f"{canonical_user_root(ctx)}/{DECISIONS_MEMORY_DIR}/{digest}.md"

    existing = await _read_if_present(service, ctx, memory_uri)
    if existing is not None:
        return {
            "payload": {
                "memoryUri": memory_uri,
                "digest": digest,
                "created": False,
                "idempotent": True,
                "existingBytes": len(existing),
                "extraction": None,
            },
            "text": f"Decision already captured at {memory_uri} (digest {digest[:16]}).",
        }

    message = "Decision worth persisting.\n\nSummary: {summary}\n\nEvidence:\n{evidence}".format(
        summary=summary_text,
        evidence="\n".join(f"- {item}" for item in evidence_items) or "(none)",
    )
    extraction = await submit_memory_extraction(
        service,
        ctx,
        [("user", message)],
        session_prefix="mcp-decision",
        session_id=f"mcp-decision-{digest[:16]}",
    )
    if not extraction.get("task_id"):
        raise MemoryExtractionUnavailableError(
            "The remember extraction path accepted no work, so the decision was not "
            "captured. Nothing was written; retry once extraction is available.",
            details={
                "reason": extraction.get("reason") or "unknown",
                "sessionId": extraction.get("session_id"),
                "digest": digest,
            },
        )

    document = _decision_document(summary_text, evidence_items, digest=digest)
    written = await service.fs.write(
        uri=memory_uri,
        content=document,
        ctx=ctx,
        mode="replace",
        wait=False,
    )
    stored_uri = str(written.get("uri") or memory_uri)
    return {
        "payload": {
            "memoryUri": stored_uri,
            "digest": digest,
            "created": True,
            "idempotent": False,
            "writtenBytes": written.get("written_bytes"),
            "extraction": {
                "sessionId": extraction.get("session_id"),
                "taskId": extraction.get("task_id"),
            },
        },
        "text": (
            f"Captured decision at {stored_uri} (digest {digest[:16]}) and submitted it "
            f"for memory extraction (task_id={extraction.get('task_id')})."
        ),
    }


async def _read_if_present(
    service: Any, ctx: RequestContext, uri: str
) -> Optional[str]:
    if not await service.viking_fs.exists(uri, ctx=ctx):
        return None
    return await service.fs.read(uri, ctx=ctx)


# -- mcpToolsByIntent -------------------------------------------------------


def _bounded_tool_descriptor(hit: Dict[str, Any]) -> Dict[str, Any]:
    tool = hit.get("tool") if isinstance(hit.get("tool"), dict) else {}
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    description = " ".join(str(tool.get("description") or "").split())
    if len(description) > MAX_MCP_TOOL_DESCRIPTION_CHARS:
        description = description[:MAX_MCP_TOOL_DESCRIPTION_CHARS] + "..."
    return {
        "uri": hit.get("uri"),
        "name": tool.get("name"),
        "server": hit.get("server"),
        "score": hit.get("score"),
        "description": description,
        "required": [str(name) for name in required[:MAX_MCP_TOOL_REQUIRED]],
        "inputProperties": [
            str(name) for name in list(properties)[:MAX_MCP_TOOL_PROPERTIES]
        ],
        "readOnlyHint": bool(annotations.get("readOnlyHint")),
        "destructiveHint": bool(annotations.get("destructiveHint")),
    }


def _bounded_invocation(tool_uri: str, raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        result = raw
    elif hasattr(raw, "model_dump"):
        result = raw.model_dump(mode="json", by_alias=True, exclude_none=True)
    else:
        result = {}
    blocks = result.get("content") if isinstance(result.get("content"), list) else []
    chunks: List[str] = []
    non_text = 0
    for block in blocks:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            chunks.append(block["text"])
        else:
            non_text += 1
    text = "\n".join(chunks)
    text_truncated = len(text) > MAX_MCP_INVOKE_TEXT_CHARS
    if text_truncated:
        text = text[:MAX_MCP_INVOKE_TEXT_CHARS]

    structured = result.get("structuredContent")
    structured_truncated = False
    if structured is not None:
        encoded = json.dumps(structured, ensure_ascii=False)
        if len(encoded) > MAX_MCP_INVOKE_STRUCTURED_CHARS:
            structured = None
            structured_truncated = True

    return {
        "toolUri": tool_uri,
        "isError": bool(result.get("isError")),
        "text": text,
        "textTruncated": text_truncated,
        "nonTextBlocks": non_text,
        "structuredContent": structured,
        "structuredTruncated": structured_truncated,
    }


async def mcp_tools_by_intent(
    service: Any,
    ctx: RequestContext,
    *,
    intent: Any,
    limit: Any = 10,
    invoke: Any = False,
    tool_uri: Any = "",
    arguments: Any = None,
) -> Dict[str, Any]:
    """Find MCP tools for ``intent`` and optionally invoke one of them.

    Descriptors are bounded (description, required names and property names) and
    never include the full upstream schema. ``invoke=True`` is only accepted for
    a ``tool_uri`` this call selected, so the passthrough cannot be used to reach
    an unrelated tool through an intent search. Zero matches raise
    :class:`McpToolCatalogEmptyError`; an empty intent is a typed argument error.
    """
    intent_text = _require_text(intent, field="intent", max_chars=MAX_MCP_INTENT_CHARS)
    tool_limit = _require_int(
        limit, field="limit", minimum=1, maximum=MAX_MCP_TOOL_LIMIT
    )
    if not isinstance(invoke, bool):
        raise InvalidArgumentError(
            "'invoke' must be a boolean",
            details={"field": "invoke", "expected": "boolean"},
        )
    if arguments is not None and not isinstance(arguments, dict):
        raise InvalidArgumentError(
            "'arguments' must be an object when supplied",
            details={"field": "arguments", "expected": "object"},
        )

    result = await service.mcps.find(intent_text, tool_limit, ctx, include_definition=True)
    hits = result.get("tools") if isinstance(result, dict) else None
    hits = hits if isinstance(hits, list) else []
    descriptors = [_bounded_tool_descriptor(hit) for hit in hits if isinstance(hit, dict)]
    if not descriptors:
        raise McpToolCatalogEmptyError(
            "No MCP tool matched the intent. The account's external tool catalog "
            "may be empty or not registered; register the MCP servers before "
            "relying on mcpToolsByIntent.",
            details={
                "intent": intent_text,
                "limit": tool_limit,
                "requiredAction": "register_mcp_servers",
            },
        )
    payload: Dict[str, Any] = {
        "intent": intent_text,
        "total": len(descriptors),
        "tools": descriptors,
    }

    if invoke:
        target = _require_text(
            tool_uri, field="toolUri", max_chars=MAX_MCP_INTENT_CHARS
        )
        if target not in {descriptor["uri"] for descriptor in descriptors}:
            raise InvalidArgumentError(
                "toolUri must be one of the tools returned for this intent; call "
                "mcpToolsByIntent without invoke first and pass a returned uri.",
                details={"field": "toolUri", "intent": intent_text},
            )
        raw = await service.mcps.invoke(target, arguments or {}, ctx)
        payload["invoked"] = _bounded_invocation(target, raw)

    return {"payload": payload, "text": json.dumps(payload, ensure_ascii=False)}


# -- skillFromTrajectory ----------------------------------------------------


def _skill_name_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        raise InvalidArgumentError(
            "title cannot be converted to a skill name; use ASCII letters or digits",
            details={"field": "title"},
        )
    # A long but valid title still yields a usable name instead of a failure.
    slug = slug[:MAX_SKILL_NAME_LENGTH].rstrip("-")
    try:
        return validate_skill_name(slug)
    except InvalidArgumentError as exc:
        raise InvalidArgumentError(
            "title produces an invalid skill name", details=exc.details
        ) from exc


def _skill_document(
    *, name: str, title: str, steps: List[str], citations: List[str]
) -> str:
    description = title.strip()
    if len(description) > MAX_SKILL_DESCRIPTION_CHARS:
        description = description[:MAX_SKILL_DESCRIPTION_CHARS].rstrip()
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {title}",
        "",
        "## When to use",
        "",
        f"Reuse this trajectory when a task resembles: {title}.",
        "",
        "## Steps",
        "",
    ]
    lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    if citations:
        lines.extend(["", "## Citations", ""])
        lines.extend(f"- {citation}" for citation in citations)
    lines.append("")
    return "\n".join(lines)


async def skill_from_trajectory(
    service: Any,
    ctx: RequestContext,
    *,
    title: Any,
    steps: Any,
    citations: Any = None,
) -> Dict[str, Any]:
    """Create a skill from a trajectory and verify it by reading it back.

    ``title`` becomes both the description and the skill-name slug; ``steps`` are
    written as an ordered runbook and ``citations`` as evidence links. A skill is
    only reported as created when the stored SKILL.md parses with the expected
    name and a non-empty description.
    """
    title_text = _require_text(title, field="title", max_chars=MAX_SKILL_TITLE_CHARS)
    step_items = _require_str_list(
        steps, field="steps", max_items=MAX_SKILL_STEPS, max_item_chars=MAX_SKILL_STEP_CHARS
    )
    if not step_items:
        raise InvalidArgumentError(
            "'steps' must contain at least one step",
            details={"field": "steps", "min_items": 1},
        )
    citation_items = _require_str_list(
        [] if citations is None else citations,
        field="citations",
        max_items=MAX_SKILL_CITATIONS,
        max_item_chars=MAX_SKILL_CITATION_CHARS,
    )
    name = _skill_name_from_title(title_text)
    skills_root = SkillProcessor._resolve_skill_root_uri(ctx, None)  # noqa: SLF001
    document = _skill_document(
        name=name, title=title_text, steps=step_items, citations=citation_items
    )

    created = await service.resources.add_skill(
        data=document,
        ctx=ctx,
        wait=False,
        allow_local_path_resolution=False,
        target_uri=skills_root,
        source_metadata={
            "type": "mcp",
            "source": "skill_from_trajectory",
            "operation": "add",
        },
    )
    if not isinstance(created, dict) or created.get("status") == "error":
        message = (
            created.get("message")
            if isinstance(created, dict)
            else "skill processing returned no result"
        )
        raise FailedPreconditionError(
            f"Skill creation failed: {message}",
            details={"name": name, "skillsRoot": skills_root},
        )

    root_uri = str(created.get("root_uri") or created.get("uri") or f"{skills_root}/{name}")
    skill_uri = f"{root_uri}/SKILL.md"
    stored = await service.fs.read(skill_uri, ctx=ctx)
    parsed: Dict[str, Any] = {}
    try:
        parsed = SkillLoader.parse(stored[:MAX_SKILL_READBACK_CHARS])
    except ValueError as exc:
        raise SkillVerificationError(
            f"Stored skill did not parse as SKILL.md: {exc}",
            details={"name": name, "skillUri": skill_uri},
        ) from exc
    if parsed.get("name") != name or not str(parsed.get("description") or "").strip():
        raise SkillVerificationError(
            "Stored skill frontmatter does not match what was written",
            details={
                "name": name,
                "skillUri": skill_uri,
                "storedName": parsed.get("name"),
                "hasDescription": bool(str(parsed.get("description") or "").strip()),
            },
        )

    payload = {
        "name": name,
        "description": str(parsed.get("description")),
        "skillUri": skill_uri,
        "rootUri": root_uri,
        "verified": True,
        "steps": len(step_items),
        "citations": len(citation_items),
        "revision": created.get("current_revision"),
    }
    return {
        "payload": payload,
        "text": f"Skill '{name}' created and verified at {skill_uri}.",
    }


# -- toolSchema / skillsCatalog / skillInstructions --------------------------


def _bounded_schema(value: Any) -> tuple[Any, int, bool]:
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) <= MAX_MCP_SCHEMA_CHARS:
        return value, len(encoded), False
    marker = {
        "notice": "inputSchema exceeded its character budget",
        "chars": len(encoded),
        "limit": MAX_MCP_SCHEMA_CHARS,
        "truncated": True,
    }
    return marker, len(encoded), True


async def tool_schema(service: Any, ctx: RequestContext, *, tool_uri: str) -> Dict[str, Any]:
    """Return one chosen tool's full inputSchema, on demand.

    Discovery returns compact descriptors, so the expensive schema crosses the
    wire only for the tool the caller actually selected. Anything that is not a
    catalogued uri under the caller's roots is a typed not-found.
    """
    uri = _require_text(tool_uri, field="toolUri", max_chars=MAX_MCP_INTENT_CHARS)
    if not uri.startswith("viking://"):
        raise InvalidArgumentError(
            "toolUri must be a viking:// uri returned by find_mcp_tools",
            details={"field": "toolUri"},
        )
    definition = await service.mcps.definition(uri, ctx)
    schema, schema_chars, truncated = _bounded_schema(definition.get("inputSchema") or {})
    payload = {
        "uri": definition.get("uri"),
        "server": definition.get("server"),
        "name": definition.get("name"),
        "description": str(definition.get("description") or ""),
        "inputSchema": schema,
        "inputSchemaChars": schema_chars,
        "truncated": truncated,
    }
    text = json.dumps(schema, ensure_ascii=False)
    return {"payload": payload, "text": text}


async def skill_catalog(
    service: Any,
    ctx: RequestContext,
    *,
    limit: Any = MAX_SKILL_CATALOG_ENTRIES,
    target_uri: Any = None,
) -> Dict[str, Any]:
    """List installed skills as compact name+description rows, never bodies.

    This is the cheap discovery step: one bounded call replaces a semantic
    search that would otherwise carry abstracts (and often read bodies) before
    the caller knows which skill it needs. The body comes only from
    :func:`skill_instructions`.
    """
    from openviking.server.routers.skills import list_skill_catalog

    budget = _require_int(limit, field="limit", minimum=1, maximum=MAX_SKILL_CATALOG_ENTRIES)
    requested = target_uri if isinstance(target_uri, str) and target_uri.strip() else None
    rows = await list_skill_catalog(
        service, ctx, target_uri=requested, node_limit=budget + 1
    )
    skills: List[Dict[str, Any]] = []
    for row in rows[:budget]:
        description = " ".join(str(row.get("description") or "").split())
        if len(description) > MAX_SKILL_CATALOG_DESCRIPTION_CHARS:
            description = description[:MAX_SKILL_CATALOG_DESCRIPTION_CHARS] + "..."
        skills.append(
            {
                "name": str(row.get("name") or ""),
                "description": description,
                "uri": str(row.get("root_uri") or row.get("uri") or ""),
                "rootUri": str(row.get("root_uri") or ""),
            }
        )
    truncated = len(rows) > budget
    payload = {"skills": skills, "total": len(rows), "truncated": truncated}
    text = "\n".join(f"- {item['name']}: {item['description']}" for item in skills)
    return {"payload": payload, "text": text or "No skills installed."}


def _safe_skill_reference(reference: str) -> str:
    """Reject any reference that could escape the skill package."""
    normalized = reference.strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    segments = normalized.split("/")
    if not normalized or normalized.startswith("/") or ".." in segments or "\\" in normalized:
        raise InvalidArgumentError(
            "reference must be a relative path inside the skill package",
            details={"field": "reference"},
        )
    return normalized


async def skill_instructions(
    service: Any,
    ctx: RequestContext,
    *,
    skill: str,
    reference: Any = None,
    max_chars: Any = MAX_SKILL_INSTRUCTIONS_CHARS,
) -> Dict[str, Any]:
    """Read one skill's SKILL.md (or one reference leaf) on demand.

    Progressive disclosure: the catalog carries only names and descriptions;
    the instructions cross the wire only after the caller chose a skill. The
    body is clamped and ``truncated`` tells the caller when to page instead of
    assuming the whole file arrived.
    """
    value = _require_text(skill, field="skill", max_chars=MAX_SKILL_NAME_LENGTH)
    budget = _require_int(max_chars, field="maxChars", minimum=200, maximum=MAX_SKILL_INSTRUCTIONS_CHARS)
    if value.startswith("viking://"):
        roots = [value.rstrip("/")]
    else:
        validate_skill_name(value)
        user_root = canonical_user_root(ctx)
        roots = [f"{user_root}/skills/{value}", f"viking://agent/skills/{value}"]
    chosen_root: Optional[str] = None
    for root in roots:
        if await service.viking_fs.exists(root, ctx=ctx):
            chosen_root = root
            break
    if chosen_root is None:
        raise NotFoundError(value, "skill")
    rel_ref = _safe_skill_reference(reference) if isinstance(reference, str) and reference.strip() else None
    target = f"{chosen_root}/{rel_ref}" if rel_ref else f"{chosen_root}/SKILL.md"
    body = await service.fs.read(target, ctx=ctx)
    if body is None:
        raise NotFoundError(target, "skill document")
    text = str(body)
    truncated = len(text) > budget
    if truncated:
        text = (
            text[:budget]
            + f"\n\n[truncated at {budget} characters; read the skill file for the remainder]"
        )
    payload = {
        "uri": target,
        "skill": chosen_root.rsplit("/", 1)[-1],
        "reference": rel_ref,
        "chars": len(str(body)),
        "returnedChars": len(text),
        "truncated": truncated,
    }
    return {"payload": payload, "text": text}


# -- remember (shared extraction path) --------------------------------------


async def remember_messages(
    service: Any, ctx: RequestContext, messages: Iterable[tuple[str, str]]
) -> str:
    """The remember tool body, expressed on the shared extraction helper."""
    submission = await submit_memory_extraction(
        service, ctx, messages, session_prefix="mcp-store"
    )
    if not submission.get("task_id"):
        reason = submission.get("reason") or "unknown"
        return f"Nothing was committed for memory extraction (reason: {reason})."
    return (
        f"Submitted {submission['added']} message(s) for memory extraction "
        f"(session {submission['session_id']}, task_id={submission['task_id']}). "
        "Extraction runs in the background and decides which memories to create or update."
    )


__all__ = [
    "McpToolCatalogEmptyError",
    "MemoryExtractionUnavailableError",
    "RuleCorpusNotCompiledError",
    "SkillVerificationError",
    "capture_decision",
    "context_pack",
    "mcp_tools_by_intent",
    "remember_messages",
    "rules_for",
    "skill_catalog",
    "skill_from_trajectory",
    "skill_instructions",
    "submit_memory_extraction",
    "tool_schema",
]