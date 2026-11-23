"""Legacy reporting settings load without activating an external sender."""

import json

import pytest

from openviking.config.config_file import preview_config_file
from openviking.observability.reporting import runtime
from openviking.server.config import load_server_config
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture
def reporting_ov_conf(tmp_path, monkeypatch):
    monkeypatch.setenv("NACOS_CONFIG_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("NACOS_SERVER_ADDR", "http://127.0.0.1:1")
    monkeypatch.setenv("OV_REPORTING_TEST_FRAGMENT", 'replacement-with-"quotes"')
    monkeypatch.setenv("OV_REPORTING_TEST_PORT", "1944")
    monkeypatch.setenv("OV_REPORTING_TEST_MODEL_KEY", "business-key")
    reporting = {
        "enabled": True,
        "kafka": {
            "bootstrap_servers": "broker:9092",
            "topic": "metric-log",
            "security_protocol": "SASL_SSL",
            "username": "kafka-user",
            "password": "literal-${OV_REPORTING_TEST_FRAGMENT}",
        },
        "telemetry": {
            "enabled": True,
            "queue_capacity": 32,
            "deployment": {"account_id": "cloud", "resource_id": "library", "region": "test"},
        },
    }
    content = json.dumps(
        {
            "server": {
                "port": "UNQUOTED_PORT",
                "observability": {"reporting": reporting},
            },
            "storage": {"workspace": str(tmp_path / "data")},
            "vlm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "${OV_REPORTING_TEST_MODEL_KEY}",
            },
        }
    ).replace('"UNQUOTED_PORT"', "${OV_REPORTING_TEST_PORT}")
    path = tmp_path / "ov.conf"
    path.write_text(content, encoding="utf-8")
    return path, content, reporting


@pytest.mark.asyncio
async def test_reporting_stays_disabled_with_legacy_config(reporting_ov_conf):
    path, _, reporting = reporting_ov_conf
    config = load_server_config(str(path))
    assert config.port == 1944
    assert "reporting" not in config.observability.model_dump()
    assert await runtime.start_reporting(reporting) is None
    assert not runtime.is_reporting_enabled()
    await runtime.stop_reporting()


def test_config_readers_preserve_literal_credentials(reporting_ov_conf, monkeypatch):
    path, content, _ = reporting_ov_conf
    for field in ("_instance", "_config_file", "_config_file_revision", "_config_file_content"):
        monkeypatch.setattr(
            OpenVikingConfigSingleton, field, getattr(OpenVikingConfigSingleton, field)
        )
    config = OpenVikingConfigSingleton.initialize(config_path=str(path))
    assert config.vlm.api_key == "business-key"
    assert preview_config_file(content)["content"] == content
