# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Build usage reporter instances from server config."""

from __future__ import annotations

from openviking.server.config import UsageReporterConfig

from .extractors import MemoryUsageExtractor
from .file_log_sink import FileLogUsageSink
from .reporter import UsageReporter


def build_usage_reporter(config: UsageReporterConfig) -> UsageReporter | None:
    if not config.enabled:
        return None

    # Only the built-in local log sink may receive usage events.
    sinks = [
        FileLogUsageSink(**dict(sink.config)) for sink in config.sinks if sink.type == "file_log"
    ]
    if not sinks:
        return None

    extractors = []
    for name in config.extractors:
        if name == "memory_usage":
            extractors.append(MemoryUsageExtractor())
        else:
            raise ValueError(f"Unsupported usage extractor: {name}")

    return UsageReporter(
        extractors=extractors,
        sinks=sinks,
    )
