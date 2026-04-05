"""M4 Adversarial Tests: Config Model Strictness (extra='forbid').

Proves that unknown YAML fields in score configs are rejected, not silently
ignored. The composer directive (2026-04-04) requires ERROR severity for
unknown fields — score authors must not think they're using features that
Mozart drops on the floor.

Attack surface: every config model reachable from JobConfig that could appear
in a user-authored YAML score.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozart.core.config.backend import (
    BackendConfig,
    BridgeConfig,
    MCPServerConfig,
    OllamaConfig,
    RecursiveLightConfig,
    SheetBackendOverride,
)
from mozart.core.config.execution import (
    CircuitBreakerConfig,
    CostLimitConfig,
    ParallelConfig,
    PreflightConfig,
    RateLimitConfig,
    RetryConfig,
    SkipWhenCommand,
    StaleDetectionConfig,
    ValidationRule,
)
from mozart.core.config.job import (
    InjectionItem,
    InstrumentDef,
    JobConfig,
    MovementDef,
    PromptConfig,
    SheetConfig,
)
from mozart.core.config.learning import (
    AutoApplyConfig,
    CheckpointConfig,
    CheckpointTriggerConfig,
    EntropyResponseConfig,
    ExplorationBudgetConfig,
    GroundingConfig,
    GroundingHookConfig,
    LearningConfig,
)
from mozart.core.config.orchestration import (
    ConcertConfig,
    ConductorConfig,
    ConductorPreferences,
    NotificationConfig,
    PostSuccessHookConfig,
)
from mozart.core.config.spec import SpecCorpusConfig, SpecFragment
from mozart.core.config.workspace import (
    AIReviewConfig,
    CrossSheetConfig,
    FeedbackConfig,
    IsolationConfig,
    LogConfig,
    WorkspaceLifecycleConfig,
)


class TestJobConfigRejectsUnknownFields:
    """JobConfig is the root of every score — unknown fields here are the
    most dangerous because they're the most visible to score authors."""

    def test_unknown_top_level_field_rejected(self) -> None:
        """A field that doesn't exist in JobConfig must raise ValidationError."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(
                name="test",
                sheet=SheetConfig(size=1, total_items=1),
                prompt=PromptConfig(template="test prompt"),
                bogus_field_that_doesnt_exist=True,
            )

    def test_instrument_fallbacks_rejected_until_implemented(self) -> None:
        """instrument_fallbacks is not yet a field — must not silently pass."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(
                name="test",
                sheet=SheetConfig(size=1, total_items=1),
                prompt=PromptConfig(template="test prompt"),
                instrument_fallbacks=["gemini-cli"],
            )

    def test_typo_in_field_name_rejected(self) -> None:
        """Common typo: 'retries' instead of 'retry'. Must not pass."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(
                name="test",
                sheet=SheetConfig(size=1, total_items=1),
                prompt=PromptConfig(template="test prompt"),
                retries=RetryConfig(),  # Wrong name
            )

    def test_multiple_unknown_fields_all_reported(self) -> None:
        """Multiple unknown fields should all appear in error, not just first."""
        with pytest.raises(ValidationError) as exc_info:
            JobConfig(
                name="test",
                sheet=SheetConfig(size=1, total_items=1),
                prompt=PromptConfig(template="test prompt"),
                bogus_a=1,
                bogus_b=2,
            )
        errors = exc_info.value.errors()
        extra_errors = [e for e in errors if e["type"] == "extra_forbidden"]
        assert len(extra_errors) >= 2, f"Expected 2+ extra errors, got {extra_errors}"


class TestSheetConfigRejectsUnknownFields:
    """SheetConfig controls per-sheet behavior — unknown fields here mean
    sheet-level features are silently ignored."""

    def test_unknown_sheet_field_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            SheetConfig(size=5, total_items=5, unknown_sheet_option=True)

    def test_timeout_typo_rejected(self) -> None:
        """'timeout' is not a SheetConfig field (it's stale_detection.idle_timeout_seconds)."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            SheetConfig(size=5, total_items=5, timeout=300)


class TestNestedConfigModelsRejectUnknownFields:
    """Every nested config model reachable from JobConfig must reject unknowns.
    One permissive model in the tree makes the whole config unsafe."""

    @pytest.mark.parametrize(
        "model_cls,kwargs",
        [
            pytest.param(RetryConfig, {"bogus": 1}, id="RetryConfig"),
            pytest.param(RateLimitConfig, {"bogus": 1}, id="RateLimitConfig"),
            pytest.param(CircuitBreakerConfig, {"bogus": 1}, id="CircuitBreakerConfig"),
            pytest.param(CostLimitConfig, {"bogus": 1}, id="CostLimitConfig"),
            pytest.param(StaleDetectionConfig, {"bogus": 1}, id="StaleDetectionConfig"),
            pytest.param(PreflightConfig, {"bogus": 1}, id="PreflightConfig"),
            pytest.param(ParallelConfig, {"bogus": 1}, id="ParallelConfig"),
            pytest.param(
                ValidationRule,
                {"type": "file_exists", "path": "/tmp/x", "bogus": 1},
                id="ValidationRule",
            ),
            pytest.param(
                SkipWhenCommand,
                {"command": "true", "bogus": 1},
                id="SkipWhenCommand",
            ),
        ],
        ids=lambda x: x if isinstance(x, str) else None,
    )
    def test_execution_models_reject_unknown(
        self, model_cls: type, kwargs: dict
    ) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            model_cls(**kwargs)

    @pytest.mark.parametrize(
        "model_cls,kwargs",
        [
            pytest.param(LearningConfig, {"bogus": 1}, id="LearningConfig"),
            pytest.param(ExplorationBudgetConfig, {"bogus": 1}, id="ExplorationBudgetConfig"),
            pytest.param(EntropyResponseConfig, {"bogus": 1}, id="EntropyResponseConfig"),
            pytest.param(AutoApplyConfig, {"bogus": 1}, id="AutoApplyConfig"),
            pytest.param(GroundingConfig, {"bogus": 1}, id="GroundingConfig"),
            pytest.param(
                GroundingHookConfig,
                {"type": "pre_execution", "bogus": 1},
                id="GroundingHookConfig",
            ),
            pytest.param(CheckpointConfig, {"bogus": 1}, id="CheckpointConfig"),
            pytest.param(CheckpointTriggerConfig, {"bogus": 1}, id="CheckpointTriggerConfig"),
        ],
    )
    def test_learning_models_reject_unknown(
        self, model_cls: type, kwargs: dict
    ) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            model_cls(**kwargs)

    @pytest.mark.parametrize(
        "model_cls,kwargs",
        [
            pytest.param(BackendConfig, {"bogus": 1}, id="BackendConfig"),
            pytest.param(RecursiveLightConfig, {"bogus": 1}, id="RecursiveLightConfig"),
            pytest.param(OllamaConfig, {"bogus": 1}, id="OllamaConfig"),
            pytest.param(
                MCPServerConfig,
                {"name": "test", "type": "stdio", "command": "echo", "bogus": 1},
                id="MCPServerConfig",
            ),
            pytest.param(BridgeConfig, {"bogus": 1}, id="BridgeConfig"),
            pytest.param(
                SheetBackendOverride,
                {"sheet": 1, "bogus": 1},
                id="SheetBackendOverride",
            ),
        ],
    )
    def test_backend_models_reject_unknown(
        self, model_cls: type, kwargs: dict
    ) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            model_cls(**kwargs)

    @pytest.mark.parametrize(
        "model_cls,kwargs",
        [
            pytest.param(ConductorConfig, {"bogus": 1}, id="ConductorConfig"),
            pytest.param(ConductorPreferences, {"bogus": 1}, id="ConductorPreferences"),
            pytest.param(NotificationConfig, {"bogus": 1}, id="NotificationConfig"),
            pytest.param(
                PostSuccessHookConfig,
                {"command": "echo done", "bogus": 1},
                id="PostSuccessHookConfig",
            ),
            pytest.param(ConcertConfig, {"bogus": 1}, id="ConcertConfig"),
        ],
    )
    def test_orchestration_models_reject_unknown(
        self, model_cls: type, kwargs: dict
    ) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            model_cls(**kwargs)

    @pytest.mark.parametrize(
        "model_cls,kwargs",
        [
            pytest.param(IsolationConfig, {"bogus": 1}, id="IsolationConfig"),
            pytest.param(WorkspaceLifecycleConfig, {"bogus": 1}, id="WorkspaceLifecycleConfig"),
            pytest.param(LogConfig, {"bogus": 1}, id="LogConfig"),
            pytest.param(AIReviewConfig, {"bogus": 1}, id="AIReviewConfig"),
            pytest.param(CrossSheetConfig, {"bogus": 1}, id="CrossSheetConfig"),
            pytest.param(FeedbackConfig, {"bogus": 1}, id="FeedbackConfig"),
        ],
    )
    def test_workspace_models_reject_unknown(
        self, model_cls: type, kwargs: dict
    ) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            model_cls(**kwargs)

    def test_spec_fragment_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            SpecFragment(name="test", content="test content", bogus=1)

    def test_spec_corpus_config_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            SpecCorpusConfig(bogus=1)

    def test_prompt_config_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            PromptConfig(template="test prompt", bogus=1)

    def test_injection_item_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            InjectionItem(file="test.md", as_="context", bogus=1)

    def test_instrument_def_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            InstrumentDef(profile="claude-cli", bogus=1)

    def test_movement_def_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            MovementDef(bogus=1)


class TestYAMLRoundTripRejectsUnknown:
    """Simulate what happens when a user loads a YAML score with typos."""

    def test_yaml_score_with_typo_fails_validation(self) -> None:
        """A YAML score with a typo'd field must fail, not silently pass."""
        import yaml as _yaml

        score_yaml = """\
name: my-score
workspace: /tmp/test
sheet:
  size: 5
  total_items: 5
prompt:
  template: "Do something"
retries:
  max_attempts: 5
"""
        data = _yaml.safe_load(score_yaml)
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_yaml_nested_typo_fails(self) -> None:
        """A typo in a nested config block must fail."""
        import yaml as _yaml

        score_yaml = """\
name: my-score
workspace: /tmp/test
sheet:
  size: 5
  total_items: 5
prompt:
  template: "Do something"
retry:
  max_attemps: 5
"""
        data = _yaml.safe_load(score_yaml)
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)


