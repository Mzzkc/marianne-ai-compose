"""Tests for credential environment filtering in PluginCliBackend.

Addresses F-025: PluginCliBackend passes full parent environment to
instrument subprocesses. This test suite verifies that when an instrument
profile declares `required_env`, only those variables (plus system
essentials) are passed to the subprocess — preventing credential leakage
to untrusted instruments.

The safety design spec explicitly requires: "The PluginCliBackend passes
only explicitly declared env vars to subprocesses."

TDD: Tests written first, defining the contract for env filtering.
"""

import os
from unittest.mock import patch

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.execution.instruments.cli_backend import (
    SYSTEM_ENV_VARS,
    PluginCliBackend,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_profile(
    *,
    env: dict[str, str] | None = None,
    required_env: list[str] | None = None,
    inherit_env: bool = False,
) -> InstrumentProfile:
    """Create a minimal InstrumentProfile for env filtering tests."""
    return InstrumentProfile(
        name="test-instrument",
        display_name="Test Instrument",
        kind="cli",
        models=[
            ModelCapacity(
                name="test-model",
                context_window=128000,
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.03,
            ),
        ],
        default_model="test-model",
        cli=CliProfile(
            command=CliCommand(
                executable="echo",
                prompt_flag="-p",
                env=env or {},
                required_env=required_env,
                inherit_env=inherit_env,
            ),
            output=CliOutputConfig(format="text"),
            errors=CliErrorConfig(),
        ),
    )


# ---------------------------------------------------------------------------
# Test: SYSTEM_ENV_VARS constant
# ---------------------------------------------------------------------------


class TestSystemEnvVars:
    """Verify the system env vars constant is well-defined."""

    def test_path_in_system_vars(self) -> None:
        """PATH must be in system env vars — instruments need it to find binaries."""
        assert "PATH" in SYSTEM_ENV_VARS

    def test_home_in_system_vars(self) -> None:
        """HOME must be in system vars — many CLI tools need it."""
        assert "HOME" in SYSTEM_ENV_VARS

    def test_term_in_system_vars(self) -> None:
        """TERM should be in system vars — prevents terminal rendering issues."""
        assert "TERM" in SYSTEM_ENV_VARS

    def test_is_frozenset(self) -> None:
        """Must be immutable to prevent accidental mutation."""
        assert isinstance(SYSTEM_ENV_VARS, frozenset)


# ---------------------------------------------------------------------------
# Test: Fail-closed default and explicit migration opt-out
# ---------------------------------------------------------------------------


class TestDefaultFiltering:
    def test_required_env_none_now_filters_to_system_vars(self) -> None:
        with patch.dict(os.environ, {"PATH": "/usr/bin", "FOREIGN_SECRET": "probe"}, clear=True):
            assert PluginCliBackend(_make_profile())._build_env() == {"PATH": "/usr/bin"}

    def test_profile_env_now_filters_to_system_vars(self) -> None:
        with patch.dict(os.environ, {"PATH": "/usr/bin", "FOREIGN_SECRET": "probe"}, clear=True):
            env = PluginCliBackend(_make_profile(env={"CUSTOM": "value"}))._build_env()
        assert env == {"PATH": "/usr/bin", "CUSTOM": "value"}

    def test_inherit_env_opt_out_restores_full_inherit(self) -> None:
        with patch.dict(os.environ, {"FOREIGN_SECRET": "probe"}, clear=True):
            assert PluginCliBackend(_make_profile(inherit_env=True))._build_env() is None
            profile = _make_profile(inherit_env=True, env={"CUSTOM": "v"})
            env = PluginCliBackend(profile)._build_env()
        assert env == {"FOREIGN_SECRET": "probe", "CUSTOM": "v"}

    def test_inherit_env_does_not_override_explicit_allowlist(self) -> None:
        with patch.dict(os.environ, {"FOREIGN_SECRET": "probe", "PATH": "/bin"}, clear=True):
            env = PluginCliBackend(_make_profile(inherit_env=True, required_env=[]))._build_env()
        assert env == {"PATH": "/bin"}

    def test_inherit_env_schema_accepts_and_rejects(self) -> None:
        from pydantic import ValidationError

        assert CliCommand(executable="echo").inherit_env is False
        assert CliCommand(executable="echo", inherit_env=True).inherit_env is True
        with pytest.raises(ValidationError):
            CliCommand(executable="echo", inherit_env={"invalid": True})


# ---------------------------------------------------------------------------
# Test: Credential filtering with required_env
# ---------------------------------------------------------------------------


class TestCredentialEnvFiltering:
    """When required_env is specified, only declared vars are passed."""

    def test_required_env_filters_credentials(self) -> None:
        """Credentials not in required_env are excluded from subprocess env."""
        profile = _make_profile(required_env=["GEMINI_API_KEY"])
        backend = PluginCliBackend(profile)
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-gemini-key",
                "ANTHROPIC_API_KEY": "sk-ant-secret",
                "OPENAI_API_KEY": "sk-openai-secret",
                "AWS_SECRET_ACCESS_KEY": "aws-secret",
            },
            clear=False,
        ):
            env = backend._build_env()

        assert env is not None
        assert env["GEMINI_API_KEY"] == "test-gemini-key"
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env

    def test_required_env_allows_system_vars(self) -> None:
        """System env vars (PATH, HOME, etc.) always pass through."""
        profile = _make_profile(required_env=["GEMINI_API_KEY"])
        backend = PluginCliBackend(profile)
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "key", "PATH": "/usr/bin"},
            clear=False,
        ):
            env = backend._build_env()

        assert env is not None
        assert "PATH" in env

    def test_required_env_empty_list_blocks_all_non_system(self) -> None:
        """Empty required_env = only system vars pass through."""
        profile = _make_profile(required_env=[])
        backend = PluginCliBackend(profile)
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "secret",
                "RANDOM_VAR": "value",
                "PATH": "/usr/bin",
                "HOME": "/home/test",
            },
            clear=False,
        ):
            env = backend._build_env()

        assert env is not None
        assert "ANTHROPIC_API_KEY" not in env
        assert "RANDOM_VAR" not in env
        assert "PATH" in env
        assert "HOME" in env

    def test_required_env_missing_var_not_in_env(self) -> None:
        """If a required var doesn't exist in os.environ, it's silently skipped."""
        profile = _make_profile(required_env=["NONEXISTENT_KEY"])
        backend = PluginCliBackend(profile)
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            env = backend._build_env()

        assert env is not None
        assert "NONEXISTENT_KEY" not in env

    def test_profile_env_merged_with_required_env(self) -> None:
        """Profile-declared env vars are included alongside required_env vars."""
        profile = _make_profile(
            env={"CUSTOM_VAR": "custom_value"},
            required_env=["GEMINI_API_KEY"],
        )
        backend = PluginCliBackend(profile)
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "key",
                "ANTHROPIC_API_KEY": "secret",
            },
            clear=False,
        ):
            env = backend._build_env()

        assert env is not None
        assert env["CUSTOM_VAR"] == "custom_value"
        assert env["GEMINI_API_KEY"] == "key"
        assert "ANTHROPIC_API_KEY" not in env

    def test_profile_env_expansion_works_with_required_env(self) -> None:
        """${VAR} expansion in profile env works alongside required_env."""
        profile = _make_profile(
            env={"MY_KEY": "${SOURCE_KEY}"},
            required_env=["SOURCE_KEY"],
        )
        backend = PluginCliBackend(profile)
        with patch.dict(
            os.environ,
            {"SOURCE_KEY": "expanded_value"},
            clear=False,
        ):
            env = backend._build_env()

        assert env is not None
        assert env["MY_KEY"] == "expanded_value"
        assert env["SOURCE_KEY"] == "expanded_value"

    def test_required_env_with_multiple_vars(self) -> None:
        """Multiple required vars all pass through."""
        profile = _make_profile(
            required_env=["VAR_A", "VAR_B", "VAR_C"],
        )
        backend = PluginCliBackend(profile)
        with patch.dict(
            os.environ,
            {
                "VAR_A": "a",
                "VAR_B": "b",
                "VAR_C": "c",
                "SECRET": "should-not-pass",
            },
            clear=False,
        ):
            env = backend._build_env()

        assert env is not None
        assert env["VAR_A"] == "a"
        assert env["VAR_B"] == "b"
        assert env["VAR_C"] == "c"
        assert "SECRET" not in env


