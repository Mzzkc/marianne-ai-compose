"""#265: IPC protocol version negotiation.

The JSON-RPC layer had ``jsonrpc: "2.0"`` but no Marianne-specific protocol
version, so a wire-format change could not be detected at runtime — CLI and
conductor had to be upgraded atomically with no diagnostic on mismatch.

Mechanism (additive, no new IPC method):

- ``PROTOCOL_VERSION`` constant in ``daemon/ipc/protocol.py`` is the single
  source of truth.
- The conductor advertises it in ``daemon.health`` (liveness — the handshake
  every ``mzt status`` already performs) and in ``daemon.status``
  (the typed ``DaemonStatus`` consumed by dashboard/TUI).
- ``DaemonStatus.protocol_version`` defaults to 0, meaning "pre-versioning
  conductor" — old daemons that omit the field are detectable, not crashes.
- ``mzt status`` warns when the conductor's version differs from the
  client's (``protocol_mismatch_warning``).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from marianne.daemon.config import ResourceLimitConfig
from marianne.daemon.health import HealthChecker
from marianne.daemon.ipc.protocol import PROTOCOL_VERSION
from marianne.daemon.monitor import ResourceMonitor
from marianne.daemon.types import DaemonStatus


@pytest.fixture
def mock_manager() -> MagicMock:
    m = MagicMock()
    m.running_count = 2
    m.active_job_count = 3
    m.shutting_down = False
    m.failure_rate_elevated = False
    m.notifications_degraded = False
    return m


@pytest.fixture
def health_checker(mock_manager: MagicMock) -> HealthChecker:
    monitor = ResourceMonitor(
        ResourceLimitConfig(max_memory_mb=1024, max_processes=20),
        manager=mock_manager,
    )
    return HealthChecker(
        mock_manager, monitor, start_time=time.monotonic() - 120.0
    )


class TestProtocolVersionConstant:
    def test_is_positive_int(self) -> None:
        assert isinstance(PROTOCOL_VERSION, int)
        assert PROTOCOL_VERSION >= 1


class TestLivenessAdvertisesVersion:
    async def test_liveness_includes_protocol_version(
        self, health_checker: HealthChecker
    ) -> None:
        result = await health_checker.liveness()
        assert result["protocol_version"] == PROTOCOL_VERSION


class TestDaemonStatusCarriesVersion:
    def test_field_defaults_to_zero_for_pre_versioning_daemons(self) -> None:
        status = DaemonStatus(
            pid=1,
            uptime_seconds=1.0,
            running_jobs=0,
            total_jobs_active=0,
            memory_usage_mb=0.0,
            version="0.1.0",
        )
        assert status.protocol_version == 0

    def test_field_parses_when_present(self) -> None:
        status = DaemonStatus(
            pid=1,
            uptime_seconds=1.0,
            running_jobs=0,
            total_jobs_active=0,
            memory_usage_mb=0.0,
            version="0.1.0",
            protocol_version=PROTOCOL_VERSION,
        )
        assert status.protocol_version == PROTOCOL_VERSION


class TestManagerStatusIncludesVersion:
    async def test_get_daemon_status_advertises_version(self, tmp_path) -> None:
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.manager import JobManager

        config = DaemonConfig(
            pid_file=tmp_path / "test.pid",
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        status = await mgr.get_daemon_status()
        assert status["protocol_version"] == PROTOCOL_VERSION
        # The wire dict round-trips through the typed client model.
        assert DaemonStatus(**status).protocol_version == PROTOCOL_VERSION


class TestMismatchWarning:
    def test_warns_on_older_daemon(self) -> None:
        from marianne.cli.commands.status import protocol_mismatch_warning

        msg = protocol_mismatch_warning(0)
        assert msg is not None
        assert "restart" in msg.lower() or "mzt" in msg.lower()

    def test_warns_on_newer_daemon(self) -> None:
        from marianne.cli.commands.status import protocol_mismatch_warning

        msg = protocol_mismatch_warning(PROTOCOL_VERSION + 1)
        assert msg is not None

    def test_silent_on_match(self) -> None:
        from marianne.cli.commands.status import protocol_mismatch_warning

        assert protocol_mismatch_warning(PROTOCOL_VERSION) is None
