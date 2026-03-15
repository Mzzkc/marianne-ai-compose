"""Tests for mozart.core.config module."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from mozart.core.config import (
    BackendConfig,
    ConductorConfig,
    ConductorPreferences,
    ConductorRole,
    CostLimitConfig,
    FeedbackConfig,
    InjectionCategory,
    InjectionItem,
    IsolationConfig,
    JobConfig,
    LearningConfig,
    PromptConfig,
    RateLimitConfig,
    RetryConfig,
    SheetConfig,
    SkipWhenCommand,
    ValidationRule,
)


class TestRetryConfig:
    """Tests for RetryConfig model."""

    def test_defaults(self):
        """Test default values are applied."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay_seconds == 10.0
        assert config.max_delay_seconds == 3600.0
        assert config.exponential_base == 2.0
        assert config.jitter is True
        assert config.max_completion_attempts == 5

    def test_custom_values(self):
        """Test custom values override defaults."""
        config = RetryConfig(
            max_retries=5,
            base_delay_seconds=5.0,
            jitter=False,
        )
        assert config.max_retries == 5
        assert config.base_delay_seconds == 5.0
        assert config.jitter is False

    def test_validation_max_retries_non_negative(self):
        """Test max_retries must be non-negative."""
        with pytest.raises(ValidationError):
            RetryConfig(max_retries=-1)

    def test_validation_base_delay_positive(self):
        """Test base_delay_seconds must be positive."""
        with pytest.raises(ValidationError):
            RetryConfig(base_delay_seconds=0)


class TestSheetConfig:
    """Tests for SheetConfig model."""

    def test_required_fields(self):
        """Test required fields are enforced."""
        with pytest.raises(ValidationError):
            SheetConfig()

    def test_total_sheets_calculation(self):
        """Test total_sheets is calculated correctly."""
        config = SheetConfig(size=10, total_items=35)
        assert config.total_sheets == 4  # ceil(35/10)

        config = SheetConfig(size=10, total_items=30)
        assert config.total_sheets == 3  # exact

    def test_start_item_default(self):
        """Test start_item defaults to 1."""
        config = SheetConfig(size=10, total_items=30)
        assert config.start_item == 1

    def test_skip_when_default_empty(self):
        """Test skip_when defaults to empty dict."""
        config = SheetConfig(size=1, total_items=5)
        assert config.skip_when == {}

    def test_skip_when_accepts_conditions(self):
        """Test skip_when accepts condition strings per sheet."""
        config = SheetConfig(
            size=1, total_items=5,
            skip_when={
                3: "sheets.get(1) and sheets[1].validation_passed",
                5: "job.total_retry_count > 10",
            },
        )
        assert config.skip_when[3] == "sheets.get(1) and sheets[1].validation_passed"
        assert config.skip_when[5] == "job.total_retry_count > 10"

    def test_skip_when_command_default_empty(self):
        """Test skip_when_command defaults to empty dict."""
        config = SheetConfig(size=1, total_items=5)
        assert config.skip_when_command == {}

    def test_skip_when_command_accepts_rules(self):
        """Test skip_when_command accepts SkipWhenCommand per sheet."""
        config = SheetConfig(
            size=1, total_items=10,
            skip_when_command={
                8: {"command": 'grep -q "TOTAL_PHASES: [1]$" "{workspace}/plan.md"',
                    "description": "Skip phase 2 if plan has only 1 phase"},
                9: {"command": 'grep -q "TOTAL_PHASES: [1]$" "{workspace}/plan.md"'},
            },
        )
        assert 8 in config.skip_when_command
        assert config.skip_when_command[8].command.startswith("grep")
        assert config.skip_when_command[8].description == "Skip phase 2 if plan has only 1 phase"
        assert config.skip_when_command[9].description is None

    def test_skip_when_command_in_jobconfig(self):
        """Test skip_when_command works in full JobConfig."""
        config = JobConfig.model_validate({
            "name": "test",
            "sheet": {
                "size": 1, "total_items": 5,
                "skip_when_command": {
                    3: {"command": "test -f /tmp/skip", "description": "test"},
                },
            },
            "prompt": {"template": "{{ sheet_num }}"},
        })
        assert 3 in config.sheet.skip_when_command
        assert config.sheet.skip_when_command[3].timeout_seconds == 10.0

    def test_prompt_extensions_default_empty(self):
        """Test sheet prompt_extensions defaults to empty dict."""
        config = SheetConfig(size=1, total_items=5)
        assert config.prompt_extensions == {}

    def test_prompt_extensions_accepts_per_sheet(self):
        """Test sheet prompt_extensions accepts per-sheet directives."""
        config = SheetConfig(
            size=1, total_items=5,
            prompt_extensions={
                2: ["Be careful with imports"],
                4: ["Run linter before committing", "Check type annotations"],
            },
        )
        assert 2 in config.prompt_extensions
        assert len(config.prompt_extensions[2]) == 1
        assert len(config.prompt_extensions[4]) == 2

    def test_prompt_extensions_in_jobconfig(self):
        """Test sheet prompt_extensions works in full JobConfig."""
        config = JobConfig.model_validate({
            "name": "test",
            "sheet": {
                "size": 1, "total_items": 3,
                "prompt_extensions": {
                    2: ["Extra directive for sheet 2"],
                },
            },
            "prompt": {"template": "{{ sheet_num }}"},
        })
        assert 2 in config.sheet.prompt_extensions
        assert "Extra directive for sheet 2" in config.sheet.prompt_extensions[2]


