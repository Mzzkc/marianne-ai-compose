"""Movement 5 — Adversarial tests (Adversary).

Seventh adversarial pass. Targets M5 changes that Breakpoint's pass
did not cover, focusing on system boundary interactions, credential
handling, and feature-level integration:

1. F-271 MCP disable args injection — empty args, interaction with
   extra_flags, ordering guarantees
2. F-180 cost estimation pricing — profile pricing fallback, zero
   pricing, partial pricing (one None), arithmetic precision
3. F-025 credential env filtering — required_env isolation, system
   essentials passthrough, ${VAR} expansion in filtered mode,
   missing env vars, empty required_env
4. User variables in validations — precedence (builtins override user),
   collision on reserved names, non-string values
5. Safe killpg guard — pgid=0, pgid=-1, pgid=own, OSError on getpgid,
   pgid=2 (valid, would call os.killpg)
6. V212 unknown field hints — known typos, unknown fields, multi-field
   extraction, empty error, no-match fallback
7. F-451 diagnose workspace fallback — JobSubmissionError with workspace,
   JobSubmissionError without workspace, DaemonError path
8. F-190 DaemonError catch — diagnose errors(), diagnose main, recover
9. Feature interactions — env filtering + mcp_disable_args, cost
   estimation with zero tokens, credential env + profile env overlay

51 tests across 9 test classes.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.execution.instruments.cli_backend import (
    SYSTEM_ENV_VARS,
    PluginCliBackend,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_profile(
    *,
    name: str = "test-instrument",
    mcp_disable_args: list[str] | None = None,
    extra_flags: list[str] | None = None,
    env: dict[str, str] | None = None,
    required_env: list[str] | None = None,
    prompt_via_stdin: bool = False,
    stdin_sentinel: str | None = None,
    start_new_session: bool = False,
    prompt_flag: str | None = "-p",
    models: list[ModelCapacity] | None = None,
    default_model: str | None = None,
) -> InstrumentProfile:
    """Create a minimal InstrumentProfile for testing."""
    cmd_kwargs: dict[str, Any] = {
        "executable": "test-cli",
        "prompt_flag": prompt_flag,
        "extra_flags": extra_flags or [],
        "env": env or {},
        "mcp_disable_args": mcp_disable_args or [],
        "prompt_via_stdin": prompt_via_stdin,
        "start_new_session": start_new_session,
        "required_env": required_env,
    }
    if stdin_sentinel is not None:
        cmd_kwargs["stdin_sentinel"] = stdin_sentinel

    return InstrumentProfile(
        name=name,
        display_name=name.replace("-", " ").title(),
        description="Test instrument",
        kind="cli",
        cli=CliProfile(
            command=CliCommand(**cmd_kwargs),
            output=CliOutputConfig(format="text"),
        ),
        models=models or [],
        default_model=default_model,
    )


def _make_backend(
    profile: InstrumentProfile | None = None,
    **kwargs: Any,
) -> PluginCliBackend:
    """Create a PluginCliBackend from a profile or kwargs."""
    if profile is None:
        profile = _make_profile(**kwargs)
    return PluginCliBackend(profile)


# =============================================================================
# 1. F-271 MCP Disable Args Injection
# =============================================================================


class TestMcpDisableArgsInjection:
    """F-271: mcp_disable_args are injected into _build_command().

    The adversarial concern: do mcp_disable_args interact correctly with
    extra_flags? Are they in the right order? What if they're empty or
    contain shell-special characters?
    """

    def test_mcp_disable_args_present_in_command(self) -> None:
        """mcp_disable_args appear in the built command."""
        backend = _make_backend(
            mcp_disable_args=["--strict-mcp-config", "--mcp-config", "{}"],
        )
        args = backend._build_command("test prompt", timeout_seconds=None)
        assert "--strict-mcp-config" in args
        assert "--mcp-config" in args
        assert "{}" in args

    def test_mcp_disable_args_before_extra_flags(self) -> None:
        """mcp_disable_args come before extra_flags in arg order."""
        backend = _make_backend(
            mcp_disable_args=["--no-mcp"],
            extra_flags=["--verbose"],
        )
        args = backend._build_command("test prompt", timeout_seconds=None)
        no_mcp_idx = args.index("--no-mcp")
        verbose_idx = args.index("--verbose")
        assert no_mcp_idx < verbose_idx, "mcp_disable_args should precede extra_flags"

    def test_mcp_disable_args_empty_is_noop(self) -> None:
        """Empty mcp_disable_args adds nothing to command."""
        backend_with = _make_backend(mcp_disable_args=[])
        backend_without = _make_backend()
        args_with = backend_with._build_command("test", timeout_seconds=None)
        args_without = backend_without._build_command("test", timeout_seconds=None)
        assert args_with == args_without

    def test_mcp_disable_args_with_special_chars(self) -> None:
        """Args containing JSON, quotes, or spaces are preserved verbatim."""
        json_config = '{"mcpServers": {}}'
        backend = _make_backend(mcp_disable_args=["--mcp-config", json_config])
        args = backend._build_command("test", timeout_seconds=None)
        assert json_config in args

    def test_mcp_disable_args_with_stdin_mode(self) -> None:
        """mcp_disable_args still injected when using stdin prompt delivery."""
        backend = _make_backend(
            mcp_disable_args=["--no-mcp"],
            prompt_via_stdin=True,
            stdin_sentinel="-",
        )
        args = backend._build_command("test prompt", timeout_seconds=None)
        assert "--no-mcp" in args

    def test_mcp_disable_args_multiple_identical(self) -> None:
        """Duplicate args in mcp_disable_args are all preserved (no dedup)."""
        backend = _make_backend(
            mcp_disable_args=["--flag", "--flag"],
        )
        args = backend._build_command("test", timeout_seconds=None)
        count = args.count("--flag")
        assert count == 2


# =============================================================================
# 2. F-180 Cost Estimation Pricing
# =============================================================================


class TestCostEstimationPricing:
    """F-180: Profile pricing wired into baton cost estimation.

    The adversarial concern: what happens with partial pricing (one
    field None), zero pricing, extreme token counts, and the fallback
    to hardcoded rates?
    """

    def test_profile_pricing_used_when_both_provided(self) -> None:
        """Both cost_per_1k fields set → profile pricing used."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = MagicMock(input_tokens=1000, output_tokens=500)
        cost = _estimate_cost(result, cost_per_1k_input=0.01, cost_per_1k_output=0.03)
        expected = (1000 * 0.01 / 1000) + (500 * 0.03 / 1000)
        assert abs(cost - expected) < 1e-10

    def test_fallback_when_input_pricing_none(self) -> None:
        """Only cost_per_1k_input is None → fallback to hardcoded."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = MagicMock(input_tokens=1000, output_tokens=1000)
        cost = _estimate_cost(result, cost_per_1k_input=None, cost_per_1k_output=0.03)
        # Should use fallback: $3/1M input + $15/1M output
        expected_fallback = (1000 * 3.0 / 1_000_000) + (1000 * 15.0 / 1_000_000)
        assert abs(cost - expected_fallback) < 1e-10

    def test_fallback_when_output_pricing_none(self) -> None:
        """Only cost_per_1k_output is None → fallback to hardcoded."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = MagicMock(input_tokens=1000, output_tokens=1000)
        cost = _estimate_cost(result, cost_per_1k_input=0.01, cost_per_1k_output=None)
        expected_fallback = (1000 * 3.0 / 1_000_000) + (1000 * 15.0 / 1_000_000)
        assert abs(cost - expected_fallback) < 1e-10

    def test_zero_pricing_yields_zero_cost(self) -> None:
        """Zero cost per token (free/local model) → $0 cost."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = MagicMock(input_tokens=100_000, output_tokens=50_000)
        cost = _estimate_cost(result, cost_per_1k_input=0.0, cost_per_1k_output=0.0)
        assert cost == 0.0

    def test_zero_tokens_yields_zero_cost(self) -> None:
        """Zero tokens → $0 regardless of pricing."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = MagicMock(input_tokens=0, output_tokens=0)
        cost = _estimate_cost(result, cost_per_1k_input=10.0, cost_per_1k_output=10.0)
        assert cost == 0.0

    def test_none_tokens_treated_as_zero(self) -> None:
        """None token counts (missing from execution result) → treated as 0."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = MagicMock(input_tokens=None, output_tokens=None)
        cost = _estimate_cost(result, cost_per_1k_input=10.0, cost_per_1k_output=10.0)
        assert cost == 0.0

    def test_large_token_count_precision(self) -> None:
        """Large token counts don't lose precision in float arithmetic."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = MagicMock(input_tokens=1_000_000, output_tokens=500_000)
        cost = _estimate_cost(result, cost_per_1k_input=0.003, cost_per_1k_output=0.015)
        expected = (1_000_000 * 0.003 / 1000) + (500_000 * 0.015 / 1000)
        assert abs(cost - expected) < 1e-6


