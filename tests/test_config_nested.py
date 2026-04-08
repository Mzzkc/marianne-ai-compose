"""Integration tests for nested config models in marianne.core.config.

These tests verify that Pydantic correctly parses nested configuration structures
that are commonly used in job YAML files. This catches issues with field types,
validators, and nested model instantiation.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from marianne.core.config import (
    AIReviewConfig,
    AutoApplyConfig,
    BackendConfig,
    CheckpointConfig,
    CheckpointTriggerConfig,
    CircuitBreakerConfig,
    CostLimitConfig,
    EntropyResponseConfig,
    ExplorationBudgetConfig,
    GroundingConfig,
    GroundingHookConfig,
    IsolationConfig,
    IsolationMode,
    JobConfig,
    LearningConfig,
    LogConfig,
    NotificationConfig,
    ParallelConfig,
    PromptConfig,
    RateLimitConfig,
    RecursiveLightConfig,
    RetryConfig,
    SheetConfig,
)


class TestIsolationConfig:
    """Tests for IsolationConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = IsolationConfig()
        assert config.enabled is False
        assert config.mode == IsolationMode.WORKTREE
        assert config.branch_prefix == "marianne"
        assert config.cleanup_on_success is True
        assert config.cleanup_on_failure is False
        assert config.lock_during_execution is True
        assert config.fallback_on_error is True

    def test_enabled_config(self):
        """Test fully enabled configuration."""
        config = IsolationConfig(
            enabled=True,
            mode=IsolationMode.WORKTREE,
            branch_prefix="feature",
            source_branch="develop",
            cleanup_on_success=False,
        )
        assert config.enabled is True
        assert config.source_branch == "develop"
        assert config.cleanup_on_success is False

    def test_get_worktree_base_default(self):
        """Test worktree base path calculation."""
        config = IsolationConfig()
        workspace = Path("/tmp/workspace")
        assert config.get_worktree_base(workspace) == Path("/tmp/workspace/.worktrees")

    def test_get_worktree_base_custom(self):
        """Test custom worktree base path."""
        config = IsolationConfig(worktree_base=Path("/custom/worktrees"))
        workspace = Path("/tmp/workspace")
        assert config.get_worktree_base(workspace) == Path("/custom/worktrees")

    def test_get_branch_name(self):
        """Test branch name generation."""
        config = IsolationConfig(branch_prefix="feature")
        assert config.get_branch_name("job-123") == "feature/job-123"

    def test_invalid_branch_prefix(self):
        """Test validation rejects invalid branch prefix."""
        with pytest.raises(ValidationError):
            IsolationConfig(branch_prefix="123-invalid")  # Can't start with number

        with pytest.raises(ValidationError):
            IsolationConfig(branch_prefix="has spaces")


class TestLogConfig:
    """Tests for LogConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = LogConfig()
        assert config.level == "INFO"
        assert config.format == "console"
        assert config.file_path is None
        assert config.max_file_size_mb == 50
        assert config.backup_count == 5
        assert config.include_timestamps is True
        assert config.include_context is True

    def test_json_format(self):
        """Test JSON format configuration."""
        config = LogConfig(format="json", level="DEBUG")
        assert config.format == "json"
        assert config.level == "DEBUG"

    def test_both_format_with_file(self):
        """Test both format requires file path."""
        config = LogConfig(format="both", file_path=Path("/var/log/marianne.json"))
        assert config.format == "both"
        assert config.file_path == Path("/var/log/marianne.json")

    def test_invalid_level(self):
        """Test validation rejects invalid log level."""
        with pytest.raises(ValidationError):
            LogConfig(level="TRACE")  # Not a valid level

    def test_invalid_format(self):
        """Test validation rejects invalid format."""
        with pytest.raises(ValidationError):
            LogConfig(format="xml")  # Not a valid format


class TestAIReviewConfig:
    """Tests for AIReviewConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = AIReviewConfig()
        assert config.enabled is False
        assert config.min_score == 60
        assert config.target_score == 80
        assert config.on_low_score == "warn"

    def test_enabled_with_retry(self):
        """Test enabled config with retry action."""
        config = AIReviewConfig(
            enabled=True,
            min_score=70,
            target_score=90,
            on_low_score="retry",
            max_retry_for_review=3,
        )
        assert config.enabled is True
        assert config.min_score == 70
        assert config.on_low_score == "retry"

    def test_score_bounds(self):
        """Test score bounds are enforced."""
        with pytest.raises(ValidationError):
            AIReviewConfig(min_score=101)  # Above max

        with pytest.raises(ValidationError):
            AIReviewConfig(min_score=-1)  # Below min


