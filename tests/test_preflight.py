"""Tests for mozart.execution.preflight module."""

from pathlib import Path

from marianne.core.tokens import estimate_tokens
from marianne.execution.preflight import (
    LINE_WARNING_THRESHOLD,
    PreflightChecker,
    PreflightResult,
    PromptMetrics,
    run_preflight_check,
)


class TestPromptMetrics:
    """Tests for PromptMetrics dataclass."""

    def test_from_prompt_basic(self):
        """Test basic prompt metrics extraction."""
        prompt = "Hello, world!\nThis is a test prompt."
        metrics = PromptMetrics.from_prompt(prompt)

        assert metrics.character_count == len(prompt)
        assert metrics.line_count == 2
        assert metrics.estimated_tokens > 0
        assert metrics.estimated_tokens < metrics.character_count
        assert metrics.estimated_tokens == estimate_tokens(prompt)
        assert metrics.word_count == 7
        assert metrics.has_file_references is False
        assert metrics.referenced_paths == []

    def test_from_prompt_with_file_paths(self):
        """Test extraction of file paths from prompt."""
        prompt = """
        Please read the file /home/user/data.txt and process it.
        Save output to ./output/result.json
        Check the config at "../config/settings.yaml"
        """
        metrics = PromptMetrics.from_prompt(prompt)

        assert metrics.has_file_references is True
        # Should find absolute, relative, and quoted paths
        assert any("/home/user/data.txt" in p for p in metrics.referenced_paths)
        assert any("./output/result.json" in p or "output/result.json" in p
                   for p in metrics.referenced_paths)

    def test_from_prompt_with_template_paths(self):
        """Test extraction of template paths like {workspace}/file.txt."""
        prompt = """
        Process files in {workspace}/input/
        Save to {workspace}/output/sheet-1.txt
        """
        metrics = PromptMetrics.from_prompt(prompt)

        # Template paths should be captured
        assert metrics.has_file_references is True

    def test_empty_prompt(self):
        """Test metrics for empty prompt."""
        metrics = PromptMetrics.from_prompt("")

        assert metrics.character_count == 0
        assert metrics.line_count == 1  # Empty string is 1 line
        assert metrics.estimated_tokens == 0
        assert metrics.word_count == 0
        assert metrics.has_file_references is False

    def test_large_prompt_token_estimation(self):
        """Test token estimation for large prompts."""
        # Create a 200K character prompt
        prompt = "x" * 200_000
        metrics = PromptMetrics.from_prompt(prompt)

        assert metrics.character_count == 200_000
        assert metrics.estimated_tokens == estimate_tokens(prompt)
        # Estimated tokens should be in a reasonable range for 200K chars
        assert 40_000 < metrics.estimated_tokens < 80_000

    def test_multiline_prompt(self):
        """Test line counting for multiline prompts."""
        prompt = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
        metrics = PromptMetrics.from_prompt(prompt)

        assert metrics.line_count == 5

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metrics = PromptMetrics(
            character_count=100,
            estimated_tokens=25,
            line_count=5,
            has_file_references=True,
            referenced_paths=["/path/to/file.txt"],
            word_count=20,
        )
        data = metrics.to_dict()

        assert data["character_count"] == 100
        assert data["estimated_tokens"] == 25
        assert data["line_count"] == 5
        assert data["word_count"] == 20
        assert data["has_file_references"] is True
        assert data["referenced_paths"] == ["/path/to/file.txt"]


