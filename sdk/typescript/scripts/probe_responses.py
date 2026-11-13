#!/usr/bin/env python3
"""Infer response types for read-only endpoints from live server responses.

The OpenAPI spec declares request schemas but no response schemas, so the SDK
would otherwise type every response as ``unknown``. This script calls each
read-only GET endpoint, infers a TypeScript type from the JSON it returns, and
emits ``src/generated/responses.ts``.

Only endpoints that are safe to call are probed: GET with no path parameters.
A GET that needs a query parameter is probed only when a value can be supplied
without guessing — either a fixed literal or a URI discovered from an earlier
listing call. Anything that fails, or whose prerequisite cannot be discovered,
is skipped and stays ``unknown``.

Usage:
    python scripts/probe_responses.py
    python scripts/probe_responses.py --url http://127.0.0.1:1933
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK = HERE.parent
OUT = SDK / "src" / "generated" / "responses.ts"

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
    """A minimal structural type used for inference and merging."""

    def __init__(self, kind: str, **kwargs: object) -> None:
        self.kind = kind
        self.props: dict[str, Type] = kwargs.get("props", {})  # type: ignore[assignment]
        self.required: set[str] = kwargs.get("required", set())  # type: ignore[assignment]
        self.items: Type | None = kwargs.get("items")  # type: ignore[assignment]
        self.variants: list[Type] = kwargs.get("variants", [])  # type: ignore[assignment]

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        if self.kind == "string":
            return "string"
        if self.kind == "number":
            return "number"
        if self.kind == "boolean":
            return "boolean"
        if self.kind == "null":
            return "null"
        if self.kind == "unknown":
            return "unknown"
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
            parts = [v.render(indent) for v in self.variants]
            uniq: list[str] = []
            for part in parts:
                if part not in uniq:
                    uniq.append(part)
            return " | ".join(uniq) if uniq else "unknown"
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
            return Type("array", items=merge(left.items or Type("unknown"), right.items or Type("unknown")))
        return left
    if left.kind == "null":
        return Type("union", variants=[right, Type("null")])
    if right.kind == "null":
        return Type("union", variants=[left, Type("null")])
    return Type("union", variants=[left, right])


MEMORY_ROOT = "viking://user/default/memories"
DISCOVERY_LS = "/api/v1/fs/ls"

# A query value is either a literal, a date placeholder, or a ``discover`` slot
# filled from an earlier read-only response. A probe whose slot is missing is
# skipped rather than called with a guess.
DATE_PLACEHOLDER = "$"

QUERY: dict[str, dict[str, object]] = {
    "/api/v1/fs/ls": {"uri": ("directory_uri",)},
    "/api/v1/fs/stat": {"uri": ("file_uri", "directory_uri")},
    "/api/v1/fs/tree": {
        "uri": ("directory_uri",),
        "level_limit": 1,
        "output": "original",
    },
    "/api/v1/fs/attrs": {"uri": ("directory_uri",)},
    "/api/v1/content/read": {"uri": ("file_uri",)},
    "/api/v1/content/abstract": {"uri": ("directory_uri",)},
    "/api/v1/content/overview": {"uri": ("directory_uri",)},
    "/api/v1/content/download": {"uri": ("file_uri",)},
    "/api/v1/console/tokens": {"start_date": "$week_ago", "end_date": "$today"},
    "/api/v1/console/context-commits": {"start_date": "$week_ago", "end_date": "$today"},
    "/api/v1/stats/memories": {"category": ("memory_category",)},
}

READ_ONLY_POSTS: dict[str, dict] = {
    "/api/v1/search/find": {"query": "local AI stack", "limit": 2},
    "/api/v1/search/search": {"query": "local AI stack", "limit": 2},
    "/api/v1/search/recall": {"query": "local AI stack", "max_chars": 500},
    "/api/v1/search/grep": {
        "uri": "viking://user/default/memories",
        "pattern": "OpenViking",
    },
    "/api/v1/search/glob": {
        "pattern": "**/*.md",
        "uri": "viking://user/default/memories",
    },
    "/api/v1/skills/find": {"query": "local"},
    "/api/v1/mcps/find": {"query": "search"},
    "/api/v1/rules/search": {"task_context": "telemetry"},
    "/api/v1/privacy-configs/scan": {"uri": "viking://user/default/memories"},
    "/api/v1/system/consistency": {"uri": "viking://user/default/memories"},
    "/api/v1/system/wait": {},
    "/api/v1/privacy-configs/scan_categories": {},
}


class Reply:
    def __init__(self, status: int, body: bytes, content_type: str) -> None:
        self.status = status
        self.body = body
        self.content_type = content_type

    def json(self) -> object | None:
        try:
            return json.loads(self.body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None

    def text(self) -> str | None:
        if not self.content_type.startswith("text/") and "json" not in self.content_type:
            if "octet-stream" not in self.content_type:
                return None
        try:
            decoded = self.body.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if any(ord(ch) < 9 for ch in decoded):
            return None
        return decoded


def request(
    url: str,
    timeout: float,
    payload: object | None = None,
    method: str = "GET",
) -> Reply | None:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return Reply(
                response.status,
                response.read(),
                response.headers.get("content-type", ""),
            )
    except urllib.error.HTTPError as error:
        try:
            return Reply(error.code, error.read(), error.headers.get("content-type", "") or "")
        except OSError:
            return Reply(error.code, b"", "")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def unwrap(payload: object) -> object:
    if isinstance(payload, dict) and "result" in payload and "status" in payload:
        return payload["result"]
    return payload


def fetch_json(url: str, timeout: float) -> object | None:
    reply = request(url, timeout)
    return None if reply is None else reply.json()


def date_placeholder(name: str) -> str:
    today = datetime.now(timezone.utc).date()
    if name == "$today":
        return today.isoformat()
    return (today - timedelta(days=7)).isoformat()


def build_query(spec: dict[str, object], available: dict[str, str]) -> dict[str, object] | None:
    query: dict[str, object] = {}
    for name, value in spec.items():
        if isinstance(value, str):
            query[name] = date_placeholder(value) if value.startswith(DATE_PLACEHOLDER) else value
            continue
        if not isinstance(value, tuple):
            query[name] = value
            continue
        for slot in value:
            if slot in available:
                query[name] = available[slot]
                break
        else:
            return None
    return query


def memory_category(url: str) -> str | None:
    payload = unwrap(fetch_json(f"{url}/api/v1/stats/memories", 10.0))
    if not isinstance(payload, dict):
        return None
    categories = payload.get("by_category")
    if not isinstance(categories, dict):
        return None
    return next(iter(categories), None)


def discover_uris(url: str, available: dict[str, str]) -> None:
    """Fill the URI slots from a real listing so no probe has to guess one."""
    listed = unwrap(fetch_json(url, 10.0))
    entries = listed if isinstance(listed, list) else []
    if not entries:
        print("no URI listing available; fs/content GET probes will be skipped")
        return
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("uri"), str):
            continue
        if entry.get("isDir"):
            available.setdefault("directory_uri", entry["uri"])
        else:
            available.setdefault("file_uri", entry["uri"])
    directory = available.get("directory_uri")
    if directory is not None:
        children = unwrap(
            fetch_json(f"{url}?uri={urllib.parse.quote(directory, safe='')}", 10.0)
        )
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict) and isinstance(child.get("uri"), str):
                    available.setdefault("file_uri", child["uri"])
                    break


def discover(server: str, available: dict[str, str]) -> None:
    quoted = urllib.parse.quote(MEMORY_ROOT, safe="")
    discover_uris(f"{server}{DISCOVERY_LS}?uri={quoted}", available)
    if "directory_uri" not in available:
        available["directory_uri"] = MEMORY_ROOT
    found = memory_category(server)
    if found:
        available["memory_category"] = found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:1933", help="server base url")
    parser.add_argument("--spec", default=None, help="path to a saved openapi.json")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    else:
        spec = fetch_json(f"{args.url}/openapi.json", args.timeout)
        if not isinstance(spec, dict):
            print("could not read the OpenAPI spec", file=sys.stderr)
            return 1

    entries: list[tuple[str, str, Type]] = []
    skipped: list[str] = []

    available: dict[str, str] = {}
    discover(args.url, available)

    for path, item in spec.get("paths", {}).items():
        op = item.get("get")
        if not op or "{" in path:
            continue
        query_spec = QUERY.get(path, {})
        query = build_query(query_spec, available)
        if query is None:
            missing = sorted(
                {
                    slot
                    for value in query_spec.values()
                    if not isinstance(value, str)
                    for slot in value
                }
            )
            skipped.append(f"{path} (no value discovered for {', '.join(missing)})")
            continue
        suffix = "?" + urllib.parse.urlencode(query, doseq=True) if query else ""
        reply = request(f"{args.url}{path}{suffix}", args.timeout)
        if reply is None or reply.status >= 400:
            status = "unreachable" if reply is None else str(reply.status)
            skipped.append(f"{path} ({status})")
            continue
        payload = reply.json()
        if payload is None:
            text = reply.text()
            if not text:
                skipped.append(f"{path} (non-text response {reply.content_type})")
                continue
            payload = text
        else:
            payload = unwrap(payload)
        operation_id = op.get("operationId") or path
        entries.append((camel(operation_id), operation_id, infer(payload)))

    for path, payload in READ_ONLY_POSTS.items():
        item = spec.get("paths", {}).get(path) or {}
        op = item.get("post")
        if not op:
            continue
        operation_id = op.get("operationId") or path
        reply = request(f"{args.url}{path}", args.timeout, payload, "POST")
        response = None if reply is None else reply.json()
        if reply is None or reply.status >= 400 or response is None:
            skipped.append(f"{path} ({'unreachable' if reply is None else reply.status})")
            continue
        entries.append((camel(operation_id), operation_id, infer(unwrap(response))))

    lines = [
        "// Generated by scripts/probe_responses.py — do not edit by hand.",
        "// Inferred from live responses of read-only endpoints.",
        "",
    ]
    for key, operation_id, inferred in entries:
        lines.append(f"export type {pascal(key)}Response = {inferred.render()};")
        lines.append("")

    lines.append("export interface ResponseTypes {")
    for key, _operation_id, _inferred in entries:
        lines.append(f"  {key}: {pascal(key)}Response;")
    lines.append("}")
    lines.append("")

    if not entries and OUT.is_file():
        print(f"no endpoint answered; kept the existing {OUT}")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {OUT} ({len(entries)} inferred responses, {len(skipped)} skipped)")
    for item in skipped:
        print(f"  skipped {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
