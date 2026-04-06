"""Tests for user-defined prompt.variables in validation paths.

Score authors should be able to use {my_var} in validation paths
when those variables are defined in prompt.variables. This works
at three levels:
1. Preview (mozart validate) — rendering.py
2. Runtime (legacy runner) — already works via sheet.py:408
3. Runtime (baton) — already works via Sheet.template_variables()

This test suite covers the preview path (rendering.py) since that's
the gap identified in the composer directive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marianne.validation.rendering import _expand_path, generate_preview
from marianne.core.config import JobConfig


class TestExpandPathWithUserVariables:
    """_expand_path should expand user-defined variables."""

    def test_user_variable_expands(self) -> None:
        """User-defined variable {output_dir} expands in path."""
        context = {
            "workspace": "/tmp/ws",
            "sheet_num": "1",
            "output_dir": "results/phase1",
        }
        result = _expand_path("{workspace}/{output_dir}/output.md", context)
        assert result == "/tmp/ws/results/phase1/output.md"

    def test_mixed_builtin_and_user_vars(self) -> None:
        """Both built-in and user variables expand in same path."""
        context = {
            "workspace": "/tmp/ws",
            "sheet_num": "3",
            "project_name": "acme",
            "env": "staging",
        }
        result = _expand_path(
            "{workspace}/{project_name}/{env}/sheet-{sheet_num}.txt",
            context,
        )
        assert result == "/tmp/ws/acme/staging/sheet-3.txt"

    def test_unknown_variable_left_intact(self) -> None:
        """Variables not in context are left as-is (not KeyError)."""
        context = {"workspace": "/tmp/ws"}
        result = _expand_path("{workspace}/{unknown_var}/file.txt", context)
        assert result == "/tmp/ws/{unknown_var}/file.txt"

    def test_user_variable_with_numeric_value(self) -> None:
        """Numeric user variables are coerced to string."""
        context = {
            "workspace": "/tmp/ws",
            "batch_size": "100",
        }
        result = _expand_path("{workspace}/batch-{batch_size}.csv", context)
        assert result == "/tmp/ws/batch-100.csv"


class TestPreviewIncludesUserVariables:
    """generate_preview should include prompt.variables in path expansion."""

    def _make_config(
        self,
        user_vars: dict[str, Any] | None = None,
        validation_path: str = "{workspace}/output.md",
    ) -> JobConfig:
        """Create a minimal JobConfig with user variables and a validation."""
        return JobConfig.from_yaml_string(f"""
name: test-user-vars
prompt:
  template: "Do work"
  variables:
    {self._format_vars(user_vars or {})}
sheet:
  total_items: 1
  size: 1
workspace: /tmp/test-ws
validations:
  - type: file_exists
    path: "{validation_path}"
    description: "Check output"
""")

    @staticmethod
    def _format_vars(vars_dict: dict[str, Any]) -> str:
        """Format variables dict as YAML."""
        if not vars_dict:
            return "{}"
        lines = []
        for k, v in vars_dict.items():
            if isinstance(v, str):
                lines.append(f'{k}: "{v}"')
            else:
                lines.append(f"{k}: {v}")
        return "\n    ".join(lines)

    def test_user_var_in_validation_path_expands(self) -> None:
        """User-defined variable in validation path is expanded during preview."""
        config = JobConfig.from_yaml_string("""
name: test-user-vars
prompt:
  template: "Do work"
  variables:
    output_dir: "results"
sheet:
  total_items: 1
  size: 1
workspace: /tmp/test-ws
validations:
  - type: file_exists
    path: "{workspace}/{output_dir}/output.md"
    description: "Check output"
""")
        preview = generate_preview(config, Path("/tmp/test.yaml"))
        assert len(preview.sheets) == 1
        ev = preview.sheets[0].expanded_validations[0]
        assert ev.expanded_path is not None
        assert "results" in ev.expanded_path
        assert "{output_dir}" not in ev.expanded_path

    def test_builtin_vars_override_user_vars(self) -> None:
        """Built-in variables (workspace, sheet_num) take precedence."""
        config = JobConfig.from_yaml_string("""
name: test-override
prompt:
  template: "Do work"
  variables:
    workspace: "should-be-overridden"
    sheet_num: "999"
sheet:
  total_items: 1
  size: 1
workspace: /tmp/real-ws
validations:
  - type: file_exists
    path: "{workspace}/sheet-{sheet_num}.md"
    description: "Check"
""")
        preview = generate_preview(config, Path("/tmp/test.yaml"))
        ev = preview.sheets[0].expanded_validations[0]
        assert ev.expanded_path is not None
        # Built-in workspace should win
        assert "/tmp/real-ws" in ev.expanded_path
        assert "should-be-overridden" not in ev.expanded_path
        # Built-in sheet_num should win
        assert "sheet-1" in ev.expanded_path
        assert "sheet-999" not in ev.expanded_path

    def test_user_var_in_condition_context(self) -> None:
        """User variables that are integers work in conditions (future)."""
        # This verifies user vars don't break condition evaluation
        config = JobConfig.from_yaml_string("""
name: test-condition
prompt:
  template: "Do work"
  variables:
    threshold: 5
sheet:
  total_items: 3
  size: 1
workspace: /tmp/test-ws
validations:
  - type: file_exists
    path: "{workspace}/output.md"
    description: "Check output"
    condition: "sheet_num >= 1"
""")
        preview = generate_preview(config, Path("/tmp/test.yaml"))
        assert len(preview.sheets) == 3
        # All sheets should have applicable=True
        for sp in preview.sheets:
            assert sp.expanded_validations[0].applicable

    def test_no_user_vars_still_works(self) -> None:
        """Preview works normally when no user variables are defined."""
        config = JobConfig.from_yaml_string("""
name: test-no-vars
prompt:
  template: "Do work"
sheet:
  total_items: 1
  size: 1
workspace: /tmp/test-ws
validations:
  - type: file_exists
    path: "{workspace}/output.md"
    description: "Check output"
""")
        preview = generate_preview(config, Path("/tmp/test.yaml"))
        ev = preview.sheets[0].expanded_validations[0]
        assert ev.expanded_path == "/tmp/test-ws/output.md"
