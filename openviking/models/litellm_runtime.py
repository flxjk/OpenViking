"""Configure LiteLLM before any provider imports it."""

import os

# LiteLLM otherwise fetches its price catalog as an import side effect.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_TELEMETRY"] = "False"

import litellm  # noqa: E402

# Provider requests are intentional; SDK usage reporting is not.
litellm.telemetry = False

from litellm import acompletion, completion, model_cost  # noqa: E402
from litellm.types.utils import LlmProviders, all_litellm_params  # noqa: E402
from litellm.utils import ProviderConfigManager  # noqa: E402

__all__ = [
    "litellm",
    "acompletion",
    "completion",
    "model_cost",
    "LlmProviders",
    "all_litellm_params",
    "ProviderConfigManager",
]