class TestPreludeCadenza:
    """Tests for prelude/cadenza context injection config (GH#53)."""

    def test_prelude_default_empty(self):
        """Test prelude defaults to empty list."""
        config = SheetConfig(size=1, total_items=5)
        assert config.prelude == []

    def test_cadenzas_default_empty(self):
        """Test cadenzas defaults to empty dict."""
        config = SheetConfig(size=1, total_items=5)
        assert config.cadenzas == {}

    def test_prelude_accepts_injection_items(self):
        """Test prelude accepts a list of InjectionItems."""
        config = SheetConfig(
            size=1,
            total_items=3,
            prelude=[
                InjectionItem(file="shared-context.md", **{"as": "context"}),
                InjectionItem(file="shared-skill.md", **{"as": "skill"}),
            ],
        )
        assert len(config.prelude) == 2
        assert config.prelude[0].file == "shared-context.md"
        assert config.prelude[0].as_ == InjectionCategory.CONTEXT
        assert config.prelude[1].as_ == InjectionCategory.SKILL

    def test_cadenzas_accepts_per_sheet_items(self):
        """Test cadenzas accepts per-sheet injection items."""
        config = SheetConfig(
            size=1,
            total_items=3,
            cadenzas={
                2: [InjectionItem(file="sheet2-tool.md", **{"as": "tool"})],
            },
        )
        assert 2 in config.cadenzas
        assert len(config.cadenzas[2]) == 1
        assert config.cadenzas[2][0].as_ == InjectionCategory.TOOL

    def test_prelude_cadenza_in_jobconfig(self):
        """Test prelude/cadenzas work via YAML-like dict in JobConfig."""
        config = JobConfig.model_validate({
            "name": "test",
            "sheet": {
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "context.md", "as": "context"},
                ],
                "cadenzas": {
                    1: [{"file": "setup.md", "as": "skill"}],
                },
            },
            "prompt": {"template": "{{ sheet_num }}"},
        })
        assert len(config.sheet.prelude) == 1
        assert config.sheet.prelude[0].file == "context.md"
        assert 1 in config.sheet.cadenzas

    def test_injection_item_invalid_category_raises(self):
        """Test InjectionItem rejects invalid category values."""
        with pytest.raises(ValidationError):
            InjectionItem(file="test.md", **{"as": "invalid_category"})

    def test_backward_compat_no_prelude(self):
        """Test existing configs without prelude/cadenzas still parse."""
        config = JobConfig.model_validate({
            "name": "legacy",
            "sheet": {"size": 5, "total_items": 10},
            "prompt": {"template": "{{ sheet_num }}"},
        })
        assert config.sheet.prelude == []
        assert config.sheet.cadenzas == {}


class TestValidationRule:
    """Tests for ValidationRule model."""

    def test_file_exists_rule(self):
        """Test file_exists validation rule."""
        rule = ValidationRule(
            type="file_exists",
            path="{workspace}/output.txt",
            description="Output file exists",
        )
        assert rule.type == "file_exists"
        assert rule.path == "{workspace}/output.txt"
        assert rule.pattern is None

    def test_content_contains_rule(self):
        """Test content_contains validation rule."""
        rule = ValidationRule(
            type="content_contains",
            path="{workspace}/log.txt",
            pattern="SUCCESS",
            description="Success marker present",
        )
        assert rule.type == "content_contains"
        assert rule.pattern == "SUCCESS"


