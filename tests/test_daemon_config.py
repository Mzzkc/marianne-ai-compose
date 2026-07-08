"""Tests for marianne.daemon.config module."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from marianne.daemon.config import (
    DaemonConfig,
    DaemonLoggingConfig,
    ResourceLimitConfig,
    SemanticLearningConfig,
    SocketConfig,
)


class TestResourceLimitConfig:
    """Tests for ResourceLimitConfig model."""

    def test_defaults(self):
        """Test default values are applied."""
        config = ResourceLimitConfig()
        assert config.max_memory_mb == 8192
        assert config.max_processes == 50
        assert config.max_api_calls_per_minute == 60

    def test_custom_values(self):
        """Test custom values override defaults (except reserved fields)."""
        config = ResourceLimitConfig(
            max_memory_mb=1024,
            max_processes=10,
        )
        assert config.max_memory_mb == 1024
        assert config.max_processes == 10

    def test_max_memory_mb_minimum(self):
        """Test max_memory_mb must be >= 512."""
        config = ResourceLimitConfig(max_memory_mb=512)
        assert config.max_memory_mb == 512

        with pytest.raises(ValidationError):
            ResourceLimitConfig(max_memory_mb=511)

    def test_max_memory_mb_below_minimum_rejected(self):
        """Test max_memory_mb below 512 is rejected."""
        with pytest.raises(ValidationError):
            ResourceLimitConfig(max_memory_mb=0)

    def test_max_processes_minimum(self):
        """Test max_processes must be >= 5."""
        config = ResourceLimitConfig(max_processes=5)
        assert config.max_processes == 5

        with pytest.raises(ValidationError):
            ResourceLimitConfig(max_processes=4)

    def test_max_processes_below_minimum_rejected(self):
        """Test max_processes below 5 is rejected."""
        with pytest.raises(ValidationError):
            ResourceLimitConfig(max_processes=0)

    def test_max_api_calls_non_default_rejected(self):
        """Test any non-default max_api_calls_per_minute is rejected (not yet enforced)."""
        with pytest.raises(ValidationError, match="not yet enforced"):
            ResourceLimitConfig(max_api_calls_per_minute=30)

        with pytest.raises(ValidationError, match="not yet enforced"):
            ResourceLimitConfig(max_api_calls_per_minute=1)

        # Values below ge=1 are rejected by field-level validation
        with pytest.raises(ValidationError):
            ResourceLimitConfig(max_api_calls_per_minute=0)

        with pytest.raises(ValidationError):
            ResourceLimitConfig(max_api_calls_per_minute=-1)


class TestSocketConfig:
    """Tests for SocketConfig model."""

    def test_defaults(self):
        """Test default values are applied."""
        config = SocketConfig()
        assert config.path == Path.home() / ".config" / "mzt" / "mzt.sock"
        assert config.permissions == 0o660
        assert config.backlog == 5

    def test_custom_path(self):
        """Test custom socket path."""
        config = SocketConfig(path=Path("/run/user/1000/marianne.sock"))
        assert config.path == Path("/run/user/1000/marianne.sock")

    def test_custom_permissions(self):
        """Test custom socket permissions."""
        config = SocketConfig(permissions=0o600)
        assert config.permissions == 0o600

    def test_backlog_minimum(self):
        """Test backlog must be >= 1."""
        config = SocketConfig(backlog=1)
        assert config.backlog == 1

        with pytest.raises(ValidationError):
            SocketConfig(backlog=0)

    def test_backlog_negative_rejected(self):
        """Test negative backlog is rejected."""
        with pytest.raises(ValidationError):
            SocketConfig(backlog=-1)

    def test_custom_backlog(self):
        """Test custom backlog value."""
        config = SocketConfig(backlog=128)
        assert config.backlog == 128


class TestDaemonConfig:
    """Tests for DaemonConfig model."""

    def test_defaults(self):
        """Test default values are applied."""
        config = DaemonConfig()
        assert config.pid_file == Path.home() / ".config" / "mzt" / "mzt.pid"
        assert config.max_concurrent_jobs == 15
        assert config.max_concurrent_sheets == 25
        assert config.state_backend_type == "sqlite"
        assert config.state_db_path == Path("~/.marianne/daemon-state.db")
        assert config.log_level == "info"
        assert isinstance(config.logging, DaemonLoggingConfig)
        assert config.logging.root == Path.home() / ".marianne" / "logs"
        assert config.logging.event_log_name == "conductor.log"
        assert config.log_file == Path.home() / ".marianne" / "logs" / "conductor.log"

    def test_nested_socket_defaults(self):
        """Test socket config is initialized with defaults."""
        config = DaemonConfig()
        assert isinstance(config.socket, SocketConfig)
        assert config.socket.path == Path.home() / ".config" / "mzt" / "mzt.sock"

    def test_nested_resource_limits_defaults(self):
        """Test resource limits config is initialized with defaults."""
        config = DaemonConfig()
        assert isinstance(config.resource_limits, ResourceLimitConfig)
        assert config.resource_limits.max_memory_mb == 8192

    def test_max_concurrent_jobs_minimum(self):
        """Test max_concurrent_jobs must be >= 1."""
        config = DaemonConfig(max_concurrent_jobs=1)
        assert config.max_concurrent_jobs == 1

        with pytest.raises(ValidationError):
            DaemonConfig(max_concurrent_jobs=0)

    def test_max_concurrent_jobs_maximum(self):
        """Test max_concurrent_jobs must be <= 50."""
        config = DaemonConfig(max_concurrent_jobs=50)
        assert config.max_concurrent_jobs == 50

        with pytest.raises(ValidationError):
            DaemonConfig(max_concurrent_jobs=51)

    def test_max_concurrent_sheets_minimum(self):
        """Test max_concurrent_sheets must be >= 1."""
        config = DaemonConfig(max_concurrent_sheets=1)
        assert config.max_concurrent_sheets == 1

        with pytest.raises(ValidationError):
            DaemonConfig(max_concurrent_sheets=0)

    def test_max_concurrent_sheets_maximum(self):
        """Test max_concurrent_sheets must be <= 100."""
        config = DaemonConfig(max_concurrent_sheets=100)
        assert config.max_concurrent_sheets == 100

        with pytest.raises(ValidationError):
            DaemonConfig(max_concurrent_sheets=101)

    def test_state_backend_type_sqlite(self):
        """Test state_backend_type accepts sqlite (the only valid value)."""
        config = DaemonConfig(state_backend_type="sqlite")
        assert config.state_backend_type == "sqlite"

    def test_state_backend_type_json_rejected(self):
        """Test state_backend_type='json' is rejected (reserved, frozen to sqlite)."""
        with pytest.raises(ValidationError):
            DaemonConfig(state_backend_type="json")

    def test_state_backend_type_invalid_rejected(self):
        """Test invalid state_backend_type is rejected."""
        with pytest.raises(ValidationError):
            DaemonConfig(state_backend_type="redis")

    def test_custom_pid_file(self):
        """Test custom PID file path."""
        config = DaemonConfig(pid_file=Path("/run/marianne.pid"))
        assert config.pid_file == Path("/run/marianne.pid")

    def test_custom_state_db_path(self):
        """Test custom state DB path."""
        config = DaemonConfig(state_db_path=Path("/var/lib/marianne/state.db"))
        assert config.state_db_path == Path("/var/lib/marianne/state.db")

    def test_custom_log_level(self):
        """Test custom log level."""
        config = DaemonConfig(log_level="debug")
        assert config.log_level == "debug"

    def test_custom_log_file(self):
        """Legacy log_file input is normalized into daemon logging config."""
        config = DaemonConfig(log_file=Path("/var/log/marianne.log"))
        assert config.log_file == Path("/var/log/marianne.log")
        assert config.logging.root == Path("/var/log")
        assert config.logging.event_log_name == "marianne.log"

    def test_custom_logging_root(self):
        """Daemon logging config owns the event log path."""
        config = DaemonConfig(
            logging=DaemonLoggingConfig(
                root=Path("~/custom-mzt-logs"),
                event_log_name="events.jsonl",
                max_file_size_mb=25,
                backup_count=3,
                compress=False,
                retention_days=30,
            )
        )
        assert config.logging.root == Path.home() / "custom-mzt-logs"
        assert config.log_file == Path.home() / "custom-mzt-logs" / "events.jsonl"
        assert config.logging.max_file_size_mb == 25
        assert config.logging.backup_count == 3
        assert config.logging.compress is False
        assert config.logging.retention_days == 30

    def test_logging_event_log_name_must_be_file_name(self):
        """Daemon event log names cannot escape the logging root."""
        with pytest.raises(ValidationError, match="single file name"):
            DaemonLoggingConfig(event_log_name="../events.log")

    def test_full_custom_config(self):
        """Test fully customized daemon config (only implemented fields)."""
        config = DaemonConfig(
            socket=SocketConfig(
                path=Path("/run/user/1000/marianne.sock"),
                permissions=0o600,
                backlog=10,
            ),
            pid_file=Path("/run/marianne.pid"),
            max_concurrent_jobs=10,
            max_concurrent_sheets=20,  # Warns (Phase 3), but accepted
            resource_limits=ResourceLimitConfig(
                max_memory_mb=4096,
                max_processes=25,
            ),
            state_backend_type="sqlite",
            state_db_path=Path("/data/marianne.db"),
            log_level="debug",
            logging=DaemonLoggingConfig(
                root=Path("/var/log/marianne"),
                event_log_name="conductor.jsonl",
            ),
        )
        assert config.socket.path == Path("/run/user/1000/marianne.sock")
        assert config.socket.permissions == 0o600
        assert config.socket.backlog == 10
        assert config.pid_file == Path("/run/marianne.pid")
        assert config.max_concurrent_jobs == 10
        assert config.max_concurrent_sheets == 20
        assert config.resource_limits.max_memory_mb == 4096
        assert config.resource_limits.max_processes == 25
        # Non-default values are rejected; this stays at default
        assert config.resource_limits.max_api_calls_per_minute == 60
        assert config.state_backend_type == "sqlite"
        assert config.state_db_path == Path("/data/marianne.db")
        assert config.log_level == "debug"
        assert config.log_file == Path("/var/log/marianne/conductor.jsonl")

    def test_serialization_roundtrip(self):
        """Test config survives model_dump -> model_validate roundtrip."""
        original = DaemonConfig(
            max_concurrent_jobs=8,
            log_level="warning",
            state_backend_type="sqlite",
        )
        dumped = original.model_dump()
        restored = DaemonConfig.model_validate(dumped)
        assert restored.max_concurrent_jobs == original.max_concurrent_jobs
        assert restored.log_level == original.log_level
        assert restored.state_backend_type == original.state_backend_type

    def test_nested_dict_construction(self):
        """Test constructing DaemonConfig from nested dicts (YAML-style)."""
        config = DaemonConfig.model_validate(
            {
                "socket": {
                    "path": "/tmp/custom.sock",
                    "backlog": 10,
                },
                "max_concurrent_jobs": 3,
                "resource_limits": {
                    "max_memory_mb": 2048,
                },
            }
        )
        assert config.socket.path == Path("/tmp/custom.sock")
        assert config.socket.backlog == 10
        assert config.max_concurrent_jobs == 3
        assert config.resource_limits.max_memory_mb == 2048
        # Non-specified fields should get defaults
        assert config.resource_limits.max_processes == 50

    def test_config_file_field_accepts_path(self):
        """config_file can be set to a Path without triggering warnings."""
        config = DaemonConfig(config_file=Path("/tmp/test.yaml"))
        assert config.config_file == Path("/tmp/test.yaml")

    def test_config_file_default_is_none(self):
        """config_file defaults to None."""
        config = DaemonConfig()
        assert config.config_file is None

    def test_load_legacy_log_file_from_yaml_normalizes_logging(self, tmp_path: Path):
        """_load_config accepts existing log_file YAML while preserving one authority."""
        from marianne.daemon.process import _load_config

        log_path = tmp_path / "legacy" / "marianne.log"
        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(yaml.dump({"log_file": str(log_path)}))

        config = _load_config(cfg_path)
        assert config.log_file == log_path
        assert config.logging.root == log_path.parent
        assert config.logging.event_log_name == log_path.name

    def test_load_logging_root_from_yaml(self, tmp_path: Path):
        """_load_config accepts the canonical daemon logging model."""
        from marianne.daemon.process import _load_config

        log_root = tmp_path / "logs"
        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "logging": {
                        "root": str(log_root),
                        "event_log_name": "daemon.jsonl",
                        "backup_count": 2,
                    }
                }
            )
        )

        config = _load_config(cfg_path)
        assert config.logging.root == log_root
        assert config.logging.event_log_name == "daemon.jsonl"
        assert config.logging.backup_count == 2
        assert config.log_file == log_root / "daemon.jsonl"


class TestLoadConfig:
    """Tests for _load_config() helper."""

    def test_load_from_yaml_sets_config_file(self, tmp_path: Path):
        """_load_config sets config_file to the resolved path of the loaded file."""
        from marianne.daemon.process import _load_config

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(yaml.dump({"max_concurrent_jobs": 3}))

        config = _load_config(cfg_path)
        assert config.config_file == cfg_path.resolve()
        assert config.max_concurrent_jobs == 3

    def test_load_with_none_returns_defaults(self):
        """_load_config(None) returns defaults; config_file depends on auto-discovery."""
        from marianne.daemon.process import _load_config

        config = _load_config(None)
        # When ~/.marianne/conductor.yaml exists, auto-discovery sets config_file;
        # otherwise config_file is None.  Either way, defaults apply.
        default_path = Path("~/.marianne/conductor.yaml").expanduser()
        if default_path.exists():
            assert config.config_file == default_path.resolve()
        else:
            assert config.config_file is None
        assert config.max_concurrent_jobs == 15  # default

    def test_load_nonexistent_file_returns_defaults(self, tmp_path: Path):
        """_load_config with non-existent file returns defaults."""
        from marianne.daemon.process import _load_config

        config = _load_config(tmp_path / "nope.yaml")
        assert config.config_file is None


class TestJobManagerApplyConfig:
    """Tests for JobManager.apply_config() hot-reload method."""

    def test_apply_config_resizes_gate_in_place_on_change(self):
        """apply_config resizes the concurrency gate IN PLACE (#231) — it does
        NOT replace the object. Replacing it orphaned in-flight acquisitions and
        over-admitted; the gate's limit is now adjusted via set_limit()."""
        from marianne.daemon.manager import JobManager

        config = DaemonConfig(max_concurrent_jobs=5)
        manager = JobManager(config)
        old_sem = manager._concurrency_semaphore

        new_config = DaemonConfig(max_concurrent_jobs=10)
        manager.apply_config(new_config)

        assert manager._concurrency_semaphore is old_sem  # same object, resized
        assert manager._concurrency_semaphore.limit == 10
        assert manager._config.max_concurrent_jobs == 10

    def test_apply_config_no_semaphore_change_when_unchanged(self):
        """apply_config keeps the same semaphore when max_concurrent_jobs is unchanged."""
        from marianne.daemon.manager import JobManager

        config = DaemonConfig(max_concurrent_jobs=5)
        manager = JobManager(config)
        old_sem = manager._concurrency_semaphore

        new_config = DaemonConfig(max_concurrent_jobs=5)
        manager.apply_config(new_config)

        assert manager._concurrency_semaphore is old_sem

    def test_apply_config_updates_config_reference(self):
        """apply_config replaces the _config reference."""
        from marianne.daemon.manager import JobManager

        old_config = DaemonConfig(job_timeout_seconds=3600.0)
        manager = JobManager(old_config)

        new_config = DaemonConfig(job_timeout_seconds=7200.0)
        manager.apply_config(new_config)

        assert manager._config is new_config
        assert manager._config.job_timeout_seconds == 7200.0

    def test_apply_config_noop_when_identical(self):
        """apply_config with identical config does not rebuild semaphore."""
        from marianne.daemon.manager import JobManager

        config = DaemonConfig()
        manager = JobManager(config)
        old_sem = manager._concurrency_semaphore

        identical_config = DaemonConfig()
        manager.apply_config(identical_config)

        assert manager._concurrency_semaphore is old_sem
        assert manager._config is identical_config


class TestResourceMonitorUpdateLimits:
    """Tests for ResourceMonitor.update_limits() hot-reload method."""

    def test_update_limits_replaces_config(self):
        """update_limits replaces the internal config reference."""
        from marianne.daemon.monitor import ResourceMonitor

        old_limits = ResourceLimitConfig(max_memory_mb=4096)
        monitor = ResourceMonitor(old_limits)

        new_limits = ResourceLimitConfig(max_memory_mb=8192)
        monitor.update_limits(new_limits)

        assert monitor._config is new_limits
        assert monitor._config.max_memory_mb == 8192

    def test_update_limits_preserves_other_state(self):
        """update_limits does not affect degraded/failure state."""
        from marianne.daemon.monitor import ResourceMonitor

        limits = ResourceLimitConfig()
        monitor = ResourceMonitor(limits)
        monitor._degraded = True
        monitor._consecutive_failures = 3

        new_limits = ResourceLimitConfig(max_memory_mb=2048)
        monitor.update_limits(new_limits)

        # Internal state untouched
        assert monitor._degraded is True
        assert monitor._consecutive_failures == 3
        assert monitor._config is new_limits


class TestSemanticLearningConfig:
    """Tests for SemanticLearningConfig model."""

    def test_defaults(self):
        """Test default values are applied correctly."""
        config = SemanticLearningConfig()
        assert config.enabled is True
        assert config.instrument == "anthropic_api"
        assert config.model is None
        assert config.timeout_seconds == 120.0
        assert config.analyze_on == ["success", "failure"]
        assert config.max_concurrent_analyses == 3

    def test_custom_backend(self):
        """Test custom backend config overrides defaults."""
        config = SemanticLearningConfig(
            enabled=False,
            instrument="ollama",
            model="llama3.1:8b",
            timeout_seconds=60.0,
            analyze_on=["failure"],
            max_concurrent_analyses=5,
        )
        assert config.enabled is False
        assert config.instrument == "ollama"
        assert config.model == "llama3.1:8b"
        assert config.analyze_on == ["failure"]
        assert config.max_concurrent_analyses == 5
        assert config.timeout_seconds == 60.0

    def test_max_concurrent_analyses_minimum(self):
        """Test max_concurrent_analyses must be >= 1."""
        config = SemanticLearningConfig(max_concurrent_analyses=1)
        assert config.max_concurrent_analyses == 1

        with pytest.raises(ValidationError):
            SemanticLearningConfig(max_concurrent_analyses=0)

    def test_max_concurrent_analyses_maximum(self):
        """Test max_concurrent_analyses must be <= 20."""
        config = SemanticLearningConfig(max_concurrent_analyses=20)
        assert config.max_concurrent_analyses == 20

        with pytest.raises(ValidationError):
            SemanticLearningConfig(max_concurrent_analyses=21)

    def test_analyze_on_empty_rejected(self):
        """Test analyze_on cannot be empty."""
        with pytest.raises(ValidationError, match="at least one value"):
            SemanticLearningConfig(analyze_on=[])

    def test_analyze_on_invalid_value_rejected(self):
        """Test analyze_on rejects invalid values."""
        with pytest.raises(ValidationError):
            SemanticLearningConfig(analyze_on=["unknown"])

    def test_analyze_on_success_only(self):
        """Test analyze_on with only success."""
        config = SemanticLearningConfig(analyze_on=["success"])
        assert config.analyze_on == ["success"]

    def test_analyze_on_failure_only(self):
        """Test analyze_on with only failure."""
        config = SemanticLearningConfig(analyze_on=["failure"])
        assert config.analyze_on == ["failure"]

    def test_daemon_config_has_learning_field(self):
        """Test DaemonConfig includes learning field with defaults."""
        config = DaemonConfig()
        assert hasattr(config, "learning")
        assert isinstance(config.learning, SemanticLearningConfig)
        assert config.learning.enabled is True
        assert config.learning.instrument == "anthropic_api"

    def test_daemon_config_custom_learning(self):
        """Test DaemonConfig accepts custom learning config."""
        config = DaemonConfig(
            learning=SemanticLearningConfig(
                enabled=False,
                instrument="anthropic_api",
                model="claude-haiku-4-5-20251001",
                timeout_seconds=120.0,
            ),
        )
        assert config.learning.enabled is False
        assert config.learning.model == "claude-haiku-4-5-20251001"

    def test_daemon_config_learning_from_dict(self):
        """Test DaemonConfig constructs learning from nested dict (YAML-style)."""
        config = DaemonConfig.model_validate(
            {
                "learning": {
                    "enabled": False,
                    "max_concurrent_analyses": 5,
                },
            }
        )
        assert config.learning.enabled is False
        assert config.learning.max_concurrent_analyses == 5

    def test_serialization_roundtrip(self):
        """Test SemanticLearningConfig survives model_dump -> model_validate."""
        original = SemanticLearningConfig(
            enabled=False,
            instrument="anthropic_api",
            model="test-model",
            timeout_seconds=120.0,
            analyze_on=["failure"],
            max_concurrent_analyses=7,
        )
        dumped = original.model_dump()
        restored = SemanticLearningConfig.model_validate(dumped)
        assert restored.enabled == original.enabled
        assert restored.model == original.model
        assert restored.instrument == original.instrument
        assert restored.analyze_on == original.analyze_on
        assert restored.max_concurrent_analyses == original.max_concurrent_analyses

    def test_daemon_config_roundtrip_preserves_learning(self):
        """Test DaemonConfig roundtrip preserves learning field."""
        original = DaemonConfig(
            learning=SemanticLearningConfig(
                enabled=False,
                instrument="anthropic_api",
                model="claude-haiku-4-5-20251001",
            ),
        )
        dumped = original.model_dump()
        restored = DaemonConfig.model_validate(dumped)
        assert restored.learning.enabled is False
        assert restored.learning.model == "claude-haiku-4-5-20251001"
