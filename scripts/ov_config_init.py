#!/usr/bin/env python3
"""Generate ``ov.conf`` for the local OpenViking stack.

The config keeps every listener on loopback and uses only the two functional
model providers (OpenRouter for embeddings, Gemini for the VLM). No telemetry
endpoint is written: metrics, traces and the usage reporter stay local.

Keys are read from the environment first, then from the shell profile the
Antigravity installer wrote to (``~/.zshrc``, ``~/.bashrc``, ``~/.profile``),
then from the DeepSeek Harness credentials file (``~/.dsh/.credentials.yaml``).

Usage:
    python scripts/ov_config_init.py            # write ov.conf if missing
    FORCE=1 python scripts/ov_config_init.py    # overwrite
    python scripts/ov_config_init.py --print    # print, do not write
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONF = REPO / "ov.conf"

PROFILES = (".zshrc", ".bashrc", ".profile")


def _from_profile(name: str) -> str | None:
    """Return ``export NAME=...`` from the first shell profile that defines it."""
    for profile in PROFILES:
        path = Path.home() / profile
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(f"export {name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _from_dsh_credentials(name: str) -> str | None:
    """Return a top-level ``NAME: value`` from the DSH credentials file."""
    path = Path.home() / ".dsh" / ".credentials.yaml"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith(f"{name}:"):
            continue
        value = stripped.split(":", 1)[1].strip().strip('"').strip("'")
        return value or None
    return None


def _key(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    return _from_profile(name) or _from_dsh_credentials(name)


def build_config() -> dict:
    openrouter = _key("OPENROUTER_API_KEY")
    gemini = _key("GEMINI_API_KEY")

    if not openrouter:
        raise SystemExit(
            "OPENROUTER_API_KEY is not set. Export it or add it to ~/.zshrc."
        )
    if not gemini:
        raise SystemExit(
            "GEMINI_API_KEY is not set. Export it or add it to ~/.zshrc."
        )

    host = os.environ.get("OV_HOST", "127.0.0.1")
    port = int(os.environ.get("OV_PORT", "1933"))
    # The local launcher runs the server from the checkout, even after a move.
    workspace = os.environ.get("OV_WORKSPACE", "./data")
    gateway_key = os.environ.get("OV_GATEWAY_KEY") or secrets.token_urlsafe(32)

    config: dict = {
        "server": {"host": host, "port": port},
        "storage": {"workspace": workspace},
        "embedding": {
            "dense": {
                "provider": "litellm",
                "model": os.environ.get(
                    "OV_EMBEDDING_MODEL", "openrouter/openai/text-embedding-3-small"
                ),
                "api_key": openrouter,
                "api_base": os.environ.get(
                    "OV_EMBEDDING_API_BASE", "https://openrouter.ai/api/v1"
                ),
                "dimension": int(os.environ.get("OV_EMBEDDING_DIM", "1536")),
            }
        },
        "vlm": {
            "provider": "litellm",
            "model": os.environ.get("OV_VLM_MODEL", "gemini/gemini-3.8-flash"),
            "api_key": gemini,
        },
        "gateway": {"enabled": True, "management_key": gateway_key},
    }
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_CONF, help="config path (default: ov.conf)"
    )
    parser.add_argument(
        "--print", action="store_true", help="print the config instead of writing it"
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing config"
    )
    args = parser.parse_args(argv)

    config = build_config()
    rendered = json.dumps(config, indent=2) + "\n"

    if args.print:
        sys.stdout.write(rendered)
        return 0

    if args.out.exists() and not (args.force or os.environ.get("FORCE") == "1"):
        print(f"{args.out} already exists (pass --force or FORCE=1 to overwrite)")
        return 0

    args.out.write_text(rendered, encoding="utf-8")
    args.out.chmod(0o600)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