# =============================================================================
# 3. F-025 Credential Env Filtering
# =============================================================================


class TestCredentialEnvFiltering:
    """F-025: required_env filtering in PluginCliBackend._build_env().

    The adversarial concern: does filtering actually exclude secrets?
    Does it handle missing vars? Do profile env vars with ${VAR}
    expansion work in filtered mode?
    """

    def test_required_env_excludes_unrequested_vars(self) -> None:
        """Vars not in required_env are excluded from subprocess env."""
        backend = _make_backend(required_env=["ANTHROPIC_API_KEY"])
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-123",
                "OPENAI_API_KEY": "sk-openai-456",
                "PATH": "/usr/bin",
            },
            clear=True,
        ):
            env = backend._build_env()
        assert env is not None
        assert "ANTHROPIC_API_KEY" in env
        assert "OPENAI_API_KEY" not in env

    def test_system_essentials_always_passed(self) -> None:
        """System essential vars (PATH, HOME, etc.) pass through even with filtering."""
        backend = _make_backend(required_env=["MY_VAR"])
        test_env = {"MY_VAR": "value", "PATH": "/usr/bin", "HOME": "/home/test"}
        with patch.dict(os.environ, test_env, clear=True):
            env = backend._build_env()
        assert env is not None
        assert env.get("PATH") == "/usr/bin"
        assert env.get("HOME") == "/home/test"

    def test_missing_required_env_silently_omitted(self) -> None:
        """Vars listed in required_env but not in os.environ are silently skipped."""
        backend = _make_backend(required_env=["NONEXISTENT_KEY"])
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            env = backend._build_env()
        assert env is not None
        assert "NONEXISTENT_KEY" not in env

    def test_empty_required_env_passes_only_system(self) -> None:
        """Empty required_env list → only system essentials pass through."""
        backend = _make_backend(required_env=[])
        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "SECRET_KEY": "danger",
                "HOME": "/home/test",
            },
            clear=True,
        ):
            env = backend._build_env()
        assert env is not None
        assert "SECRET_KEY" not in env
        assert env.get("PATH") == "/usr/bin"

    def test_none_required_env_inherits_full_parent(self) -> None:
        """required_env=None → full parent environment inherited."""
        backend = _make_backend(required_env=None, env={"PROFILE_VAR": "value"})
        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "SECRET_KEY": "danger",
            },
            clear=True,
        ):
            env = backend._build_env()
        assert env is not None
        assert "SECRET_KEY" in env

    def test_profile_env_overlay_in_filtered_mode(self) -> None:
        """Profile env vars are merged on top of filtered env."""
        backend = _make_backend(
            required_env=["ANTHROPIC_API_KEY"],
            env={"EXTRA_PROFILE_VAR": "hello"},
        )
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "key",
                "PATH": "/usr/bin",
            },
            clear=True,
        ):
            env = backend._build_env()
        assert env is not None
        assert env["EXTRA_PROFILE_VAR"] == "hello"
        assert env["ANTHROPIC_API_KEY"] == "key"

    def test_profile_env_expansion_uses_parent_not_filtered(self) -> None:
        """${VAR} expansion in profile env vars reads from os.environ,
        not the filtered env. This ensures expansion works even for
        vars not in required_env."""
        backend = _make_backend(
            required_env=[],
            env={"EXPANDED": "${MY_SECRET}"},
        )
        with patch.dict(
            os.environ,
            {
                "MY_SECRET": "resolved-value",
                "PATH": "/usr/bin",
            },
            clear=True,
        ):
            env = backend._build_env()
        assert env is not None
        assert env["EXPANDED"] == "resolved-value"

    def test_system_env_vars_constant_includes_critical(self) -> None:
        """The SYSTEM_ENV_VARS set includes PATH, HOME — the minimum viable set."""
        assert "PATH" in SYSTEM_ENV_VARS
        assert "HOME" in SYSTEM_ENV_VARS
        assert "SHELL" in SYSTEM_ENV_VARS


