"""Tests for resume command error message quality.

Error messages without hints are unhelpful — they tell the user what went
wrong but not what to do about it. The resume command has error paths in
_reconstruct_config() that call output_error() without constructive hints:

1. "Error loading config file: ..." — bad config YAML on resume --config
2. "Error reloading config: ..." — stored config path exists but invalid

Each should include a hint so the user knows what to try next.

Lens M2: Add hints to hintless output_error() calls in resume.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
import yaml

from marianne.cli.commands.resume import _reconstruct_config
from marianne.core.checkpoint import CheckpointState, JobStatus


def _make_state(
    *,
    config_path: str | None = None,
    config_snapshot: dict | None = None,
) -> CheckpointState:
    """Build minimal CheckpointState for _reconstruct_config tests."""
    now = datetime.now(UTC)
    return CheckpointState(
        job_id="test-resume",
        job_name="test-resume",
        total_sheets=1,
        last_completed_sheet=0,
        status=JobStatus.PAUSED,
        created_at=now,
        started_at=now,
        updated_at=now,
        config_path=config_path,
        config_snapshot=config_snapshot or {
            "name": "test-resume",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
        },
    )


class TestResumeConfigLoadErrorHints:
    """output_error in _reconstruct_config must include hints."""

    def test_bad_config_file_includes_hint(self, tmp_path: Path) -> None:
        """When --config points to a file with invalid YAML structure,
        the error should include a hint about validation."""
        bad_config = tmp_path / "broken.yaml"
        bad_config.write_text("name: test\ninvalid: [unclosed")

        state = _make_state()

        with (
            patch(
                "marianne.cli.commands.resume.output_error"
            ) as mock_error,
            pytest.raises(typer.Exit),
        ):
            _reconstruct_config(state, bad_config, no_reload=False)

        mock_error.assert_called_once()
        kwargs = mock_error.call_args[1] if mock_error.call_args[1] else {}
        assert "hints" in kwargs, (
            "output_error called without hints — user gets no guidance"
        )
        hints = kwargs["hints"]
        assert len(hints) > 0
        assert any(
            "validate" in h.lower() or "yaml" in h.lower()
            for h in hints
        ), f"Hints don't mention validation or YAML: {hints}"

    def test_incomplete_config_includes_hint(self, tmp_path: Path) -> None:
        """When config is valid YAML but missing required fields,
        hint should suggest mzt validate."""
        incomplete = tmp_path / "incomplete.yaml"
        incomplete.write_text(yaml.dump({"name": "test"}))

        state = _make_state()

        with (
            patch(
                "marianne.cli.commands.resume.output_error"
            ) as mock_error,
            pytest.raises(typer.Exit),
        ):
            _reconstruct_config(state, incomplete, no_reload=False)

        mock_error.assert_called_once()
        kwargs = mock_error.call_args[1] if mock_error.call_args[1] else {}
        assert "hints" in kwargs

    def test_stored_config_reload_includes_hint(self, tmp_path: Path) -> None:
        """When the stored config path exists but is invalid,
        the reload error should include hints."""
        bad_stored = tmp_path / "stored-score.yaml"
        bad_stored.write_text("completely: [invalid: yaml: structure")

        state = _make_state(config_path=str(bad_stored))

        with (
            patch(
                "marianne.cli.commands.resume.output_error"
            ) as mock_error,
            pytest.raises(typer.Exit),
        ):
            _reconstruct_config(state, config_file=None, no_reload=False)

        mock_error.assert_called_once()
        kwargs = mock_error.call_args[1] if mock_error.call_args[1] else {}
        assert "hints" in kwargs, (
            "Config reload error has no hints — user doesn't know what to do"
        )