class TestExplorationBudgetConfig:
    """Tests for ExplorationBudgetConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = ExplorationBudgetConfig()
        assert config.enabled is False
        assert config.floor == 0.05
        assert config.ceiling == 0.50
        assert config.decay_rate == 0.95

    def test_enabled_with_limits(self):
        """Test enabled config with custom limits."""
        config = ExplorationBudgetConfig(
            enabled=True,
            floor=0.10,
            ceiling=0.40,
            decay_rate=0.90,
            boost_amount=0.15,
        )
        assert config.enabled is True
        assert config.floor == 0.10
        assert config.ceiling == 0.40
        assert config.decay_rate == 0.90


class TestEntropyResponseConfig:
    """Tests for EntropyResponseConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = EntropyResponseConfig()
        assert config.enabled is False
        assert config.entropy_threshold == 0.3
        assert config.cooldown_seconds == 3600

    def test_enabled_config(self):
        """Test enabled configuration."""
        config = EntropyResponseConfig(
            enabled=True,
            entropy_threshold=0.4,
            cooldown_seconds=1800,
        )
        assert config.enabled is True
        assert config.entropy_threshold == 0.4


class TestLearningConfig:
    """Tests for LearningConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = LearningConfig()
        assert config.enabled is True
        assert config.use_global_patterns is True
        assert config.escalation_enabled is False
        assert config.exploration_rate == 0.15

    def test_disabled_subsystems(self):
        """Test disabling specific subsystems."""
        config = LearningConfig(
            enabled=True,
            use_global_patterns=False,
            escalation_enabled=False,
            auto_apply_enabled=True,
        )
        assert config.use_global_patterns is False
        assert config.escalation_enabled is False
        assert config.auto_apply_enabled is True

    def test_nested_exploration_budget(self):
        """Test nested exploration budget config."""
        config = LearningConfig(
            exploration_budget=ExplorationBudgetConfig(
                enabled=True,
                floor=0.08,
            )
        )
        assert config.exploration_budget.enabled is True
        assert config.exploration_budget.floor == 0.08

    def test_nested_entropy_response(self):
        """Test nested entropy response config."""
        config = LearningConfig(
            entropy_response=EntropyResponseConfig(
                enabled=True,
                entropy_threshold=0.35,
            )
        )
        assert config.entropy_response.enabled is True
        assert config.entropy_response.entropy_threshold == 0.35


class TestCheckpointConfig:
    """Tests for CheckpointConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = CheckpointConfig()
        assert config.enabled is False
        assert config.triggers == []

    def test_with_trigger_config(self):
        """Test with nested trigger config."""
        config = CheckpointConfig(
            enabled=True,
            triggers=[
                CheckpointTriggerConfig(
                    name="high_risk",
                    sheet_nums=[5, 6],
                    message="These sheets modify production",
                )
            ],
        )
        assert config.enabled is True
        assert len(config.triggers) == 1
        assert config.triggers[0].name == "high_risk"


