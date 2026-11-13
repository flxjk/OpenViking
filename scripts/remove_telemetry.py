#!/usr/bin/env python3
"""Remove every non-local telemetry path and external link from OpenViking.

This is a one-shot, idempotent migration script. It deletes the outbound
telemetry subsystems (Kafka event reporting, OTLP trace/metric/log export,
Nacos config fetch) together with the instrumentation that existed only to
feed them, and drops the dependencies that served only those transports.

Local-only observability is deliberately preserved:

* ``openviking.telemetry`` operation summaries returned in API responses
* local JSONL trace export (``protocol = "local"``)
* Prometheus metrics
* local SQLite usage audit
* local ``usage_reporter`` file-log sink
* ``openviking.privacy`` (data protection, not telemetry)

Usage:
    python scripts/remove_telemetry.py --dry-run
    python scripts/remove_telemetry.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class MigrationError(RuntimeError):
    """Raised when the tree does not match the expected pre-migration shape."""


def _path(rel: str) -> Path:
    return REPO / rel


def _read(rel: str) -> str:
    return _path(rel).read_text(encoding="utf-8")


def _write(rel: str, content: str) -> None:
    _path(rel).write_text(content, encoding="utf-8")


def _replace(rel: str, old: str, new: str, *, count: int = 1) -> None:
    """Replace ``old`` with ``new`` exactly ``count`` times."""
    text = _read(rel)
    found = text.count(old)
    if found != count:
        raise MigrationError(
            f"{rel}: expected {count} occurrence(s) of the target block, found {found}\n"
            f"--- target ---\n{old[:400]}\n--------------"
        )
    _write(rel, text.replace(old, new))


def _delete(rel: str) -> None:
    target = _path(rel)
    if not target.exists():
        return
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _remove_block(rel: str, start: str, end: str) -> None:
    """Remove the inclusive line range from the line containing ``start`` to ``end``."""
    lines = _read(rel).splitlines(keepends=True)
    start_idx = end_idx = None
    for idx, line in enumerate(lines):
        if start_idx is None and start in line:
            start_idx = idx
        elif start_idx is not None and end in line:
            end_idx = idx
            break
    if start_idx is None or end_idx is None:
        raise MigrationError(f"{rel}: could not locate block {start!r} .. {end!r}")
    del lines[start_idx : end_idx + 1]
    _write(rel, "".join(lines))


# ---------------------------------------------------------------------------
# 1. new local-only facts module
# ---------------------------------------------------------------------------

FACTS_MODULE = '''"""Vendor-neutral metric context adapters around committed business operations.

These adapters only bind labels for the local metrics pipeline. No document
contents, prompts, API keys or Task auth fields enter these events.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from openviking.observability.context import get_observability_context

_embedding_purpose = ContextVar("metric_embedding_purpose", default="unknown")
_memory_type = ContextVar("metric_memory_type", default=None)
_memory_task_id = ContextVar("metric_memory_task_id", default=None)
_memory_source_task_ids = ContextVar("metric_memory_source_task_ids", default=None)
_memory_batch_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "metric_memory_batch_context", default=None
)


@contextmanager
def bind_memory_type(memory_type, *, task_id=None, source_task_ids=None):
    token = _memory_type.set(memory_type)
    task_token = _memory_task_id.set(task_id)
    sources_token = _memory_source_task_ids.set(source_task_ids)
    try:
        yield
    finally:
        _memory_source_task_ids.reset(sources_token)
        _memory_task_id.reset(task_token)
        _memory_type.reset(token)


@contextmanager
def bind_memory_batch(ctx, task_ids):
    """Report a shared batch from its inputs, never its timer's inherited root.

    A model call shared by several commits is one observation. Preserve those
    sources without duplicating usage or choosing one submitter as its owner.
    Empty request/task IDs deliberately suppress stale queue-root fallbacks.
    """
    task_ids = tuple(task_ids)
    sources = sorted({task_id for task_id in task_ids if task_id})
    task_id = sources[0] if len(sources) == 1 and all(task_ids) else ""
    token = _memory_batch_context.set(
        {
            "account_id": ctx.account_id,
            "user_id": ctx.user.user_id,
            "task_id": task_id,
            "request_id": task_id,
            "task_type": "session_commit",
            "source_task_ids": sources,
        }
    )
    try:
        yield
    finally:
        _memory_batch_context.reset(token)


@contextmanager
def bind_embedding_purpose(purpose):
    token = _embedding_purpose.set(
        purpose if purpose in {"memory", "resource", "skill", "search_query"} else "unknown"
    )
    try:
        yield
    finally:
        _embedding_purpose.reset(token)


def event_context(name):
    """Use the existing Task context, including after queue delivery/restart."""
    if name not in {
        "http.request",
        "vlm.call",
        "embedding.call",
        "understanding_call",
        "file_mutation",
        "skill_mutation",
        "task_terminal",
    }:
        return {}
    memory_task_id = _memory_task_id.get()
    memory_batch = _memory_batch_context.get()
    if memory_batch is not None and name in {
        "file_mutation",
        "skill_mutation",
        "vlm.call",
        "embedding.call",
    }:
        batch_fields = dict(memory_batch)
        sources = _memory_source_task_ids.get()
        if memory_task_id:
            batch_fields.update(
                task_id=memory_task_id,
                request_id=memory_task_id,
                source_task_ids=[memory_task_id],
            )
        elif sources is not None:
            # An explicitly ambiguous operation cannot inherit a single batch
            # submitter, even when some source Task metadata is unavailable.
            batch_fields.update(task_id="", request_id="", source_task_ids=sorted(set(sources)))
        if name == "vlm.call":
            batch_fields["usage_purpose"] = "memory_extract"
        elif name == "embedding.call":
            batch_fields["usage_purpose"] = _embedding_purpose.get()
        return batch_fields
    if name == "file_mutation" and memory_task_id:
        # Shared memory batch workers outlive their first queue request. Use
        # the committed operation's provenance without reattaching Task Work.
        return {
            "task_id": memory_task_id,
            "request_id": memory_task_id,
            "task_type": "session_commit",
        }
    from openviking.service.task_work_index import TaskExecutionContext, get_task_context

    context = get_task_context()
    if context is None:
        root = get_observability_context().root
        # The session queue owns this root and sets request_id to its persisted
        # Task ID. Extraction callbacks can retain it after losing Task Work
        # context across event loops. HTTP request IDs are never Task evidence.
        if (
            root is not None
            and root.http_method == "QUEUE"
            and root.http_route == "/queuefs/session_commit"
            and root.request_id
            and root.account_id
            and root.user_id
        ):
            context = TaskExecutionContext(
                root.request_id, root.account_id, root.user_id, task_type="session_commit"
            )
    fields: dict[str, str | None] = {}
    if context is not None:
        fields.update(
            task_id=context.task_id, account_id=context.account_id, user_id=context.user_id
        )
        if context.task_type:
            fields.update(task_type=context.task_type, mode=context.mode)
        if name == "vlm.call":
            fields["usage_purpose"] = {
                "session_commit": "memory_extract",
                "add_resource": "resource_parse",
                "content_write": "resource_parse",
                "compile": "compile",
                "add_skill": "other",
            }.get(context.task_type, "unknown")
    if name == "embedding.call":
        fields["usage_purpose"] = _embedding_purpose.get()
    return fields