class TestBackendConfig:
    """Tests for BackendConfig model."""

    def test_claude_cli_default(self):
        """Test claude_cli is the default backend type."""
        config = BackendConfig()
        assert config.type == "claude_cli"
        assert config.skip_permissions is True

    def test_anthropic_api_config(self):
        """Test anthropic_api backend configuration."""
        config = BackendConfig(
            type="anthropic_api",
            model="claude-sonnet-4-5-20250929",
            api_key_env="ANTHROPIC_API_KEY",
        )
        assert config.type == "anthropic_api"
        assert config.model == "claude-sonnet-4-5-20250929"

    def test_disable_mcp_default(self):
        """Test disable_mcp defaults to True for faster batch execution."""
        config = BackendConfig()
        assert config.disable_mcp is True

    def test_disable_mcp_can_be_disabled(self):
        """Test MCP can be re-enabled when needed."""
        config = BackendConfig(disable_mcp=False)
        assert config.disable_mcp is False

    def test_output_format_default(self):
        """Test output_format defaults to text for human-readable output."""
        config = BackendConfig()
        assert config.output_format == "text"

    def test_output_format_json(self):
        """Test output_format can be json."""
        config = BackendConfig(output_format="json")
        assert config.output_format == "json"

    def test_output_format_text(self):
        """Test output_format can be text."""
        config = BackendConfig(output_format="text")
        assert config.output_format == "text"

    def test_output_format_stream_json(self):
        """Test output_format can be stream-json."""
        config = BackendConfig(output_format="stream-json")
        assert config.output_format == "stream-json"

    def test_cli_model_default_none(self):
        """Test cli_model defaults to None (uses Claude Code default)."""
        config = BackendConfig()
        assert config.cli_model is None

    def test_cli_model_override(self):
        """Test cli_model can be set to specific model."""
        config = BackendConfig(cli_model="claude-sonnet-4-5-20250929")
        assert config.cli_model == "claude-sonnet-4-5-20250929"

    def test_allowed_tools_default_none(self):
        """Test allowed_tools defaults to None (all tools available)."""
        config = BackendConfig()
        assert config.allowed_tools is None

    def test_allowed_tools_restriction(self):
        """Test allowed_tools can restrict to specific tools."""
        config = BackendConfig(allowed_tools=["Read", "Grep", "Glob"])
        assert config.allowed_tools == ["Read", "Grep", "Glob"]

    def test_allowed_tools_empty_list(self):
        """Test allowed_tools as empty list (very restrictive)."""
        config = BackendConfig(allowed_tools=[])
        assert config.allowed_tools == []

    def test_system_prompt_file_default_none(self):
        """Test system_prompt_file defaults to None (default prompt)."""
        config = BackendConfig()
        assert config.system_prompt_file is None

    def test_system_prompt_file_path(self):
        """Test system_prompt_file accepts Path."""
        config = BackendConfig(system_prompt_file=Path("/custom/prompt.md"))
        assert config.system_prompt_file == Path("/custom/prompt.md")

    def test_timeout_seconds_default(self):
        """Test timeout_seconds default is 30 minutes."""
        config = BackendConfig()
        assert config.timeout_seconds == 1800.0

    def test_timeout_overrides_default_empty(self):
        """Test timeout_overrides defaults to empty dict."""
        config = BackendConfig()
        assert config.timeout_overrides == {}

    def test_timeout_overrides_per_sheet(self):
        """Test per-sheet timeout overrides."""
        config = BackendConfig(timeout_overrides={1: 60.0, 7: 28800.0})
        assert config.timeout_overrides == {1: 60.0, 7: 28800.0}
        assert config.timeout_overrides.get(1) == 60.0
        assert config.timeout_overrides.get(7) == 28800.0
        assert config.timeout_overrides.get(2) is None

    def test_timeout_overrides_does_not_affect_global(self):
        """Test that per-sheet overrides don't change the global timeout."""
        config = BackendConfig(
            timeout_seconds=2400.0,
            timeout_overrides={7: 28800.0},
        )
        assert config.timeout_seconds == 2400.0
        assert config.timeout_overrides[7] == 28800.0

    def test_cli_extra_args_default_empty(self):
        """Test cli_extra_args defaults to empty list."""
        config = BackendConfig()
        assert config.cli_extra_args == []

    def test_cli_extra_args_escape_hatch(self):
        """Test cli_extra_args allows raw flags as escape hatch."""
        config = BackendConfig(cli_extra_args=["--verbose", "--some-new-flag"])
        assert config.cli_extra_args == ["--verbose", "--some-new-flag"]

    def test_backwards_compatibility_minimal(self):
        """Test minimal config (pre-new-fields) still works."""
        # This is what old configs would look like
        config = BackendConfig(
            type="claude_cli",
            skip_permissions=True,
        )
        assert config.type == "claude_cli"
        assert config.skip_permissions is True
        # New fields should have sensible defaults
        assert config.disable_mcp is True
        assert config.output_format == "text"
        assert config.cli_model is None
        assert config.allowed_tools is None

    def test_backwards_compatibility_with_cli_extra_args(self):
        """Test old configs using cli_extra_args still work."""
        # This simulates configs that used cli_extra_args for MCP disable
        config = BackendConfig(
            type="claude_cli",
            skip_permissions=True,
            cli_extra_args=["--strict-mcp-config", "{}"],
        )
        assert config.cli_extra_args == ["--strict-mcp-config", "{}"]
        # Note: Both disable_mcp=True default AND cli_extra_args will
        # add the flag, but CLI should handle duplicate gracefully

    def test_full_cli_config(self):
        """Test fully specified CLI config with all new options."""
        config = BackendConfig(
            type="claude_cli",
            skip_permissions=True,
            disable_mcp=False,  # Explicitly enable MCP
            output_format="stream-json",
            cli_model="claude-sonnet-4-5-20250929",
            allowed_tools=["Read", "Edit", "Write"],
            system_prompt_file=Path("./prompts/custom.md"),
            working_directory=Path("/project"),
            timeout_seconds=3600,
            cli_extra_args=["--verbose"],
        )
        assert config.type == "claude_cli"
        assert config.skip_permissions is True
        assert config.disable_mcp is False
        assert config.output_format == "stream-json"
        assert config.cli_model == "claude-sonnet-4-5-20250929"
        assert config.allowed_tools == ["Read", "Edit", "Write"]
        assert config.system_prompt_file == Path("./prompts/custom.md")
        assert config.working_directory == Path("/project")
        assert config.timeout_seconds == 3600
        assert config.cli_extra_args == ["--verbose"]