class TestAutoApplyConfig:
    """Tests for AutoApplyConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = AutoApplyConfig()
        assert config.enabled is False
        assert config.trust_threshold == 0.85
        assert config.max_patterns_per_sheet == 3

    def test_enabled_config(self):
        """Test enabled configuration."""
        config = AutoApplyConfig(
            enabled=True,
            trust_threshold=0.9,
            max_patterns_per_sheet=5,
            require_validated_status=False,
        )
        assert config.enabled is True
        assert config.trust_threshold == 0.9
        assert config.require_validated_status is False


class TestGroundingConfig:
    """Tests for GroundingConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = GroundingConfig()
        assert config.enabled is False

    def test_with_hooks(self):
        """Test with hook configurations."""
        config = GroundingConfig(
            enabled=True,
            hooks=[
                GroundingHookConfig(
                    type="file_checksum",
                    name="test-hook",
                    expected_checksums={"file.txt": "abc123"},
                )
            ],
        )
        assert config.enabled is True
        assert len(config.hooks) == 1
        assert config.hooks[0].name == "test-hook"


class TestRateLimitConfig:
    """Tests for RateLimitConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = RateLimitConfig()
        assert config.wait_minutes == 60
        assert config.max_waits == 24

    def test_custom_limits(self):
        """Test custom rate limits."""
        config = RateLimitConfig(
            wait_minutes=30,
            max_waits=12,
        )
        assert config.wait_minutes == 30
        assert config.max_waits == 12


class TestCircuitBreakerConfig:
    """Tests for CircuitBreakerConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = CircuitBreakerConfig()
        assert config.enabled is True
        assert config.failure_threshold == 5
        assert config.recovery_timeout_seconds == 300.0

    def test_custom_thresholds(self):
        """Test custom thresholds."""
        config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            cross_workspace_coordination=False,
        )
        assert config.failure_threshold == 3
        assert config.recovery_timeout_seconds == 120


class TestCostLimitConfig:
    """Tests for CostLimitConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = CostLimitConfig()
        assert config.enabled is False
        assert config.max_cost_per_job is None
        assert config.warn_at_percent == 80

    def test_enabled_with_limits(self):
        """Test enabled config with limits."""
        config = CostLimitConfig(
            enabled=True,
            max_cost_per_job=10.0,
            max_cost_per_sheet=1.0,
            warn_at_percent=70,
        )
        assert config.enabled is True
        assert config.max_cost_per_job == 10.0


class TestNotificationConfig:
    """Tests for NotificationConfig nested model."""

    def test_desktop_config(self):
        """Test desktop notification config."""
        config = NotificationConfig(
            type="desktop",
            on_events=["job_complete", "job_failed"],
        )
        assert config.type == "desktop"
        assert "job_complete" in config.on_events

    def test_slack_config(self):
        """Test Slack notification config."""
        config = NotificationConfig(
            type="slack",
            on_events=["job_failed"],
            config={"webhook_url": "https://hooks.slack.com/test"},
        )
        assert config.type == "slack"
        assert config.config["webhook_url"] == "https://hooks.slack.com/test"

    def test_webhook_config(self):
        """Test webhook notification config."""
        config = NotificationConfig(
            type="webhook",
            on_events=["job_complete"],
            config={
                "url": "https://example.com/hook",
                "headers": {"Authorization": "Bearer token"},
            },
        )
        assert config.type == "webhook"
        assert "url" in config.config


class TestRecursiveLightConfig:
    """Tests for RecursiveLightConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = RecursiveLightConfig()
        assert config.endpoint == "http://localhost:8080"
        assert config.user_id is None
        assert config.timeout == 30.0

    def test_custom_config(self):
        """Test custom configuration."""
        config = RecursiveLightConfig(
            endpoint="http://custom:9000",
            user_id="test-user-123",
            timeout=60.0,
        )
        assert config.endpoint == "http://custom:9000"
        assert config.user_id == "test-user-123"