class TestPreflightResult:
    """Tests for PreflightResult dataclass."""

    def test_has_errors(self):
        """Test has_errors property."""
        metrics = PromptMetrics.from_prompt("test")

        # No errors
        result = PreflightResult(prompt_metrics=metrics)
        assert result.has_errors is False

        # With errors
        result = PreflightResult(
            prompt_metrics=metrics,
            errors=["Fatal error occurred"],
        )
        assert result.has_errors is True

    def test_has_warnings(self):
        """Test has_warnings property."""
        metrics = PromptMetrics.from_prompt("test")

        # No warnings
        result = PreflightResult(prompt_metrics=metrics)
        assert result.has_warnings is False

        # With warnings
        result = PreflightResult(
            prompt_metrics=metrics,
            warnings=["Large prompt detected"],
        )
        assert result.has_warnings is True

    def test_can_proceed(self):
        """Test can_proceed property."""
        metrics = PromptMetrics.from_prompt("test")

        # Can proceed without errors
        result = PreflightResult(
            prompt_metrics=metrics,
            warnings=["Some warning"],
        )
        assert result.can_proceed is True

        # Cannot proceed with errors
        result = PreflightResult(
            prompt_metrics=metrics,
            errors=["Fatal error"],
        )
        assert result.can_proceed is False

    def test_inaccessible_paths(self):
        """Test inaccessible_paths property."""
        metrics = PromptMetrics.from_prompt("test")
        result = PreflightResult(
            prompt_metrics=metrics,
            paths_accessible={
                "/exists/file.txt": True,
                "/missing/file.txt": False,
                "/another/missing.txt": False,
            },
        )

        inaccessible = result.inaccessible_paths
        assert len(inaccessible) == 2
        assert "/missing/file.txt" in inaccessible
        assert "/another/missing.txt" in inaccessible

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metrics = PromptMetrics.from_prompt("test prompt")
        result = PreflightResult(
            prompt_metrics=metrics,
            warnings=["Warning 1"],
            errors=["Error 1"],
            paths_accessible={"/path.txt": True},
            working_directory_valid=True,
        )
        data = result.to_dict()

        assert "prompt_metrics" in data
        assert data["warnings"] == ["Warning 1"]
        assert data["errors"] == ["Error 1"]
        assert data["paths_accessible"] == {"/path.txt": True}
        assert data["working_directory_valid"] is True
        assert data["can_proceed"] is False  # Has errors


class TestPreflightChecker:
    """Tests for PreflightChecker class."""

    def test_check_normal_prompt(self, temp_workspace: Path):
        """Test preflight check on a normal prompt."""
        checker = PreflightChecker(workspace=temp_workspace)
        result = checker.check("Process some data and save results.")

        assert result.prompt_metrics.character_count > 0
        assert result.has_errors is False
        assert result.has_warnings is False
        assert result.can_proceed is True

    def test_check_large_prompt_warning(self, temp_workspace: Path):
        """Test warning for prompts exceeding token threshold."""
        # Create prompt unambiguously above warning threshold (50K tokens)
        large_prompt = "x" * 250_000

        checker = PreflightChecker(workspace=temp_workspace)
        result = checker.check(large_prompt)

        assert result.has_warnings is True
        assert result.has_errors is False
        assert any("Large prompt" in w for w in result.warnings)
        assert result.can_proceed is True

    def test_check_huge_prompt_error(self, temp_workspace: Path):
        """Test error for prompts exceeding error threshold."""
        # Create prompt unambiguously above error threshold (150K tokens)
        huge_prompt = "x" * 600_000

        checker = PreflightChecker(workspace=temp_workspace)
        result = checker.check(huge_prompt)

        assert result.has_errors is True
        assert any("exceeds token limit" in e for e in result.errors)
        assert result.can_proceed is False

    def test_check_many_lines_warning(self, temp_workspace: Path):
        """Test warning for prompts with many lines."""
        # Create prompt with many lines
        many_lines_prompt = "\n".join(["Line"] * (LINE_WARNING_THRESHOLD + 100))

        checker = PreflightChecker(workspace=temp_workspace)
        result = checker.check(many_lines_prompt)

        assert result.has_warnings is True
        assert any("many lines" in w for w in result.warnings)

    def test_check_invalid_working_directory(self, temp_workspace: Path):
        """Test error for invalid working directory."""
        nonexistent = temp_workspace / "nonexistent_directory"

        checker = PreflightChecker(
            workspace=temp_workspace,
            working_directory=nonexistent,
        )
        result = checker.check("Test prompt")

        assert result.has_errors is True
        assert result.working_directory_valid is False
        assert any("Working directory" in e for e in result.errors)

    def test_check_missing_file_references(self, temp_workspace: Path):
        """Test warning for referenced files that don't exist."""
        # Create prompt referencing a non-existent file
        prompt = f"Please process {temp_workspace}/nonexistent.txt"

        checker = PreflightChecker(workspace=temp_workspace)
        result = checker.check(prompt)

        # Should be a warning, not an error (file might be created)
        assert result.has_warnings is True
        assert result.has_errors is False
        assert any("referenced paths do not exist" in w for w in result.warnings)

    def test_check_existing_file_references(self, temp_workspace: Path):
        """Test that existing file references don't trigger warnings."""
        # Create a file
        test_file = temp_workspace / "exists.txt"
        test_file.write_text("content")

        prompt = f"Please process {test_file}"

        checker = PreflightChecker(workspace=temp_workspace)
        result = checker.check(prompt)

        # No warning about missing files
        assert not any("do not exist" in w for w in result.warnings)
        # File should be marked as accessible
        accessible = result.paths_accessible
        assert any(accessible.get(str(test_file), False) for _ in accessible)

    def test_check_with_sheet_context(self, temp_workspace: Path):
        """Test preflight check with sheet context for template expansion."""
        # Create file matching template
        (temp_workspace / "sheet-1-output.txt").write_text("output")

        prompt = "Process {workspace}/sheet-{sheet_num}-output.txt"
        sheet_context = {
            "sheet_num": 1,
            "workspace": str(temp_workspace),
        }

        checker = PreflightChecker(workspace=temp_workspace)
        result = checker.check(prompt, sheet_context)

        assert result.can_proceed is True

    def test_custom_thresholds(self, temp_workspace: Path):
        """Test custom token thresholds."""
        # Use very low thresholds for testing
        checker = PreflightChecker(
            workspace=temp_workspace,
            token_warning_threshold=10,  # ~35 chars at 3.5 ratio
            token_error_threshold=20,  # ~70 chars at 3.5 ratio
        )

        # Should trigger warning but not error
        result = checker.check("x" * 50)  # ~12 tokens
        assert result.has_warnings is True
        assert result.has_errors is False

        # Should trigger error
        result = checker.check("x" * 100)  # ~25 tokens
        assert result.has_errors is True