class TestSheetBackendOverride:
    """Tests for SheetBackendOverride per-sheet backend control (GH#78)."""

    def test_sheet_overrides_default_empty(self):
        """Test sheet_overrides defaults to empty dict."""
        config = BackendConfig()
        assert config.sheet_overrides == {}

    def test_sheet_overrides_model_override(self):
        """Test per-sheet model override."""
        from mozart.core.config.backend import SheetBackendOverride

        config = BackendConfig(
            type="anthropic_api",
            model="claude-sonnet-4-5-20250929",
            sheet_overrides={
                1: SheetBackendOverride(model="claude-opus-4-6"),
            },
        )
        override = config.sheet_overrides[1]
        assert override.model == "claude-opus-4-6"
        assert override.temperature is None  # Not overridden

    def test_sheet_overrides_temperature_validation(self):
        """Test temperature must be 0-1 in sheet overrides."""
        import pytest

        from mozart.core.config.backend import SheetBackendOverride

        with pytest.raises(Exception):  # noqa: B017
            SheetBackendOverride(temperature=1.5)

    def test_sheet_overrides_cli_model(self):
        """Test per-sheet CLI model override."""
        from mozart.core.config.backend import SheetBackendOverride

        config = BackendConfig(
            type="claude_cli",
            sheet_overrides={
                3: SheetBackendOverride(cli_model="claude-opus-4-6"),
            },
        )
        assert config.sheet_overrides[3].cli_model == "claude-opus-4-6"

    def test_sheet_overrides_timeout_override(self):
        """Test per-sheet timeout via sheet_overrides."""
        from mozart.core.config.backend import SheetBackendOverride

        config = BackendConfig(
            sheet_overrides={
                5: SheetBackendOverride(timeout_seconds=600.0),
            },
        )
        assert config.sheet_overrides[5].timeout_seconds == 600.0

    def test_sheet_overrides_multiple_sheets(self):
        """Test overrides for multiple sheets simultaneously."""
        from mozart.core.config.backend import SheetBackendOverride

        config = BackendConfig(
            type="anthropic_api",
            sheet_overrides={
                1: SheetBackendOverride(model="claude-opus-4-6", temperature=0.0),
                5: SheetBackendOverride(max_tokens=16384),
                10: SheetBackendOverride(timeout_seconds=3600.0),
            },
        )
        assert len(config.sheet_overrides) == 3
        assert config.sheet_overrides[1].model == "claude-opus-4-6"
        assert config.sheet_overrides[1].temperature == 0.0
        assert config.sheet_overrides[5].max_tokens == 16384
        assert config.sheet_overrides[10].timeout_seconds == 3600.0

    def test_sheet_overrides_model_dump_excludes_none(self):
        """Test model_dump only includes non-None fields for override application."""
        from mozart.core.config.backend import SheetBackendOverride

        override = SheetBackendOverride(model="claude-opus-4-6", temperature=0.2)
        dumped = {k: v for k, v in override.model_dump().items() if v is not None}
        assert dumped == {"model": "claude-opus-4-6", "temperature": 0.2}


import warnings as _warnings_mod


class TestBackendConfigCrossValidation:
    """Tests for BackendConfig cross-field type validation (Q018)."""

    def test_cli_defaults_no_warning(self):
        """Default BackendConfig (type=claude_cli) emits no warnings."""
        with _warnings_mod.catch_warnings(record=True) as w:
            _warnings_mod.simplefilter("always")
            BackendConfig()
            assert len(w) == 0

    def test_api_type_with_cli_fields_warns(self):
        """Setting CLI-specific fields with type=anthropic_api emits a warning."""
        with _warnings_mod.catch_warnings(record=True) as w:
            _warnings_mod.simplefilter("always")
            config = BackendConfig(
                type="anthropic_api",
                cli_model="some-model",
                disable_mcp=False,
            )
            backend_warnings = [x for x in w if "different backend" in str(x.message)]
            assert len(backend_warnings) == 1
            msg = str(backend_warnings[0].message)
            assert "cli_model" in msg
            assert "disable_mcp" in msg
            assert config.type == "anthropic_api"

    def test_cli_type_with_api_fields_warns(self):
        """Setting API-specific fields with type=claude_cli emits a warning."""
        with _warnings_mod.catch_warnings(record=True) as w:
            _warnings_mod.simplefilter("always")
            config = BackendConfig(
                type="claude_cli",
                model="some-other-model",
                temperature=0.5,
            )
            backend_warnings = [x for x in w if "different backend" in str(x.message)]
            assert len(backend_warnings) == 1
            msg = str(backend_warnings[0].message)
            assert "model" in msg
            assert "temperature" in msg
            assert config.type == "claude_cli"

    def test_api_type_with_api_defaults_no_warning(self):
        """API type with default API values should not warn (defaults are allowed)."""
        with _warnings_mod.catch_warnings(record=True) as w:
            _warnings_mod.simplefilter("always")
            BackendConfig(type="anthropic_api")
            backend_warnings = [x for x in w if "different backend" in str(x.message)]
            assert len(backend_warnings) == 0

    def test_ollama_type_with_cli_fields_warns(self):
        """Setting CLI fields with type=ollama should warn."""
        with _warnings_mod.catch_warnings(record=True) as w:
            _warnings_mod.simplefilter("always")
            BackendConfig(type="ollama", skip_permissions=False)
            backend_warnings = [x for x in w if "different backend" in str(x.message)]
            assert len(backend_warnings) == 1
            assert "skip_permissions" in str(backend_warnings[0].message)