'''


# ---------------------------------------------------------------------------
# 2. local-only tracer
# ---------------------------------------------------------------------------

SPAN_TO_DICT = '''def _span_to_dict(span: Any) -> dict[str, Any]:
    """Serialize a ReadableSpan into a plain JSON-compatible mapping."""
    context = getattr(span, "context", None)
    parent = getattr(span, "parent", None)
    status = getattr(span, "status", None)
    events = []
    for event in getattr(span, "events", ()) or ():
        events.append(
            {
                "name": getattr(event, "name", ""),
                "timestamp": getattr(event, "timestamp", 0),
                "attributes": dict(getattr(event, "attributes", {}) or {}),
            }
        )
    return {
        "trace_id": format(context.trace_id, "032x") if context is not None else "",
        "span_id": format(context.span_id, "016x") if context is not None else "",
        "parent_span_id": (
            format(parent.span_id, "016x")
            if parent is not None and getattr(parent, "span_id", None)
            else ""
        ),
        "name": getattr(span, "name", ""),
        "kind": str(getattr(span, "kind", "")),
        "start_time_unix_nano": getattr(span, "start_time", 0),
        "end_time_unix_nano": getattr(span, "end_time", 0),
        "attributes": dict(getattr(span, "attributes", {}) or {}),
        "events": events,
        "status": {
            "status_code": str(getattr(status, "status_code", "")),
            "description": getattr(status, "description", None),
        },
    }


