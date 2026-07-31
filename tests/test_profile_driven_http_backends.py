"""Behavioral contracts for generic profile-driven HTTP instruments."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from marianne.core.config.instruments import HttpProfile, InstrumentProfile
from marianne.daemon.baton.backend_pool import _create_backend_for_profile
from marianne.execution.instruments.openai_compat_backend import OpenAICompatibleBackend
from marianne.instruments.loader import load_all_profiles


def _ollama_profile() -> InstrumentProfile:
    return InstrumentProfile(
        name="ollama",
        display_name="Ollama",
        kind="http",
        default_model="llama3.1:8b",
        http=HttpProfile(
            base_url="http://localhost:11434/v1",
            endpoint="/chat/completions",
            schema_family="openai",
            auth_env_var=None,
        ),
    )


@pytest.mark.asyncio
async def test_generic_openai_http_profile_allows_unauthenticated_local_execution() -> None:
    """A local OpenAI-compatible profile sends no auth header when none is configured."""
    backend = _create_backend_for_profile(_ollama_profile(), model="override-model")
    assert isinstance(backend, OpenAICompatibleBackend)
    assert backend.model == "override-model"
    assert backend.endpoint == "/chat/completions"
    assert backend._httpx_headers == {"Content-Type": "application/json"}

    response = httpx.Response(
        200,
        json={
            "model": "override-model",
            "choices": [{"message": {"content": "local answer"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 4},
        },
        request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions"),
    )
    client = AsyncMock()
    client.post.return_value = response
    backend._get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    result = await backend.execute("answer locally")

    assert result.success is True
    assert result.stdout == "local answer"
    assert (result.input_tokens, result.output_tokens) == (2, 4)
    assert client.post.call_args.args == ("/chat/completions",)
    assert client.post.call_args.kwargs["json"]["model"] == "override-model"


def test_ollama_is_the_discovered_generic_openai_profile() -> None:
    """The maintained local profile uses the generic OpenAI chat contract."""
    profiles = load_all_profiles()

    assert "ollama" in profiles
    assert profiles["ollama"].http is not None
    assert profiles["ollama"].http.base_url == "http://localhost:11434/v1"
    assert profiles["ollama"].http.endpoint == "/chat/completions"
    assert profiles["ollama"].http.auth_env_var is None


def test_http_profile_without_a_selected_model_is_rejected() -> None:
    """Generic transport never smuggles in a provider-specific default model."""
    profile = InstrumentProfile(
        name="model-less-http",
        display_name="Model-less HTTP",
        kind="http",
        http=HttpProfile(
            base_url="http://localhost:9000/v1",
            endpoint="/chat/completions",
            schema_family="openai",
        ),
    )

    with pytest.raises(ValueError, match="requires a model"):
        _create_backend_for_profile(profile)