class TestConductorPreferences:
    """Tests for ConductorPreferences model."""

    def test_defaults(self):
        """Test default values for conductor preferences."""
        prefs = ConductorPreferences()
        assert prefs.prefer_minimal_output is False
        assert prefs.escalation_response_timeout_seconds == 300.0
        assert prefs.auto_retry_on_transient_errors is True
        assert prefs.notification_channels == []

    def test_custom_values(self):
        """Test custom conductor preferences."""
        prefs = ConductorPreferences(
            prefer_minimal_output=True,
            escalation_response_timeout_seconds=60.0,
            auto_retry_on_transient_errors=False,
            notification_channels=["slack", "desktop"],
        )
        assert prefs.prefer_minimal_output is True
        assert prefs.escalation_response_timeout_seconds == 60.0
        assert prefs.auto_retry_on_transient_errors is False
        assert prefs.notification_channels == ["slack", "desktop"]

    def test_timeout_must_be_positive(self):
        """Test that escalation timeout must be positive."""
        with pytest.raises(ValidationError):
            ConductorPreferences(escalation_response_timeout_seconds=0)
        with pytest.raises(ValidationError):
            ConductorPreferences(escalation_response_timeout_seconds=-10)


class TestConductorConfig:
    """Tests for ConductorConfig model (Vision.md Phase 2)."""

    def test_defaults(self):
        """Test default conductor configuration values."""
        config = ConductorConfig()
        assert config.name == "default"
        assert config.role == ConductorRole.HUMAN
        assert config.identity_context is None
        assert config.preferences is not None
        assert isinstance(config.preferences, ConductorPreferences)

    def test_human_conductor(self):
        """Test human conductor configuration."""
        config = ConductorConfig(
            name="Alice",
            role=ConductorRole.HUMAN,
            identity_context="Lead engineer on the project",
        )
        assert config.name == "Alice"
        assert config.role == ConductorRole.HUMAN
        assert config.identity_context == "Lead engineer on the project"

    def test_ai_conductor(self):
        """Test AI conductor configuration (Vision.md: AI people as peers)."""
        config = ConductorConfig(
            name="Claude Evolution Agent",
            role=ConductorRole.AI,
            identity_context="Self-improving orchestration agent created by RLF",
            preferences=ConductorPreferences(
                prefer_minimal_output=True,
                auto_retry_on_transient_errors=True,
            ),
        )
        assert config.name == "Claude Evolution Agent"
        assert config.role == ConductorRole.AI
        assert config.preferences.prefer_minimal_output is True

    def test_hybrid_conductor(self):
        """Test hybrid conductor configuration (human+AI collaboration)."""
        config = ConductorConfig(
            name="Team Alpha",
            role=ConductorRole.HYBRID,
            identity_context="Human-AI pair conducting evolution",
        )
        assert config.role == ConductorRole.HYBRID

    def test_role_enum_values(self):
        """Test all ConductorRole enum values are valid."""
        assert ConductorRole.HUMAN.value == "human"
        assert ConductorRole.AI.value == "ai"
        assert ConductorRole.HYBRID.value == "hybrid"

    def test_role_from_string(self):
        """Test creating conductor with role as string (YAML loading)."""
        config = ConductorConfig(name="Test", role="ai")
        assert config.role == ConductorRole.AI

    def test_invalid_role(self):
        """Test that invalid role raises ValidationError."""
        with pytest.raises(ValidationError):
            ConductorConfig(role="invalid_role")

    def test_name_validation_min_length(self):
        """Test that name must have at least 1 character."""
        with pytest.raises(ValidationError):
            ConductorConfig(name="")

    def test_name_validation_max_length(self):
        """Test that name is limited to 100 characters."""
        # 100 chars should work
        config = ConductorConfig(name="a" * 100)
        assert len(config.name) == 100
        # 101 chars should fail
        with pytest.raises(ValidationError):
            ConductorConfig(name="a" * 101)

    def test_identity_context_max_length(self):
        """Test that identity_context is limited to 500 characters."""
        # 500 chars should work
        config = ConductorConfig(identity_context="a" * 500)
        assert len(config.identity_context) == 500
        # 501 chars should fail
        with pytest.raises(ValidationError):
            ConductorConfig(identity_context="a" * 501)

    def test_nested_preferences(self):
        """Test that nested preferences are properly parsed."""
        config = ConductorConfig(
            name="Test",
            preferences={
                "prefer_minimal_output": True,
                "escalation_response_timeout_seconds": 120.0,
            },
        )
        assert config.preferences.prefer_minimal_output is True
        assert config.preferences.escalation_response_timeout_seconds == 120.0

    def test_serialization_roundtrip(self):
        """Test that conductor config serializes and deserializes correctly."""
        original = ConductorConfig(
            name="Claude Agent",
            role=ConductorRole.AI,
            identity_context="Test agent",
            preferences=ConductorPreferences(
                prefer_minimal_output=True,
                notification_channels=["slack"],
            ),
        )
        # Serialize to dict
        data = original.model_dump()
        # Deserialize back
        restored = ConductorConfig.model_validate(data)
        assert restored.name == original.name
        assert restored.role == original.role
        assert restored.identity_context == original.identity_context
        assert (
            restored.preferences.prefer_minimal_output
            == original.preferences.prefer_minimal_output
        )
        assert (
            restored.preferences.notification_channels
            == original.preferences.notification_channels
        )


