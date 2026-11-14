#!/usr/bin/env python3
"""Generate the TypeScript SDK surface from the live OpenViking OpenAPI spec.

Emits two files under ``src/generated``:

* ``schemas.ts``   — one TypeScript type per OpenAPI component schema.
* ``endpoints.ts`` — one typed descriptor per operation, plus the
  ``EndpointTypes`` map that binds each operation to its body, query, path and
  response types.

The spec is the authoritative surface: whatever the server exposes is what the
SDK can call. Regenerate with::

    python scripts/generate.py
    python scripts/generate.py --spec /tmp/ov-openapi.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK = HERE.parent
OUT = SDK / "src" / "generated"

HTTP_METHODS = ("get", "head", "post", "put", "patch", "delete", "options", "propfind", "mkcol", "move")

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
    """Return a safe TypeScript identifier."""
    cleaned = re.sub(r"[^A-Za-z0-9_$]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "_" + cleaned
    if cleaned in RESERVED:
        cleaned = cleaned + "_"
    return cleaned


def camel(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    parts = [p for p in parts if p]
    if not parts:
        return "op"
    head, *tail = parts
    out = head[0].lower() + head[1:] + "".join(p[0].upper() + p[1:] for p in tail)
    return ident(out)


def pascal(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    parts = [p for p in parts if p]
    if not parts:
        return "Schema"
    return ident("".join(p[0].upper() + p[1:] for p in parts))


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def ts_type(schema: object, depth: int = 0) -> str:
    """Convert one OpenAPI schema fragment to a TypeScript type expression."""
    if depth > 24:
        return "unknown"
    if not isinstance(schema, dict):
        return "unknown"

    if "$ref" in schema:
        return pascal(ref_name(str(schema["$ref"])))

    if "const" in schema:
        return json.dumps(schema["const"])

    if "enum" in schema and isinstance(schema["enum"], list):
        values = [json.dumps(v) for v in schema["enum"]]
        return " | ".join(values) if values else "never"

    for key in ("anyOf", "oneOf"):
        if key in schema and isinstance(schema[key], list):
            parts = [ts_type(s, depth + 1) for s in schema[key]]
            uniq: list[str] = []
            for part in parts:
                if part not in uniq:
                    uniq.append(part)
            return " | ".join(uniq) if uniq else "unknown"

    if "allOf" in schema and isinstance(schema["allOf"], list):
        parts = [ts_type(s, depth + 1) for s in schema["allOf"]]
        return " & ".join(parts) if parts else "unknown"

    raw_type = schema.get("type")
    nullable = False
    if isinstance(raw_type, list):
        nullable = "null" in raw_type
        types = [t for t in raw_type if t != "null"]
        raw_type = types[0] if len(types) == 1 else types

    if isinstance(raw_type, list):
        parts = [ts_type({**schema, "type": t}, depth + 1) for t in raw_type]
        base = " | ".join(dict.fromkeys(parts))
    elif raw_type == "string":
        base = "string"
    elif raw_type == "integer" or raw_type == "number":
        base = "number"
    elif raw_type == "boolean":
        base = "boolean"
    elif raw_type == "null":
        base = "null"
    elif raw_type == "array":
        items = schema.get("items")
        inner = ts_type(items, depth + 1) if items else "unknown"
        if " | " in inner and not inner.startswith("("):
            inner = f"({inner})"
        base = f"{inner}[]"
    elif raw_type == "object" or "properties" in schema or "additionalProperties" in schema:
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if props:
            lines = ["{"]
            for name, sub in props.items():
                optional = "" if name in required else "?"
                lines.append(f"  {ident(name)}{optional}: {ts_type(sub, depth + 1)};")
            lines.append("}")
            base = "\n".join(lines)
        else:
            extra = schema.get("additionalProperties")
            if isinstance(extra, dict):
                base = f"Record<string, {ts_type(extra, depth + 1)}>"
            elif extra is True:
                base = "Record<string, unknown>"
            else:
                base = "Record<string, unknown>"
    else:
        base = "unknown"

    if nullable and base != "null":
        return f"{base} | null"
    return base


def load_spec(path: str | None, url: str) -> dict:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def emit_schemas(spec: dict) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    lines = [
        "// Generated by scripts/generate.py — do not edit by hand.",
        "// Source: OpenViking OpenAPI spec.",
        "",
    ]
    for name in sorted(schemas):
        body = ts_type(schemas[name])
        lines.append(f"export type {pascal(name)} = {body};")
        lines.append("")
    return "\n".join(lines)


def operation_types(op: dict) -> tuple[str, str, str, str]:
    """Return (body, query, path, response) TypeScript types for one operation."""
    body = "never"
    request_body = op.get("requestBody") or {}
    content = request_body.get("content") or {}
    for media in ("application/json", "multipart/form-data", "application/x-www-form-urlencoded"):
        if media in content:
            body = ts_type(content[media].get("schema"))
            break

    query_parts: list[str] = []
    path_parts: list[str] = []
    for param in op.get("parameters") or []:
        location = param.get("in")
        name = param.get("name")
        if not name:
            continue
        schema = param.get("schema") or {}
        optional = "" if param.get("required") else "?"
        rendered = f"  {ident(name)}{optional}: {ts_type(schema)};"
        if location == "query":
            query_parts.append(rendered)
        elif location == "path":
            path_parts.append(rendered)

    query = "{\n" + "\n".join(query_parts) + "\n}" if query_parts else "never"
    path = "{\n" + "\n".join(path_parts) + "\n}" if path_parts else "never"

    response = "unknown"
    responses = op.get("responses") or {}
    for code in ("200", "201", "202", "204", "default"):
        entry = responses.get(code)
        if not entry:
            continue
        content = entry.get("content") or {}
        if "application/json" in content:
            response = ts_type(content["application/json"].get("schema"))
            break
        if code == "204":
            response = "void"
            break
    return body, query, path, response


def emit_endpoints(spec: dict) -> str:
    rows: list[dict] = []
    used_keys: set[str] = set()
    for path, item in sorted(spec.get("paths", {}).items()):
        unsupported = [
            method for method, operation in item.items()
            if isinstance(operation, dict) and "responses" in operation
            and method not in HTTP_METHODS
        ]
        if unsupported:
            raise ValueError(f"Unsupported advertised HTTP methods for {path}: {unsupported}")
        for method in HTTP_METHODS:
            op = item.get(method)
            if not op:
                continue
            operation_id = op.get("operationId") or f"{method}_{path}"
            # FastAPI assigns one nondeterministic ID to its combined GET/HEAD route.
            # Keep GET names stable and give HEAD its own deterministic callable alias.
            if method in ("get", "head") and operation_id.startswith("get_or_head_"):
                operation_id = re.sub(r"_(?:get|head)$", f"_{method}", operation_id)
            key = camel(operation_id)
            if key in used_keys:
                operation_id = f"{operation_id}_{method}"
                key = camel(operation_id)
            if key in used_keys:
                raise ValueError(f"Duplicate endpoint key {key}: {method.upper()} {path}")
            used_keys.add(key)
            tags = op.get("tags") or ["default"]
            body, query, path_t, response = operation_types(op)
            raw_body = path.startswith("/webdav/resources") and method == "put"
            if raw_body:
                body = "string | Uint8Array"
            if method == "head" or (
                path.startswith("/webdav/resources")
                and method in ("options", "put", "delete", "mkcol", "move")
            ):
                response = "void"
            elif path.startswith("/webdav/resources") and method == "propfind":
                response = "string"
            elif path.startswith("/webdav/resources") and method == "get":
                response = "string | Uint8Array"
            rows.append(
                {
                    "key": key,
                    "requestEncoding": "raw" if raw_body else "json",
                    "operationId": operation_id,
                    "method": method.upper(),
                    "url": path,
                    "tag": tags[0],
                    "summary": (op.get("summary") or operation_id).replace('"', "'"),
                    "body": body,
                    "query": query,
                    "pathType": path_t,
                    "response": response,
                }
            )

    rows.sort(key=lambda r: (r["tag"], r["url"], r["method"]))

    schema_names = sorted(pascal(name) for name in spec.get("components", {}).get("schemas", {}))
    import_line = (
        "import type { " + ", ".join(schema_names) + ' } from "./schemas.js";'
        if schema_names
        else ""
    )
    if (OUT / "responses.ts").is_file():
        import_line = import_line + "\nimport type { ResponseTypes } from \"./responses.js\";"
    else:
        import_line = import_line + "\ntype ResponseTypes = Record<never, never>;"
    if (OUT / "mutations.ts").is_file():
        import_line = (
            import_line + "\nimport type { MutationResponseTypes } from \"./mutations.js\";"
        )
    else:
        import_line = import_line + "\ntype MutationResponseTypes = Record<never, never>;"

    lines = [
        "// Generated by scripts/generate.py — do not edit by hand.",
        "// Source: OpenViking OpenAPI spec.",
        "",
        import_line,
        "",
        'export type HttpMethod = ' + ' | '.join(json.dumps(method.upper()) for method in HTTP_METHODS) + ';',
        "",
        "export interface EndpointDescriptor {",
        "  readonly operationId: string;",
        "  readonly method: HttpMethod;",
        "  readonly path: string;",
        "  readonly tag: string;",
        "  readonly summary: string;",
        '  readonly requestEncoding?: "json" | "raw";',
        "}",
        "",
        "export const endpoints = {",
    ]
    for row in rows:
        lines.append(
            f'  {row["key"]}: {{ operationId: "{row["operationId"]}", method: "{row["method"]}", '
            f'path: "{row["url"]}", tag: "{row["tag"]}", summary: "{row["summary"]}", requestEncoding: "{row["requestEncoding"]}" }},'
        )
    lines.append("} as const satisfies Record<string, EndpointDescriptor>;")
    lines.append("")
    lines.append("export type EndpointKey = keyof typeof endpoints;")
    lines.append("")
    lines.append("export interface EndpointTypes {")
    for row in rows:
        lines.append(
            f'  {row["key"]}: {{ body: {row["body"]}; query: {row["query"]}; '
            f'path: {row["pathType"]}; response: {row["response"]} }};'
        )
    lines.append("}")
    lines.append("")
    lines.append("export type EndpointBody<K extends EndpointKey> = EndpointTypes[K][\"body\"];")
    lines.append("export type EndpointQuery<K extends EndpointKey> = EndpointTypes[K][\"query\"];")
    lines.append("export type EndpointPath<K extends EndpointKey> = EndpointTypes[K][\"path\"];")
    lines.append("")
    lines.append("export type EndpointResponse<K extends EndpointKey> =")
    lines.append('  (typeof endpoints)[K]["tag"] extends "webdav"')
    lines.append('    ? EndpointTypes[K]["response"]')
    lines.append("    : K extends keyof ResponseTypes")
    lines.append("    ? ResponseTypes[K]")
    lines.append("    : K extends keyof MutationResponseTypes")
    lines.append("      ? MutationResponseTypes[K]")
    lines.append("      : EndpointTypes[K][\"response\"];")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=None, help="path to a saved openapi.json")
    parser.add_argument(
        "--url", default="http://127.0.0.1:1933/openapi.json", help="spec URL"
    )
    args = parser.parse_args(argv)

    spec = load_spec(args.spec, args.url)
    OUT.mkdir(parents=True, exist_ok=True)

    schemas = emit_schemas(spec)
    endpoints = emit_endpoints(spec)

    (OUT / "schemas.ts").write_text(schemas, encoding="utf-8")
    (OUT / "endpoints.ts").write_text(endpoints, encoding="utf-8")

    operations = sum(
        1
        for item in spec.get("paths", {}).values()
        for method in HTTP_METHODS
        if method in item
    )
    print(
        f"wrote {OUT / 'schemas.ts'} "
        f"({len(spec.get('components', {}).get('schemas', {}))} schemas)"
    )
    print(f"wrote {OUT / 'endpoints.ts'} ({operations} operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