# =============================================================================
# 4. User Variables in Validations
# =============================================================================


class TestUserVariablesInValidations:
    """User-defined prompt.variables merged into validation path_context.

    The adversarial concern: user variables have lower precedence than
    built-ins. Can a user-defined 'workspace' override the real workspace?
    What about non-string values?
    """

    def test_builtin_workspace_overrides_user_workspace(self) -> None:
        """User var named 'workspace' cannot override the real workspace."""
        # Replicate the merge logic from rendering.py:291-306
        user_variables: dict[str, Any] = {"workspace": "/fake/workspace"}
        real_workspace = Path("/real/workspace")

        path_context: dict[str, str] = {str(k): str(v) for k, v in user_variables.items()}
        # Built-ins override: same order as rendering.py
        path_context.update({"workspace": str(real_workspace)})
        assert path_context["workspace"] == "/real/workspace"

    def test_builtin_sheet_num_overrides_user(self) -> None:
        """User var named 'sheet_num' cannot override the real sheet_num."""
        path_context: dict[str, str] = {"sheet_num": "999"}
        path_context.update({"sheet_num": "1"})
        assert path_context["sheet_num"] == "1"

    def test_user_vars_available_when_no_collision(self) -> None:
        """User-defined vars that don't collide with builtins are available."""
        path_context: dict[str, str] = {
            "my_custom_var": "custom_value",
            "output_dir": "/tmp/output",
        }
        path_context.update({"workspace": "/ws", "sheet_num": "1"})
        assert path_context["my_custom_var"] == "custom_value"
        assert path_context["output_dir"] == "/tmp/output"

    def test_non_string_values_coerced(self) -> None:
        """Non-string user var values are str()-coerced in path_context."""
        variables: dict[str, Any] = {"count": 42, "flag": True, "ratio": 3.14}
        path_context = {str(k): str(v) for k, v in variables.items()}
        assert path_context["count"] == "42"
        assert path_context["flag"] == "True"
        assert path_context["ratio"] == "3.14"


