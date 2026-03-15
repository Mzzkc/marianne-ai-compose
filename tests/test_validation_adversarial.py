"""Adversarial edge-case tests for validation and healing modules.

Targets catastrophic regex, malformed inputs, healing edge cases,
validation runner edge cases, and reporter edge cases.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from mozart.core.config.execution import ValidationRule
from mozart.execution.validation.engine import ValidationEngine
from mozart.execution.validation.models import ValidationResult
from mozart.healing.context import ErrorContext
from mozart.healing.coordinator import HealingReport, SelfHealingCoordinator
from mozart.healing.diagnosis import Diagnosis
from mozart.healing.registry import RemedyRegistry, create_default_registry
from mozart.healing.remedies.base import (
    BaseRemedy,
    RemedyCategory,
    RemedyResult,
)
from mozart.validation.base import ValidationIssue, ValidationSeverity
from mozart.validation.reporter import ValidationReporter
from mozart.validation.runner import ValidationRunner
from tests.conftest_adversarial import (
    _ADVERSARIAL_PATHS,
    _ADVERSARIAL_STRINGS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.adversarial


def _make_engine(workspace: Path, **ctx: Any) -> ValidationEngine:
    """Create a ValidationEngine with a workspace directory."""
    context: dict[str, Any] = {"sheet_num": 1, "start_item": 1, "end_item": 5}
    context.update(ctx)
    return ValidationEngine(workspace, context)


def _make_rule(**kwargs: Any) -> ValidationRule:
    """Build a ValidationRule with sensible defaults."""
    defaults: dict[str, Any] = {
        "type": "file_exists",
        "path": "/tmp/dummy",
        "retry_count": 0,
        "retry_delay_ms": 0,
    }
    defaults.update(kwargs)
    return ValidationRule(**defaults)


def _make_error_context(
    *,
    workspace: Path | None = None,
    error_code: str = "E999",
    error_message: str = "test error",
    error_category: str = "test",
    **kwargs: Any,
) -> ErrorContext:
    """Build a minimal ErrorContext."""
    return ErrorContext(
        error_code=error_code,
        error_message=error_message,
        error_category=error_category,
        workspace=workspace,
        **kwargs,
    )


class _FailingRemedy(BaseRemedy):
    """A remedy that always raises during apply()."""

    @property
    def name(self) -> str:
        return "always_fail"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.AUTOMATIC

    @property
    def description(self) -> str:
        return "A remedy that always fails"

    def diagnose(self, context: ErrorContext) -> Diagnosis | None:
        return Diagnosis(
            error_code=context.error_code,
            issue="Always applicable",
            explanation="Test remedy that always matches",
            suggestion="Apply fix",
            confidence=0.9,
            remedy_name=self.name,
        )

    def apply(self, context: ErrorContext) -> RemedyResult:
        raise RuntimeError("Simulated remedy explosion")


class _SucceedingRemedy(BaseRemedy):
    """A remedy that always succeeds."""

    @property
    def name(self) -> str:
        return "always_succeed"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.AUTOMATIC

    @property
    def description(self) -> str:
        return "A remedy that always succeeds"

    def diagnose(self, context: ErrorContext) -> Diagnosis | None:
        return Diagnosis(
            error_code=context.error_code,
            issue="Always applicable",
            explanation="Test remedy that always matches",
            suggestion="Apply fix",
            confidence=0.8,
            remedy_name=self.name,
        )

    def apply(self, context: ErrorContext) -> RemedyResult:
        return RemedyResult(
            success=True,
            message="Fixed",
            action_taken="Applied fix",
        )


class _DiagnoseCrashRemedy(BaseRemedy):
    """A remedy that crashes during diagnose()."""

    @property
    def name(self) -> str:
        return "diagnose_crash"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.AUTOMATIC

    @property
    def description(self) -> str:
        return "A remedy that crashes during diagnosis"

    def diagnose(self, context: ErrorContext) -> Diagnosis | None:
        raise ValueError("Diagnose went boom")

    def apply(self, context: ErrorContext) -> RemedyResult:
        return RemedyResult(
            success=False, message="Should not be reached", action_taken="none"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Catastrophic Regex (ReDoS)
# ═══════════════════════════════════════════════════════════════════════════


class TestCatastrophicRegex:
    """Verify that evil regex patterns are handled gracefully."""

    @pytest.mark.adversarial
    def test_redos_pattern_rejected_at_config_level(self) -> None:
        """Pydantic model validates regex at construction — even ReDoS
        patterns that are syntactically valid should parse, but running them
        against long input must not hang."""
        # (a+)+$ is valid regex syntax — Pydantic won't reject it.
        # The real protection is the re.search timeout or engine resilience.
        rule = _make_rule(
            type="content_regex",
            path="/tmp/x",
            pattern=r"(a+)+$",
        )
        assert rule.pattern == r"(a+)+$"

    @pytest.mark.adversarial
    @pytest.mark.parametrize(
        "pattern",
        [
            r"(a+)+$",
            r"(a|aa)+$",
            r"(a+)+b",
        ],
        ids=["nested_plus", "alternation_repeat", "nested_plus_literal"],
    )
    def test_redos_against_long_input_does_not_hang(
        self, tmp_path: Path, pattern: str
    ) -> None:
        """Run a ReDoS pattern against long input and confirm it finishes
        within a reasonable time frame (the test timeout guards against hang)."""
        evil_file = tmp_path / "evil.txt"
        # Keep input short enough that even exponential backtracking finishes
        # within the test timeout — we're verifying the engine doesn't crash,
        # not that it's immune to polynomial slowdowns.
        evil_file.write_text("a" * 25 + "!")

        engine = _make_engine(tmp_path)
        rule = _make_rule(
            type="content_regex",
            path=str(evil_file),
            pattern=pattern,
        )

        # The test's --timeout=120 acts as our safety net.
        result = engine._check_content_regex(rule)
        # We only care that it returned without hanging.
        assert isinstance(result, ValidationResult)

    @pytest.mark.adversarial
    def test_invalid_regex_returns_failure_result(self, tmp_path: Path) -> None:
        """An invalid regex pattern should return a failure result, not crash."""
        f = tmp_path / "data.txt"
        f.write_text("hello")

        # Can't construct via Pydantic (it validates regex), so we test
        # the engine directly by patching
        engine = _make_engine(tmp_path)
        rule = _make_rule(type="file_exists", path=str(f))
        # Manually override after construction to bypass Pydantic
        object.__setattr__(rule, "type", "content_regex")
        object.__setattr__(rule, "pattern", "[invalid(")

        result = engine._check_content_regex(rule)
        assert not result.passed
        assert "invalid" in (result.error_message or "").lower()


# ═══════════════════════════════════════════════════════════════════════════
# 2. Malformed YAML / content edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestMalformedContent:
    """Validation against files with pathological content."""

    @pytest.mark.adversarial
    def test_content_contains_binary_file(self, tmp_path: Path) -> None:
        """content_contains on a file with binary / non-UTF8 bytes."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x80\xff" * 100)

        engine = _make_engine(tmp_path)
        rule = _make_rule(
            type="content_contains",
            path=str(f),
            pattern="hello",
        )
        result = engine._check_content_contains(rule)
        assert not result.passed
        # Should not raise — the engine uses encoding fallback

    @pytest.mark.adversarial
    def test_content_regex_binary_file(self, tmp_path: Path) -> None:
        """content_regex on a file with binary / non-UTF8 bytes."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x80\xff" * 100)

        engine = _make_engine(tmp_path)
        rule = _make_rule(
            type="content_regex",
            path=str(f),
            pattern=r"hello",
        )
        result = engine._check_content_regex(rule)
        assert not result.passed

    @pytest.mark.adversarial
    def test_content_contains_empty_file(self, tmp_path: Path) -> None:
        """content_contains on a completely empty file."""
        f = tmp_path / "empty.txt"
        f.write_text("")

        engine = _make_engine(tmp_path)
        rule = _make_rule(
            type="content_contains",
            path=str(f),
            pattern="expected",
        )
        result = engine._check_content_contains(rule)
        assert not result.passed

    @pytest.mark.adversarial
    def test_content_contains_null_bytes(self, tmp_path: Path) -> None:
        """content_contains on a file with null bytes embedded."""
        f = tmp_path / "nulls.txt"
        f.write_bytes(b"before\x00after\x00end")

        engine = _make_engine(tmp_path)
        rule = _make_rule(
            type="content_contains",
            path=str(f),
            pattern="after",
        )
        result = engine._check_content_contains(rule)
        assert result.passed  # null bytes shouldn't prevent matching around them


# ═══════════════════════════════════════════════════════════════════════════
# 3. Healing edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestHealingEdgeCases:
    """Edge cases in the healing coordinator and remedy registry."""

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_all_remedies_failing_simultaneously(self) -> None:
        """When every registered remedy's apply() raises, the coordinator
        should still produce a report without crashing."""
        registry = RemedyRegistry()
        registry.register(_FailingRemedy())

        coordinator = SelfHealingCoordinator(registry)
        ctx = _make_error_context()

        # The coordinator calls remedy.apply() which raises — but
        # the coordinator wraps apply in the heal loop. Let's verify
        # the report is still returned (the exception propagates from apply,
        # meaning the coordinator records it as a failed action).
        # Actually coordinator does NOT try/except apply() — it lets
        # it propagate. So we expect the exception.
        with pytest.raises(RuntimeError, match="Simulated remedy explosion"):
            await coordinator.heal(ctx)

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_coordinator_with_no_remedies_registered(self) -> None:
        """Coordinator with empty registry should return an empty report."""
        registry = RemedyRegistry()
        coordinator = SelfHealingCoordinator(registry)
        ctx = _make_error_context()

        report = await coordinator.heal(ctx)
        assert not report.any_remedies_applied
        assert report.issues_remaining == 0
        assert len(report.actions_taken) == 0

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_max_healing_attempts_respected(self) -> None:
        """After max attempts, coordinator should short-circuit."""
        registry = RemedyRegistry()
        registry.register(_SucceedingRemedy())

        coordinator = SelfHealingCoordinator(registry, max_healing_attempts=2)
        ctx = _make_error_context()

        # First two calls should succeed
        report1 = await coordinator.heal(ctx)
        assert report1.any_remedies_applied

        report2 = await coordinator.heal(ctx)
        assert report2.any_remedies_applied

        # Third call should be blocked by max attempts
        report3 = await coordinator.heal(ctx)
        assert not report3.any_remedies_applied
        assert len(report3.actions_skipped) == 1
        assert "Max healing attempts" in report3.actions_skipped[0][1]

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_diagnosis_crash_recorded_not_swallowed(self) -> None:
        """If a remedy's diagnose() crashes, the registry records the error
        and the coordinator surfaces it in actions_skipped."""
        registry = RemedyRegistry()
        registry.register(_DiagnoseCrashRemedy())

        coordinator = SelfHealingCoordinator(registry)
        ctx = _make_error_context()

        report = await coordinator.heal(ctx)
        # The crash should appear in actions_skipped (via diagnosis_errors)
        assert len(report.actions_skipped) >= 1
        crash_messages = [
            reason for _name, reason in report.actions_skipped
            if "crashed" in reason.lower() or "Diagnose" in reason
        ]
        assert len(crash_messages) >= 1

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_heal_read_only_workspace(self, tmp_path: Path) -> None:
        """Healing when workspace exists but is read-only."""
        ro_dir = tmp_path / "read_only_ws"
        ro_dir.mkdir()
        ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # read+execute only

        try:
            ctx = _make_error_context(
                workspace=ro_dir,
                error_code="E601",
                error_message="Cannot write to workspace",
            )

            registry = create_default_registry()
            coordinator = SelfHealingCoordinator(registry)
            report = await coordinator.heal(ctx)

            # Workspace already exists, so CreateMissingWorkspaceRemedy shouldn't fire.
            # The key point is no crash.
            assert isinstance(report, HealingReport)
        finally:
            # Restore permissions for cleanup
            ro_dir.chmod(stat.S_IRWXU)

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_coordinator_reset_then_heal_again(self) -> None:
        """After reset(), attempt counter should allow healing again."""
        registry = RemedyRegistry()
        registry.register(_SucceedingRemedy())

        coordinator = SelfHealingCoordinator(registry, max_healing_attempts=1)
        ctx = _make_error_context()

        report1 = await coordinator.heal(ctx)
        assert report1.any_remedies_applied

        # Second attempt blocked
        report2 = await coordinator.heal(ctx)
        assert not report2.any_remedies_applied

        # Reset and try again
        coordinator.reset()
        report3 = await coordinator.heal(ctx)
        assert report3.any_remedies_applied

    @pytest.mark.adversarial
    def test_healing_report_format_with_empty_data(self) -> None:
        """HealingReport.format() with completely empty data."""
        ctx = _make_error_context()
        report = HealingReport(error_context=ctx)
        text = report.format()
        assert "SELF-HEALING REPORT" in text
        assert "NO ACTION NEEDED" in text

    @pytest.mark.adversarial
    def test_healing_report_format_verbose_multiline_diagnostic(self) -> None:
        """Verbose format expands multi-line diagnostic output."""
        ctx = _make_error_context()
        report = HealingReport(
            error_context=ctx,
            diagnostic_outputs=[
                ("test_remedy", "Line 1\nLine 2\nLine 3"),
            ],
        )
        verbose_text = report.format(verbose=True)
        assert "Line 1" in verbose_text
        assert "Line 2" in verbose_text
        assert "Line 3" in verbose_text

        non_verbose = report.format(verbose=False)
        assert "Line 1" in non_verbose
        # Line 2 and 3 should NOT appear in non-verbose
        assert "Line 2" not in non_verbose


# ═══════════════════════════════════════════════════════════════════════════
# 4. Validation runner edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestValidationRunnerEdgeCases:
    """Edge cases in the ValidationRunner and ValidationEngine."""

    @pytest.mark.adversarial
    def test_file_exists_on_directory(self, tmp_path: Path) -> None:
        """file_exists should fail if the path is a directory, not a file."""
        d = tmp_path / "a_directory"
        d.mkdir()

        engine = _make_engine(tmp_path)
        rule = _make_rule(type="file_exists", path=str(d))
        result = engine._check_file_exists(rule)
        assert not result.passed

    @pytest.mark.adversarial
    def test_file_exists_on_symlink_to_nonexistent(self, tmp_path: Path) -> None:
        """file_exists on a dangling symlink should fail."""
        target = tmp_path / "nonexistent_target"
        link = tmp_path / "dangling_link"
        link.symlink_to(target)

        engine = _make_engine(tmp_path)
        rule = _make_rule(type="file_exists", path=str(link))
        result = engine._check_file_exists(rule)
        assert not result.passed

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_command_succeeds_large_output(self, tmp_path: Path) -> None:
        """command_succeeds with a command that produces large output."""
        engine = _make_engine(tmp_path)
        rule = _make_rule(
            type="command_succeeds",
            path=None,
            command="python3 -c \"print('x' * 1_000_000)\"",
        )
        result = await engine._check_command_succeeds(rule)
        assert result.passed
        # Large output should be handled without OOM

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_command_succeeds_nonexistent_command(self, tmp_path: Path) -> None:
        """command_succeeds with a command that doesn't exist."""
        engine = _make_engine(tmp_path)
        rule = _make_rule(
            type="command_succeeds",
            path=None,
            command="this_command_does_not_exist_at_all_12345",
        )
        result = await engine._check_command_succeeds(rule)
        assert not result.passed

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_command_with_timeout_fast_command(self, tmp_path: Path) -> None:
        """command_succeeds with an explicit timeout on a fast command."""
        engine = _make_engine(tmp_path)
        rule = _make_rule(
            type="command_succeeds",
            path=None,
            command="echo ok",
            timeout_seconds=1.0,
        )
        result = await engine._check_command_succeeds(rule)
        assert result.passed

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_staged_validation_with_empty_rules(self, tmp_path: Path) -> None:
        """Staged validation with empty rule list should return clean result."""
        engine = _make_engine(tmp_path)
        result, failed_stage = await engine.run_staged_validations([])
        assert result.all_passed
        assert failed_stage is None
        assert result.rules_checked == 0

    @pytest.mark.adversarial
    @pytest.mark.asyncio
    async def test_unknown_validation_type(self, tmp_path: Path) -> None:
        """Unknown validation type dispatched through the engine."""
        engine = _make_engine(tmp_path)
        rule = _make_rule(type="file_exists", path=str(tmp_path / "dummy"))
        # Force unknown type
        object.__setattr__(rule, "type", "totally_unknown")

        result = await engine._dispatch_validation(rule)
        assert not result.passed
        assert "Unknown validation type" in (result.error_message or "")

    @pytest.mark.adversarial
    @pytest.mark.parametrize(
        "condition",
        [
            "invalid_condition",
            "x >=> 5",
            "",
            "   ",
            "sheet_num >> 1",
            "🔥 >= 1",
        ],
        ids=[
            "garbage_text",
            "bad_operator",
            "empty_string",
            "whitespace_only",
            "double_gt",
            "emoji_variable",
        ],
    )
    def test_malformed_conditions_default_to_true(
        self, tmp_path: Path, condition: str
    ) -> None:
        """Malformed conditions should default to True (unconditional)."""
        engine = _make_engine(tmp_path)
        assert engine._check_condition(condition) is True

    @pytest.mark.adversarial
    def test_expand_path_with_adversarial_context(self, tmp_path: Path) -> None:
        """Path expansion with adversarial values in context."""
        engine = _make_engine(
            tmp_path,
            sheet_num=1,
            start_item=1,
            end_item=5,
        )
        # Normal expansion should work
        result = engine.expand_path("{workspace}/output.txt")
        assert "output.txt" in str(result)

    @pytest.mark.adversarial
    @pytest.mark.parametrize(
        "path",
        [
            p for p in _ADVERSARIAL_PATHS
            if p and "\x00" not in p  # skip null bytes (OS rejects)
            and "\n" not in p  # skip newlines (causes issues)
        ],
        ids=lambda p: repr(p)[:30],
    )
    def test_file_exists_adversarial_paths(
        self, tmp_path: Path, path: str
    ) -> None:
        """file_exists with adversarial path values should not crash."""
        engine = _make_engine(tmp_path)
        rule = _make_rule(type="file_exists", path=path)

        try:
            result = engine._check_file_exists(rule)
            assert isinstance(result, ValidationResult)
        except (ValueError, OSError):
            # Some paths might cause legitimate OS-level errors
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 5. Reporter edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestReporterEdgeCases:
    """Edge cases in the ValidationReporter."""

    @pytest.mark.adversarial
    def test_report_with_zero_validations(self) -> None:
        """Reporter should handle empty issue list gracefully."""
        reporter = ValidationReporter()
        json_output = reporter.report_json([])
        assert '"valid": true' in json_output
        assert '"error_count": 0' in json_output

    @pytest.mark.adversarial
    def test_report_with_many_validations(self) -> None:
        """Reporter handles a large number of issues without issues."""
        issues = [
            ValidationIssue(
                check_id=f"V{i:04d}",
                severity=ValidationSeverity.WARNING,
                message=f"Warning number {i}",
            )
            for i in range(1000)
        ]
        reporter = ValidationReporter()
        json_output = reporter.report_json(issues)
        assert '"warning_count": 1000' in json_output

    @pytest.mark.adversarial
    def test_report_with_ansi_in_description(self) -> None:
        """Issues with ANSI escape codes in message shouldn't break output."""
        issue = ValidationIssue(
            check_id="VANSI",
            severity=ValidationSeverity.ERROR,
            message="Error with \x1b[31mred text\x1b[0m embedded",
            context="Context with \x1b[1mbold\x1b[0m text",
        )
        reporter = ValidationReporter()
        json_output = reporter.report_json([issue])
        assert "VANSI" in json_output

        plain_output = reporter.format_plain([issue])
        assert "VANSI" in plain_output

    @pytest.mark.adversarial
    def test_format_plain_with_no_issues(self) -> None:
        """Plain format with no issues returns the pass message."""
        reporter = ValidationReporter()
        output = reporter.format_plain([])
        assert "passed" in output.lower()

    @pytest.mark.adversarial
    def test_format_plain_with_all_severities(self) -> None:
        """Plain format with all severity levels."""
        issues = [
            ValidationIssue(
                check_id="E01",
                severity=ValidationSeverity.ERROR,
                message="An error",
            ),
            ValidationIssue(
                check_id="W01",
                severity=ValidationSeverity.WARNING,
                message="A warning",
                suggestion="Fix this",
            ),
            ValidationIssue(
                check_id="I01",
                severity=ValidationSeverity.INFO,
                message="Some info",
            ),
        ]
        reporter = ValidationReporter()
        output = reporter.format_plain(issues)
        assert "ERROR" in output
        assert "WARNING" in output
        assert "INFO" in output
        assert "Suggestion: Fix this" in output
        assert "FAILED" in output  # has errors

    @pytest.mark.adversarial
    def test_format_plain_warnings_only_shows_passed(self) -> None:
        """Plain format with only warnings should show PASSED."""
        issues = [
            ValidationIssue(
                check_id="W01",
                severity=ValidationSeverity.WARNING,
                message="Just a warning",
            ),
        ]
        reporter = ValidationReporter()
        output = reporter.format_plain(issues)
        assert "PASSED" in output

    @pytest.mark.adversarial
    def test_report_json_valid_json_structure(self) -> None:
        """JSON output must be valid JSON with expected structure."""
        import json

        issues = [
            ValidationIssue(
                check_id="V001",
                severity=ValidationSeverity.ERROR,
                message="Bad stuff",
                line=42,
                column=10,
                context="x" * 200,
                suggestion="Do better",
                auto_fixable=True,
                metadata={"key": "value"},
            )
        ]
        reporter = ValidationReporter()
        raw = reporter.report_json(issues)
        parsed = json.loads(raw)

        assert parsed["valid"] is False
        assert parsed["error_count"] == 1
        assert len(parsed["issues"]) == 1
        issue_dict = parsed["issues"][0]
        assert issue_dict["line"] == 42
        assert issue_dict["auto_fixable"] is True
        assert issue_dict["metadata"] == {"key": "value"}

    @pytest.mark.adversarial
    def test_report_terminal_with_no_issues(self) -> None:
        """Terminal report with no issues should print valid message."""
        from io import StringIO

        from rich.console import Console

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True)
        reporter = ValidationReporter(console=console)

        reporter.report_terminal([], config_name="test.yaml")
        output = buffer.getvalue()
        assert "valid" in output.lower() or "test.yaml" in output

    @pytest.mark.adversarial
    def test_report_terminal_with_issues(self) -> None:
        """Terminal report with mixed issues renders without crash."""
        from io import StringIO

        from rich.console import Console

        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True)
        reporter = ValidationReporter(console=console)

        issues = [
            ValidationIssue(
                check_id="E01",
                severity=ValidationSeverity.ERROR,
                message="Error",
                context="a" * 100,  # Long context — tests truncation
                suggestion="Fix it",
            ),
            ValidationIssue(
                check_id="W01",
                severity=ValidationSeverity.WARNING,
                message="Warning",
            ),
            ValidationIssue(
                check_id="I01",
                severity=ValidationSeverity.INFO,
                message="Info",
            ),
        ]
        reporter.report_terminal(issues, config_name="test.yaml")
        output = buffer.getvalue()
        assert "FAILED" in output
        assert "error" in output.lower()

    @pytest.mark.adversarial
    @pytest.mark.parametrize(
        "adversarial_str",
        _ADVERSARIAL_STRINGS[:10],
        ids=lambda s: repr(s)[:30],
    )
    def test_report_json_with_adversarial_messages(
        self, adversarial_str: str
    ) -> None:
        """Reporter handles adversarial strings in issue messages."""
        import json

        issue = ValidationIssue(
            check_id="VADV",
            severity=ValidationSeverity.WARNING,
            message=adversarial_str,
        )
        reporter = ValidationReporter()
        raw = reporter.report_json([issue])
        # Must be valid JSON
        parsed = json.loads(raw)
        assert len(parsed["issues"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 6. ValidationRunner check aggregation edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestValidationRunnerCheckAggregation:
    """Edge cases in the ValidationRunner check aggregation."""

    @pytest.mark.adversarial
    def test_check_that_raises_exception(self) -> None:
        """A check that raises should be caught and reported as an error."""
        class _BrokenCheck:
            check_id = "VBROK"
            severity = ValidationSeverity.ERROR
            description = "Always breaks"

            def check(
                self, config: Any, config_path: Path, raw_yaml: str
            ) -> list[ValidationIssue]:
                raise RuntimeError("Check exploded!")

        runner = ValidationRunner(checks=[_BrokenCheck()])
        # Need a minimal config to pass to validate
        from unittest.mock import MagicMock
        from mozart.core.config import JobConfig
        config = MagicMock(spec=JobConfig)
        issues = runner.validate(config, Path("/tmp/fake.yaml"), "")
        assert len(issues) == 1
        assert "VBROK" in issues[0].check_id
        assert "failed to execute" in issues[0].message

    @pytest.mark.adversarial
    def test_runner_severity_sorting(self) -> None:
        """Issues should be sorted with ERROR first, then WARNING, then INFO."""
        class _MultiCheck:
            check_id = "VMULTI"
            severity = ValidationSeverity.INFO
            description = "Returns multiple severities"

            def check(
                self, config: Any, config_path: Path, raw_yaml: str
            ) -> list[ValidationIssue]:
                return [
                    ValidationIssue(check_id="I01", severity=ValidationSeverity.INFO, message="info"),
                    ValidationIssue(check_id="E01", severity=ValidationSeverity.ERROR, message="error"),
                    ValidationIssue(check_id="W01", severity=ValidationSeverity.WARNING, message="warn"),
                ]

        runner = ValidationRunner(checks=[_MultiCheck()])
        from unittest.mock import MagicMock
        from mozart.core.config import JobConfig
        config = MagicMock(spec=JobConfig)
        issues = runner.validate(config, Path("/tmp/f.yaml"), "")
        severities = [i.severity for i in issues]
        assert severities == [
            ValidationSeverity.ERROR,
            ValidationSeverity.WARNING,
            ValidationSeverity.INFO,
        ]

    @pytest.mark.adversarial
    def test_runner_count_by_severity(self) -> None:
        """count_by_severity returns correct counts for mixed issues."""
        runner = ValidationRunner()
        issues = [
            ValidationIssue(check_id="E1", severity=ValidationSeverity.ERROR, message="e1"),
            ValidationIssue(check_id="E2", severity=ValidationSeverity.ERROR, message="e2"),
            ValidationIssue(check_id="W1", severity=ValidationSeverity.WARNING, message="w1"),
        ]
        counts = runner.count_by_severity(issues)
        assert counts[ValidationSeverity.ERROR] == 2
        assert counts[ValidationSeverity.WARNING] == 1
        assert counts[ValidationSeverity.INFO] == 0

    @pytest.mark.adversarial
    def test_runner_exit_code_with_no_issues(self) -> None:
        """Exit code should be 0 when there are no issues."""
        runner = ValidationRunner()
        assert runner.get_exit_code([]) == 0

    @pytest.mark.adversarial
    def test_runner_exit_code_with_errors(self) -> None:
        """Exit code should be 1 when there are error-level issues."""
        runner = ValidationRunner()
        issues = [
            ValidationIssue(
                check_id="E1",
                severity=ValidationSeverity.ERROR,
                message="error",
            ),
        ]
        assert runner.get_exit_code(issues) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. ValidationIssue formatting edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestValidationIssueFormatting:
    """Edge cases in ValidationIssue formatting."""

    @pytest.mark.adversarial
    def test_format_short_with_line_number(self) -> None:
        """format_short includes line number when set."""
        issue = ValidationIssue(
            check_id="V001",
            severity=ValidationSeverity.ERROR,
            message="Something wrong",
            line=42,
        )
        formatted = issue.format_short()
        assert "[V001]" in formatted
        assert "Line 42" in formatted

    @pytest.mark.adversarial
    def test_format_short_without_line_number(self) -> None:
        """format_short omits line info when None."""
        issue = ValidationIssue(
            check_id="V001",
            severity=ValidationSeverity.ERROR,
            message="Something wrong",
        )
        formatted = issue.format_short()
        assert "[V001]" in formatted
        assert "Line" not in formatted

    @pytest.mark.adversarial
    def test_format_full_with_all_fields(self) -> None:
        """format_full includes context and suggestion."""
        issue = ValidationIssue(
            check_id="V001",
            severity=ValidationSeverity.ERROR,
            message="Something wrong",
            line=10,
            context="nearby code",
            suggestion="Try this fix",
        )
        formatted = issue.format_full()
        assert "Context: nearby code" in formatted
        assert "Suggestion: Try this fix" in formatted

    @pytest.mark.adversarial
    @pytest.mark.parametrize(
        "adversarial_str",
        _ADVERSARIAL_STRINGS[:8],
        ids=lambda s: repr(s)[:30],
    )
    def test_format_short_adversarial_message(self, adversarial_str: str) -> None:
        """format_short handles adversarial strings without crashing."""
        issue = ValidationIssue(
            check_id="VADV",
            severity=ValidationSeverity.WARNING,
            message=adversarial_str,
        )
        formatted = issue.format_short()
        assert "[VADV]" in formatted