class TestBackendConfig:
    """Tests for BackendConfig nested model."""

    def test_claude_cli_default(self):
        """Test Claude CLI backend config."""
        config = BackendConfig(type="claude_cli")
        assert config.type == "claude_cli"
        assert config.skip_permissions is True
        assert config.disable_mcp is True

    def test_anthropic_api_config(self):
        """Test Anthropic API backend config."""
        config = BackendConfig(
            type="anthropic_api",
            model="claude-3-opus-20240229",
        )
        assert config.type == "anthropic_api"
        assert config.model == "claude-3-opus-20240229"

    def test_recursive_light_backend(self):
        """Test with recursive light config."""
        config = BackendConfig(
            type="recursive_light",
            recursive_light=RecursiveLightConfig(
                endpoint="http://localhost:9090",
                timeout=45.0,
            ),
        )
        assert config.type == "recursive_light"
        assert config.recursive_light.endpoint == "http://localhost:9090"

    def test_circuit_breaker_embedding(self):
        """Test circuit breaker config on backend."""
        # Note: circuit_breaker is a top-level JobConfig field, not in BackendConfig
        # This test validates the BackendConfig model itself
        config = BackendConfig(
            type="anthropic_api",
            model="claude-3-sonnet-20240229",
        )
        assert config.type == "anthropic_api"


class TestParallelConfig:
    """Tests for ParallelConfig nested model."""

    def test_defaults(self):
        """Test default values are correct."""
        config = ParallelConfig()
        assert config.enabled is False
        assert config.max_concurrent == 3  # Default is 3 based on config

    def test_enabled_config(self):
        """Test enabled parallel config."""
        config = ParallelConfig(
            enabled=True,
            max_concurrent=4,
            fail_fast=False,
        )
        assert config.enabled is True
        assert config.max_concurrent == 4
        assert config.fail_fast is False


class TestJobConfigNestedIntegration:
    """Integration tests for JobConfig with all nested configs."""

    def test_minimal_job_config(self):
        """Test minimal job config with required fields only."""
        config = JobConfig(
            name="test-job",
            workspace=Path("/tmp/workspace"),
            sheet=SheetConfig(size=10, total_items=100),
            prompt=PromptConfig(template="Do task {sheet_num}"),
        )
        assert config.name == "test-job"
        assert config.sheet.total_sheets == 10

    def test_full_job_config(self):
        """Test job config with all nested configs."""
        config = JobConfig(
            name="full-test-job",
            workspace=Path("/tmp/workspace"),
            sheet=SheetConfig(size=10, total_items=100),
            prompt=PromptConfig(template="Do task {sheet_num}"),
            retry=RetryConfig(max_retries=5),
            logging=LogConfig(level="DEBUG", format="json"),
            backend=BackendConfig(
                type="anthropic_api",
                model="claude-3-sonnet-20240229",
            ),
            learning=LearningConfig(
                enabled=True,
                exploration_budget=ExplorationBudgetConfig(enabled=True),
            ),
            isolation=IsolationConfig(enabled=True),
            notifications=[
                NotificationConfig(type="desktop", on_events=["job_complete"]),
            ],
        )

        assert config.retry.max_retries == 5
        assert config.logging.level == "DEBUG"
        assert config.backend.type == "anthropic_api"
        assert config.learning.exploration_budget.enabled is True
        assert config.isolation.enabled is True
        assert len(config.notifications) == 1

    def test_job_config_from_dict(self):
        """Test creating job config from dictionary (simulating YAML load)."""
        data = {
            "name": "yaml-test-job",
            "workspace": "/tmp/workspace",
            "sheet": {"size": 10, "total_items": 50},
            "prompt": {"template": "Process {sheet_num}"},
            "retry": {"max_retries": 3, "jitter": False},
            "logging": {"level": "INFO", "format": "console"},
            "learning": {
                "enabled": True,
                "use_global_patterns": False,
            },
            "isolation": {
                "enabled": True,
                "branch_prefix": "feature",
            },
        }

        config = JobConfig(**data)

        assert config.name == "yaml-test-job"
        assert config.retry.jitter is False
        assert config.learning.use_global_patterns is False
        assert config.isolation.branch_prefix == "feature"