'''


# ---------------------------------------------------------------------------
# 3. migration steps
# ---------------------------------------------------------------------------


def step_delete_external_subsystems() -> None:
    """Delete modules whose only purpose was external telemetry."""
    _delete("openviking/observability/reporting")
    _delete("openviking/metrics/exporters/otel.py")
    _delete("tests/upload_offline_trace.py")
    _delete("openviking_cli/utils/config/telemetry_config.py")


def step_write_local_facts() -> None:
    _write("openviking/observability/facts.py", FACTS_MODULE)


def step_rewire_facts_imports() -> None:
    """Point local-metric call sites at the new local-only facts module."""
    for rel in (
        "openviking/models/embedder/base.py",
        "openviking/storage/collection_schemas.py",
        "openviking/session/memory/streaming_memory_updater.py",
        "openviking/session/memory/memory_updater.py",
        "openviking/session/memory/memory_type_registry.py",
    ):
        text = _read(rel)
        updated = text.replace(
            "from openviking.observability.reporting.facts import",
            "from openviking.observability.facts import",
        )
        if updated == text:
            raise MigrationError(f"{rel}: no reporting.facts import found")
        _write(rel, updated)


def step_remove_reporting_call_sites() -> None:
    # --- events.py: drop reporter-only event id + repoint event_context ---
    _replace(
        "openviking/observability/events.py",
        """    normalized_payload = dict(payload or {})
    from openviking.observability.reporting.runtime import is_reporting_enabled

    if is_reporting_enabled() and not normalized_payload.get("event_id"):
        from uuid import uuid4

        normalized_payload["event_id"] = "evt_" + uuid4().hex
    from openviking.observability.reporting.facts import event_context
""",
        """    normalized_payload = dict(payload or {})
    from openviking.observability.facts import event_context
""",
    )

    # --- http middleware: drop request reporting bootstrap ---
    _replace(
        "openviking/observability/http_observability_middleware.py",
        """        from openviking.observability.reporting.facts import begin_request_reporting

        begin_request_reporting()
""",
        "",
    )

    # --- task tracker: drop terminal reporting + request task memory ---
    _replace(
        "openviking/service/task_tracker.py",
        """            if existing is not None:
                from openviking.observability.reporting.facts import remember_request_task

                remember_request_task(existing)
                return existing
""",
        """            if existing is not None:
                return existing
""",
    )
    _replace(
        "openviking/service/task_tracker.py",
        """        from openviking.observability.reporting.facts import remember_request_task

        remember_request_task(created)
        return created
""",
        """        return created
""",
    )
    _remove_block(
        "openviking/service/task_tracker.py",
        "    async def _emit_terminal(self, task_id, account_id, user_id):",
        '            logger.warning("Task terminal telemetry could not be projected")',
    )
    text = _read("openviking/service/task_tracker.py")
    text = text.replace("        await self._emit_terminal(task_id, account_id, user_id)\n", "")
    text = text.replace("            await self._emit_terminal(task_id, account_id, user_id)\n", "")
    _write("openviking/service/task_tracker.py", text)

    # --- resource target: drop file mutation reporting ---
    _replace(
        "openviking/storage/resource_target.py",
        "from openviking.observability.reporting.facts import emit_file_change\n",
        "",
    )
    _replace(
        "openviking/storage/resource_target.py",
        '        emit_file_change(uri, self._ctx, "deleted", is_dir=is_dir)\n',
        "",
    )

    # --- viking fs ops: drop file mutation reporting ---
    _replace(
        "openviking/storage/viking_fs/_ops.py",
        "from openviking.observability.reporting.facts import emit_file_change, file_exists_before\n",
        "",
    )
    _replace(
        "openviking/storage/viking_fs/_ops.py",
        """        existed = await file_exists_before(self, path)
        result = await self._async_agfs.write(path, data)
        emit_file_change(uri, ctx, "modified" if existed else "added" if existed is False else None)
""",
        """        result = await self._async_agfs.write(path, data)
""",
    )
    _replace(
        "openviking/storage/viking_fs/_ops.py",
        '            emit_file_change(target_uri, ctx, "deleted", is_dir=is_dir)\n',
        "",
    )
    _replace(
        "openviking/storage/viking_fs/_ops.py",
        """            emit_file_change(
                new_uri, ctx, "modified", old_uri=old_uri, is_dir=is_dir, operation="move"
            )
""",
        "",
    )
    text = _read("openviking/storage/viking_fs/_ops.py")
    text = text.replace(
        """        existed = await file_exists_before(self, path)
""",
        "",
    )
    text = text.replace(
        """        emit_file_change(uri, ctx, "modified" if existed else "added" if existed is False else None)
""",
        "",
    )
    text = text.replace(
        """            existed = await file_exists_before(self, path)
""",
        "",
    )
    text = text.replace(
        """            emit_file_change(uri, ctx, "modified" if existed else "added" if existed is False else None)
""",
        "",
    )
    _write("openviking/storage/viking_fs/_ops.py", text)

    # --- content write: drop deferred file change publication ---
    _replace(
        "openviking/storage/content_write.py",
        """        from openviking.observability.reporting.facts import defer_file_changes

        with defer_file_changes() as pending_file_changes:
            try:
                result = await self._write(
                    uri=uri,
                    content=content,
                    ctx=ctx,
                    mode=mode,
                    wait=wait,
                    processing_mode=processing_mode,
                    tags=tags,
                    tag_mode=tag_mode,
                    acl=acl,
                    resolve_conflicts=resolve_conflicts,
                    root_operation="content_write",
                    root_task_factory=create_root_task,
                )
            except BaseException as exc:
                if task_id is not None and not _root_owns_failure(exc):
                    await self._fail_content_write_task(
                        task.task_id if task is not None else task_id, exc, ctx=ctx
                    )
                raise
            if task is None:
                raise RuntimeError("Content write completed without creating a root Task")
            pending_file_changes.publish(task)
""",
        """        try:
            result = await self._write(
                uri=uri,
                content=content,
                ctx=ctx,
                mode=mode,
                wait=wait,
                processing_mode=processing_mode,
                tags=tags,
                tag_mode=tag_mode,
                acl=acl,
                resolve_conflicts=resolve_conflicts,
                root_operation="content_write",
                root_task_factory=create_root_task,
            )
        except BaseException as exc:
            if task_id is not None and not _root_owns_failure(exc):
                await self._fail_content_write_task(
                    task.task_id if task is not None else task_id, exc, ctx=ctx
                )
            raise
        if task is None:
            raise RuntimeError("Content write completed without creating a root Task")
""",
    )

    # --- compile targets: drop compile output reporting ---
    _replace(
        "openviking/service/compile_targets.py",
        """                                from openviking.observability.reporting.facts import (
                                    emit_compile_outputs,
                                )

                                emit_compile_outputs(
                                    task,
                                    changes,
                                    snapshot_id=head,
                                    committed_at=run_meta["target_committed_at"],
                                )
""",
        "",
    )

    # --- admin router: drop the offline metric inventory export endpoint ---
    _remove_block(
        "openviking/server/routers/admin.py",
        '@router.get("/metric-inventory")',
        'media_type="application/x-ndjson",',
    )


def step_remove_server_wiring() -> None:
    # --- app.py ---
    _replace(
        "openviking/server/app.py",
        "from openviking_cli.utils.logger import init_otel_log_handler_from_server_config\n",
        "",
    )
    _replace(
        "openviking/server/app.py",
        """        from openviking.observability.reporting import start_reporting

        app.state.event_reporter = await start_reporting(config.observability.reporting)

""",
        "",
    )
    _replace(
        "openviking/server/app.py",
        """        from openviking.observability.reporting import stop_reporting

        await stop_reporting()
""",
        "",
    )
    _replace(
        "openviking/server/app.py",
        """        # Initialize tracing and OTLP log export from server.observability.
        from openviking.telemetry import tracer_module

        tracer_module.init_tracer_from_server_config(config)
        init_otel_log_handler_from_server_config(config)
""",
        """        # Initialize local tracing from server.observability.
        from openviking.telemetry import tracer_module

        tracer_module.init_tracer_from_server_config(config)
""",
    )

    # --- server config schema ---
    _remove_block(
        "openviking/server/config.py",
        "class EventReportingConfig(BaseModel):",
        "    nacos_group: Optional[str] = None",
    )
    _replace(
        "openviking/server/config.py",
        "    reporting: EventReportingConfig = Field(default_factory=EventReportingConfig)\n",
        "",
    )
    _replace(
        "openviking/server/config.py",
        '''class OTelExporterConfig(BaseModel):
    """OpenTelemetry exporter configuration."""

    class TLSConfig(BaseModel):
        """TLS configuration for OTLP exporters."""

        insecure: bool = False

    enabled: bool = False
    protocol: str = "grpc"  # "grpc", "http", or "local" for traces
    tls: TLSConfig = Field(default_factory=TLSConfig)
    endpoint: str = "localhost:4317"  # gRPC default: 4317; HTTP default: 4318
    service_name: str = "openviking-server"
    export_interval_ms: int = 10000
    headers: Dict[str, str] = Field(default_factory=dict)
    local_path: str = "~/.openviking/logs/traces.jsonl"
    local_rotation_mb: int = Field(default=40, gt=0)
    local_backup_count: int = Field(default=2, ge=0)