class TestPromptConfig:
    """Tests for PromptConfig model."""

    def test_inline_template(self):
        """Test inline template configuration."""
        config = PromptConfig(template="Hello {{ name }}!")
        assert config.template == "Hello {{ name }}!"
        assert config.template_file is None

    def test_variables(self):
        """Test custom variables in prompt config."""
        config = PromptConfig(
            template="{{ greeting }}",
            variables={"greeting": "Hello!"},
        )
        assert config.variables["greeting"] == "Hello!"

    def test_prompt_extensions_default_empty(self):
        """Test prompt_extensions defaults to empty list."""
        config = PromptConfig(template="test")
        assert config.prompt_extensions == []

    def test_prompt_extensions_accepts_list(self):
        """Test prompt_extensions accepts a list of strings."""
        config = PromptConfig(
            template="test",
            prompt_extensions=["Be thorough", "Follow coding standards"],
        )
        assert len(config.prompt_extensions) == 2
        assert config.prompt_extensions[0] == "Be thorough"

    def test_prompt_extensions_in_jobconfig(self, sample_config_dict: dict):
        """Test prompt_extensions flows through to JobConfig."""
        sample_config_dict["prompt"]["prompt_extensions"] = [
            "Always write tests",
            "Use type hints",
        ]
        config = JobConfig(**sample_config_dict)
        assert len(config.prompt.prompt_extensions) == 2
        assert "Always write tests" in config.prompt.prompt_extensions


class TestJobConfig:
    """Tests for JobConfig model."""

    def test_from_dict(self, sample_config_dict: dict):
        """Test creating JobConfig from dictionary."""
        config = JobConfig(**sample_config_dict)
        assert config.name == "test-job"
        assert config.sheet.total_items == 30
        assert config.backend.type == "claude_cli"

    def test_from_yaml(self, sample_yaml_config: Path):
        """Test loading JobConfig from YAML file."""
        config = JobConfig.from_yaml(sample_yaml_config)
        assert config.name == "test-job"
        assert config.sheet.size == 10

    def test_workspace_default(self, sample_config_dict: dict):
        """Test workspace defaults to ./workspace resolved to absolute path (#12/#34)."""
        config = JobConfig(**sample_config_dict)
        assert config.workspace.is_absolute()
        assert config.workspace == Path("./workspace").resolve()

    def test_custom_workspace(self, sample_config_dict: dict):
        """Test custom workspace path is resolved to absolute."""
        sample_config_dict["workspace"] = "/custom/path"
        config = JobConfig(**sample_config_dict)
        assert config.workspace == Path("/custom/path")

    def test_conductor_default(self, sample_config_dict: dict):
        """Test conductor has default configuration."""
        config = JobConfig(**sample_config_dict)
        assert config.conductor is not None
        assert config.conductor.name == "default"
        assert config.conductor.role == ConductorRole.HUMAN

    def test_conductor_custom(self, sample_config_dict: dict):
        """Test custom conductor configuration."""
        sample_config_dict["conductor"] = {
            "name": "Evolution Agent",
            "role": "ai",
            "identity_context": "Self-improving orchestration",
            "preferences": {
                "prefer_minimal_output": True,
            },
        }
        config = JobConfig(**sample_config_dict)
        assert config.conductor.name == "Evolution Agent"
        assert config.conductor.role == ConductorRole.AI
        assert config.conductor.identity_context == "Self-improving orchestration"
        assert config.conductor.preferences.prefer_minimal_output is True


