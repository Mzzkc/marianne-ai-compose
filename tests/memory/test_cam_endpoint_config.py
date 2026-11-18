"""Operator-side CAM endpoints: ``memory_backends.cam.<name>`` in the conductor config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marianne.daemon.config import CamEndpointConfig, DaemonConfig


def _cfg(**endpoint: object) -> dict[str, object]:
    return {"memory_backends": {"cam": {"lab": endpoint}}}


def test_default_is_no_endpoints() -> None:
    assert DaemonConfig().memory_backends.cam == {}


def test_loopback_endpoint_with_a_key_name_is_accepted() -> None:
    c = DaemonConfig.model_validate(
        _cfg(base_url="http://127.0.0.1:8321", api_key_env="CAM_API_KEY")
    )
    ep = c.memory_backends.cam["lab"]
    assert ep.as_settings()["api_key_env"] == "CAM_API_KEY"
    assert "api_key_file" not in ep.as_settings()


def test_key_file_is_carried_as_a_path() -> None:
    ep = CamEndpointConfig(base_url="http://localhost:1", api_key_file="/etc/cam/key")
    assert ep.as_settings()["api_key_file"] == "/etc/cam/key"


def test_remote_without_allow_remote_is_refused() -> None:
    with pytest.raises(ValidationError, match="not loopback"):
        DaemonConfig.model_validate(_cfg(base_url="https://cam.example.org", api_key_env="K"))


def test_remote_needs_https_even_with_allow_remote() -> None:
    with pytest.raises(ValidationError, match="https"):
        DaemonConfig.model_validate(
            _cfg(base_url="http://cam.example.org", api_key_env="K", allow_remote=True)
        )
    DaemonConfig.model_validate(
        _cfg(base_url="https://cam.example.org", api_key_env="K", allow_remote=True)
    )


def test_exactly_one_key_source() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        DaemonConfig.model_validate(_cfg(base_url="http://127.0.0.1:1"))
    with pytest.raises(ValidationError, match="exactly one"):
        DaemonConfig.model_validate(
            _cfg(base_url="http://127.0.0.1:1", api_key_env="K", api_key_file="/x")
        )


def test_unknown_field_and_bad_endpoint_name_are_refused() -> None:
    with pytest.raises(ValidationError):
        DaemonConfig.model_validate(
            _cfg(base_url="http://127.0.0.1:1", api_key_env="K", api_key="literal")
        )
    with pytest.raises(ValidationError, match="must match"):
        DaemonConfig.model_validate(
            {"memory_backends": {"cam": {"Bad Name": {
                "base_url": "http://127.0.0.1:1", "api_key_env": "K"}}}}
        )