''',
        '''class LocalTraceConfig(BaseModel):
    """Local-only trace export configuration."""

    enabled: bool = False
    service_name: str = "openviking-server"
    local_path: str = "~/.openviking/logs/traces.jsonl"
    local_rotation_mb: int = Field(default=40, gt=0)
    local_backup_count: int = Field(default=2, ge=0)
''',
    )
    _replace(
        "openviking/server/config.py",
        """    traces: OTelExporterConfig = Field(default_factory=OTelExporterConfig)
    logs: OTelExporterConfig = Field(default_factory=OTelExporterConfig)
""",
        """    traces: LocalTraceConfig = Field(default_factory=LocalTraceConfig)
""",
    )
    _replace(
        "openviking/server/config.py",
        """    prometheus: PrometheusExporterConfig = Field(default_factory=PrometheusExporterConfig)
    otel: OTelExporterConfig = Field(default_factory=OTelExporterConfig)
""",
        """    prometheus: PrometheusExporterConfig = Field(default_factory=PrometheusExporterConfig)
""",
    )

    # --- metrics bootstrap ---
    _replace(
        "openviking/metrics/global_api.py",
        "from .exporters.otel import OTelMetricExporter\n",
        "",
    )
    _replace(
        "openviking/metrics/global_api.py",
        """        # Initialize OTel exporter if enabled
        if exporters_config.otel.enabled:
            otel_exporter = OTelMetricExporter(
                registry=_registry,
                collector_manager=collector_manager,
                protocol=exporters_config.otel.protocol,
                insecure=exporters_config.otel.tls.insecure,
                endpoint=exporters_config.otel.endpoint,
                service_name=exporters_config.otel.service_name,
                export_interval_ms=exporters_config.otel.export_interval_ms,
                headers=exporters_config.otel.headers,
            )
            otel_exporter.start()
            _exporters.append(otel_exporter)

""",
        "",
    )


def step_rewrite_tracer() -> None:
    rel = "openviking/telemetry/tracer.py"

    # Drop the protobuf/OTLP encoder import.
    _replace(
        rel,
        """    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    except ImportError:
        MessageToDict = None
        encode_spans = None

""",
        "",
    )
    # Drop the OTLP span exporter imports.
    _replace(
        rel,
        """    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as OTLPGrpcSpanExporter,
        )
    except ImportError:
        OTLPGrpcSpanExporter = None

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as OTLPHttpSpanExporter,
        )
    except ImportError:
        OTLPHttpSpanExporter = None
except ImportError:
""",
        "except ImportError:\n",
    )
    _replace(
        rel,
        """    OTLPGrpcSpanExporter = None
    OTLPHttpSpanExporter = None
""",
        "",
    )
    _replace(
        rel,
        """    MessageToDict = None
    encode_spans = None
""",
        "",
    )

    # Insert the protobuf-free span serializer before the exporter class.
    _replace(
        rel,
        "_SpanExporterBase = SpanExporter if SpanExporter is not None else object\n",
        "_SpanExporterBase = SpanExporter if SpanExporter is not None else object\n\n\n" + SPAN_TO_DICT,
    )

    # Local exporter no longer needs the OTLP encoder.
    _replace(
        rel,
        """        if (
            SpanExporter is None
            or SpanExportResult is None
            or MessageToDict is None
            or encode_spans is None
        ):
            raise ImportError("OpenTelemetry trace exporter dependencies are not available")
""",
        """        if SpanExporter is None or SpanExportResult is None:
            raise ImportError("OpenTelemetry trace exporter dependencies are not available")
""",
    )
    _replace(
        rel,
        """        try:
            request = encode_spans(spans)
            payload = MessageToDict(
                request,
                preserving_proto_field_name=False,
                always_print_fields_with_no_presence=False,
            )
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\\n"
""",
        """        try:
            payload = {"spans": [_span_to_dict(span) for span in spans]}
            line = (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str) + "\\n"
            )
""",
    )

    # init_tracer_from_server_config: local-only.
    _replace(
        rel,
        """        if trace_cfg.protocol.lower() != "local" and not trace_cfg.endpoint:
            logger.warning("[TRACER] server.observability.traces.endpoint not configured")
            return None

        return init_tracer(
            endpoint=trace_cfg.endpoint,
            service_name=trace_cfg.service_name,
            protocol=trace_cfg.protocol,
            insecure=trace_cfg.tls.insecure,
            headers=trace_cfg.headers,
            enabled=trace_cfg.enabled,
            local_path=trace_cfg.local_path,
            local_rotation_mb=trace_cfg.local_rotation_mb,
            local_backup_count=trace_cfg.local_backup_count,
        )
""",
        """        return init_tracer(
            service_name=trace_cfg.service_name,
            enabled=trace_cfg.enabled,
            local_path=trace_cfg.local_path,
            local_rotation_mb=trace_cfg.local_rotation_mb,
            local_backup_count=trace_cfg.local_backup_count,
        )
""",
    )

    # Drop asyncio instrumentation (external instrumentation package).
    _remove_block(
        rel,
        "def _init_asyncio_instrumentation() -> None:",
        '        logger.warning(f"[TRACER] failed to init AsyncioInstrumentor: {e}")',
    )
    _replace(
        rel,
        """        # Initialize asyncio instrumentation to create child spans for create_task
        _init_asyncio_instrumentation()

""",
        "",
    )

    # init_tracer signature + body: local-only.
    _replace(
        rel,
        """def init_tracer(
    endpoint: str,
    service_name: str,
    protocol: str = "grpc",
    insecure: bool = False,
    headers: Optional[dict[str, str]] = None,
    enabled: bool = True,
    local_path: str = "~/.openviking/logs/traces.jsonl",
    local_rotation_mb: int = 40,
    local_backup_count: int = 2,
) -> Any:
    \"\"\"Initialize the OpenTelemetry tracer.

    Args:
        endpoint: OTLP endpoint
        service_name: Service name for tracing
        protocol: OTLP protocol ("grpc" or "http")
        insecure: For OTLP/gRPC only. When True, use plaintext instead of TLS.
        headers: Additional OTLP exporter headers for vendor-specific auth.
        enabled: Whether to enable tracing
        local_path: JSONL file path when protocol is "local".
        local_rotation_mb: Maximum size in MB before rotating local JSONL file.
        local_backup_count: Number of rotated local JSONL files to keep.

    Returns:
        The initialized tracer, or None if initialization failed
    \"\"\"
""",
        """def init_tracer(
    service_name: str,
    enabled: bool = True,
    local_path: str = "~/.openviking/logs/traces.jsonl",
    local_rotation_mb: int = 40,
    local_backup_count: int = 2,
) -> Any:
    \"\"\"Initialize the local OpenTelemetry tracer.

    Args:
        service_name: Service name for tracing
        enabled: Whether to enable tracing
        local_path: JSONL file path for local span export.
        local_rotation_mb: Maximum size in MB before rotating local JSONL file.
        local_backup_count: Number of rotated local JSONL files to keep.

    Returns:
        The initialized tracer, or None if initialization failed
    \"\"\"
""",
    )
    _replace(
        rel,
        """    try:
        normalized_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        resource_attributes = {
            "service.name": service_name,
        }
        resource = Resource.create(resource_attributes)

        protocol = protocol.lower()
        if protocol == "grpc":
            if OTLPGrpcSpanExporter is None:
                raise ImportError("gRPC OTLP trace exporter not available")
            try:
                trace_exporter = OTLPGrpcSpanExporter(
                    endpoint=endpoint,
                    insecure=insecure,
                    headers=normalized_headers,
                )
            except TypeError:
                trace_exporter = OTLPGrpcSpanExporter(
                    endpoint=endpoint,
                    headers=normalized_headers,
                )
        elif protocol == "http":
            if OTLPHttpSpanExporter is None:
                raise ImportError("HTTP OTLP trace exporter not available")
            if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
                raise ValueError(
                    "OTLP/HTTP trace endpoint must include scheme, e.g. 'http://localhost:4318/v1/traces'"
                )
            trace_exporter = OTLPHttpSpanExporter(
                endpoint=endpoint,
                headers=normalized_headers,
            )
        elif protocol == "local":
            trace_exporter = LocalJsonlSpanExporter(
                local_path,
                rotation_mb=local_rotation_mb,
                backup_count=local_backup_count,
            )
        else:
            raise ValueError(f"Unsupported trace protocol: {protocol}")
""",
        """    try:
        resource_attributes = {
            "service.name": service_name,
        }
        resource = Resource.create(resource_attributes)

        trace_exporter = LocalJsonlSpanExporter(
            local_path,
            rotation_mb=local_rotation_mb,
            backup_count=local_backup_count,
        )
""",
    )
    _replace(
        rel,
        """        logger.debug(
            "[TRACER] initialized with service_name=%s, protocol=%s, endpoint=%s",
            service_name,
            protocol,
            endpoint,
        )
""",
        """        logger.debug(
            "[TRACER] initialized with service_name=%s, local_path=%s",
            service_name,
            local_path,
        )
""",
    )


def step_rewrite_logger() -> None:
    rel = "openviking_cli/utils/logger.py"

    # Keep local trace-context imports; drop the OTLP log exporter imports.
    _replace(
        rel,
        """# Try to import opentelemetry - will be None if not installed
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import Status, StatusCode, format_span_id, format_trace_id

    # Try to import the gRPC exporter.
    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter as OTLPGrpcLogExporter,
        )
    except ImportError:
        OTLPGrpcLogExporter = None

    # Try to import the HTTP exporter.
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter as OTLPHttpLogExporter,
        )
    except ImportError:
        OTLPHttpLogExporter = None
except ImportError:
    otel_trace = None
    set_logger_provider = None
    LoggerProvider = None
    LoggingHandler = None
    BatchLogRecordProcessor = None
    OTLPGrpcLogExporter = None
    OTLPHttpLogExporter = None
    Resource = None
    Status = None
    StatusCode = None
    format_span_id = None
    format_trace_id = None


# Global OTel log handler state
_otel_log_handler_initialized = False
_otel_log_handler: Any = None

""",
        """# Try to import opentelemetry - will be None if not installed
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Status, StatusCode, format_span_id, format_trace_id
except ImportError:
    otel_trace = None
    Status = None
    StatusCode = None
    format_span_id = None
    format_trace_id = None

""",
    )

    # Remove the whole OTLP log handler block.
    _remove_block(
        rel,
        "def init_otel_log_handler(",
        "            logger.addHandler(_otel_log_handler)",
    )

    # _configure_logger_instance: drop OTel handler attachment.
    _replace(
        rel,
        """    if handler is not None:
        logger.addHandler(handler)
        if _otel_log_handler is not None and isinstance(_otel_log_handler, logging.Handler):
            logger.addHandler(_otel_log_handler)
""",
        """    if handler is not None:
        logger.addHandler(handler)
""",
    )
    _replace(
        rel,
        """        if old_handler in current_handlers or old_handler is _otel_log_handler:
            continue
""",
        """        if old_handler in current_handlers:
            continue
""",
    )

    # get_logger: drop the add_otel_handler parameter and auto-attach.
    _replace(
        rel,
        """def get_logger(
    name: str = "openviking",
    format_string: Optional[str] = None,
    add_otel_handler: bool = False,
) -> logging.Logger:
""",
        """def get_logger(
    name: str = "openviking",
    format_string: Optional[str] = None,
) -> logging.Logger:
""",
    )
    _replace(
        rel,
        """        format_string: Custom log format string, optional.
        add_otel_handler: Whether to add OTel LoggingHandler, defaults to False.
""",
        """        format_string: Custom log format string, optional.
""",
    )
    _replace(
        rel,
        """    # If OTel log export is globally initialized, attach the handler automatically.
    if add_otel_handler or _otel_log_handler_initialized:
        add_otel_log_handler_to_logger(logger)

""",
        "",
    )


def step_remove_cli_telemetry_config() -> None:
    _replace(
        "openviking_cli/utils/config/open_viking_config.py",
        "from .telemetry_config import TelemetryConfig\n",
        "",
    )
    _replace(
        "openviking_cli/utils/config/open_viking_config.py",
        """    telemetry: "TelemetryConfig" = Field(
        default_factory=TelemetryConfig, description="Telemetry configuration"
    )
""",
        "",
    )
    _replace(
        "openviking_cli/utils/config/__init__.py",
        "from .telemetry_config import TelemetryConfig, TracerConfig\n",
        "",
    )
    _replace(
        "openviking_cli/utils/config/__init__.py",
        """    "TelemetryConfig",
    "TracerConfig",
""",
        "",
    )


def step_remove_dependencies() -> None:
    _replace(
        "pyproject.toml",
        """    "opentelemetry-exporter-otlp-proto-grpc>=1.40,<2",
    "opentelemetry-exporter-otlp-proto-http>=1.40,<2",
    "opentelemetry-instrumentation-asyncio>=0.61b0",
""",
        "",
    )
    _replace(
        "pyproject.toml",
        """reporting = [
    "kafka-python>=3.0.12,<4",
]
""",
        "",
    )


def step_update_tests() -> None:
    # Config loader: external exporter header assertions no longer exist.
    _replace(
        "tests/test_server_config_loader.py",
        """    assert config.observability.traces.headers == {
        "X-ByteAPM-AppKey": "trace-appkey",
    }
    assert config.observability.logs.headers == {
        "X-ByteAPM-AppKey": "log-appkey",
    }
    assert config.observability.metrics.exporters.otel.headers == {
        "X-ByteAPM-AppKey": "metric-appkey",
    }
""",
        "",
    )

    # Telemetry runtime: drop the OTLP trace/log exporter tests.
    _remove_block(
        "tests/test_telemetry_runtime.py",
        "def test_init_tracer_forwards_headers_to_grpc_exporter(monkeypatch):",
        '    assert captured["headers"] == {"x-byteapm-appkey": "trace-appkey"}',
    )
    _remove_block(
        "tests/test_telemetry_runtime.py",
        "def test_init_tracer_forwards_headers_to_http_exporter(monkeypatch):",
        '    assert captured["headers"] == {"X-ByteAPM-AppKey": "trace-appkey"}',
    )
    _remove_block(
        "tests/test_telemetry_runtime.py",
        "def test_init_otel_log_handler_forwards_headers_to_grpc_exporter(monkeypatch):",
        '    assert captured["headers"] == {"x-byteapm-appkey": "log-appkey"}',
    )
    _remove_block(
        "tests/test_telemetry_runtime.py",
        "def test_init_otel_log_handler_forwards_headers_to_http_exporter(monkeypatch):",
        '    assert captured["headers"] == {"X-ByteAPM-AppKey": "log-appkey"}',
    )

    # Metrics exporter test: keep the Prometheus assertions only.
    _write(
        "tests/metrics/core/test_exporter.py",
        '''# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0