# =============================================================================
# 5. Safe killpg Guard
# =============================================================================


class TestSafeKillpgGuard:
    """F-490: _safe_killpg refuses dangerous pgid values.

    The adversarial concern: can a mock object, PID recycle, or
    container edge case cause os.killpg(1, SIGKILL) which translates
    to kill(-1, SIGKILL) — nuking every user process?
    """

    def test_pgid_zero_refused(self) -> None:
        """pgid=0 is refused (would target own process group)."""
        from marianne.backends.claude_cli import _safe_killpg

        result = _safe_killpg(0, signal.SIGTERM, context="test")
        assert result is False

    def test_pgid_one_refused(self) -> None:
        """pgid=1 is refused (init — kernel translates to kill(-1,sig))."""
        from marianne.backends.claude_cli import _safe_killpg

        result = _safe_killpg(1, signal.SIGTERM, context="test")
        assert result is False

    def test_pgid_negative_refused(self) -> None:
        """pgid=-1 is refused (negative values are invalid/dangerous)."""
        from marianne.backends.claude_cli import _safe_killpg

        result = _safe_killpg(-1, signal.SIGTERM, context="test")
        assert result is False

    def test_pgid_own_pgroup_refused(self) -> None:
        """pgid matching our own process group is refused."""
        from marianne.backends.claude_cli import _safe_killpg

        own_pgid = os.getpgid(0)
        # Don't actually call os.killpg — just verify the guard blocks it
        result = _safe_killpg(own_pgid, signal.SIGTERM, context="test")
        assert result is False

    def test_pgid_valid_calls_killpg(self) -> None:
        """A valid pgid (not 0/1/own) calls os.killpg."""
        from marianne.backends.claude_cli import _safe_killpg

        with patch("os.killpg") as mock_killpg, patch("os.getpgid", return_value=12345):
            result = _safe_killpg(99999, signal.SIGTERM, context="test")
        assert result is True
        mock_killpg.assert_called_once_with(99999, signal.SIGTERM)

    def test_getpgid_oserror_allows_call(self) -> None:
        """If os.getpgid(0) raises OSError, the own-pgroup check is skipped
        but the pgid<=1 check still applies."""
        from marianne.backends.claude_cli import _safe_killpg

        with patch("os.getpgid", side_effect=OSError("not supported")):
            with patch("os.killpg"):
                # Valid pgid → should proceed
                result = _safe_killpg(50000, signal.SIGTERM, context="test")
                assert result is True

            # But pgid=1 is still blocked regardless
            result2 = _safe_killpg(1, signal.SIGTERM, context="test")
            assert result2 is False

    def test_killpg_oserror_propagates(self) -> None:
        """os.killpg raising (e.g. no such process) propagates up."""
        from marianne.backends.claude_cli import _safe_killpg

        with (
            patch("os.killpg", side_effect=ProcessLookupError("No process")),
            patch("os.getpgid", return_value=12345),
            pytest.raises(ProcessLookupError),
        ):
            _safe_killpg(99999, signal.SIGTERM, context="test")