# ---------------------------------------------------------------------------
# Test: Adversarial — credential patterns in var names
# ---------------------------------------------------------------------------


@pytest.mark.adversarial
class TestAdversarialEnvFiltering:
    """Attack the env filtering with unusual inputs."""

    def test_required_env_is_case_sensitive(self) -> None:
        """Env var names are case-sensitive — 'path' != 'PATH'."""
        profile = _make_profile(required_env=["path"])
        backend = PluginCliBackend(profile)
        with patch.dict(
            os.environ,
            {"PATH": "/usr/bin", "path": "lowercase"},
            clear=True,
        ):
            env = backend._build_env()

        assert env is not None
        # 'PATH' comes through as system var
        assert env["PATH"] == "/usr/bin"
        # 'path' comes through as required var
        assert env["path"] == "lowercase"

    def test_required_env_prevents_env_var_injection(self) -> None:
        """A malicious instrument profile can't access credentials it doesn't declare."""
        profile = _make_profile(required_env=["HARMLESS_VAR"])
        backend = PluginCliBackend(profile)
        with patch.dict(
            os.environ,
            {
                "HARMLESS_VAR": "safe",
                "ANTHROPIC_API_KEY": "sk-ant-api03-verysecret",
                "OPENAI_API_KEY": "sk-proj-secret",
                "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG",
                "GITHUB_TOKEN": "ghp_xxxxxxxxxxxxxxxxxxxx",
                "DATABASE_PASSWORD": "super-secret-db-pass",
            },
            clear=False,
        ):
            env = backend._build_env()

        assert env is not None
        assert env["HARMLESS_VAR"] == "safe"
        for secret_var in [
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "DATABASE_PASSWORD",
        ]:
            assert secret_var not in env, f"{secret_var} leaked through filtering"


