#!/usr/bin/env python3
"""Probe mutating endpoints against an isolated sandbox and infer response types.

The read-only probes in ``probe_responses.py`` cannot reach POST/PUT/PATCH/DELETE
operations because they would change a live workspace. This script targets a
throwaway server instead (see the ``sdk-sandbox`` recipe), runs a curated list of
mutations with prerequisites in order, records what each one returns, and writes
``src/generated/mutations.ts``.

Nothing here is inferred from assumptions: every type comes from a real response.
A probe whose prerequisite id was not returned by an earlier step is dropped, and
a probe the server rejects stays ``unknown``.

Usage:
    python scripts/probe_mutations.py --url http://127.0.0.1:1944
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK = HERE.parent
OUT = SDK / "src" / "generated" / "mutations.ts"

RUN = str(int(time.time()))
ACCOUNT = "default"
SANDBOX = f"viking://user/default/sdk-sandbox-{RUN}"
NOTE = f"{SANDBOX}/note.md"
COPY = f"{SANDBOX}/copy.md"
SESSION = f"sdk-sandbox-{RUN}"
ADMIN_ACCOUNT = f"ovsdk-{RUN}"
ADMIN_MEMBER = "sdk-probe-member"
ADMIN_EXTRA = "sdk-probe-extra"
ADMIN_GROUP = "sdk-probe-group"
ACL_ROOT = f"viking://resources/sdk-acl-{RUN}"
ADMIN_SANDBOX = f"viking://user/{ADMIN_MEMBER}/sdk-pack"
WATCH_URI = f"viking://resources/sdk-watch-{RUN}"
PACK_BINARY = {"pack_export": "export_zip", "pack_backup": "backup_zip"}
ACCOUNT_ID = "${account_id}"
USER_ID = "${user_id}"
GROUP_ID = "${group_id}"
MEMORY_TYPE = "${memory_type}"

RESERVED = {
    "break", "case", "catch", "class", "const", "continue", "debugger", "default",
    "delete", "do", "else", "enum", "export", "extends", "false", "finally", "for",
    "function", "if", "import", "in", "instanceof", "new", "null", "return", "super",
    "switch", "this", "throw", "true", "try", "typeof", "var", "void", "while", "with",
    "as", "implements", "interface", "let", "package", "private", "protected", "public",
    "static", "yield", "any", "boolean", "constructor", "declare", "get", "module",
    "require", "number", "set", "string", "symbol", "type", "from", "of",
}


def ident(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_$]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "_" + cleaned
    if cleaned in RESERVED:
        cleaned = cleaned + "_"
    return cleaned


def camel(name: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    if not parts:
        return "op"
    head, *tail = parts
    return ident(head[0].lower() + head[1:] + "".join(p[0].upper() + p[1:] for p in tail))


def pascal(name: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    if not parts:
        return "Schema"
    return ident("".join(p[0].upper() + p[1:] for p in parts))


class Type:
    def __init__(self, kind: str, **kwargs: object) -> None:
        self.kind = kind
        self.props: dict[str, Type] = kwargs.get("props", {})  # type: ignore[assignment]
        self.required: set[str] = kwargs.get("required", set())  # type: ignore[assignment]
        self.items: Type | None = kwargs.get("items")  # type: ignore[assignment]
        self.variants: list[Type] = kwargs.get("variants", [])  # type: ignore[assignment]

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        if self.kind in ("string", "number", "boolean", "null", "unknown", "void"):
            return self.kind
        if self.kind == "array":
            inner = self.items.render(indent) if self.items else "unknown"
            if " | " in inner and not inner.startswith("("):
                inner = f"({inner})"
            return f"{inner}[]"
        if self.kind == "object":
            if not self.props:
                return "Record<string, unknown>"
            lines = ["{"]
            for name in sorted(self.props):
                optional = "" if name in self.required else "?"
                lines.append(
                    f"{pad}  {ident(name)}{optional}: {self.props[name].render(indent + 1)};"
                )
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        if self.kind == "union":
            parts: list[str] = []
            for variant in self.variants:
                rendered = variant.render(indent)
                if rendered not in parts:
                    parts.append(rendered)
            return " | ".join(parts) if parts else "unknown"
        return "unknown"


def infer(value: object) -> Type:
    if value is None:
        return Type("null")
    if isinstance(value, bool):
        return Type("boolean")
    if isinstance(value, (int, float)):
        return Type("number")
    if isinstance(value, str):
        return Type("string")
    if isinstance(value, list):
        if not value:
            return Type("array", items=Type("unknown"))
        merged = infer(value[0])
        for item in value[1:]:
            merged = merge(merged, infer(item))
        return Type("array", items=merged)
    if isinstance(value, dict):
        props = {str(k): infer(v) for k, v in value.items()}
        return Type("object", props=props, required=set(props))
    return Type("unknown")


def merge(left: Type, right: Type) -> Type:
    if left.kind == "unknown":
        return right
    if right.kind == "unknown":
        return left
    if left.kind == right.kind:
        if left.kind == "object":
            props = dict(left.props)
            required = set(left.required)
            for name, sub in right.props.items():
                if name in props:
                    props[name] = merge(props[name], sub)
                else:
                    props[name] = sub
                    required.discard(name)
            for name in left.props:
                if name not in right.props:
                    required.discard(name)
            return Type("object", props=props, required=required)
        if left.kind == "array":
            return Type(
                "array",
                items=merge(left.items or Type("unknown"), right.items or Type("unknown")),
            )
        return left
    if left.kind == "null":
        return Type("union", variants=[right, Type("null")])
    if right.kind == "null":
        return Type("union", variants=[left, Type("null")])
    return Type("union", variants=[left, right])


class RawBody:
    """A body that is not a JSON document; ``webdav`` and multipart use these."""

    def __init__(self, body: bytes, content_type: str) -> None:
        self.body = body
        self.content_type = content_type


class Reply:
    def __init__(self, status: int, body: bytes, content_type: str, json_body: object) -> None:
        self.status = status
        self.body = body
        self.content_type = content_type
        self.json = json_body

    def inferred(self) -> Type | None:
        if self.json is not None:
            return infer(unwrap(self.json))
        if self.status == 204 or not self.body:
            return Type("void")
        if self.content_type.startswith("text/") or "xml" in self.content_type:
            try:
                return infer(self.body.decode("utf-8"))
            except UnicodeDecodeError:
                return None
        if self.content_type.startswith(("application/zip", "application/octet-stream")):
            return Type("string")
        return None


SKILL_DOCUMENT = "---\nname: sdk-probe\ndescription: probe\n---\n\n# sdk-probe\n\nBody.\n"
UPDATED_SKILL_DOCUMENT = (
    "---\nname: sdk-probe\ndescription: probe updated\n---\n\n# sdk-probe\n\nBody.\n"
)
SKILL_BODY = {"data": SKILL_DOCUMENT}
UPDATED_SKILL_BODY = {"data": UPDATED_SKILL_DOCUMENT, "wait": True}


def multipart(name: str, filename: str, content: bytes) -> RawBody:
    boundary = "----sdk-probe-boundary"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return RawBody(head + content + tail, f"multipart/form-data; boundary={boundary}")


UPLOAD_FORM = multipart("file", "sdk-upload.md", b"# sdk upload\n\nbody\n")

# Above the 20k default externalization threshold so commits store a tool result.
TOOL_OUTPUT = "".join(
    f"TOOL OUTPUT LINE {index:04d}\n" + "x" * 120 for index in range(400)
)

PLACEHOLDER = re.compile(r"\$\{([a-z_]+)\}")

HANDOFF = {
    "commit_oid",
    "messages_sha256",
    "archive_id",
    "task_id",
    "watch_uri",
    "watch_task_id",
    "watch_id",
    "import_pack_id",
    "restore_pack_id",
    "temp_file_id",
    "memory_type",
    "user_id",
    "account_id",
}

API_KEY: str | None = None


def call(
    url: str,
    method: str,
    payload: object | None,
    timeout: float,
    api_key: str | None = None,
) -> Reply | None:
    headers = {}
    data: bytes | None = None
    if isinstance(payload, RawBody):
        data = payload.body
        headers["content-type"] = payload.content_type
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    key = API_KEY if api_key is None else api_key
    if key:
        headers["X-API-Key"] = key
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return Reply(
                response.status,
                body,
                response.headers.get("content-type", ""),
                parse_json(body),
            )
    except urllib.error.HTTPError as error:
        return Reply(error.code, b"", error.headers.get("content-type", "") or "", None)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def parse_json(body: bytes) -> object | None:
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def unwrap(payload: object) -> object:
    if isinstance(payload, dict) and payload.get("status") == "ok" and "result" in payload:
        return payload["result"]
    return payload


def fill(value: object, state: dict[str, object]) -> object:
    """Substitute ``${name}`` placeholders with values an earlier probe returned."""
    if isinstance(value, str):
        match = PLACEHOLDER.fullmatch(value)
        if match:
            return state.get(match.group(1))
        return PLACEHOLDER.sub(lambda item: str(state.get(item.group(1))), value)
    if isinstance(value, dict):
        return {key: fill(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [fill(item, state) for item in value]
    if isinstance(value, RawBody):
        return RawBody(fill(value.body, state), value.content_type)  # type: ignore[arg-type]
    return value


def needs(value: object) -> list[str]:
    """List the placeholders a probe needs before it can run."""
    if isinstance(value, str):
        return [item.group(1) for item in PLACEHOLDER.finditer(value)]
    if isinstance(value, dict):
        return [name for item in value.values() for name in needs(item)]
    if isinstance(value, list):
        return [name for item in value for name in needs(item)]
    return []


def find_strings(value: object, names: set[str]) -> dict[str, str]:
    """Collect the first string value for each wanted key, at any depth."""
    found: dict[str, str] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in names and isinstance(item, str) and key not in found:
                    found[key] = item
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


# Each probe is (label, method, path, payload). A payload may embed ``${name}``
# placeholders or be a callable receiving the state; both resolve from what
# earlier probes actually returned. Order matters.
ACCOUNT_PROBES: list[tuple[str, str, str, object]] = [
    # filesystem and content writes the later probes read back
    ("fs_mkdir", "POST", "/api/v1/fs/mkdir", {"uri": SANDBOX, "description": "SDK sandbox"}),
    ("content_write", "POST", "/api/v1/content/write", {"uri": NOTE, "content": "# note\nbody\n"}),
    ("fs_attrs_set_tags", "POST", "/api/v1/fs/attrs/set_tags", {"uri": NOTE, "tags": ["sdk"]}),
    ("content_set_tags", "POST", "/api/v1/content/set_tags", {"uri": NOTE, "tags": ["sdk"]}),
    (
        "content_batch_write",
        "POST",
        "/api/v1/content/batch-write",
        {
            "root_uri": SANDBOX,
            "operations": [{"uri": f"{SANDBOX}/batch.md", "content": "batch\n"}],
        },
    ),
    (
        "content_ensure_directory_index",
        "POST",
        "/api/v1/content/ensure_directory_index",
        {"uri": SANDBOX, "fallback_abstract": "SDK sandbox"},
    ),
    ("content_reindex", "POST", "/api/v1/content/reindex", {"uri": NOTE}),
    ("fs_cp", "POST", "/api/v1/fs/cp", {"from_uri": NOTE, "to_uri": COPY}),
    ("fs_mv", "POST", "/api/v1/fs/mv", {"from_uri": COPY, "to_uri": f"{SANDBOX}/moved.md"}),
    # sessions: create -> messages -> context -> config -> commit -> archive -> tools -> delete
    (
        "sessions_create",
        "POST",
        "/api/v1/sessions",
        {
            "session_id": SESSION,
            "memory_extraction_config": {"events": {"tags": ["sdk=probe"]}},
        },
    ),
    (
        "sessions_add_message",
        "POST",
        f"/api/v1/sessions/{SESSION}/messages",
        {"role": "user", "content": "hello sandbox"},
    ),
    (
        "sessions_add_tool_message",
        "POST",
        f"/api/v1/sessions/{SESSION}/messages",
        {
            "role": "assistant",
            "parts": [
                {"type": "text", "text": "calling a tool"},
                {
                    "type": "tool",
                    "tool_id": "call_sdk_1",
                    "tool_name": "sdk_probe_tool",
                    "tool_status": "success",
                    "tool_output": TOOL_OUTPUT,
                },
            ],
        },
    ),
    (
        "sessions_batch_add_messages",
        "POST",
        f"/api/v1/sessions/{SESSION}/messages/batch",
        {
            "messages": [
                {"role": "user", "content": "batch one"},
                {"role": "assistant", "content": "batch two"},
            ]
        },
    ),
    ("sessions_context", "GET", f"/api/v1/sessions/{SESSION}/context", None),
    (
        "sessions_update_config",
        "PATCH",
        f"/api/v1/sessions/{SESSION}/config",
        {"memory_extraction_config": {"events": {"tags": ["sdk=probe"]}}},
    ),
    # skills: add -> get -> update -> find -> delete
    ("skills_validate", "POST", "/api/v1/skills/validate", {"data": {"name": "sdk-check"}}),
    ("skills_add", "POST", "/api/v1/skills", SKILL_BODY),
    ("skills_get", "GET", "/api/v1/skills/sdk-probe", None),
    ("skills_update", "PUT", "/api/v1/skills/sdk-probe", UPDATED_SKILL_BODY),
    # user settings and privacy configs
    ("user_settings_patch", "PATCH", "/api/v1/user-settings/add-locations", {}),
    ("user_settings_delete", "DELETE", "/api/v1/user-settings/add-locations", None),
    ("privacy_upsert", "POST", "/api/v1/privacy-configs/sdk/target", {"values": {"sdk": True}}),
    ("privacy_current", "GET", "/api/v1/privacy-configs/sdk/target", None),
    ("privacy_activate", "POST", "/api/v1/privacy-configs/sdk/target/activate", {"version": 1}),
    ("privacy_delete", "DELETE", "/api/v1/privacy-configs/sdk/target", None),
    ("privacy_selector_delete", "DELETE", "/api/v1/privacy-configs/selector?selector=sdk", None),
    # debug and task reads
    ("tasks_get", "GET", "/api/v1/tasks/${task_id}", None),
    # sessions commit produces the task and the archive the reads below need
    ("sessions_commit", "POST", f"/api/v1/sessions/{SESSION}/commit", {"keep_recent_count": 0}),
    (
        "sessions_archive",
        "GET",
        f"/api/v1/sessions/{SESSION}/archives/${{archive_id}}",
        None,
    ),
    (
        "sessions_archive_retry",
        "POST",
        f"/api/v1/sessions/{SESSION}/archives/${{archive_id}}/retry",
        {"expected_messages_sha256": "${messages_sha256}", "allow_ownerless_ready": True},
    ),
    ("sessions_tool_results", "GET", f"/api/v1/sessions/{SESSION}/tool-results", None),
    (
        "sessions_read_tool_result",
        "GET",
        f"/api/v1/sessions/{SESSION}/tool-results/${{tool_result_id}}",
        None,
    ),
    (
        "sessions_search_tool_result",
        "GET",
        f"/api/v1/sessions/{SESSION}/tool-results/${{tool_result_id}}/search?q=LINE",
        None,
    ),
    ("sessions_delete", "DELETE", f"/api/v1/sessions/{SESSION}", None),
    (
        "skills_delete",
        "DELETE",
        "/api/v1/skills/sdk-probe",
        None,
    ),
    # webdav: raw bodies, no JSON envelope
    (
        "webdav_put_path",
        "PUT",
        "/webdav/resources/sdk-probe.md",
        RawBody(b"webdav body", "text/markdown"),
    ),
    ("webdav_get_path", "GET", "/webdav/resources/sdk-probe.md", None),
    ("webdav_delete_path", "DELETE", "/webdav/resources/sdk-probe.md", None),
    # webdav collection routes reject GET/PUT/DELETE; they stay untyped
    ("webdav_get", "GET", "/webdav/resources", None),
    ("webdav_put", "PUT", "/webdav/resources", RawBody(b"body", "text/plain")),
    ("webdav_delete", "DELETE", "/webdav/resources", None),
    # resources: the upload must precede the add that consumes it
    ("resources_temp_upload", "POST", "/api/v1/resources/temp_upload", UPLOAD_FORM),
    (
        "resources_add",
        "POST",
        "/api/v1/resources",
        {"temp_file_id": "${temp_file_id}", "to": ACL_ROOT},
    ),
    # a Watch needs a remote source, and its target must be unoccupied
    (
        "resources_add_watch",
        "POST",
        "/api/v1/resources",
        {
            "path": f"https://example.com/sdk-watch-{RUN}.md",
            "to": WATCH_URI,
            "watch_interval": 86400,
        },
    ),
    # snapshot: commit first, then the reads need a commit ref
    ("snapshot_ignore_set", "PUT", "/api/v1/snapshot/ignore", {"content": "*.tmp\n"}),
    ("snapshot_ignore_delete", "DELETE", "/api/v1/snapshot/ignore", None),
    ("snapshot_commit", "POST", "/api/v1/snapshot/commit", {"message": "sdk probe commit"}),
    ("snapshot_log", "GET", "/api/v1/snapshot/log?limit=5", None),
    ("snapshot_show", "GET", "/api/v1/snapshot/show?target_ref=${commit_oid}", None),
    (
        "snapshot_diff",
        "GET",
        f"/api/v1/snapshot/diff?path={urllib.parse.quote(ACL_ROOT + '/sdk-upload.md', safe='')}"
        "&to=${commit_oid}",
        None,
    ),
    (
        "snapshot_diff_paths",
        "GET",
        f"/api/v1/snapshot/diff-paths?paths={urllib.parse.quote(ACL_ROOT, safe='')}&to=${{commit_oid}}",
        None,
    ),
    # ACL applies to resource content only, so this runs after the resource add
    ("acl_set", "PUT", "/api/v1/acl", {"uri": ACL_ROOT, "acl_mode": "restricted"}),
    (
        "acl_grant",
        "POST",
        "/api/v1/acl/grant",
        {"uri": ACL_ROOT, "principal": "user:default", "level": "manage"},
    ),
    ("acl_get", "GET", f"/api/v1/acl?uri={urllib.parse.quote(ACL_ROOT, safe='')}", None),
    (
        "acl_revoke",
        "POST",
        "/api/v1/acl/revoke",
        {"uri": ACL_ROOT, "principal": "user:default"},
    ),
    (
        "acl_delete",
        "DELETE",
        f"/api/v1/acl?uri={urllib.parse.quote(ACL_ROOT, safe='')}",
        None,
    ),
    ("watches_list", "GET", "/api/v1/watches", None),
    ("watches_list_page", "GET", "/api/v1/watches?limit=1", None),
    ("watches_get", "GET", "/api/v1/watches/${watch_task_id}", None),
    ("watches_get_uri", "GET", "/api/v1/watches?to_uri=${watch_uri}", None),
    ("watches_patch", "PATCH", "/api/v1/watches/${watch_task_id}", {"is_active": True}),
    ("watches_patch_uri", "PATCH", "/api/v1/watches?to_uri=${watch_uri}", {"reason": "sdk"}),
    ("watches_trigger", "POST", "/api/v1/watches/${watch_task_id}/trigger", None),
    ("watches_trigger_uri", "POST", "/api/v1/watches/trigger?to_uri=${watch_uri}", None),
    ("watches_delete_uri", "DELETE", "/api/v1/watches?to_uri=${watch_uri}", None),
    (
        "resources_add_watch_by_id",
        "POST",
        "/api/v1/resources",
        {
            "path": f"https://example.com/sdk-watch-id-{RUN}.md",
            "to": f"viking://resources/sdk-watch-id-{RUN}",
            "watch_interval": 86400,
        },
    ),
    ("watches_delete", "DELETE", "/api/v1/watches/${watch_task_id}", None),
    (
        "fs_rm",
        "DELETE",
        f"/api/v1/fs?uri={urllib.parse.quote(SANDBOX, safe='')}&recursive=true",
        None,
    ),
]


ADMIN_PROBES: list[tuple[str, str, str, object]] = [
    ("admin_accounts", "GET", "/api/v1/admin/accounts", None),
    (
        "admin_create_account",
        "POST",
        "/api/v1/admin/accounts",
        {"account_id": ADMIN_ACCOUNT, "admin_user_id": ADMIN_MEMBER, "seed": "sdk"},
    ),
    ("admin_get_account", "GET", f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}", None),
    ("admin_accounts_find", "GET", f"/api/v1/admin/accounts?query={ADMIN_ACCOUNT}", None),
    (
        "admin_register_user",
        "POST",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users",
        {"user_id": ADMIN_EXTRA, "role": "user"},
    ),
    ("admin_users", "GET", f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users", None),
    (
        "admin_get_user",
        "GET",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users/{ADMIN_MEMBER}",
        None,
    ),
    (
        "admin_create_group",
        "POST",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/groups",
        {"group_id": ADMIN_GROUP},
    ),
    ("admin_groups", "GET", f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/groups", None),
    (
        "admin_group_members_put",
        "PUT",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/groups/{ADMIN_GROUP}/members",
        {"user_ids": [ADMIN_MEMBER, ADMIN_EXTRA]},
    ),
    (
        "admin_group_members",
        "GET",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/groups/{ADMIN_GROUP}/members",
        None,
    ),
    (
        "admin_memory_templates",
        "GET",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/memory-templates",
        None,
    ),
    (
        "admin_memory_template_get",
        "GET",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/memory-templates/${{memory_type}}",
        None,
    ),
    (
        "admin_memory_template_put",
        "PUT",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/memory-templates/${{memory_type}}",
        {"description": "sdk probe template"},
    ),
    (
        "admin_memory_template_reset",
        "DELETE",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/memory-templates/${{memory_type}}",
        None,
    ),
    ("admin_account_settings", "GET", f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/settings", None),
    (
        "admin_account_settings_patch",
        "PATCH",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/settings",
        {"acl": {"enabled": False}},
    ),
    (
        "admin_account_configuration",
        "GET",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/configuration",
        None,
    ),
    (
        "admin_account_configuration_patch",
        "PATCH",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/configuration",
        {"settings": {}},
    ),
    ("admin_configuration", "GET", "/api/v1/admin/configuration", None),
    ("admin_agent_evolution_get", "GET", "/api/v1/admin/agent-evolution", None),
    ("admin_agent_evolution_put", "PUT", "/api/v1/admin/agent-evolution", {"enabled": False}),
    (
        "admin_user_settings",
        "GET",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users/{ADMIN_MEMBER}/settings",
        None,
    ),
    (
        "admin_user_settings_patch",
        "PATCH",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users/{ADMIN_MEMBER}/settings",
        {"memory_policy": {}},
    ),
    (
        "admin_user_role",
        "PUT",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users/{ADMIN_EXTRA}/role",
        {"role": "admin"},
    ),
    # A root key cannot read tenant data, so the pack probes authenticate as
    # the account admin that create_account returned.
    (
        "pack_mkdir",
        "POST",
        f"/api/v1/fs/mkdir",
        {"uri": ADMIN_SANDBOX},
    ),
    (
        "pack_write",
        "POST",
        "/api/v1/content/write",
        {"uri": f"{ADMIN_SANDBOX}/note.md", "content": "# pack\n\nbody\n"},
    ),
    ("pack_export", "POST", "/api/v1/pack/export", {"uri": ADMIN_SANDBOX}),
    ("pack_backup", "POST", "/api/v1/pack/backup", {}),
    (
        "pack_import_upload",
        "POST",
        "/api/v1/resources/temp_upload",
        lambda state: multipart(
            "file", "import.ovpack", bytes(state.get("export_zip") or b"")
        )
        if state.get("export_zip")
        else None,
    ),
    (
        "pack_import",
        "POST",
        "/api/v1/pack/import",
        {"temp_file_id": "${import_pack_id}", "parent": "viking://resources", "on_conflict": "overwrite"},
    ),
    (
        "pack_restore_upload",
        "POST",
        "/api/v1/resources/temp_upload",
        lambda state: multipart(
            "file", "backup.ovpack", bytes(state.get("backup_zip") or b"")
        )
        if state.get("backup_zip")
        else None,
    ),
    (
        "pack_restore",
        "POST",
        "/api/v1/pack/restore",
        {"temp_file_id": "${restore_pack_id}", "on_conflict": "overwrite"},
    ),
    # create the second fixture the by-id teardown needs, then unwind
    (
        "admin_second_group",
        "POST",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/groups",
        {"group_id": f"{ADMIN_GROUP}-second"},
    ),
    (
        "admin_delete_second_group",
        "DELETE",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/groups/{ADMIN_GROUP}-second",
        None,
    ),
    (
        "admin_user_key",
        "POST",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users/{ADMIN_MEMBER}/key",
        None,
    ),
    (
        "admin_group_members_delete",
        "DELETE",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/groups/{ADMIN_GROUP}/members",
        {"user_ids": [ADMIN_MEMBER, ADMIN_EXTRA]},
    ),
    (
        "admin_delete_group",
        "DELETE",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/groups/{ADMIN_GROUP}",
        None,
    ),
    (
        "admin_delete_extra_user",
        "DELETE",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users/{ADMIN_EXTRA}",
        None,
    ),
    (
        "admin_delete_user",
        "DELETE",
        f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}/users/{ADMIN_MEMBER}",
        None,
    ),
    ("admin_delete_account", "DELETE", f"/api/v1/admin/accounts/{ADMIN_ACCOUNT}", None),
]


# Probes that must run as the account admin rather than root: a root key is
# barred from tenant-scoped data APIs in api_key mode.
USER_KEY_PROBES = {
    "pack_mkdir",
    "pack_write",
    "pack_export",
    "pack_backup",
    "pack_import",
    "pack_restore",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:1944", help="sandbox base url")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--spec", default=None, help="path to a saved openapi.json")
    parser.add_argument("--api-key", default=None, help="root API key for admin probes")
    parser.add_argument(
        "--scope",
        choices=["account", "admin", "all"],
        default="account",
        help="which probe group to run",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("/tmp/ov-mutation-types.json"),
        help="accumulator shared between scope passes",
    )
    args = parser.parse_args(argv)

    global API_KEY
    API_KEY = args.api_key

    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    else:
        reply = call(f"{args.url}/openapi.json", "GET", None, args.timeout)
        spec = reply.json if reply else None
        if not isinstance(spec, dict):
            print(f"sandbox not reachable at {args.url}", file=sys.stderr)
            return 1

    operation_ids = {
        op.get("operationId"): (method.upper(), path)
        for path, item in spec.get("paths", {}).items()
        for method, op in item.items()
        if method in ("get", "post", "put", "patch", "delete")
    }

    def resolve(method: str, probe_path: str) -> str | None:
        """Match a concrete probe path against the templated OpenAPI paths."""
        clean_path = probe_path.split("?")[0]
        found: str | None = None
        for operation_id, (op_method, template) in operation_ids.items():
            if op_method != method:
                continue
            if not re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), clean_path):
                continue
            if found is not None:
                return None
            found = operation_id
        return found
    entries: list[tuple[str, str, Type]] = []
    skipped: list[str] = []
    state: dict[str, object] = {}

    probes = (
        ACCOUNT_PROBES
        if args.scope == "account"
        else ADMIN_PROBES
        if args.scope == "admin"
        else ACCOUNT_PROBES + ADMIN_PROBES
    )

    for name, method, path, payload in probes:
        if callable(payload):
            payload = payload(state)
            if payload is None:
                skipped.append(f"{method} {path} (prerequisite payload unavailable)")
                continue
        missing = sorted(set(needs(path)) | set(needs(payload)))
        unresolved = [slot for slot in missing if slot not in state]
        if unresolved:
            skipped.append(f"{method} {path} (missing prerequisite {', '.join(unresolved)})")
            continue
        path = fill(path, state)  # type: ignore[assignment]
        resolved = fill(payload, state) if payload is not None else None
        key = state.get("user_key") if name in USER_KEY_PROBES else None
        if name in USER_KEY_PROBES and not isinstance(key, str):
            skipped.append(f"{method} {path} (missing prerequisite user_key)")
            continue
        reply = call(f"{args.url}{path}", method, resolved, args.timeout, key)  # type: ignore[arg-type]
        if reply is None:
            skipped.append(f"{method} {path} (unreachable)")
            continue
        if reply.status >= 400:
            skipped.append(f"{method} {path} -> {reply.status}")
            continue
        match = resolve(method, path)
        if match is None:
            skipped.append(f"{method} {path} (no operation in the spec)")
            continue
        inferred = reply.inferred()
        if inferred is None:
            skipped.append(f"{method} {path} -> {reply.status} ({reply.content_type} not typed)")
            continue
        body = unwrap(reply.json) if reply.json is not None else None
        if reply.json is None and reply.content_type.startswith("application/zip"):
            state[PACK_BINARY[name]] = reply.body
        state.update(find_strings(body, HANDOFF))
        if name == "admin_create_account" and isinstance(body, dict):
            state["user_key"] = body.get("user_key")
        if name == "sessions_commit":
            # a Watch or resource add may have left its own task id behind
            state.pop("task_id", None)
            state.pop("tool_result_id", None)
            commit = find_strings(body, {"task_id", "archive_uri"})
            state.update(commit)
            archive_uri = commit.get("archive_uri")
            if isinstance(archive_uri, str):
                state["archive_id"] = archive_uri.rsplit("/", 1)[-1]
        if name == "sessions_tool_results":
            state.update(find_strings(body, {"tool_result_id"}))
        if name == "pack_import_upload":
            state["import_pack_id"] = state.get("temp_file_id")
        if name == "pack_restore_upload":
            state["restore_pack_id"] = state.get("temp_file_id")
        if name == "resources_add_watch":
            state["watch_task_id"] = state.get("watch_id")
            state["watch_uri"] = WATCH_URI
        if name == "watches_list":
            listed = find_strings(body, {"task_id"})
            if "task_id" in listed:
                state["watch_task_id"] = listed["task_id"]
        if name == "snapshot_commit":
            state.pop("task_id", None)
        entries.append((camel(match), match, inferred))

    accumulated: dict[str, str] = {}
    if args.state.is_file():
        try:
            accumulated = json.loads(args.state.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            accumulated = {}

    for key, _operation_id, inferred in entries:
        accumulated.setdefault(key, inferred.render())

    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(accumulated, indent=1) + "\n", encoding="utf-8")

    lines = [
        "// Generated by scripts/probe_mutations.py — do not edit by hand.",
        "// Inferred from sandbox responses of mutating endpoints.",
        "",
    ]
    for key in sorted(accumulated):
        lines.append(f"export type {pascal(key)}Response = {accumulated[key]};")
        lines.append("")

    lines.append("export interface MutationResponseTypes {")
    for key in sorted(accumulated):
        lines.append(f"  {key}: {pascal(key)}Response;")
    lines.append("}")
    lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {OUT} ({len(accumulated)} typed responses; "
        f"{len(entries)} observed this pass, {len(skipped)} skipped)"
    )
    for item in skipped:
        print(f"  skipped {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