class TestWorkspacePathResolution109:
    """Regression tests for #109: workspace resolves relative to score file, not process CWD."""

    # Minimal valid score YAML — sheet and prompt are required by JobConfig
    _SCORE_TEMPLATE = (
        "name: {name}\nworkspace: {workspace}\n"
        "sheet:\n  size: 1\n  total_items: 1\nprompt:\n  template: 'test'\n"
    )

    def _load(self, score_file: "Path") -> "JobConfig":
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return JobConfig.from_yaml(score_file)

    def test_path_resolved_relative_to_score_file_not_cwd(
        self, tmp_path: "Path", monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """Relative workspace must resolve to score file's parent, not CWD."""
        score_dir = tmp_path / "subdir"
        score_dir.mkdir()
        score_file = score_dir / "score.yaml"
        score_file.write_text(self._SCORE_TEMPLATE.format(name="test-job", workspace="./outputs"))
        monkeypatch.chdir(tmp_path)
        config = self._load(score_file)
        expected = (score_dir / "outputs").resolve()
        assert config.workspace == expected
        assert config.workspace != (tmp_path / "outputs").resolve()
        assert config.workspace.is_absolute()

    def test_absolute_path_unchanged(self, tmp_path: "Path") -> None:
        """Absolute workspace in score must be preserved as-is."""
        score_file = tmp_path / "score.yaml"
        score_file.write_text(self._SCORE_TEMPLATE.format(name="test-job", workspace="/absolute/path"))
        config = self._load(score_file)
        assert config.workspace == Path("/absolute/path")

    def test_dotdot_path_resolved_correctly(
        self, tmp_path: "Path", monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """Parent-directory traversal must resolve correctly relative to score file."""
        score_dir = tmp_path / "a" / "b" / "c"
        score_dir.mkdir(parents=True)
        score_file = score_dir / "score.yaml"
        score_file.write_text(self._SCORE_TEMPLATE.format(name="test-job", workspace="../../shared"))
        monkeypatch.chdir(tmp_path)
        config = self._load(score_file)
        expected = (tmp_path / "a" / "shared").resolve()
        assert config.workspace == expected
        assert config.workspace.is_absolute()

    def test_path_resolution_happens_before_ipc(
        self, tmp_path: "Path", monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """Config loaded via from_yaml has absolute workspace before any IPC."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        score_file = project_dir / "score.yaml"
        score_file.write_text(self._SCORE_TEMPLATE.format(name="test-job", workspace="./workspaces/job"))
        monkeypatch.chdir(tmp_path)
        config = self._load(score_file)
        expected = (project_dir / "workspaces" / "job").resolve()
        assert config.workspace == expected
        assert config.workspace.is_absolute()

    def test_same_path_different_score_locations(
        self, tmp_path: "Path", monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """Same relative workspace in two score files at different dirs resolves differently."""
        dir_a = tmp_path / "project_a"
        dir_b = tmp_path / "project_b"
        dir_a.mkdir()
        dir_b.mkdir()
        score_a = dir_a / "score.yaml"
        score_b = dir_b / "score.yaml"
        score_a.write_text(self._SCORE_TEMPLATE.format(name="job-a", workspace="./outputs"))
        score_b.write_text(self._SCORE_TEMPLATE.format(name="job-b", workspace="./outputs"))
        monkeypatch.chdir(tmp_path)
        config_a = self._load(score_a)
        config_b = self._load(score_b)
        assert config_a.workspace == (dir_a / "outputs").resolve()
        assert config_b.workspace == (dir_b / "outputs").resolve()
        assert config_a.workspace != config_b.workspace


class TestIsolationConfig:
    """Tests for IsolationConfig edge cases and defaults."""

    def test_defaults(self):
        config = IsolationConfig()
        assert config.enabled is False
        assert config.mode == "worktree"
        assert config.cleanup_on_success is True
        assert config.cleanup_on_failure is False

    def test_enabled_with_worktree(self):
        config = IsolationConfig(enabled=True, mode="worktree")
        assert config.enabled is True
        assert config.mode == "worktree"

    def test_lock_during_execution_default(self):
        config = IsolationConfig()
        assert config.lock_during_execution is True

    def test_fallback_on_error_default(self):
        config = IsolationConfig()
        assert config.fallback_on_error is True


class TestLearningConfig:
    """Tests for LearningConfig validation and edge cases."""

    def test_defaults(self):
        config = LearningConfig()
        assert config.enabled is True
        assert config.high_confidence_threshold == 0.7
        assert config.min_confidence_threshold == 0.3

    def test_threshold_boundaries(self):
        """Test confidence thresholds at boundary values."""
        config = LearningConfig(
            high_confidence_threshold=1.0,
            min_confidence_threshold=0.0,
        )
        assert config.high_confidence_threshold == 1.0
        assert config.min_confidence_threshold == 0.0

    def test_disabled_learning(self):
        config = LearningConfig(enabled=False)
        assert config.enabled is False


class TestCostLimitConfig:
    """Tests for CostLimitConfig validation and edge cases."""

    def test_defaults(self):
        config = CostLimitConfig()
        assert config.enabled is False
        assert config.max_cost_per_sheet is None
        assert config.max_cost_per_job is None

    def test_enabled_with_limits(self):
        config = CostLimitConfig(
            enabled=True,
            max_cost_per_sheet=1.0,
            max_cost_per_job=10.0,
        )
        assert config.enabled is True
        assert config.max_cost_per_sheet == 1.0
        assert config.max_cost_per_job == 10.0

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            CostLimitConfig(max_cost_per_sheet=-1.0)

    def test_zero_cost_rejected(self):
        with pytest.raises(ValidationError):
            CostLimitConfig(max_cost_per_sheet=0.0)


class TestRateLimitConfig:
    """Tests for RateLimitConfig model."""

    def test_defaults(self):
        config = RateLimitConfig()
        assert config.wait_minutes == 60
        assert config.max_waits == 24

    def test_custom_values(self):
        config = RateLimitConfig(wait_minutes=2, max_waits=3)
        assert config.wait_minutes == 2
        assert config.max_waits == 3

    def test_negative_waits_rejected(self):
        with pytest.raises(ValidationError):
            RateLimitConfig(max_waits=-1)


class TestJobConfigEdgeCases:
    """Edge case tests for JobConfig — nested validation, type coercion, missing fields."""

    def test_minimal_config_from_yaml_dict(self):
        """Minimal YAML-like dict should produce a valid config."""
        config = JobConfig.model_validate({
            "name": "minimal",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 5, "total_items": 10},
            "prompt": {"template": "{{ sheet_num }}"},
        })
        assert config.name == "minimal"
        assert config.sheet.total_sheets == 2

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            JobConfig.model_validate({
                "backend": {"type": "claude_cli"},
                "sheet": {"size": 5, "total_items": 10},
                "prompt": {"template": "x"},
            })

    def test_string_workspace_coerces_to_path(self):
        """String workspace from YAML should coerce to Path."""
        config = JobConfig.model_validate({
            "name": "test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 5, "total_items": 10},
            "prompt": {"template": "x"},
            "workspace": "/tmp/test-workspace",
        })
        assert isinstance(config.workspace, Path)
        assert str(config.workspace) == "/tmp/test-workspace"

    def test_nested_validation_rule_types(self):
        """ValidationRules with different types should all parse."""
        config = JobConfig.model_validate({
            "name": "test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 5, "total_items": 10},
            "prompt": {"template": "x"},
            "validations": [
                {"type": "file_exists", "path": "{workspace}/out.txt", "description": "check"},
                {"type": "command_succeeds", "command": "echo ok", "description": "run"},
            ],
        })
        assert len(config.validations) == 2
        assert config.validations[0].type == "file_exists"
        assert config.validations[1].type == "command_succeeds"

    def test_model_dump_roundtrip(self):
        """Config should survive model_dump -> model_validate roundtrip."""
        original = JobConfig.model_validate({
            "name": "roundtrip",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 10, "total_items": 50},
            "prompt": {"template": "{{ sheet_num }}"},
            "retry": {"max_retries": 5},
        })
        dumped = original.model_dump()
        restored = JobConfig.model_validate(dumped)
        assert restored.name == original.name
        assert restored.retry.max_retries == 5
        assert restored.sheet.total_sheets == original.sheet.total_sheets


class TestFeedbackConfig:
    """Tests for FeedbackConfig model."""

    def test_defaults_disabled(self):
        """Feedback is disabled by default."""
        config = FeedbackConfig()
        assert config.enabled is False
        assert config.format == "json"

    def test_custom_pattern(self):
        """Custom feedback pattern is accepted."""
        config = FeedbackConfig(
            enabled=True,
            pattern=r"<<<FEEDBACK>>>(.*?)<<<\/FEEDBACK>>>",
            format="yaml",
        )
        assert config.enabled is True
        assert config.format == "yaml"
        assert "FEEDBACK" in config.pattern

    def test_text_format_accepted(self):
        """Text format should be accepted."""
        config = FeedbackConfig(enabled=True, format="text")
        assert config.format == "text"

    def test_jobconfig_includes_feedback(self):
        """JobConfig should include feedback as a top-level field."""
        config = JobConfig.model_validate({
            "name": "feedback-test",
            "sheet": {"size": 1, "total_items": 5},
            "prompt": {"template": "test"},
            "feedback": {"enabled": True},
        })
        assert config.feedback.enabled is True

    def test_jobconfig_feedback_defaults(self):
        """JobConfig should have feedback with defaults if not specified."""
        config = JobConfig.model_validate({
            "name": "minimal",
            "sheet": {"size": 1, "total_items": 5},
            "prompt": {"template": "test"},
        })
        assert config.feedback.enabled is False


class TestSkipWhenCommand:
    """Tests for SkipWhenCommand model."""

    def test_defaults(self):
        """Test default values."""
        cmd = SkipWhenCommand(command="grep -q DONE file.txt")
        assert cmd.command == "grep -q DONE file.txt"
        assert cmd.description is None
        assert cmd.timeout_seconds == 10.0

    def test_custom_values(self):
        """Test custom values."""
        cmd = SkipWhenCommand(
            command="test -f output.txt",
            description="Skip if output exists",
            timeout_seconds=30.0,
        )
        assert cmd.description == "Skip if output exists"
        assert cmd.timeout_seconds == 30.0

    def test_timeout_must_be_positive(self):
        """Test timeout_seconds must be > 0."""
        with pytest.raises(ValidationError):
            SkipWhenCommand(command="echo hi", timeout_seconds=0)

    def test_timeout_max_60(self):
        """Test timeout_seconds capped at 60."""
        with pytest.raises(ValidationError):
            SkipWhenCommand(command="echo hi", timeout_seconds=61)

    def test_command_required(self):
        """Test command field is required."""
        with pytest.raises(ValidationError):
            SkipWhenCommand()
