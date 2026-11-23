# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from contextlib import asynccontextmanager

import pytest

from openviking.server.app import create_app
from openviking.server.config import ServerConfig, UsageReporterConfig
from openviking.usage_reporter import FileLogUsageSink
from openviking.usage_reporter.config import build_usage_reporter


def test_custom_usage_sink_is_never_loaded():
    reporter = build_usage_reporter(
        UsageReporterConfig(
            enabled=True,
            extractors=["memory_usage"],
            sinks=[{"type": "custom", "class_path": "unimportable_external_sink.Sink"}],
        )
    )
    assert reporter is None


def test_builtin_file_log_sink_is_built_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
    log_path = tmp_path / "usage" / "usage.log"

    reporter = build_usage_reporter(
        UsageReporterConfig(
            enabled=True,
            extractors=["memory_usage"],
            sinks=[
                {
                    "type": "file_log",
                    "config": {
                        "path": str(log_path),
                        "rotation_interval_hours": 1,
                    },
                }
            ],
        )
    )

    assert reporter is not None
    assert len(reporter.sinks) == 1
    assert isinstance(reporter.sinks[0], FileLogUsageSink)
    assert reporter.sinks[0].path == log_path
    reporter.sinks[0].close()


async def test_app_reuses_and_closes_usage_reporter(monkeypatch):
    built_reporters = []
    assigned_reporters = []
    assigned_agent_evolution_configs = []

    class Reporter:
        closed = False

        async def close(self):
            self.closed = True

    reporter = Reporter()

    def build_reporter(config):
        del config
        built_reporters.append(reporter)
        return reporter

    class Sessions:
        def set_tool_output_externalization_config(self, config):
            del config

        def set_usage_reporter(self, value):
            assigned_reporters.append(value)

        def set_agent_evolution_config(self, config):
            assigned_agent_evolution_configs.append(config)

    class Service:
        sessions = Sessions()

    class TaskTracker:
        def start_cleanup_loop(self):
            return None

        def stop_cleanup_loop(self):
            return None

    async def initialize_runtime_state(app, service, config):
        del app, service, config

    @asynccontextmanager
    async def mcp_lifespan():
        yield

    monkeypatch.setattr(
        "openviking.usage_reporter.config.build_usage_reporter",
        build_reporter,
    )
    monkeypatch.setattr(
        "openviking.server.app._initialize_runtime_state",
        initialize_runtime_state,
    )
    monkeypatch.setattr("openviking.server.app.get_task_tracker", lambda: TaskTracker())
    monkeypatch.setattr("openviking.server.mcp_endpoint.mcp_lifespan", mcp_lifespan)

    config = ServerConfig.model_validate({"agent_evolution": {"enabled": True}})
    app = create_app(config=config, service=Service())
    async with app.router.lifespan_context(app):
        pass

    assert built_reporters == [reporter]
    assert assigned_reporters == [reporter, reporter]
    assert [config.enabled for config in assigned_agent_evolution_configs] == [True, True]
    assert reporter.closed is True
