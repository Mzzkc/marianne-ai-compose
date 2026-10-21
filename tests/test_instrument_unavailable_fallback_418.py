"""GH #418: a missing binary or an unreachable endpoint must advance the
fallback chain on the FIRST attempt, not after the whole retry budget.

Before the fix, PluginCliBackend's "Executable not found" result (exit_code
None) was bucketed TRANSIENT/E999 ("possible signal race — retrying") and an
openai-compat ConnectError (exit 503, error_type="connection") was bucketed
EXECUTION_ERROR; a real BatonCore dispatched the dead primary three times
before falling back (Blueprint capability-classes probe R1 / W1-P4).

Real BatonCore, in memory, no subprocess.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import sheets_to_execution_states
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import InstrumentFallback, SheetAttemptResult
from marianne.daemon.baton.musician import _classify_error
from marianne.daemon.baton.state import BatonSheetStatus
from marianne.execution.base import ExecutionResult


def _missing_binary_result() -> ExecutionResult:
    return ExecutionResult(
        success=False, stdout="", stderr="Executable not found: claude",
        exit_code=None, exit_reason="error", error_type="executable_not_found",
        duration_seconds=0.01,
    )


def _endpoint_down_result() -> ExecutionResult:
    return ExecutionResult(
        success=False, stdout="", stderr="Connection error: All connection attempts failed",
        exit_code=503, error_type="connection",
        error_message="All connection attempts failed", duration_seconds=0.01,
    )


class TestClassification:
    def test_missing_binary_is_instrument_unavailable(self) -> None:
        cls = _classify_error(_missing_binary_result())
        assert cls.classification == "INSTRUMENT_UNAVAILABLE"
        assert cls.error_code == "E505"

    def test_endpoint_down_is_instrument_unavailable(self) -> None:
        cls = _classify_error(_endpoint_down_result())
        assert cls.classification == "INSTRUMENT_UNAVAILABLE"
        assert cls.error_code == "E505"

    def test_typed_executable_not_found_is_instrument_unavailable(self) -> None:
        """The backend now TYPES the cause (design §5.2); the bucket keys on it."""
        cls = _classify_error(ExecutionResult(
            success=False, stdout="", stderr="Executable not found: claude",
            exit_code=None, exit_reason="error", error_type="executable_not_found",
            duration_seconds=0.01,
        ))
        assert cls.classification == "INSTRUMENT_UNAVAILABLE"
        cls = _classify_error(ExecutionResult(
            success=False, stdout="", stderr="Failed to start process: EACCES",
            exit_code=None, exit_reason="error", error_type="spawn_failed",
            duration_seconds=0.01,
        ))
        assert cls.classification == "INSTRUMENT_UNAVAILABLE"

    def test_classifier_enoent_on_error_exit_reason_is_instrument_unavailable(self) -> None:
        cls = _classify_error(ExecutionResult(
            success=False, stdout="", stderr="spawn claude ENOENT",
            exit_code=None, exit_reason="error", duration_seconds=0.01,
        ))
        assert cls.classification == "INSTRUMENT_UNAVAILABLE"

    @pytest.mark.parametrize("stderr", [
        "Error: ENOENT: no such file or directory, open 'notes/plan.md'",
        "bash: line 1: pytest: command not found",
        "spawn claude ENOENT",
    ])
    def test_agent_own_enoent_text_is_not_instrument_unavailable(self, stderr: str) -> None:
        """Blueprint P9 (PO-C8): a process that RAN and exited 1 with its own
        tool error must be retried on the same entry, not abandoned."""
        cls = _classify_error(ExecutionResult(
            success=False, stdout="", stderr=stderr, exit_code=1,
            exit_reason="completed", duration_seconds=2.0,
        ))
        assert cls.classification != "INSTRUMENT_UNAVAILABLE"

    def test_exit_127_without_error_exit_reason_is_retried(self) -> None:
        """Exit 127 is not inferred as route-unavailable (design §5.2)."""
        cls = _classify_error(ExecutionResult(
            success=False, stdout="", stderr="bash: foo: command not found",
            exit_code=127, duration_seconds=0.01,
        ))
        assert cls.classification != "INSTRUMENT_UNAVAILABLE"

    def test_genuine_signal_race_stays_transient(self) -> None:
        """A bare exit_code=None with no ENOENT text is still the retriable case."""
        cls = _classify_error(ExecutionResult(
            success=False, stdout="partial", stderr="", exit_code=None,
            duration_seconds=1.0,
        ))
        assert cls.classification == "TRANSIENT"


def _run_until_moved(core: BatonCore, result: ExecutionResult) -> int:
    """Feed the failing primary result until the instrument changes; return dispatches."""
    st = core._jobs["j"].sheets[1]
    cls = _classify_error(result)
    dispatches = 0
    for attempt in range(1, 10):
        if st.instrument_name != "primary":
            break
        dispatches += 1
        asyncio.run(core.handle_event(SheetAttemptResult(
            job_id="j", sheet_num=1, instrument_name="primary", attempt=attempt,
            execution_success=False, exit_code=result.exit_code,
            error_classification=cls.classification, error_message=cls.message,
            error_code=cls.error_code,
        )))
    return dispatches


def _core_with_chain(tmp_path: Path, *, max_retries: int = 3) -> BatonCore:
    sheet = Sheet(
        num=1, movement=1, voice_count=1, workspace=tmp_path,
        instrument_name="primary", instrument_fallbacks=["secondary"],
        instrument_fallback_configs=[{}],
    )
    states = sheets_to_execution_states([sheet], max_retries=max_retries)
    core = BatonCore()
    core.register_job("j", states, {})
    return core


class TestBatonFallsBackImmediately:
    @pytest.mark.parametrize("result_factory", [_missing_binary_result, _endpoint_down_result])
    def test_primary_dispatched_once_then_fallback(
        self, tmp_path: Path, result_factory
    ) -> None:
        core = _core_with_chain(tmp_path, max_retries=3)
        dispatches = _run_until_moved(core, result_factory())
        st = core._jobs["j"].sheets[1]
        assert dispatches == 1, f"primary was dispatched {dispatches} times before fallback"
        assert st.instrument_name == "secondary"
        assert st.status is BatonSheetStatus.PENDING
        assert st.normal_attempts == 0, "the unavailable attempt must not consume a retry"
        evs = [e for e in core._fallback_events if isinstance(e, InstrumentFallback)]
        assert len(evs) == 1 and evs[0].reason == "unavailable"
        assert evs[0].from_instrument == "primary" and evs[0].to_instrument == "secondary"

    def test_no_chain_left_falls_through_to_bounded_retry(self, tmp_path: Path) -> None:
        """With no fallback, the ordinary retry/exhaustion path still bounds it."""
        sheet = Sheet(num=1, movement=1, voice_count=1, workspace=tmp_path,
                      instrument_name="primary", instrument_fallbacks=[],
                      instrument_fallback_configs=[])
        core = BatonCore()
        core.register_job("j", sheets_to_execution_states([sheet], max_retries=1), {})
        st = core._jobs["j"].sheets[1]
        cls = _classify_error(_missing_binary_result())
        for attempt in (1, 2, 3):
            asyncio.run(core.handle_event(SheetAttemptResult(
                job_id="j", sheet_num=1, instrument_name="primary", attempt=attempt,
                execution_success=False, exit_code=None,
                error_classification=cls.classification, error_message=cls.message,
                error_code=cls.error_code,
            )))
            if st.status is BatonSheetStatus.FAILED:
                break
        assert st.status is BatonSheetStatus.FAILED
        assert st.instrument_name == "primary"


# ---------------------------------------------------------------------------
# GH #389: an INSTALLED harness whose backend is not usable refuses in its own
# voice ("No provider configured", "Provider not found"). The profile's
# ``errors.unavailable_patterns`` types that refusal ``unavailable``, which the
# baton reads as INSTRUMENT_UNAVAILABLE — the chain advances on the first
# attempt instead of the sheet running to its timeout per misconfigured entry.
# ---------------------------------------------------------------------------

from marianne.core.config.instruments import (  # noqa: E402
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend  # noqa: E402
from marianne.instruments.loader import InstrumentProfileLoader, profile_source_dirs  # noqa: E402


def _backend_with(errors: CliErrorConfig) -> PluginCliBackend:
    return PluginCliBackend(InstrumentProfile(
        name="harness", display_name="Harness", kind="cli",
        cli=CliProfile(
            command=CliCommand(executable="harness", prompt_flag="-p"),
            output=CliOutputConfig(format="text"),
            errors=errors,
        ),
    ))


class TestUnavailablePatterns:
    def test_profile_refusal_is_typed_unavailable(self) -> None:
        backend = _backend_with(CliErrorConfig(
            unavailable_patterns=["No provider configured"],
        ))
        typed = backend._classify_output_errors(
            "", "error: No provider configured. Run 'goose configure' first.",
            is_success=False,
        )
        assert typed == "unavailable"

    def test_auth_still_wins_over_unavailable(self) -> None:
        """A credential rejection keeps its own diagnosis (E502) even when the
        same output also matches an unavailable pattern."""
        backend = _backend_with(CliErrorConfig(
            auth_error_patterns=["unauthorized"],
            unavailable_patterns=["provider"],
        ))
        assert backend._classify_output_errors(
            "", "provider replied: unauthorized", is_success=False,
        ) == "auth"

    def test_unavailable_wins_over_retriable_patterns(self) -> None:
        backend = _backend_with(CliErrorConfig(
            unavailable_patterns=["Provider not found"],
            timeout_patterns=["timeout"],
        ))
        assert backend._classify_output_errors(
            "", "Provider not found: zai (request timeout)", is_success=False,
        ) == "unavailable"

    def test_no_pattern_means_no_typing(self) -> None:
        """Without the key the refusal stays an ordinary error on the same entry."""
        backend = _backend_with(CliErrorConfig())
        assert backend._classify_output_errors(
            "", "No provider configured", is_success=False,
        ) is None

    def test_success_is_never_typed(self) -> None:
        backend = _backend_with(CliErrorConfig(unavailable_patterns=["provider"]))
        assert backend._classify_output_errors(
            "no provider configured in this repo's README", "", is_success=True,
        ) is None

    def test_invalid_regex_is_refused_at_load(self) -> None:
        with pytest.raises(ValueError, match="Invalid error pattern regex"):
            CliErrorConfig(unavailable_patterns=["("])
        with pytest.raises(ValueError, match="Invalid error pattern regex"):
            CliErrorConfig(auth_error_patterns=["(unclosed"])

    def test_typed_unavailable_is_instrument_unavailable(self) -> None:
        cls = _classify_error(ExecutionResult(
            success=False, stdout="",
            stderr="error: No provider configured. Run 'goose configure' first.",
            exit_code=1, exit_reason="completed", error_type="unavailable",
            error_message="No provider configured", duration_seconds=0.3,
        ))
        assert cls.classification == "INSTRUMENT_UNAVAILABLE"
        assert cls.error_code == "E505"
        assert cls.message == "No provider configured"

    def test_refusal_advances_chain_on_first_attempt(self, tmp_path: Path) -> None:
        core = _core_with_chain(tmp_path, max_retries=3)
        dispatches = _run_until_moved(core, ExecutionResult(
            success=False, stdout="", stderr="No providers configured - please run 'crush'",
            exit_code=1, exit_reason="completed", error_type="unavailable",
            duration_seconds=1.8,
        ))
        st = core._jobs["j"].sheets[1]
        assert dispatches == 1
        assert st.instrument_name == "secondary"
        assert st.normal_attempts == 0, "the refusal must not consume a retry"


class TestBuiltinRefusalTexts:
    """The shipped patterns match the harness refusals measured on 21 Oct 2026
    (each harness run with an empty HOME): the profile's word is the real text."""

    @pytest.fixture(scope="class")
    def builtins(self) -> dict[str, InstrumentProfile]:
        return InstrumentProfileLoader.load_directory(profile_source_dirs()[0])

    @pytest.mark.parametrize(("name", "refusal"), [
        ("goose", "  error: No provider configured. Run 'goose configure' first.\n"),
        ("crush", "   ERROR  \n  No providers configured - please run 'crush' to set up "
                  "a provider interactively.\n"),
        ("opencode", '[04:39:49.994] ERROR (#10937): failed {\n  ref: "err_1075d3fe",\n'
                     '  error: {\n    providerID: "zai-coding-plan",\n'
                     '    _tag: "ProviderModelNotFoundError",\n  },\n}'),
        ("opencode", "Error: Provider not found: zai-coding-plan/glm-5.3-flash\n"),
    ])
    def test_builtin_matches_measured_refusal(
        self, builtins: dict[str, InstrumentProfile], name: str, refusal: str,
    ) -> None:
        profile = builtins[name]
        assert profile.cli is not None and profile.cli.errors.unavailable_patterns
        backend = PluginCliBackend(profile)
        assert backend._classify_output_errors(refusal, "", is_success=False) == "unavailable"
        assert backend._classify_output_errors("", refusal, is_success=False) == "unavailable"

    @pytest.mark.parametrize("name", ["goose", "crush", "opencode"])
    def test_builtin_does_not_type_an_agents_ordinary_failure(
        self, builtins: dict[str, InstrumentProfile], name: str,
    ) -> None:
        """An agent that ran and failed on its work is retried on the same entry."""
        backend = PluginCliBackend(builtins[name])
        out = "Traceback (most recent call last):\n  File 'x.py'\nKeyError: 'provider'\n"
        assert backend._classify_output_errors(out, "", is_success=False) != "unavailable"
