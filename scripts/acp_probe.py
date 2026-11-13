#!/usr/bin/env python3
"""Probe an ACP client with a JSON-RPC initialize handshake.

ACP clients (reasonix, agy) are stdio servers spawned on demand, so process
presence is the wrong health signal. This probe writes one `initialize`
request, closes stdin, and checks that the agent answers with a result.

Usage:
    python scripts/acp_probe.py /Users/gj/.local/bin/agy acp --disable-telemetry
    python scripts/acp_probe.py /Users/gj/.local/bin/reasonix acp
"""

from __future__ import annotations

import subprocess
import sys

INITIALIZE = (
    '{"jsonrpc":"2.0","id":1,"method":"initialize",'
    '"params":{"protocolVersion":1,"clientCapabilities":{}}}\n'
)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        completed = subprocess.run(
            sys.argv[1:],
            input=INITIALIZE,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return 0 if '"result"' in completed.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