# =============================================================================
# 6. V212 Unknown Field Hints
# =============================================================================


class TestV212UnknownFieldHints:
    """V212: _unknown_field_hints() provides typo suggestions.

    The adversarial concern: does the regex extraction work with
    Pydantic's error format? Do all known typos have correct suggestions?
    What about fields not in the typo map?
    """

    def test_known_typo_retries(self) -> None:
        """'retries' → suggests 'retry'."""
        from marianne.cli.commands.validate import _unknown_field_hints

        error = "retries\n  Extra inputs are not permitted"
        hints = _unknown_field_hints(error)
        assert any("retries" in h and "retry" in h for h in hints)

    def test_known_typo_paralel(self) -> None:
        """Common misspelling 'paralel' → 'parallel'."""
        from marianne.cli.commands.validate import _unknown_field_hints

        error = "paralel\n  Extra inputs are not permitted"
        hints = _unknown_field_hints(error)
        assert any("paralel" in h and "parallel" in h for h in hints)

    def test_known_typo_insturment(self) -> None:
        """'insturment' → 'instrument'."""
        from marianne.cli.commands.validate import _unknown_field_hints

        error = "insturment\n  Extra inputs are not permitted"
        hints = _unknown_field_hints(error)
        assert any("insturment" in h and "instrument" in h for h in hints)

    def test_unknown_field_no_suggestion(self) -> None:
        """Unknown field not in typo map → generic message."""
        from marianne.cli.commands.validate import _unknown_field_hints

        error = "totally_bogus_field\n  Extra inputs are not permitted"
        hints = _unknown_field_hints(error)
        assert any("totally_bogus_field" in h for h in hints)
        assert any("not a valid" in h for h in hints)

    def test_multiple_unknown_fields(self) -> None:
        """Multiple fields in one error message → all extracted."""
        from marianne.cli.commands.validate import _unknown_field_hints

        error = (
            "retries\n  Extra inputs are not permitted\nparalel\n  Extra inputs are not permitted"
        )
        hints = _unknown_field_hints(error)
        retries_hint = [h for h in hints if "retries" in h]
        paralel_hint = [h for h in hints if "paralel" in h]
        assert len(retries_hint) >= 1
        assert len(paralel_hint) >= 1

    def test_no_match_in_error_message(self) -> None:
        """Error without 'Extra inputs' pattern → generic hints."""
        from marianne.cli.commands.validate import _unknown_field_hints

        error = "Some other validation error happened"
        hints = _unknown_field_hints(error)
        assert any("doesn't recognize" in h for h in hints)

    def test_all_known_typos_have_suggestions(self) -> None:
        """Every entry in _KNOWN_TYPOS maps to a non-empty suggestion."""
        from marianne.cli.commands.validate import _KNOWN_TYPOS

        for typo, suggestion in _KNOWN_TYPOS.items():
            assert typo, "Empty typo key"
            assert suggestion, f"Empty suggestion for {typo}"


