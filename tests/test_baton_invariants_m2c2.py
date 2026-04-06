"""Movement 2 cycle 2 — property-based invariant verification.

Extends the invariant suite to cover M2 completions:

41. Recovery state mapping fidelity — recover_job() correctly maps checkpoint
    statuses: in_progress → PENDING, terminal → terminal.
42. Recovery attempt count preservation — attempt_count and completion_attempts
    survive recovery without loss.
43. Recovery dispatch readiness — reset sheets are dispatchable, terminal sheets
    are not.
44. Clone path mutual exclusion — different clone names produce disjoint paths.
45. Clone config path isolation — clone configs never overlap with production
    defaults.
46. Credential redaction totality — redact_credentials removes all credential
    patterns from any string.
47. Credential redaction idempotency — redact(redact(s)) == redact(s).
48. Credential redaction preserves non-credentials — text without credentials
    passes through unchanged.
49. V210 instrument name check — unknown instruments produce warnings, known
    instruments produce none.
50. Failure propagation preserves terminal states — propagate_failure_to_dependents
    never overwrites COMPLETED/FAILED/SKIPPED sheets.

Found by: Theorem, Movement 2 (cycle 2)
Method: Property-based testing with hypothesis + invariant analysis

@pytest.mark.property_based
"""

from __future__ import annotations

import asyncio
import string
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import hypothesis.strategies as st
from hypothesis import HealthCheck, assume, given, settings


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async coroutine synchronously (for hypothesis tests)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        raise RuntimeError("Cannot _run() inside a running event loop")
    return asyncio.run(coro)


# =============================================================================
# Strategies
# =============================================================================

_NONNEG_INT = st.integers(min_value=0, max_value=20)

# Checkpoint status strings that the adapter must handle
_CHECKPOINT_STATUSES = st.sampled_from(
    ["pending", "in_progress", "completed", "failed", "skipped"]
)

# Clone names: alphanumeric strings (after sanitization)
_CLONE_NAMES = st.text(
    min_size=1, max_size=30, alphabet=string.ascii_lowercase + string.digits + "-_"
)

# Credential pattern generators — produce strings that MUST be redacted
_ANTHROPIC_KEY = st.from_regex(r"sk-ant-api[A-Za-z0-9_-]{15,30}", fullmatch=True)
_OPENAI_PROJ_KEY = st.from_regex(r"sk-proj-[a-zA-Z0-9_-]{25,40}", fullmatch=True)
_OPENAI_KEY = st.from_regex(r"sk-[a-zA-Z0-9]{45,60}", fullmatch=True)
_GOOGLE_KEY = st.from_regex(r"AIzaSy[a-zA-Z0-9_-]{30,40}", fullmatch=True)
_AWS_KEY = st.from_regex(r"AKIA[A-Z0-9]{16}", fullmatch=True)
_GITHUB_PAT = st.from_regex(r"ghp_[a-zA-Z0-9]{40,50}", fullmatch=True)
_SLACK_TOKEN = st.from_regex(r"xoxb-[a-zA-Z0-9-]{25,40}", fullmatch=True)
_HF_TOKEN = st.from_regex(r"hf_[a-zA-Z0-9]{25,40}", fullmatch=True)

_ANY_CREDENTIAL = st.one_of(
    _ANTHROPIC_KEY,
    _OPENAI_PROJ_KEY,
    _OPENAI_KEY,
    _GOOGLE_KEY,
    _AWS_KEY,
    _GITHUB_PAT,
    _SLACK_TOKEN,
    _HF_TOKEN,
)

# Non-credential text — safe words that should pass through unchanged
_SAFE_TEXT = st.text(
    min_size=0,
    max_size=100,
    alphabet=st.characters(
        categories=("L", "N", "P", "Z"),
        exclude_characters="\\",
    ),
)


# =============================================================================
# 41. Recovery State Mapping Fidelity
# =============================================================================