def test_exporter_outputs_help_type_and_empty_metrics(registry, render_prometheus):
    registry.set_counter("openviking_empty_counter_total", 0)
    registry.set_gauge("openviking_empty_gauge", 0)
    registry.set_histogram(
        "openviking_empty_histogram_seconds",
        bucket_bounds=(1,),
        bucket_counts=(0, 0),
        count=0,
        value_sum=0,
    )
    text = render_prometheus(registry)
    assert "# TYPE openviking_empty_counter_total counter" in text
    assert "openviking_empty_counter_total 0.0" in text
    assert "# TYPE openviking_empty_histogram_seconds histogram" in text
    assert 'le="+Inf"} 0.0' in text
''',
    )


def step_remove_langfuse() -> None:
    """Remove the opt-in Langfuse external LLM trace exporter from the bot."""
    _delete("bot/vikingbot/integrations")

    # --- bot config schema ---
    _remove_block(
        "bot/vikingbot/config/schema.py",
        "class LangfuseConfig(BaseModel):",
        '    base_url: str = "http://localhost:3000"',
    )
    _replace(
        "bot/vikingbot/config/schema.py",
        "    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)\n",
        "",
    )

    # --- VLM adapter ---
    rel = "bot/vikingbot/providers/vlm_adapter.py"
    _replace(rel, "from vikingbot.integrations.langfuse import LangfuseClient\n", "")
    _replace(rel, "from vikingbot.utils.tracing import get_current_response_id\n", "")
    _replace(rel, "        langfuse_client: LangfuseClient | None = None,\n", "")
    _replace(rel, "        self._langfuse = langfuse_client or LangfuseClient.get_instance()\n", "")
    _replace(
        rel,
        """        # --- Langfuse: start observation ---
        langfuse_observation = None
        try:
            if self._langfuse.enabled and self._langfuse._client:
                metadata: dict[str, Any] = {"has_tools": tools is not None}
                response_id = get_current_response_id()
                if response_id:
                    metadata["response_id"] = response_id
                client = self._langfuse._client
                if hasattr(client, "start_observation"):
                    langfuse_observation = client.start_observation(
                        name="llm-chat",
                        as_type="generation",
                        model=effective_model,
                        input=redact_image_data_urls(messages),
                        metadata=metadata,
                    )
                    if response_id:
                        self._langfuse.register_generation(
                            response_id, langfuse_observation, metadata=metadata
                        )

            # --- Call VLM backend ---
""",
        """        try:
            # --- Call VLM backend ---
""",
    )
    _replace(
        rel,
        """            # --- Langfuse: end observation ---
            if langfuse_observation:
                self._end_langfuse_observation(langfuse_observation, llm_response)

            return llm_response
""",
        """            return llm_response
""",
    )
    _replace(
        rel,
        """        except Exception as e:
            if langfuse_observation:
                self._end_langfuse_observation_error(langfuse_observation, e)
            self._log_request_exception("chat", e, model=effective_model)
""",
        """        except Exception as e:
            self._log_request_exception("chat", e, model=effective_model)
""",
    )
    # Truncate the trailing Langfuse helper methods.
    text = _read(rel)
    marker = (
        "    # ------------------------------------------------------------------\n"
        "    # Langfuse helpers\n"
    )
    _write(rel, text[: text.index(marker)].rstrip() + "\n")

    # --- tracing ---
    rel = "bot/vikingbot/utils/tracing.py"
    _replace(
        rel,
        """# Try to import langfuse observe decorator
try:
    from langfuse.decorators import observe as langfuse_observe
except ImportError:
    langfuse_observe = None


""",
        "",
    )
    _replace(
        rel,
        """        span_name = name or func.__name__

        # Apply @observe decorator if available for Langfuse tracing
        wrapped_func = func
        if langfuse_observe is not None:
            wrapped_func = langfuse_observe(name=span_name)(func)

""",
        "",
    )
    _replace(
        rel,
        """            # Use context manager to set session_id for nested operations
            if session_id:
                with set_session_id(session_id):
                    # Also propagate to langfuse if available
                    from vikingbot.integrations.langfuse import LangfuseClient

                    langfuse = LangfuseClient.get_instance()
                    has_propagate = hasattr(langfuse, "propagate_attributes")
                    is_enabled = getattr(langfuse, "enabled", False)
                    # logger.info(f"[LANGFUSE] Client status: enabled={langfuse.enabled}, has_propagate_attributes={has_propagate}")
                    if is_enabled and has_propagate:
                        # logger.info(f"[LANGFUSE] Starting trace with attributes: session_id={session_id}, user_id={user_id}")
                        with langfuse.propagate_attributes(session_id=session_id, user_id=user_id):
                            return await wrapped_func(*args, **kwargs)
                    else:
                        if not has_propagate:
                            logger.warning("[LANGFUSE] propagate_attributes not available")
                    return await wrapped_func(*args, **kwargs)
            else:
                return await wrapped_func(*args, **kwargs)
""",
        """            # Use context manager to set session_id for nested operations
            if session_id:
                with set_session_id(session_id):
                    return await wrapped_func(*args, **kwargs)
            else:
                return await wrapped_func(*args, **kwargs)
""",
    )

    # --- CLI ---
    rel = "bot/vikingbot/cli/commands.py"
    _replace(rel, "from vikingbot.integrations.langfuse import LangfuseClient  # noqa: E402\n", "")
    _replace(
        rel,
        "def _make_provider(config, langfuse_client: Any = None):",
        "def _make_provider(config):",
    )
    _replace(rel, "            langfuse_client=langfuse_client,\n", "", count=3)
    _replace(
        rel,
        """    # Initialize Langfuse if enabled
    langfuse_client = None
    # logger.info(f"[LANGFUSE] Config check: has langfuse attr={hasattr(config, 'langfuse')}")

    if hasattr(config, "langfuse") and config.langfuse.enabled:
        langfuse_client = LangfuseClient(
            enabled=config.langfuse.enabled,
            secret_key=config.langfuse.secret_key,
            public_key=config.langfuse.public_key,
            base_url=config.langfuse.base_url,
        )
        LangfuseClient.set_instance(langfuse_client)
        if langfuse_client.enabled:
            logger.info(f"Langfuse: enabled (base_url={config.langfuse.base_url})")
        else:
            logger.warning("Langfuse: configured but failed to initialize")

    provider = _make_provider(config, langfuse_client)
""",
        """    provider = _make_provider(config)
""",
    )

    # --- tool registry ---
    rel = "bot/vikingbot/agent/tools/registry.py"
    _replace(rel, "import time\n", "")
    _replace(rel, "from vikingbot.integrations.langfuse import LangfuseClient\n", "")
    _replace(rel, "from vikingbot.utils.tracing import get_current_response_id\n", "")
    _replace(rel, "        self.langfuse = LangfuseClient.get_instance()\n", "")
    _replace(
        rel,
        """        # Langfuse tool call tracing - automatic for all tools
        tool_span = None
        start_time = time.time()
        result = None
        effective_params = dict(params)
        skill_uris: tuple[str, ...] = ()
        response_id = get_current_response_id()
        try:
            if self.langfuse.enabled:
                tool_ctx = self.langfuse.tool_call(
                    name=name,
                    input=params,
                    session_id=session_key.safe_name(),
                    metadata={"response_id": response_id} if response_id else None,
                )
                tool_span = tool_ctx.__enter__()

            validation_params = (
""",
        """        result = None
        effective_params = dict(params)
        skill_uris: tuple[str, ...] = ()
        try:
            validation_params = (
""",
    )
    _replace(
        rel,
        """        finally:
            # End Langfuse tool call tracing
            duration_ms = (time.time() - start_time) * 1000
            if tool_span is not None:
                try:
                    execute_success = not isinstance(result, Exception) and not (
                        isinstance(result, str) and result.lstrip().startswith("Error:")
                    )
                    output_str = str(result) if result is not None else None
                    self.langfuse.end_tool_call(
                        span=tool_span,
                        output=output_str,
                        success=execute_success,
                        metadata={
                            "duration_ms": duration_ms,
                            **({"response_id": response_id} if response_id else {}),
                        },
                    )
                    if hasattr(tool_span, "__exit__"):
                        tool_span.__exit__(None, None, None)
                    self.langfuse.flush()
                except Exception:
                    pass

        hook_result = await hook_manager.execute_hooks(
""",
        """        hook_result = await hook_manager.execute_hooks(
""",
    )

    # --- loop events ---
    rel = "bot/vikingbot/agent/loop/loop_events.py"
    _replace(rel, "from vikingbot.integrations.langfuse import LangfuseClient\n", "")
    _replace(
        rel,
        """        LangfuseClient.get_instance().update_response_outcome(
            response_id,
            outcome_payload["outcome_label"],
            outcome_payload,
        )
""",
        "",
    )

    # --- loop ---
    rel = "bot/vikingbot/agent/loop/loop.py"
    _replace(rel, "from vikingbot.integrations.langfuse import LangfuseClient\n", "")
    _replace(
        rel,
        """            LangfuseClient.get_instance().update_generation_metadata(
                response_id, response_completed
            )
""",
        "",
    )

    # --- loop package re-export ---
    _replace("bot/vikingbot/agent/loop/__init__.py", "    LangfuseClient,\n", "")

    # --- dependency ---
    _replace("pyproject.toml", '  "langfuse>=3.0.0",\n', "")

    # --- tests ---
    rel = "tests/unit/test_vikingbot_vlm_adapter_retry.py"
    _replace(
        rel,
        """class _DisabledLangfuse:
    enabled = False
    _client = None


""",
        "",
    )
    text = _read(rel)
    text = text.replace(", langfuse_client=_DisabledLangfuse()", "")
    text = text.replace("        langfuse_client=_DisabledLangfuse(),\n", "")
    _write(rel, text)

    rel = "bot/tests/test_bot_provider_thinking.py"
    _replace(rel, "from vikingbot.integrations.langfuse import LangfuseClient\n", "")
    _write(rel, _read(rel).replace(", langfuse_client=LangfuseClient()", ""))

    rel = "bot/tests/test_agent_loop_outcome.py"
    _replace(
        rel,
        """class _FakeLangfuseClient:
    def __init__(self):
        self.calls = []

    def update_generation_metadata(self, response_id, metadata):
        self.calls.append((response_id, metadata))
        return metadata

    def update_response_outcome(self, response_id, outcome_label, outcome_payload):
        self.calls.append((response_id, outcome_label, outcome_payload))
        return outcome_payload


""",
        "",
    )
    _replace(rel, "        from vikingbot.integrations.langfuse import LangfuseClient\n", "")
    text = _read(rel)
    text = text.replace(", langfuse_client=LangfuseClient()", "")
    text = text.replace("    fake_langfuse = _FakeLangfuseClient()\n", "")
    text = text.replace(
        """    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )
""",
        "",
    )
    text = text.replace(
        """    monkeypatch.setattr(
        "vikingbot.agent.loop.LangfuseClient.get_instance",
        staticmethod(lambda: _FakeLangfuseClient()),
    )
""",
        "",
    )
    _write(rel, text)

    # --- docs ---
    _replace(
        "docs/en/concepts/15-vikingbot.md",
        "- user feedback, outcome evaluation, logs, and optional Langfuse observability.",
        "- user feedback, outcome evaluation, and logs.",
    )
    _replace(
        "docs/zh/concepts/15-vikingbot.md",
        "- 用户反馈、结果评估、日志和可选 Langfuse 观测。",
        "- 用户反馈、结果评估和日志。",
    )


def step_remove_langfuse_residuals() -> None:
    """Remove the remaining Langfuse call sites, tests, docs and deploy assets."""
    # --- HTTP channel ---
    rel = "bot/vikingbot/channels/openapi.py"
    _replace(rel, "from vikingbot.integrations.langfuse import LangfuseClient\n", "")
    _replace(
        rel,
        """            LangfuseClient.get_instance().update_response_outcome(
                request.response_id,
                outcome_payload["outcome_label"],
                outcome_payload,
            )
""",
        "",
    )

    # --- tracing docstrings ---
    rel = "bot/vikingbot/utils/tracing.py"
    _replace(
        rel,
        "backend (Langfuse, OpenTelemetry, etc.), allowing for easy switching of",
        "backend, allowing for easy switching of",
    )
    _replace(
        rel,
        "    implementation (Langfuse, OpenTelemetry, etc.).",
        "    implementation.",
    )

    # --- bot tests ---
    for rel in (
        "bot/tests/test_bot_vlm_credentials.py",
        "bot/tests/test_bot_vlm_streaming.py",
        "bot/tests/test_bot_provider_thinking.py",
    ):
        _replace(
            rel,
            "        langfuse_client=SimpleNamespace(enabled=False, _client=None),\n",
            "",
        )

    rel = "bot/tests/test_compile_sources.py"
    _replace(rel, "from vikingbot.integrations.langfuse import LangfuseClient\n", "")
    _replace(
        rel,
        "_make_provider(loader.load_config(), langfuse_client=LangfuseClient())",
        "_make_provider(loader.load_config())",
    )

    # --- whole-file removals ---
    _delete("bot/tests/test_langfuse_outcome_metadata.py")
    _delete("bot/docs/zh/design/vikingbot-langfuse-local-setup.md")
    _delete("bot/deploy/docker/langfuse")
    _delete("bot/deploy/docker/deploy_langfuse.sh")


STEPS = (
    ("delete external telemetry subsystems", step_delete_external_subsystems),
    ("write local-only facts module", step_write_local_facts),
    ("rewire facts imports", step_rewire_facts_imports),
    ("remove reporting call sites", step_remove_reporting_call_sites),
    ("remove server wiring", step_remove_server_wiring),
    ("rewrite tracer to local-only", step_rewrite_tracer),
    ("rewrite logger to local-only", step_rewrite_logger),
    ("remove CLI telemetry config", step_remove_cli_telemetry_config),
    ("remove external dependencies", step_remove_dependencies),
    ("update tests", step_update_tests),
    ("remove langfuse external trace exporter", step_remove_langfuse),
    ("remove langfuse residuals", step_remove_langfuse_residuals),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the planned steps without touching the tree.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="KEY",
        help="Run only steps whose title contains KEY (repeatable).",
    )
    args = parser.parse_args()

    selected = [
        (title, step)
        for title, step in STEPS
        if not args.only or any(key.lower() in title.lower() for key in args.only)
    ]
    if not selected:
        parser.error("--only matched no steps")

    if args.dry_run:
        for title, _ in selected:
            print(f"would run: {title}")
        return 0

    for title, step in selected:
        print(f"[telemetry] {title}")
        step()

    print("[telemetry] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
