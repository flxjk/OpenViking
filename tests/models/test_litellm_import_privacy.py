"""Provider imports must not phone home before a model request is made."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "openviking.models.embedder.litellm_embedders",
        "openviking.models.vlm.backends.litellm_vlm",
    ],
)
def test_litellm_import_uses_local_catalog_and_disables_reporting(module):
    script = """
import importlib
import os
import httpx
import requests
import urllib.request

requests_made = []
def unexpected_request(*args, **kwargs):
    requests_made.append(True)
    raise AssertionError('Import attempted an HTTP request')

httpx.Client.send = unexpected_request
requests.Session.send = unexpected_request
urllib.request.urlopen = unexpected_request
importlib.import_module(MODULE)
from openviking.models.litellm_runtime import litellm
assert not requests_made, 'Import fetched a remote catalog'
assert os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] == 'True'
assert litellm.telemetry is False
""".replace("MODULE", repr(module))
    env = {key: value for key, value in os.environ.items() if not key.endswith("API_KEY")}
    env.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_rerank_uses_the_configured_runtime(monkeypatch):
    from types import SimpleNamespace

    from openviking.models import litellm_runtime
    from openviking.models.rerank.litellm_rerank import LiteLLMRerankClient

    calls = []

    def rerank(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(results=[{"index": 0, "relevance_score": 0.75}])

    monkeypatch.setattr(litellm_runtime, "litellm", SimpleNamespace(rerank=rerank))
    client = LiteLLMRerankClient("synthetic", "http://synthetic.invalid", "test-model")
    monkeypatch.setattr(client, "_extract_and_update_token_usage", lambda *args, **kwargs: None)
    assert client.rerank_batch("query", ["document"]) == [0.75]
    assert calls[0]["documents"] == ["document"]