class TestRecoveryStateMappingFidelity:
    """recover_job() correctly maps checkpoint statuses to baton statuses.

    Invariant: in_progress → PENDING (musician died on restart).
    Terminal statuses (completed, failed, skipped) → their baton equivalents.
    pending → PENDING.
    """

    @given(
        cp_status=_CHECKPOINT_STATUSES,
        attempt_count=_NONNEG_INT,
        completion_attempts=_NONNEG_INT,
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_recovery_maps_status_correctly(
        self,
        cp_status: str,
        attempt_count: int,
        completion_attempts: int,
    ) -> None:
        """Every checkpoint status maps to the correct baton status after recovery."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.daemon.baton.state import BatonSheetStatus

        # Build a minimal checkpoint with one sheet
        checkpoint = MagicMock()
        sheet_state = MagicMock(spec=SheetState)
        sheet_state.status = SheetStatus(cp_status)
        sheet_state.attempt_count = attempt_count
        sheet_state.completion_attempts = completion_attempts
        checkpoint.sheets = {1: sheet_state}

        # Build a matching Sheet entity
        sheet = Sheet(
            num=1,
            movement=1,
            voice_count=1,
            prompt_template="test",
            workspace=Path("/tmp/test"),
            timeout_seconds=300,
            instrument_name="claude-code",
        )

        adapter = BatonAdapter()
        adapter.recover_job(
            job_id="recovery_test",
            sheets=[sheet],
            dependencies={},
            checkpoint=checkpoint,
        )

        baton_sheet = adapter._baton.get_sheet_state("recovery_test", 1)
        assert baton_sheet is not None

        # The critical invariant
        if cp_status == "in_progress":
            assert baton_sheet.status == BatonSheetStatus.PENDING, (
                f"in_progress should map to PENDING, got {baton_sheet.status}"
            )
        elif cp_status == "completed":
            assert baton_sheet.status == BatonSheetStatus.COMPLETED
        elif cp_status == "failed":
            assert baton_sheet.status == BatonSheetStatus.FAILED
        elif cp_status == "skipped":
            assert baton_sheet.status == BatonSheetStatus.SKIPPED
        elif cp_status == "pending":
            assert baton_sheet.status == BatonSheetStatus.PENDING

        # Clean up
        adapter._baton.deregister_job("recovery_test")


# =============================================================================
# 42. Recovery Attempt Count Preservation
# =============================================================================


class TestRecoveryAttemptCountPreservation:
    """Attempt counts survive recovery without loss.

    Invariant: After recover_job, the baton's SheetExecutionState has
    normal_attempts == checkpoint.attempt_count and
    completion_attempts == checkpoint.completion_attempts.
    """

    @given(
        attempt_count=st.integers(min_value=0, max_value=100),
        completion_attempts=st.integers(min_value=0, max_value=50),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_attempt_counts_preserved_through_recovery(
        self,
        attempt_count: int,
        completion_attempts: int,
    ) -> None:
        """Attempt counts from checkpoint carry into baton state exactly."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.adapter import BatonAdapter

        checkpoint = MagicMock()
        sheet_state = MagicMock(spec=SheetState)
        sheet_state.status = SheetStatus.PENDING
        sheet_state.attempt_count = attempt_count
        sheet_state.completion_attempts = completion_attempts
        checkpoint.sheets = {1: sheet_state}

        sheet = Sheet(
            num=1,
            movement=1,
            voice_count=1,
            prompt_template="test",
            workspace=Path("/tmp/test"),
            timeout_seconds=300,
            instrument_name="claude-code",
        )

        adapter = BatonAdapter()
        adapter.recover_job(
            job_id="attempt_test",
            sheets=[sheet],
            dependencies={},
            checkpoint=checkpoint,
            max_retries=attempt_count + 5,  # Ensure budget isn't exhausted
            max_completion=completion_attempts + 5,
        )

        baton_sheet = adapter._baton.get_sheet_state("attempt_test", 1)
        assert baton_sheet is not None
        assert baton_sheet.normal_attempts == attempt_count, (
            f"normal_attempts={baton_sheet.normal_attempts}, "
            f"expected checkpoint.attempt_count={attempt_count}"
        )
        assert baton_sheet.completion_attempts == completion_attempts, (
            f"completion_attempts={baton_sheet.completion_attempts}, "
            f"expected checkpoint.completion_attempts={completion_attempts}"
        )

        adapter._baton.deregister_job("attempt_test")

    def test_missing_sheet_gets_zero_attempts(self) -> None:
        """Sheets not in the checkpoint start with zero attempt counts."""
        from unittest.mock import MagicMock

        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.adapter import BatonAdapter

        checkpoint = MagicMock()
        checkpoint.sheets = {}  # Empty — sheet 1 not in checkpoint

        sheet = Sheet(
            num=1,
            movement=1,
            voice_count=1,
            prompt_template="test",
            workspace=Path("/tmp/test"),
            timeout_seconds=300,
            instrument_name="claude-code",
        )

        adapter = BatonAdapter()
        adapter.recover_job(
            job_id="missing_test",
            sheets=[sheet],
            dependencies={},
            checkpoint=checkpoint,
        )

        baton_sheet = adapter._baton.get_sheet_state("missing_test", 1)
        assert baton_sheet is not None
        assert baton_sheet.normal_attempts == 0
        assert baton_sheet.completion_attempts == 0

        adapter._baton.deregister_job("missing_test")


# =============================================================================
# 43. Recovery Dispatch Readiness
# =============================================================================


class TestRecoveryDispatchReadiness:
    """After recovery, reset sheets are dispatchable, terminal sheets are not.

    Invariant: Sheets recovered as PENDING are in _DISPATCHABLE_BATON_STATUSES.
    Sheets recovered as terminal are NOT dispatchable.
    """

    @given(
        cp_status=_CHECKPOINT_STATUSES,
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_dispatch_readiness_matches_recovery_status(
        self,
        cp_status: str,
    ) -> None:
        """Dispatchability aligns with recovery status."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.daemon.baton.state import (
            _DISPATCHABLE_BATON_STATUSES,
            _TERMINAL_BATON_STATUSES,
        )

        checkpoint = MagicMock()
        sheet_state = MagicMock(spec=SheetState)
        sheet_state.status = SheetStatus(cp_status)
        sheet_state.attempt_count = 0
        sheet_state.completion_attempts = 0
        checkpoint.sheets = {1: sheet_state}

        sheet = Sheet(
            num=1,
            movement=1,
            voice_count=1,
            prompt_template="test",
            workspace=Path("/tmp/test"),
            timeout_seconds=300,
            instrument_name="claude-code",
        )

        adapter = BatonAdapter()
        adapter.recover_job(
            job_id="dispatch_test",
            sheets=[sheet],
            dependencies={},
            checkpoint=checkpoint,
        )

        baton_sheet = adapter._baton.get_sheet_state("dispatch_test", 1)
        assert baton_sheet is not None

        if cp_status in ("pending", "in_progress"):
            assert baton_sheet.status in _DISPATCHABLE_BATON_STATUSES, (
                f"Recovered {cp_status} should be dispatchable, "
                f"got {baton_sheet.status}"
            )
        elif cp_status in ("completed", "failed", "skipped"):
            assert baton_sheet.status in _TERMINAL_BATON_STATUSES, (
                f"Recovered {cp_status} should be terminal, "
                f"got {baton_sheet.status}"
            )

        adapter._baton.deregister_job("dispatch_test")


# =============================================================================
# 44. Clone Path Mutual Exclusion
# =============================================================================


class TestClonePathMutualExclusion:
    """Different clone names produce disjoint paths.

    Invariant: ∀ name_a ≠ name_b,
    resolve_clone_paths(name_a).{socket,pid_file,state_db,log_file} ∩
    resolve_clone_paths(name_b).{socket,pid_file,state_db,log_file} = ∅
    """

    @given(
        name_a=_CLONE_NAMES,
        name_b=_CLONE_NAMES,
    )
    @settings(max_examples=100)
    def test_different_names_produce_disjoint_paths(
        self, name_a: str, name_b: str
    ) -> None:
        """No path overlap between two different clone names."""
        assume(name_a != name_b)

        from marianne.daemon.clone import resolve_clone_paths

        paths_a = resolve_clone_paths(name_a)
        paths_b = resolve_clone_paths(name_b)

        set_a = {paths_a.socket, paths_a.pid_file, paths_a.state_db, paths_a.log_file}
        set_b = {paths_b.socket, paths_b.pid_file, paths_b.state_db, paths_b.log_file}

        overlap = set_a & set_b
        assert overlap == set(), (
            f"Clone names '{name_a}' and '{name_b}' share paths: {overlap}"
        )

    @given(name=_CLONE_NAMES)
    @settings(max_examples=50)
    def test_clone_paths_internally_disjoint(self, name: str) -> None:
        """All 4 paths for a single clone are distinct from each other."""
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths(name)
        all_paths = [paths.socket, paths.pid_file, paths.state_db, paths.log_file]
        assert len(set(all_paths)) == 4, (
            f"Clone '{name}' has duplicate paths: {all_paths}"
        )

    def test_default_clone_vs_named_clone_disjoint(self) -> None:
        """Default clone (name=None) and any named clone have disjoint paths."""
        from marianne.daemon.clone import resolve_clone_paths

        default_paths = resolve_clone_paths(None)
        named_paths = resolve_clone_paths("test-clone")

        set_default = {
            default_paths.socket,
            default_paths.pid_file,
            default_paths.state_db,
            default_paths.log_file,
        }
        set_named = {
            named_paths.socket,
            named_paths.pid_file,
            named_paths.state_db,
            named_paths.log_file,
        }

        overlap = set_default & set_named
        assert overlap == set(), (
            f"Default and named clone share paths: {overlap}"
        )


# =============================================================================
# 45. Clone Config Path Isolation
# =============================================================================


class TestCloneConfigPathIsolation:
    """Clone configs never overlap with production defaults.

    Invariant: build_clone_config(name).socket.path != DaemonConfig().socket.path
    Same for pid_file and state_db_path.
    """

    @given(name=st.one_of(st.none(), _CLONE_NAMES))
    @settings(max_examples=50)
    def test_clone_socket_differs_from_production(
        self, name: str | None
    ) -> None:
        """Clone socket path is never the production default."""
        from marianne.daemon.clone import build_clone_config
        from marianne.daemon.config import DaemonConfig

        prod = DaemonConfig()
        clone = build_clone_config(name)

        assert clone.socket.path != prod.socket.path, (
            f"Clone socket matches production: {clone.socket.path}"
        )

    @given(name=st.one_of(st.none(), _CLONE_NAMES))
    @settings(max_examples=50)
    def test_clone_pid_file_differs_from_production(
        self, name: str | None
    ) -> None:
        """Clone PID file is never the production default."""
        from marianne.daemon.clone import build_clone_config
        from marianne.daemon.config import DaemonConfig

        prod = DaemonConfig()
        clone = build_clone_config(name)

        assert clone.pid_file != prod.pid_file, (
            f"Clone PID file matches production: {clone.pid_file}"
        )

    @given(name=st.one_of(st.none(), _CLONE_NAMES))
    @settings(max_examples=50)
    def test_clone_state_db_differs_from_production(
        self, name: str | None
    ) -> None:
        """Clone state DB is never the production default."""
        from marianne.daemon.clone import build_clone_config
        from marianne.daemon.config import DaemonConfig

        prod = DaemonConfig()
        clone = build_clone_config(name)

        assert clone.state_db_path != prod.state_db_path, (
            f"Clone state DB matches production: {clone.state_db_path}"
        )

    def test_clone_from_base_config_inherits_non_path_fields(self) -> None:
        """When cloning from a base config, non-path fields survive."""
        from marianne.daemon.clone import build_clone_config
        from marianne.daemon.config import DaemonConfig

        base = DaemonConfig(max_concurrent_jobs=42)
        clone = build_clone_config("inherit-test", base_config=base)

        assert clone.max_concurrent_jobs == 42, (
            "Non-path field not inherited from base config"
        )
        assert clone.socket.path != base.socket.path, (
            "Socket path should be overridden"
        )


# =============================================================================
# 46. Credential Redaction Totality
# =============================================================================


class TestCredentialRedactionTotality:
    """redact_credentials removes all credential patterns from any string.

    Invariant: ∀ text containing a credential pattern,
    redact_credentials(text) does not contain the original credential.
    """

    @given(
        credential=_ANY_CREDENTIAL,
        prefix=_SAFE_TEXT,
        suffix=_SAFE_TEXT,
    )
    @settings(max_examples=200)
    def test_credential_never_survives_redaction(
        self, credential: str, prefix: str, suffix: str
    ) -> None:
        """Any credential embedded in text is removed by redaction."""
        from marianne.utils.credential_scanner import redact_credentials

        text = f"{prefix}{credential}{suffix}"
        result = redact_credentials(text)

        assert credential not in result, (
            f"Credential survived redaction: '{credential}' still in result"
        )
        assert "[REDACTED_" in result, (
            f"No redaction marker found in result: '{result}'"
        )

    @given(credential=_ANY_CREDENTIAL)
    @settings(max_examples=100)
    def test_bare_credential_fully_redacted(self, credential: str) -> None:
        """A bare credential string is fully replaced by a redaction marker."""
        from marianne.utils.credential_scanner import redact_credentials

        result = redact_credentials(credential)
        assert credential not in result
        assert result.startswith("[REDACTED_")

    @given(
        cred_a=_ANY_CREDENTIAL,
        cred_b=_ANY_CREDENTIAL,
        separator=st.sampled_from([" ", "\n", ", ", " | ", " and "]),
    )
    @settings(max_examples=100)
    def test_multiple_credentials_all_redacted(
        self, cred_a: str, cred_b: str, separator: str
    ) -> None:
        """Multiple credentials in the same string are all redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = f"{cred_a}{separator}{cred_b}"
        result = redact_credentials(text)

        assert cred_a not in result, (
            f"First credential survived: '{cred_a}'"
        )
        assert cred_b not in result, (
            f"Second credential survived: '{cred_b}'"
        )


# =============================================================================
# 47. Credential Redaction Idempotency
# =============================================================================


class TestCredentialRedactionIdempotency:
    """redact(redact(s)) == redact(s) — redaction is idempotent.

    Invariant: Applying redaction twice produces the same result as once.
    The [REDACTED_*] markers must not themselves trigger further redaction.
    """

    @given(credential=_ANY_CREDENTIAL, prefix=_SAFE_TEXT, suffix=_SAFE_TEXT)
    @settings(max_examples=100)
    def test_redaction_is_idempotent(
        self, credential: str, prefix: str, suffix: str
    ) -> None:
        """Double redaction equals single redaction."""
        from marianne.utils.credential_scanner import redact_credentials

        text = f"{prefix}{credential}{suffix}"
        once = redact_credentials(text)
        twice = redact_credentials(once)

        assert once == twice, (
            f"Redaction not idempotent:\n"
            f"  once:  {once!r}\n"
            f"  twice: {twice!r}"
        )

    def test_redaction_marker_not_itself_redacted(self) -> None:
        """The [REDACTED_*] marker text is not mistaken for a credential."""
        from marianne.utils.credential_scanner import redact_credentials

        markers = [
            "[REDACTED_ANTHROPIC_KEY]",
            "[REDACTED_OPENAI_KEY]",
            "[REDACTED_GOOGLE_KEY]",
            "[REDACTED_AWS_KEY]",
            "[REDACTED_BEARER_TOKEN]",
            "[REDACTED_GITHUB_TOKEN]",
            "[REDACTED_SLACK_TOKEN]",
            "[REDACTED_HF_TOKEN]",
        ]
        for marker in markers:
            assert redact_credentials(marker) == marker, (
                f"Marker {marker} was itself modified by redaction"
            )


# =============================================================================
# 48. Credential Redaction Preserves Non-Credentials
# =============================================================================


class TestCredentialRedactionPreservesNonCredentials:
    """Text without credentials passes through unchanged.

    Invariant: redact_credentials(safe_text) == safe_text when
    safe_text contains no credential patterns.
    """

    @given(text=st.text(min_size=0, max_size=200, alphabet=string.ascii_letters + " .,!?"))
    @settings(max_examples=100)
    def test_safe_text_unchanged(self, text: str) -> None:
        """Pure alphabetic text is never modified."""
        from marianne.utils.credential_scanner import redact_credentials

        assert redact_credentials(text) == text

    def test_none_passthrough(self) -> None:
        """None input returns None."""
        from marianne.utils.credential_scanner import redact_credentials

        assert redact_credentials(None) is None

    def test_empty_string_passthrough(self) -> None:
        """Empty string returns empty string."""
        from marianne.utils.credential_scanner import redact_credentials

        assert redact_credentials("") == ""

    def test_non_string_passthrough(self) -> None:
        """Non-string input is returned unchanged."""
        from marianne.utils.credential_scanner import redact_credentials

        assert redact_credentials(42) == 42  # type: ignore[arg-type]
        assert redact_credentials([1, 2]) == [1, 2]  # type: ignore[arg-type]


# =============================================================================
# 49. V210 Instrument Name Check Coverage
# =============================================================================


class TestV210InstrumentNameCheckCoverage:
    """InstrumentNameCheck catches unknown instrument names.

    Invariant: For any instrument name NOT in the known set, V210
    produces a warning issue. For any name IN the known set, no issue.
    """

    def _make_config(self, instrument_name: str) -> Any:
        """Build a minimal valid JobConfig with the given instrument."""
        from marianne.core.config.job import JobConfig, PromptConfig, SheetConfig

        return JobConfig(
            name="test-job",
            sheet=SheetConfig(size=1, total_items=1),
            prompt=PromptConfig(template="test"),
            instrument=instrument_name,
        )

    def test_known_instruments_produce_no_issues(self) -> None:
        """Built-in instrument names don't produce V210 warnings."""
        from unittest.mock import patch

        from marianne.validation.checks.config import InstrumentNameCheck

        known = {"claude-code", "gemini-cli", "codex-cli", "aider", "goose", "ollama"}

        check = InstrumentNameCheck()

        for name in known:
            config = self._make_config(name)
            with patch(
                "marianne.instruments.loader.load_all_profiles",
                return_value={n: None for n in known},
            ):
                issues = check.check(config, Path("test.yaml"), "")
            instrument_issues = [
                i for i in issues
                if name in i.message
            ]
            assert instrument_issues == [], (
                f"Known instrument '{name}' produced issues: {instrument_issues}"
            )

    @given(
        name=st.text(
            min_size=3,
            max_size=20,
            alphabet=string.ascii_lowercase + "-",
        ).filter(
            lambda n: n not in {
                "claude-code", "gemini-cli", "codex-cli",
                "aider", "goose", "ollama",
            }
        ),
    )
    @settings(max_examples=50)
    def test_unknown_instruments_produce_warning(self, name: str) -> None:
        """Unknown instrument names always produce V210 warnings."""
        from unittest.mock import patch

        from marianne.validation.checks.config import InstrumentNameCheck

        known = {"claude-code", "gemini-cli", "codex-cli", "aider", "goose", "ollama"}

        check = InstrumentNameCheck()
        config = self._make_config(name)

        with patch(
            "marianne.instruments.loader.load_all_profiles",
            return_value={n: None for n in known},
        ):
            issues = check.check(config, Path("test.yaml"), "")

        assert len(issues) > 0, (
            f"Unknown instrument '{name}' produced no V210 issues"
        )
        assert any("V210" in i.check_id for i in issues), (
            f"Issues found but none with V210 ID: {issues}"
        )


# =============================================================================
# 50. Failure Propagation Preserves Terminal States
# =============================================================================


class TestFailurePropagationPreservesTerminals:
    """propagate_failure_to_dependents never overwrites terminal sheets.

    Invariant: If a dependent sheet is already COMPLETED, FAILED, or
    SKIPPED, failure propagation does not change its status.
    """

    @given(
        terminal_status=st.sampled_from(["COMPLETED", "FAILED", "SKIPPED"]),
        chain_length=st.integers(min_value=3, max_value=10),
        terminal_position=st.integers(min_value=2, max_value=9),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_terminal_sheets_not_overwritten(
        self,
        terminal_status: str,
        chain_length: int,
        terminal_position: int,
    ) -> None:
        """Terminal sheets in the dependency chain are never overwritten."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        assume(terminal_position <= chain_length)

        baton = BatonCore()
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code")
            for i in range(1, chain_length + 1)
        }
        # Linear chain: 1 → 2 → ... → N
        deps = {i: [i - 1] for i in range(2, chain_length + 1)}
        baton.register_job("j1", sheets, deps)

        # Set one sheet to terminal status before propagation
        target = sheets[terminal_position]
        target.status = BatonSheetStatus[terminal_status]

        # Fail sheet 1 — propagation should respect terminal positions
        from marianne.daemon.baton.events import SheetAttemptResult

        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            validations_passed=0,
            validations_total=0,
            validation_pass_rate=0.0,
            error_classification="AUTH_FAILURE",
        )
        _run(baton.handle_event(result))

        # The terminal sheet must keep its original status
        assert target.status == BatonSheetStatus[terminal_status], (
            f"Terminal sheet {terminal_position} ({terminal_status}) was "
            f"overwritten to {target.status} by failure propagation"
        )

    def test_completed_sheet_survives_upstream_failure(self) -> None:
        """COMPLETED sheet is never changed to FAILED by propagation."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        # 1 → 2 → 3, where 2 is already COMPLETED
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code")
            for i in range(1, 4)
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("j1", sheets, deps)

        sheets[2].status = BatonSheetStatus.COMPLETED

        # Fail sheet 1
        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            validations_passed=0,
            validations_total=0,
            validation_pass_rate=0.0,
            error_classification="AUTH_FAILURE",
        )
        _run(baton.handle_event(result))

        # Sheet 2 must still be COMPLETED
        assert sheets[2].status == BatonSheetStatus.COMPLETED
        # Sheet 3 depends on COMPLETED sheet 2, not on failed sheet 1 directly
        # (depends on 2, not on 1, so it should remain unchanged unless
        # the implementation traverses through completed nodes)


# =============================================================================
# Additional: Recovery Multi-Sheet Composition
# =============================================================================


class TestRecoveryMultiSheetComposition:
    """Recovery with mixed statuses across sheets produces correct composition.

    Invariant: Each sheet's recovery status is independent — a completed
    sheet 1 and an in_progress sheet 2 should recover as COMPLETED and
    PENDING respectively.
    """

    @given(
        statuses=st.lists(
            _CHECKPOINT_STATUSES,
            min_size=2,
            max_size=10,
        ),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_mixed_status_recovery(self, statuses: list[str]) -> None:
        """Each sheet recovers independently based on its checkpoint status."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.daemon.baton.state import BatonSheetStatus

        checkpoint = MagicMock()
        checkpoint.sheets = {}
        sheets_list = []

        for i, cp_status in enumerate(statuses, start=1):
            sheet_state = MagicMock(spec=SheetState)
            sheet_state.status = SheetStatus(cp_status)
            sheet_state.attempt_count = i  # Unique attempt count per sheet
            sheet_state.completion_attempts = 0
            checkpoint.sheets[i] = sheet_state

            sheets_list.append(
                Sheet(
                    num=i,
                    movement=1,
                    voice_count=1,
                    prompt_template=f"test sheet {i}",
                    workspace=Path("/tmp/test"),
                    timeout_seconds=300,
                    instrument_name="claude-code",
                )
            )

        adapter = BatonAdapter()
        adapter.recover_job(
            job_id="multi_test",
            sheets=sheets_list,
            dependencies={},
            checkpoint=checkpoint,
            max_retries=200,  # Well above any attempt count
        )

        for i, cp_status in enumerate(statuses, start=1):
            baton_sheet = adapter._baton.get_sheet_state("multi_test", i)
            assert baton_sheet is not None

            if cp_status == "in_progress":
                assert baton_sheet.status == BatonSheetStatus.PENDING, (
                    f"Sheet {i}: in_progress → {baton_sheet.status}, expected PENDING"
                )
            elif cp_status == "completed":
                assert baton_sheet.status == BatonSheetStatus.COMPLETED
            elif cp_status == "failed":
                assert baton_sheet.status == BatonSheetStatus.FAILED
            elif cp_status == "skipped":
                assert baton_sheet.status == BatonSheetStatus.SKIPPED
            elif cp_status == "pending":
                assert baton_sheet.status == BatonSheetStatus.PENDING

            # Attempt count preserved
            assert baton_sheet.normal_attempts == i

        adapter._baton.deregister_job("multi_test")
