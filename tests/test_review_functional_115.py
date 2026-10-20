"""Independent functional review tests for GH #115 (reviewer sheet s5-a1).

Written by the quality-review stage against the implementation of
"mozart stop non-blocking; mozart start supersedes a shutting-down
daemon". These complement the implementer's tests with gaps found in
review:

- CLI-level ``--wait=0`` (documented as "keeps the default")
- CLI-level ``--wait SECONDS`` space-separated syntax (advertised in help)
- ordering inside ``stop --wait``: safety check → signal → bounded wait
- superseding start in non-foreground mode reaches _daemonize (full
  fall-through to a normal start)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()


# ─── stop: --wait surface gaps ──────────────────────────────────────────


class TestStopWaitReviewerGaps:
    def test_stop_wait_zero_cli_keeps_non_blocking(self, tmp_path: Path):
        """``mzt stop --wait=0`` is the documented default: signal and
        return, never call wait_for_conductor_exit (GH #115)."""
        from unittest.mock import patch

        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")

        with (
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.os.kill"),
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch(
                "marianne.daemon.process.wait_for_conductor_exit"
            ) as mock_wait,
        ):
            result = runner.invoke(
                app, ["stop", "--pid-file", str(pid_file), "--wait=0"]
            )

        assert result.exit_code == 0
        mock_wait.assert_not_called()

    def test_stop_wait_space_syntax_passes_budget(self, tmp_path: Path):
        """``mzt stop --wait 7`` (space-separated, advertised in --help)
        reaches wait_for_conductor_exit with timeout=7.0."""
        from unittest.mock import patch

        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")

        with (
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.os.kill"),
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch(
                "marianne.daemon.process.wait_for_conductor_exit", return_value=True
            ) as mock_wait,
        ):
            result = runner.invoke(
                app, ["stop", "--pid-file", str(pid_file), "--wait", "7"]
            )

        assert result.exit_code == 0
        mock_wait.assert_called_once()
        assert mock_wait.call_args.kwargs.get("timeout") == 7.0

    def test_stop_wait_ordering_safety_signal_then_wait(self, tmp_path: Path):
        """Ordering inside stop --wait: safety probe runs before the
        signal, and the bounded wait runs after it — never before."""
        from unittest.mock import patch

        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")


        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")

        # Shared recorder: each mock appends its name when called.
        calls: list[str] = []

        def _record(name):
            def _hook(*args, **kwargs):
                calls.append(name)
                if name == "probe":
                    return {"running_jobs": 0, "job_ids": []}
                if name == "wait":
                    return True
                return None

            return _hook

        with (
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch(
                "marianne.daemon.process._check_running_jobs",
                side_effect=_record("probe"),
                return_value={"running_jobs": 0, "job_ids": []},
            ) as mock_probe,
            patch(
                "marianne.daemon.process.os.kill", side_effect=_record("kill")
            ) as mock_kill,
            patch(
                "marianne.daemon.process.wait_for_conductor_exit",
                side_effect=_record("wait"),
            ) as mock_wait,
        ):
            result = runner.invoke(
                app, ["stop", "--pid-file", str(pid_file), "--wait="]
            )

        assert result.exit_code == 0
        mock_probe.assert_called_once()
        mock_kill.assert_called_once()
        mock_wait.assert_called_once()
        # Safe order: safety probe → signal → bounded wait. The wait must
        # never run before the signal (that would wait on a process that
        # was never told to stop), and the probe must gate the signal (#94).
        assert calls == ["probe", "kill", "wait"]


# ─── start: supersede fall-through ─────────────────────────────────────


class TestStartSupersedeReviewerGaps:
    def test_supersede_falls_through_to_daemonize_in_background_mode(
        self, tmp_path: Path
    ):
        """After a drained conductor exits, a background (default, not
        --foreground) start proceeds through the normal path: _daemonize
        is called and DaemonProcess constructed with the config (GH #115).
        """
        from unittest.mock import patch

        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text(str(os.getpid()))

        with (
            patch("marianne.daemon.process._load_config") as mock_config,
            patch(
                "marianne.daemon.process._probe_shutting_down", return_value=True
            ),
            patch(
                "marianne.daemon.process.wait_for_conductor_exit", return_value=True
            ),
            patch("marianne.daemon.process._daemonize") as mock_daemonize,
            patch("marianne.core.logging.configure_logging"),
            patch("marianne.daemon.process.DaemonProcess") as mock_daemon,
            patch("marianne.daemon.process.asyncio.run"),
        ):
            cfg = MagicMock()
            cfg.pid_file = pid_file
            cfg.log_level = "info"
            cfg.log_file = None
            mock_config.return_value = cfg

            # No --foreground: background start must reach _daemonize.
            result = runner.invoke(app, ["start"])

        assert result.exit_code == 0
        assert "superseding" in result.output
        mock_daemonize.assert_called_once()
        mock_daemon.assert_called_once()