class TestRunPreflightCheck:
    """Tests for the convenience function run_preflight_check."""

    def test_basic_usage(self, temp_workspace: Path):
        """Test basic usage of convenience function."""
        result = run_preflight_check(
            prompt="Simple test prompt",
            workspace=temp_workspace,
        )

        assert isinstance(result, PreflightResult)
        assert result.can_proceed is True
        assert result.prompt_metrics.character_count > 0

    def test_with_working_directory(self, temp_workspace: Path):
        """Test with explicit working directory."""
        subdir = temp_workspace / "subdir"
        subdir.mkdir()

        result = run_preflight_check(
            prompt="Test",
            workspace=temp_workspace,
            working_directory=subdir,
        )

        assert result.working_directory_valid is True

    def test_with_sheet_context(self, temp_workspace: Path):
        """Test with sheet context."""
        result = run_preflight_check(
            prompt="Process sheet {sheet_num}",
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1},
        )

        assert result.can_proceed is True


class TestFilePathExtraction:
    """Tests for file path extraction edge cases."""

    def test_unix_absolute_paths(self):
        """Test extraction of Unix absolute paths."""
        prompt = "Read /etc/config.json and /var/log/app.log"
        metrics = PromptMetrics.from_prompt(prompt)

        assert "/etc/config.json" in metrics.referenced_paths
        assert "/var/log/app.log" in metrics.referenced_paths

    def test_relative_paths(self):
        """Test extraction of relative paths."""
        prompt = "Check ./config/local.yaml and ../shared/settings.json"
        metrics = PromptMetrics.from_prompt(prompt)

        paths = metrics.referenced_paths
        assert any("./config/local.yaml" in p or "config/local.yaml" in p for p in paths)
        assert any("../shared/settings.json" in p or "shared/settings.json" in p for p in paths)

    def test_quoted_paths(self):
        """Test extraction of quoted paths."""
        prompt = '''
        Open "path/to/file.txt" and 'another/path.json'
        '''
        metrics = PromptMetrics.from_prompt(prompt)

        assert metrics.has_file_references is True

    def test_no_false_positives(self):
        """Test that common non-paths aren't extracted."""
        prompt = """
        Version 1.0 and 2.0 are supported.
        The regex /s is a space pattern.
        Use HTTP://example.com for the API.
        """
        metrics = PromptMetrics.from_prompt(prompt)

        # Should not include version numbers or regex escapes
        paths = metrics.referenced_paths
        assert "1.0" not in paths
        assert "2.0" not in paths
        assert "/s" not in paths

    def test_deduplication(self):
        """Test that duplicate paths are deduplicated."""
        prompt = """
        Read /path/to/file.txt
        Process /path/to/file.txt again
        Save to /path/to/file.txt
        """
        metrics = PromptMetrics.from_prompt(prompt)

        # Count occurrences of the path
        count = sum(1 for p in metrics.referenced_paths if p == "/path/to/file.txt")
        assert count == 1  # Should appear only once

    def test_paths_are_sorted(self):
        """Test that extracted paths are sorted."""
        prompt = "Check /z/file.txt and /a/file.txt and /m/file.txt"
        metrics = PromptMetrics.from_prompt(prompt)

        assert metrics.referenced_paths == sorted(metrics.referenced_paths)

    def test_quoted_path_regex_excludes_newlines(self):
        """Test that quoted path regex doesn't match across newlines.

        Regression test: injected markdown content containing backtick-quoted
        code like `.replace("-", " ").title()` was matched across multiple
        lines by the quoted-path regex, producing 800+ char false-positive
        "paths" that caused OSError ENAMETOOLONG when checked.
        """
        # Content from a real collective.md that triggered the bug
        prompt = """
- **Roster name fallback:** `.replace("-", " ").title()` heuristic
- **Growth cap formula:** `effective_delta = raw_delta * (1.0 - current_skill) * learning_rate`
- **Two-layer growth:** Aggregation applies diminishing returns
"""
        metrics = PromptMetrics.from_prompt(prompt)

        # No extracted path should be longer than 500 chars
        for path in metrics.referenced_paths:
            assert len(path) <= 500, f"Extracted path too long ({len(path)} chars): {path[:100]}..."

        # The multi-line content should NOT be extracted as a path
        assert not any(").title()" in p for p in metrics.referenced_paths)

    def test_very_long_strings_rejected_as_paths(self):
        """Test that _is_plausible_path rejects very long strings."""
        long_str = "a/b.txt" + " extra content " * 50
        from marianne.execution.preflight import _is_plausible_path

        assert not _is_plausible_path(long_str)