class TestLoadCheckpointDaemonRegistry:
    """Adversarial tests for _load_checkpoint reading from daemon registry
    instead of workspace JSON files (F-255 fix)."""

    def test_load_checkpoint_returns_none_for_missing_job(self) -> None:
        """When the registry has no checkpoint, return None — don't look
        for workspace files."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        manager = MagicMock()
        manager._registry = AsyncMock()
        manager._registry.load_checkpoint = AsyncMock(return_value=None)

        from mozart.daemon.manager import JobManager

        result = asyncio.run(
            JobManager._load_checkpoint(manager, "test-job", __import__("pathlib").Path("/tmp/ws"))
        )
        assert result is None
        manager._registry.load_checkpoint.assert_called_once_with("test-job")

    def test_load_checkpoint_parses_valid_json(self) -> None:
        """Valid checkpoint JSON from registry should parse to CheckpointState."""
        import asyncio
        import json
        from unittest.mock import AsyncMock, MagicMock

        from mozart.core.checkpoint import CheckpointState

        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            workspace="/tmp/ws",
            total_sheets=5,
        )
        checkpoint_json = json.dumps(state.model_dump(mode="json"))

        manager = MagicMock()
        manager._registry = AsyncMock()
        manager._registry.load_checkpoint = AsyncMock(return_value=checkpoint_json)

        from mozart.daemon.manager import JobManager

        result = asyncio.run(
            JobManager._load_checkpoint(manager, "test-job", __import__("pathlib").Path("/tmp/ws"))
        )
        assert result is not None
        assert result.job_id == "test-job"
        assert result.total_sheets == 5

    def test_load_checkpoint_handles_corrupt_json(self) -> None:
        """Corrupt JSON from registry should return None, not crash."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        manager = MagicMock()
        manager._registry = AsyncMock()
        manager._registry.load_checkpoint = AsyncMock(return_value="{corrupt json!!")

        from mozart.daemon.manager import JobManager

        result = asyncio.run(
            JobManager._load_checkpoint(manager, "test-job", __import__("pathlib").Path("/tmp/ws"))
        )
        assert result is None

    def test_load_checkpoint_handles_valid_json_invalid_state(self) -> None:
        """Valid JSON but not a valid CheckpointState should return None."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        manager = MagicMock()
        manager._registry = AsyncMock()
        manager._registry.load_checkpoint = AsyncMock(
            return_value='{"not_a_real_field": 123}'
        )

        from mozart.daemon.manager import JobManager

        result = asyncio.run(
            JobManager._load_checkpoint(manager, "test-job", __import__("pathlib").Path("/tmp/ws"))
        )
        assert result is None

    def test_load_checkpoint_ignores_workspace_parameter(self) -> None:
        """The workspace parameter is kept for API compat but must not be used
        to read files. Even if a workspace JSON file exists, the daemon
        registry is the only source of truth."""
        import asyncio
        import json
        from pathlib import Path
        from unittest.mock import AsyncMock, MagicMock

        # Create a workspace with a stale checkpoint file
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            stale_file = Path(tmp) / "test-job.json"
            stale_file.write_text(json.dumps({
                "job_id": "STALE-FROM-FILE",
                "workspace": tmp,
                "total_sheets": 999,
            }))

            manager = MagicMock()
            manager._registry = AsyncMock()
            # Registry says no checkpoint
            manager._registry.load_checkpoint = AsyncMock(return_value=None)

            from mozart.daemon.manager import JobManager

            result = asyncio.run(
                JobManager._load_checkpoint(manager, "test-job", Path(tmp))
            )
            # Must return None from registry, NOT the stale workspace file
            assert result is None

    def test_load_checkpoint_empty_string_json(self) -> None:
        """Empty string from registry should not crash."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        manager = MagicMock()
        manager._registry = AsyncMock()
        manager._registry.load_checkpoint = AsyncMock(return_value="")

        from mozart.daemon.manager import JobManager

        result = asyncio.run(
            JobManager._load_checkpoint(manager, "test-job", __import__("pathlib").Path("/tmp/ws"))
        )
        assert result is None