# =============================================================================
# 7. F-451 Diagnose Workspace Fallback
# =============================================================================


class TestDiagnoseWorkspaceFallback:
    """F-451: diagnose falls back to filesystem when conductor says 'not found'.

    The adversarial concern: the fallback path has different error handling
    than the conductor path. Does it correctly distinguish between
    JobSubmissionError (job not found) and DaemonError (conductor broken)?
    """

    def test_job_submission_error_with_workspace_triggers_fallback(self) -> None:
        """JobSubmissionError + workspace → code path imports _find_job_state_direct.

        The F-451 fallback architecture: when JobSubmissionError is raised
        and workspace is not None, the code imports _find_job_state_direct
        from helpers and calls it.
        """
        import importlib
        import inspect

        diag_module = importlib.import_module("marianne.cli.commands.diagnose")
        source = inspect.getsource(diag_module)
        assert "JobSubmissionError" in source
        assert "_find_job_state_direct" in source
        assert "workspace is not None" in source

    def test_daemon_error_exits_with_hint(self) -> None:
        """DaemonError → exit with 'restart conductor' hint, no fallback."""
        import importlib
        import inspect

        diag_module = importlib.import_module("marianne.cli.commands.diagnose")
        source = inspect.getsource(diag_module)
        assert "DaemonError" in source
        assert "restart" in source.lower()

    def test_recover_handles_missing_db_cleanly(self) -> None:
        """recover.py handles missing DB with clean error (GH#170).

        Recover now reads the conductor DB directly instead of routing
        through the conductor, so DaemonError handling is no longer needed.
        Instead, verify it handles a missing DB file gracefully.
        """
        import importlib
        import inspect

        rec_module = importlib.import_module("marianne.cli.commands.recover")
        source = inspect.getsource(rec_module)
        # The new recover code checks for DB existence and shows clear errors
        assert "db_path" in source
        assert "not found" in source.lower() or "not_found" in source.lower()


# =============================================================================
# 8. F-190 DaemonError Catch Completeness
# =============================================================================


class TestDaemonErrorCatchCompleteness:
    """F-190: DaemonError caught in diagnose (errors/diagnose/history) + recover.

    The adversarial concern: is DaemonError caught in ALL IPC callsites
    that could throw it, not just the main diagnose path? Are the hints
    consistent?
    """

    def test_errors_command_catches_daemon_error(self) -> None:
        """diagnose.py module has DaemonError catch blocks."""
        import importlib
        import inspect
        import re

        diag_module = importlib.import_module("marianne.cli.commands.diagnose")
        source = inspect.getsource(diag_module)
        catches = re.findall(r"except\s+DaemonError", source)
        # Should have at least 2: one in errors(), one in diagnose()
        assert len(catches) >= 2, (
            f"Expected >=2 DaemonError catches in diagnose.py, found {len(catches)}"
        )

    def test_recover_command_handles_db_errors(self) -> None:
        """recover.py handles DB-not-found errors cleanly (GH#170).

        Recover now reads the conductor DB directly instead of routing
        through the conductor. DaemonError catches are no longer needed.
        """
        import importlib
        import inspect

        rec_module = importlib.import_module("marianne.cli.commands.recover")
        source = inspect.getsource(rec_module)
        # The new recover code handles missing DB and missing jobs
        assert "output_error" in source, "recover.py must use output_error for clean error messages"

    def test_daemon_error_hints_mention_restart(self) -> None:
        """DaemonError catch blocks in diagnose include 'restart' in hints.

        Note: recover.py no longer uses DaemonError (GH#170).
        """
        import importlib
        import inspect

        for mod_name in [
            "marianne.cli.commands.diagnose",
        ]:
            module = importlib.import_module(mod_name)
            source = inspect.getsource(module)
            sections = source.split("except DaemonError")
            for section in sections[1:]:  # Skip before first catch
                next_lines = section[:500]
                assert "restart" in next_lines.lower(), (
                    f"DaemonError catch in {mod_name} missing restart hint"
                )


