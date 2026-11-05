"""A first score on a local OpenAI-compatible server must not fail on the
transport's defaults or on where the conductor was started.

Measured 2026-11-05 against a PolyStrata-shaped fake (one loaded model,
8k context, 400 on max_tokens above it, no auth): the walk-through a new
user takes from docs/instrument-guide.md failed four ways before any model
ran. Three are product defects covered here; the fourth (classes write
seats a hinted HTTP profile only in classes whose policy its capabilities
satisfy) is documented, not changed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from marianne.core.config import JobConfig
from marianne.core.config.instruments import HttpProfile, InstrumentProfile, ModelCapacity
from marianne.daemon.baton.backend_pool import _create_backend_for_profile
from marianne.execution.instruments.openai_compat_backend import OpenAICompatibleBackend
from marianne.instruments.availability import ProfileProbe
from marianne.validation.checks.config import NoUsableInstrumentCheck

PROFILE_YAML = """\
name: localbox
display_name: "Local box"
kind: http
default_model: small-model
models:
  - name: small-model
    context_window: 8192
    cost_per_1k_input: 0.0
    cost_per_1k_output: 0.0
http:
  base_url: http://127.0.0.1:18080/v1
  endpoint: /chat/completions
  schema_family: openai
"""


def _local_profile(
    *, context_window: int = 8192, max_output_tokens: int | None = None
) -> InstrumentProfile:
    return InstrumentProfile(
        name="localbox",
        display_name="Local box",
        kind="http",
        default_model="small-model",
        models=[
            ModelCapacity(
                name="small-model",
                context_window=context_window,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
                max_output_tokens=max_output_tokens,
            )
        ],
        http=HttpProfile(
            base_url="http://127.0.0.1:18080/v1",
            endpoint="/chat/completions",
            schema_family="openai",
            auth_env_var=None,
        ),
    )


async def _sent_payload(backend: OpenAICompatibleBackend) -> dict[str, object]:
    response = httpx.Response(
        200,
        json={
            "model": "small-model",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        request=httpx.Request("POST", "http://127.0.0.1:18080/v1/chat/completions"),
    )
    client = AsyncMock()
    client.post.return_value = response
    backend._get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]
    result = await backend.execute("hello")
    assert result.success, result.error_message
    payload: dict[str, object] = client.post.call_args.kwargs["json"]
    return payload


class TestMaxTokensFollowsTheModelsCapacity:
    @pytest.mark.asyncio
    async def test_default_request_is_capped_to_the_context_window(self) -> None:
        backend = _create_backend_for_profile(_local_profile(), model=None)
        assert isinstance(backend, OpenAICompatibleBackend)
        assert backend.max_tokens == 16384  # the transport default, unchanged
        assert (await _sent_payload(backend))["max_tokens"] == 8192

    @pytest.mark.asyncio
    async def test_max_output_tokens_is_the_tighter_bound(self) -> None:
        backend = _create_backend_for_profile(
            _local_profile(context_window=32768, max_output_tokens=4096), model=None
        )
        assert isinstance(backend, OpenAICompatibleBackend)
        assert (await _sent_payload(backend))["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_a_request_within_capacity_is_sent_as_is(self) -> None:
        backend = _create_backend_for_profile(
            _local_profile(context_window=131072), model=None
        )
        assert isinstance(backend, OpenAICompatibleBackend)
        assert (await _sent_payload(backend))["max_tokens"] == 16384

    @pytest.mark.asyncio
    async def test_an_undeclared_model_keeps_the_request(self) -> None:
        backend = _create_backend_for_profile(_local_profile(), model="other-model")
        assert isinstance(backend, OpenAICompatibleBackend)
        assert (await _sent_payload(backend))["max_tokens"] == 16384


class TestV215CountsAReadyLocalServer:
    def _config(self, instrument: str) -> JobConfig:
        return JobConfig.model_validate(
            {
                "name": "first",
                "workspace": "./ws",
                "instrument": instrument,
                "sheet": {"size": 1, "total_items": 1},
                "prompt": {"template": "x"},
            }
        )

    def _run(self, probe: ProfileProbe) -> list:
        profiles = {"localbox": _local_profile()}
        with (
            patch("marianne.instruments.loader.load_all_profiles", return_value=profiles),
            patch("marianne.instruments.availability.probe_profile", return_value=probe),
        ):
            return NoUsableInstrumentCheck().check(
                self._config("localbox"), Path("s.yaml"), "instrument: localbox"
            )

    def test_a_ready_local_http_profile_is_runnable(self) -> None:
        assert self._run(ProfileProbe("ready")) == []

    def test_an_unreachable_local_server_still_warns(self) -> None:
        issues = self._run(ProfileProbe("unreachable", "no server answered"))
        assert [i.check_id for i in issues] == ["V215"]
        assert "HTTP" in issues[0].message


class TestSubmitRefusesAVenueProfileTheConductorNeverLoaded:
    @pytest.mark.asyncio
    async def test_rejection_names_both_directories(self, tmp_path: Path) -> None:
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.manager import JobManager
        from marianne.daemon.types import JobRequest
        from marianne.instruments.registry import InstrumentRegistry

        project = tmp_path / "project"
        (project / ".marianne" / "instruments").mkdir(parents=True)
        (project / ".marianne" / "instruments" / "localbox.yaml").write_text(PROFILE_YAML)
        score = project / "first.yaml"
        score.write_text(
            "name: first\nworkspace: ./ws\ninstrument: localbox\n"
            "sheet:\n  size: 1\n  total_items: 1\nprompt:\n  template: x\n"
        )
        daemon_home = tmp_path / "daemon-home"
        (daemon_home / ".marianne" / "instruments").mkdir(parents=True)

        config = DaemonConfig(
            max_concurrent_jobs=2,
            pid_file=tmp_path / "test.pid",
            state_db_path=tmp_path / "registry.db",
        )
        manager = JobManager(
            config,
            profile_dirs=(
                daemon_home / ".marianne" / "instruments",
                daemon_home / ".marianne" / "instruments",
            ),
        )
        await manager._registry.open()
        try:
            manager._service = MagicMock()
            manager._instrument_registry = InstrumentRegistry()  # the daemon loaded nothing

            response = await manager.submit_job(
                JobRequest(config_path=score, client_cwd=project)
            )
            assert response.status == "rejected"
            assert "'localbox'" in response.message
            assert str(project / ".marianne" / "instruments") in response.message
            assert str((daemon_home / ".marianne" / "instruments").resolve()) in response.message
            assert "mzt conductor reload" in response.message

            # The same score is admitted once the conductor holds the profile.
            manager._instrument_registry.register(_local_profile())
            response = await manager.submit_job(
                JobRequest(config_path=score, client_cwd=project)
            )
            assert response.status != "rejected", response.message
        finally:
            await manager._registry.close()
