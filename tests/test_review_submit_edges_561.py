"""Further error-path checks found during functional review of GH #561."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marianne.cli.commands import agents_cmd


def test_live_pid_with_refused_socket_is_busy_in_agents_submit(tmp_path: Path) -> None:
    score = tmp_path / "score.yaml"
    score.write_text("name: review\nsheet: {size: 1, total_items: 1}\nprompt: {template: hi}\n")
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=False)),
        patch("marianne.cli.helpers.conductor_socket_present", return_value=True),
        patch("marianne.cli.helpers.live_conductor_pid", return_value=12345),
    ):
        result = agents_cmd._submit_score(score, "review-job")
    assert not result.ok
    assert "busy" in result.hint and "12345" in result.hint
    assert "mzt start" not in result.hint


@pytest.mark.parametrize("invalid", ["name: [", "name: review\nsheet: {size: 1}\n"])
def test_invalid_score_reports_validation_error_even_when_conductor_is_down(
    tmp_path: Path, invalid: str
) -> None:
    score = tmp_path / "score.yaml"
    score.write_text(invalid)
    available = AsyncMock(return_value=False)
    with patch("marianne.daemon.detect.is_daemon_available", new=available):
        result = agents_cmd._submit_score(score, "review-job")
    assert not result.ok and result.hint
    assert "conductor is not running" not in result.hint
    available.assert_not_awaited()