class TestCheckPathsOSError:
    """Tests for _check_paths handling of filesystem errors."""

    def test_path_exists_oserror_handled(self, temp_workspace: Path):
        """Test that OSError from path.exists() is caught gracefully.

        Regression test: when regex extracted a 900-char false-positive
        "path" from injected content, prepending workspace produced a path
        with a component exceeding 255 bytes. path.exists() raised
        OSError ENAMETOOLONG instead of returning False, crashing the job.
        """
        checker = PreflightChecker(workspace=temp_workspace)

        # Simulate a path with a component exceeding filesystem limits
        long_component = "x" * 300
        prompt = f'Read "{long_component}/file.txt" now'
        result = checker.check(prompt)

        # Should not crash — inaccessible paths should just be warnings
        assert result.can_proceed is True

    def test_bare_braces_in_injected_content_no_crash(self, temp_workspace: Path):
        """Bare {} from non-Python format strings (e.g., Rust) must not crash.

        Regression test: when cadenza files inject Rust test code containing
        format!("{}") or assert!("Graph has {} symbols"), the preflight
        path checker extracts path-like strings containing bare {}. Calling
        .format(**context) on these raises IndexError, not KeyError/ValueError.
        """
        checker = PreflightChecker(workspace=temp_workspace)

        # Simulate prompt with Rust assert! containing {} inside a path-like string
        prompt = (
            'Read "src/tests.rs" for context.\n'
            '"dep_graph edge {} has weight 0. '
            'Expected path/to/mod.rs not {}/mod.rs"\n'
        )
        result = checker.check(prompt)

        # Should not crash — unparseable templates are silently skipped
        assert result.can_proceed is True