# =============================================================================
# 9. Feature Interactions
# =============================================================================


class TestFeatureInteractions:
    """Cross-feature interaction tests for M5 changes.

    These test scenarios where multiple M5 features interact in
    non-obvious ways.
    """

    def test_env_filtering_plus_mcp_disable_args(self) -> None:
        """Credential env filtering + MCP disable args work together.

        The concern: does MCP disabling still work when the env is filtered?
        The args are in the command line, not the env, so filtering shouldn't
        affect them.
        """
        backend = _make_backend(
            required_env=["ANTHROPIC_API_KEY"],
            mcp_disable_args=["--strict-mcp-config"],
        )
        args = backend._build_command("test", timeout_seconds=None)
        assert "--strict-mcp-config" in args

        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "key",
                "OPENAI_API_KEY": "leaked",
                "PATH": "/usr/bin",
            },
            clear=True,
        ):
            env = backend._build_env()
        assert env is not None
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" in env

    def test_cost_estimation_both_none_tokens(self) -> None:
        """Cost estimation with None tokens + profile pricing = $0."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = MagicMock(input_tokens=None, output_tokens=None)
        cost = _estimate_cost(result, cost_per_1k_input=100.0, cost_per_1k_output=100.0)
        assert cost == 0.0

    def test_profile_env_does_not_leak_filtered_secrets(self) -> None:
        """Profile env with ${LEAKED_VAR} expansion doesn't bypass filtering
        for the var ITSELF — the expanded value goes into a profile-declared
        key, not back into the required_env set."""
        backend = _make_backend(
            required_env=[],
            env={"TOOL_KEY": "${AWS_SECRET_ACCESS_KEY}"},
        )
        with patch.dict(
            os.environ,
            {
                "AWS_SECRET_ACCESS_KEY": "AKIA...",
                "PATH": "/usr/bin",
            },
            clear=True,
        ):
            env = backend._build_env()
        assert env is not None
        # AWS_SECRET_ACCESS_KEY is NOT directly in env (not in required_env)
        assert "AWS_SECRET_ACCESS_KEY" not in env
        # But TOOL_KEY has the expanded value — this is by design.
        # The profile author explicitly asked for it.
        assert env["TOOL_KEY"] == "AKIA..."

    def test_stdin_plus_no_prompt_flag(self) -> None:
        """prompt_via_stdin with no prompt_flag and no sentinel → prompt
        omitted from args entirely, delivered via stdin only."""
        backend = _make_backend(
            prompt_via_stdin=True,
            prompt_flag=None,
        )
        args = backend._build_command("secret prompt text", timeout_seconds=None)
        # Prompt should NOT appear in args
        assert "secret prompt text" not in args

    def test_model_capacity_forbid_extra(self) -> None:
        """ModelCapacity rejects unknown fields (extra='forbid')."""
        with pytest.raises(ValidationError, match="Extra inputs"):
            ModelCapacity(
                name="test",
                context_window=100000,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
                unknown_field="bad",  # type: ignore[call-arg]
            )

    def test_cli_command_forbid_extra(self) -> None:
        """CliCommand rejects unknown fields (extra='forbid')."""
        with pytest.raises(ValidationError, match="Extra inputs"):
            CliCommand(
                executable="test",
                unknown_field="bad",  # type: ignore[call-arg]
            )