# ---------------------------------------------------------------------------
# Test: Data model — required_env on CliCommand
# ---------------------------------------------------------------------------


class TestRequiredEnvModel:
    """Test the required_env field on CliCommand."""

    def test_required_env_default_is_none(self) -> None:
        """Default is None (system essentials only)."""
        cmd = CliCommand(executable="echo")
        assert cmd.required_env is None

    def test_required_env_accepts_list(self) -> None:
        """required_env accepts a list of strings."""
        cmd = CliCommand(executable="echo", required_env=["VAR_A", "VAR_B"])
        assert cmd.required_env == ["VAR_A", "VAR_B"]

    def test_required_env_accepts_empty_list(self) -> None:
        """Empty list means 'no extra vars needed' (strictest filtering)."""
        cmd = CliCommand(executable="echo", required_env=[])
        assert cmd.required_env == []

    def test_required_env_from_yaml_roundtrip(self) -> None:
        """required_env survives YAML parse → Pydantic validation."""
        profile = _make_profile(required_env=["GEMINI_API_KEY", "CUSTOM_VAR"])
        assert profile.cli is not None
        assert profile.cli.command.required_env == ["GEMINI_API_KEY", "CUSTOM_VAR"]


@pytest.mark.parametrize("name", ["aider", "cline-cli", "crush", "cli"])
def test_builtin_profile_requires_explicit_credential_grants(name: str) -> None:
    """Shipped generic/provider-configured tools never acquire ambient keys."""
    from pathlib import Path

    from marianne.instruments.loader import InstrumentProfileLoader

    directory = Path(__file__).parents[1] / "src/marianne/instruments/builtins"
    profile = InstrumentProfileLoader.load_directory(directory)[name]
    assert profile.cli is not None
    assert profile.cli.command.required_env == []
    with patch.dict(os.environ, {"PATH": "/usr/bin", "OPENAI_API_KEY": "probe"}, clear=True):
        assert PluginCliBackend(profile)._build_env() == {"PATH": "/usr/bin"}
        profile.cli.command.required_env = ["OPENAI_API_KEY"]
        assert PluginCliBackend(profile)._build_env() == {
            "PATH": "/usr/bin", "OPENAI_API_KEY": "probe",
        }
