#!/usr/bin/env python3
"""Write a throwaway OpenViking config for the SDK sandbox.

Copies the model providers from the main config, points the server at another
port and a separate workspace, and mints a fresh gateway key so the sandbox
never shares state with the live instance.

Usage:
    python scripts/ov_sandbox.py --out /tmp/ov-sandbox.conf --port 1944 \
        --workspace /tmp/ov-sandbox/data
"""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO / "ov.conf"


def build(source: dict, port: int, workspace: str, gateway: bool, root_key: str | None) -> dict:
    config = dict(source)
    server = {"host": "127.0.0.1", "port": port}
    if root_key:
        server["root_api_key"] = root_key
    config["server"] = server
    config["storage"] = {"workspace": workspace}
    if gateway:
        config["gateway"] = {
            "enabled": True,
            "management_key": secrets.token_urlsafe(32),
        }
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=1944)
    parser.add_argument("--workspace", default="/tmp/ov-sandbox/data")
    parser.add_argument("--no-gateway", action="store_true")
    parser.add_argument(
        "--root-key",
        default=None,
        help="set server.root_api_key so admin endpoints are reachable",
    )
    args = parser.parse_args(argv)

    if not args.source.is_file():
        raise SystemExit(f"missing {args.source}; run: just ov-config-init")

    source = json.loads(args.source.read_text(encoding="utf-8"))
    config = build(source, args.port, args.workspace, not args.no_gateway, args.root_key)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    args.out.chmod(0o600)
    print(f"wrote {args.out} (port {args.port}, workspace {args.workspace})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
