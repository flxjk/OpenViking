#!/usr/bin/env python3
"""Prove that OpenViking ships no outbound telemetry.

Three independent checks:

1. **Imports** — no telemetry SDK may be imported by shipped Python or
   TypeScript sources.
2. **Endpoints** — no collector or analytics host may appear as a URL literal.
3. **Config** — the runtime config must not expose an event-reporting or OTLP
   exporter surface, and the trace protocol must be ``local``.

Functional model providers (OpenRouter, Gemini) are reported separately: they
carry inference traffic, not telemetry.

Usage:
    python scripts/telemetry_audit.py
    python scripts/telemetry_audit.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SOURCE_ROOTS = (
    "openviking",
    "openviking_cli",
    "openviking_gateway",
    "bot",
    "web-studio/src",
)

SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".venv",
    ".git",
    "target",
    "generated",
}

PY_SUFFIXES = {".py"}
TS_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".cjs"}

# Telemetry SDKs that must never be imported.
FORBIDDEN_PY_IMPORTS = (
    "kafka",
    "confluent_kafka",
    "langfuse",
    "nacos",
    "sentry_sdk",
    "posthog",
    "mixpanel",
    "datadog",
    "ddtrace",
    "amplitude",
    "newrelic",
    "honeycomb",
    "jaeger_client",
    "opentelemetry.exporter.otlp",
    "opentelemetry.exporter.otlp_proto_grpc",
    "opentelemetry.exporter.otlp_proto_http",
)

FORBIDDEN_TS_IMPORTS = (
    "@opentelemetry/exporter-",
    "@sentry/",
    "langfuse",
    "posthog-js",
    "posthog-node",
    "mixpanel",
    "dd-trace",
    "@amplitude/",
    "newrelic",
    "@honeycombio/",
)

# Analytics / collector hosts that must never appear as a URL literal.
FORBIDDEN_HOSTS = (
    "sentry.io",
    "api.segment.io",
    "segment.io",
    "posthog.com",
    "mixpanel.com",
    "datadoghq.com",
    "amplitude.com",
    "langfuse.com",
    "honeycomb.io",
    "newrelic.com",
    "jaeger",
    "zipkin",
)

# Functional providers: inference traffic only, reported but never failed.
FUNCTIONAL_HOSTS = (
    "openrouter.ai",
    "generativelanguage.googleapis.com",
    "api.openai.com",
    "api.anthropic.com",
    "127.0.0.1",
    "localhost",
)

PY_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_.]+)", re.MULTILINE)
TS_IMPORT_RE = re.compile(
    r"""(?:from\s+|require\(\s*|import\(\s*)['"]([^'"]+)['"]""", re.MULTILINE
)
URL_RE = re.compile(r"""https?://([A-Za-z0-9._-]+)""")


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SOURCE_ROOTS:
        root = REPO / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix in PY_SUFFIXES | TS_SUFFIXES:
                files.append(path)
    return files


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def check_imports(files: list[Path]) -> list[dict]:
    findings: list[dict] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if path.suffix in PY_SUFFIXES:
            for match in PY_IMPORT_RE.finditer(text):
                module = match.group(1)
                for bad in FORBIDDEN_PY_IMPORTS:
                    if module == bad or module.startswith(bad + "."):
                        line = text[: match.start()].count("\n") + 1
                        findings.append(
                            {
                                "kind": "import",
                                "file": _rel(path),
                                "line": line,
                                "detail": module,
                            }
                        )
        else:
            for match in TS_IMPORT_RE.finditer(text):
                module = match.group(1)
                for bad in FORBIDDEN_TS_IMPORTS:
                    if module == bad or module.startswith(bad):
                        line = text[: match.start()].count("\n") + 1
                        findings.append(
                            {
                                "kind": "import",
                                "file": _rel(path),
                                "line": line,
                                "detail": module,
                            }
                        )
    return findings


def check_endpoints(files: list[Path]) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    functional: list[dict] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in URL_RE.finditer(text):
            host = match.group(1).lower()
            line = text[: match.start()].count("\n") + 1
            if any(host == bad or host.endswith("." + bad) for bad in FORBIDDEN_HOSTS):
                findings.append(
                    {
                        "kind": "endpoint",
                        "file": _rel(path),
                        "line": line,
                        "detail": host,
                    }
                )
            elif any(
                host == good or host.endswith("." + good) for good in FUNCTIONAL_HOSTS
            ):
                functional.append(
                    {
                        "kind": "functional",
                        "file": _rel(path),
                        "line": line,
                        "detail": host,
                    }
                )
    return findings, functional


def check_config() -> list[dict]:
    findings: list[dict] = []
    config_path = REPO / "openviking" / "server" / "config.py"
    if not config_path.is_file():
        return findings
    text = config_path.read_text(encoding="utf-8", errors="ignore")

    for banned in ("EventReportingConfig", "OTelExporterConfig"):
        if banned in text:
            findings.append(
                {
                    "kind": "config",
                    "file": _rel(config_path),
                    "line": 0,
                    "detail": f"{banned} still declared",
                }
            )

    if 'Literal["local"]' not in text:
        findings.append(
            {
                "kind": "config",
                "file": _rel(config_path),
                "line": 0,
                "detail": 'trace protocol is not pinned to Literal["local"]',
            }
        )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    files = _iter_files()
    import_findings = check_imports(files)
    endpoint_findings, functional = check_endpoints(files)
    config_findings = check_config()

    findings = import_findings + endpoint_findings + config_findings
    ok = not findings

    if args.json:
        json.dump(
            {
                "ok": ok,
                "scannedFiles": len(files),
                "findings": findings,
                "functionalHosts": sorted({item["detail"] for item in functional}),
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0 if ok else 1

    print(f"scanned {len(files)} source files")
    print()

    if findings:
        print(f"FAIL — {len(findings)} external telemetry reference(s):")
        for item in findings:
            location = f"{item['file']}:{item['line']}" if item["line"] else item["file"]
            print(f"  [{item['kind']}] {location}  {item['detail']}")
    else:
        print("PASS — no telemetry SDK import, collector endpoint or exporter config")

    hosts = sorted({item["detail"] for item in functional})
    if hosts:
        print()
        print("functional providers (inference traffic, not telemetry):")
        for host in hosts:
            print(f"  {host}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
