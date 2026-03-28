"""Tests for mozart.core.config.instruments — Instrument Plugin System data models.

TDD: These tests define the contract for InstrumentProfile, ModelCapacity,
CliProfile, and related sub-models. Implementation follows.

Tests cover: happy path, defaults, validation, adversarial inputs, edge cases.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


# --- ModelCapacity ---


class TestModelCapacity:
    """Tests for ModelCapacity data model."""

    def test_valid_model(self):
        """Test a fully specified model capacity."""
        from mozart.core.config.instruments import ModelCapacity

        mc = ModelCapacity(
            name="gemini-2.5-pro",
            context_window=1_000_000,
            cost_per_1k_input=0.00125,
            cost_per_1k_output=0.005,
            max_output_tokens=65536,
        )
        assert mc.name == "gemini-2.5-pro"
        assert mc.context_window == 1_000_000
        assert mc.cost_per_1k_input == 0.00125
        assert mc.cost_per_1k_output == 0.005
        assert mc.max_output_tokens == 65536

    def test_optional_max_output_tokens(self):
        """max_output_tokens is optional (None by default)."""
        from mozart.core.config.instruments import ModelCapacity

        mc = ModelCapacity(
            name="test-model",
            context_window=128000,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
        )
        assert mc.max_output_tokens is None

    def test_context_window_must_be_positive(self):
        """context_window must be >= 1."""
        from mozart.core.config.instruments import ModelCapacity

        with pytest.raises(ValidationError, match="context_window"):
            ModelCapacity(
                name="bad",
                context_window=0,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_cost_must_be_non_negative(self):
        """Costs cannot be negative."""
        from mozart.core.config.instruments import ModelCapacity

        with pytest.raises(ValidationError, match="cost_per_1k_input"):
            ModelCapacity(
                name="bad",
                context_window=1000,
                cost_per_1k_input=-0.01,
                cost_per_1k_output=0.0,
            )

    def test_max_output_tokens_must_be_positive_if_set(self):
        """max_output_tokens must be >= 1 when provided."""
        from mozart.core.config.instruments import ModelCapacity

        with pytest.raises(ValidationError, match="max_output_tokens"):
            ModelCapacity(
                name="bad",
                context_window=1000,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
                max_output_tokens=0,
            )

    def test_free_model(self):
        """Zero cost is valid (local models, free tiers)."""
        from mozart.core.config.instruments import ModelCapacity

        mc = ModelCapacity(
            name="local-llama",
            context_window=32768,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
        )
        assert mc.cost_per_1k_input == 0.0
        assert mc.cost_per_1k_output == 0.0


# --- CliCommand ---


class TestCliCommand:
    """Tests for CliCommand sub-model."""

    def test_minimal_command(self):
        """Test minimal CLI command with just executable and prompt flag."""
        from mozart.core.config.instruments import CliCommand

        cmd = CliCommand(
            executable="claude",
            prompt_flag="-p",
        )
        assert cmd.executable == "claude"
        assert cmd.prompt_flag == "-p"
        assert cmd.subcommand is None
        assert cmd.model_flag is None
        assert cmd.auto_approve_flag is None
        assert cmd.extra_flags == []
        assert cmd.env == {}

    def test_full_command(self):
        """Test fully-specified CLI command."""
        from mozart.core.config.instruments import CliCommand

        cmd = CliCommand(
            executable="codex",
            subcommand="exec",
            prompt_flag=None,  # positional
            model_flag="--model",
            auto_approve_flag="--full-auto",
            output_format_flag="--json",
            output_format_value=None,  # flag-only
            extra_flags=["--skip-git-repo-check"],
            env={"CODEX_API_KEY": "${CODEX_API_KEY}"},
        )
        assert cmd.executable == "codex"
        assert cmd.subcommand == "exec"
        assert cmd.prompt_flag is None
        assert cmd.output_format_value is None
        assert len(cmd.extra_flags) == 1
        assert cmd.env["CODEX_API_KEY"] == "${CODEX_API_KEY}"

    def test_null_prompt_flag_for_positional(self):
        """prompt_flag=None means the prompt is a positional argument."""
        from mozart.core.config.instruments import CliCommand

        cmd = CliCommand(executable="cline", prompt_flag=None)
        assert cmd.prompt_flag is None


# --- CliOutputConfig ---


class TestCliOutputConfig:
    """Tests for CliOutputConfig sub-model."""

    def test_text_format_defaults(self):
        """Text format has sensible defaults — no path extraction."""
        from mozart.core.config.instruments import CliOutputConfig

        out = CliOutputConfig()
        assert out.format == "text"
        assert out.result_path is None
        assert out.error_path is None

    def test_json_format_with_paths(self):
        """JSON format specifies dot-paths for extraction."""
        from mozart.core.config.instruments import CliOutputConfig

        out = CliOutputConfig(
            format="json",
            result_path="result",
            error_path="error.message",
            input_tokens_path="usage.input_tokens",
            output_tokens_path="usage.output_tokens",
        )
        assert out.format == "json"
        assert out.result_path == "result"
        assert out.input_tokens_path == "usage.input_tokens"

    def test_jsonl_format_with_event_filtering(self):
        """JSONL format specifies event type and filter for completion."""
        from mozart.core.config.instruments import CliOutputConfig

        out = CliOutputConfig(
            format="jsonl",
            completion_event_type="item.completed",
            completion_event_filter={"type": "agent_message"},
        )
        assert out.format == "jsonl"
        assert out.completion_event_type == "item.completed"
        assert out.completion_event_filter == {"type": "agent_message"}

    def test_invalid_format_rejected(self):
        """Only text, json, jsonl are valid formats."""
        from mozart.core.config.instruments import CliOutputConfig

        with pytest.raises(ValidationError, match="format"):
            CliOutputConfig(format="xml")  # type: ignore[arg-type]


# --- CliErrorConfig ---


class TestCliErrorConfig:
    """Tests for CliErrorConfig sub-model."""

    def test_defaults(self):
        """Default is exit code 0 = success, no patterns."""
        from mozart.core.config.instruments import CliErrorConfig

        err = CliErrorConfig()
        assert err.success_exit_codes == [0]
        assert err.rate_limit_patterns == []
        assert err.auth_error_patterns == []

    def test_custom_patterns(self):
        """Custom regex patterns for rate limits and auth errors."""
        from mozart.core.config.instruments import CliErrorConfig

        err = CliErrorConfig(
            success_exit_codes=[0, 1],
            rate_limit_patterns=[r"rate.?limit", "429"],
            auth_error_patterns=[r"authenticat", "unauthorized"],
        )
        assert err.success_exit_codes == [0, 1]
        assert len(err.rate_limit_patterns) == 2

    def test_rate_limit_event_fields(self):
        """Structured rate limit detection for JSONL instruments."""
        from mozart.core.config.instruments import CliErrorConfig

        err = CliErrorConfig(
            rate_limit_event_type="error",
            rate_limit_event_filter={"code": "rate_limited"},
        )
        assert err.rate_limit_event_type == "error"


# --- CliProfile ---


class TestCliProfile:
    """Tests for CliProfile data model."""

    def test_minimal_profile(self):
        """Minimal CLI profile with just a command and output config."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )

        profile = CliProfile(
            command=CliCommand(executable="test-cli", prompt_flag="-p"),
            output=CliOutputConfig(),
        )
        assert profile.command.executable == "test-cli"
        assert profile.output.format == "text"
        assert profile.errors.success_exit_codes == [0]

    def test_full_profile(self):
        """Full CLI profile with all sub-models specified."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliErrorConfig,
            CliOutputConfig,
            CliProfile,
        )

        profile = CliProfile(
            command=CliCommand(
                executable="gemini",
                prompt_flag="-p",
                model_flag="-m",
                auto_approve_flag="--yolo",
                output_format_flag="--output-format",
                output_format_value="json",
            ),
            output=CliOutputConfig(
                format="json",
                result_path="response",
                error_path="error.message",
            ),
            errors=CliErrorConfig(
                rate_limit_patterns=[r"rate.?limit", "429"],
                auth_error_patterns=[r"authenticat"],
            ),
        )
        assert profile.command.auto_approve_flag == "--yolo"
        assert profile.errors.rate_limit_patterns[0] == r"rate.?limit"


# --- CodeModeConfig + CodeModeInterface ---


class TestCodeModeConfig:
    """Tests for CodeModeConfig and CodeModeInterface (foundation — not wired in v1)."""

    def test_code_mode_interface(self):
        """CodeModeInterface stores TypeScript interface definitions."""
        from mozart.core.config.instruments import CodeModeInterface

        iface = CodeModeInterface(
            name="Workspace",
            typescript="interface Workspace { read(path: string): Promise<string>; }",
            description="File operations in the job workspace",
        )
        assert iface.name == "Workspace"
        assert "interface Workspace" in iface.typescript

    def test_code_mode_config_defaults(self):
        """CodeModeConfig has sensible defaults."""
        from mozart.core.config.instruments import CodeModeConfig

        cfg = CodeModeConfig()
        assert cfg.runtime == "deno"
        assert cfg.max_execution_ms == 30000
        assert cfg.interfaces == []

    def test_code_mode_config_with_interfaces(self):
        """CodeModeConfig with declared interfaces."""
        from mozart.core.config.instruments import CodeModeConfig, CodeModeInterface

        cfg = CodeModeConfig(
            runtime="node_vm",
            max_execution_ms=10000,
            interfaces=[
                CodeModeInterface(
                    name="FS",
                    typescript="interface FS { read(p: string): string; }",
                ),
            ],
        )
        assert cfg.runtime == "node_vm"
        assert len(cfg.interfaces) == 1

    def test_invalid_runtime_rejected(self):
        """Only deno, node_vm, v8_isolate are valid runtimes."""
        from mozart.core.config.instruments import CodeModeConfig

        with pytest.raises(ValidationError, match="runtime"):
            CodeModeConfig(runtime="python")  # type: ignore[arg-type]

    def test_max_execution_ms_bounds(self):
        """max_execution_ms must be >= 100."""
        from mozart.core.config.instruments import CodeModeConfig

        with pytest.raises(ValidationError, match="max_execution_ms"):
            CodeModeConfig(max_execution_ms=50)


# --- HttpProfile ---


class TestHttpProfile:
    """Tests for HttpProfile (stub — deferred to v1.1)."""

    def test_minimal_http_profile(self):
        """HttpProfile exists as a stub with base fields."""
        from mozart.core.config.instruments import HttpProfile

        hp = HttpProfile(
            base_url="http://localhost:11434",
            schema_family="openai",
        )
        assert hp.base_url == "http://localhost:11434"
        assert hp.endpoint == "/v1/chat/completions"
        assert hp.schema_family == "openai"
        assert hp.auth_env_var is None

    def test_invalid_schema_family(self):
        """Only openai, anthropic, gemini are valid."""
        from mozart.core.config.instruments import HttpProfile

        with pytest.raises(ValidationError, match="schema_family"):
            HttpProfile(
                base_url="http://localhost",
                schema_family="invalid",  # type: ignore[arg-type]
            )


# --- InstrumentProfile ---


class TestInstrumentProfile:
    """Tests for InstrumentProfile — the top-level instrument data model."""

    def test_minimal_cli_instrument(self):
        """Minimal CLI instrument with required fields only."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        profile = InstrumentProfile(
            name="test-cli",
            display_name="Test CLI",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="test", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        assert profile.name == "test-cli"
        assert profile.display_name == "Test CLI"
        assert profile.kind == "cli"
        assert profile.capabilities == set()
        assert profile.models == []
        assert profile.default_model is None
        assert profile.default_timeout_seconds == 1800.0
        assert profile.code_mode is None
        assert profile.http is None

    def test_full_cli_instrument(self):
        """Full CLI instrument profile (modeled on Gemini CLI from spec)."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliErrorConfig,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
            ModelCapacity,
        )

        profile = InstrumentProfile(
            name="gemini-cli",
            display_name="Gemini CLI",
            description="Google's Gemini CLI with tool use and vision",
            kind="cli",
            capabilities={"tool_use", "file_editing", "shell_access", "vision",
                          "structured_output"},
            default_model="gemini-2.5-pro",
            default_timeout_seconds=1800.0,
            models=[
                ModelCapacity(
                    name="gemini-2.5-pro",
                    context_window=1_000_000,
                    cost_per_1k_input=0.00125,
                    cost_per_1k_output=0.005,
                    max_output_tokens=65536,
                ),
                ModelCapacity(
                    name="gemini-2.5-flash",
                    context_window=1_000_000,
                    cost_per_1k_input=0.00015,
                    cost_per_1k_output=0.0006,
                ),
            ],
            cli=CliProfile(
                command=CliCommand(
                    executable="gemini",
                    prompt_flag="-p",
                    model_flag="-m",
                    auto_approve_flag="--yolo",
                    output_format_flag="--output-format",
                    output_format_value="json",
                ),
                output=CliOutputConfig(
                    format="json",
                    result_path="response",
                    error_path="error.message",
                    input_tokens_path="stats.models.*.tokens.prompt",
                    output_tokens_path="stats.models.*.tokens.candidates",
                ),
                errors=CliErrorConfig(
                    rate_limit_patterns=[r"rate.?limit", r"quota.?exceeded", "429"],
                    auth_error_patterns=[r"authenticat", "unauthorized"],
                ),
            ),
        )
        assert profile.name == "gemini-cli"
        assert len(profile.models) == 2
        assert profile.default_model == "gemini-2.5-pro"
        assert "vision" in profile.capabilities
        assert profile.cli is not None
        assert profile.cli.command.auto_approve_flag == "--yolo"

    def test_cli_instrument_requires_cli_profile(self):
        """kind=cli without cli profile should fail validation."""
        from mozart.core.config.instruments import InstrumentProfile

        with pytest.raises(ValidationError, match="cli"):
            InstrumentProfile(
                name="bad",
                display_name="Bad",
                kind="cli",
                # No cli field → should fail
            )

    def test_http_instrument_requires_http_profile(self):
        """kind=http without http profile should fail validation."""
        from mozart.core.config.instruments import InstrumentProfile

        with pytest.raises(ValidationError, match="http"):
            InstrumentProfile(
                name="bad",
                display_name="Bad",
                kind="http",
                # No http field → should fail
            )

    def test_invalid_kind_rejected(self):
        """Only cli and http are valid kinds."""
        from mozart.core.config.instruments import InstrumentProfile

        with pytest.raises(ValidationError, match="kind"):
            InstrumentProfile(
                name="bad",
                display_name="Bad",
                kind="grpc",  # type: ignore[arg-type]
            )

    def test_timeout_must_be_positive(self):
        """default_timeout_seconds must be > 0."""
        from mozart.core.config.instruments import InstrumentProfile

        with pytest.raises(ValidationError, match="default_timeout_seconds"):
            InstrumentProfile(
                name="bad",
                display_name="Bad",
                kind="cli",
                default_timeout_seconds=0,
            )

    def test_capabilities_as_list_coerced_to_set(self):
        """YAML loads capabilities as a list; Pydantic coerces to set."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        profile = InstrumentProfile(
            name="test",
            display_name="Test",
            kind="cli",
            capabilities=["tool_use", "vision", "tool_use"],  # type: ignore[arg-type]
            cli=CliProfile(
                command=CliCommand(executable="test", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        assert isinstance(profile.capabilities, set)
        assert len(profile.capabilities) == 2

    def test_description_is_optional(self):
        """description defaults to None."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        profile = InstrumentProfile(
            name="test",
            display_name="Test",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="test", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        assert profile.description is None

    def test_serialization_roundtrip(self):
        """Profile can be serialized to dict and reconstructed."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
            ModelCapacity,
        )

        profile = InstrumentProfile(
            name="roundtrip-test",
            display_name="Roundtrip",
            kind="cli",
            capabilities={"tool_use"},
            models=[
                ModelCapacity(
                    name="test-model",
                    context_window=128000,
                    cost_per_1k_input=0.01,
                    cost_per_1k_output=0.03,
                ),
            ],
            default_model="test-model",
            cli=CliProfile(
                command=CliCommand(executable="test", prompt_flag="-p"),
                output=CliOutputConfig(format="json", result_path="result"),
            ),
        )
        data = profile.model_dump()
        reconstructed = InstrumentProfile.model_validate(data)
        assert reconstructed.name == profile.name
        assert reconstructed.models[0].context_window == 128000
        assert reconstructed.cli is not None
        assert reconstructed.cli.output.result_path == "result"


# --- Adversarial Tests ---


class TestInstrumentModelsAdversarial:
    """Adversarial tests for instrument data models."""

    @pytest.mark.adversarial
    def test_empty_name_rejected(self):
        """Empty string name should fail (Pydantic min_length or custom validator)."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        with pytest.raises(ValidationError, match="name"):
            InstrumentProfile(
                name="",
                display_name="Empty",
                kind="cli",
                cli=CliProfile(
                    command=CliCommand(executable="test", prompt_flag="-p"),
                    output=CliOutputConfig(),
                ),
            )

    @pytest.mark.adversarial
    def test_empty_executable_rejected(self):
        """Empty executable should fail."""
        from mozart.core.config.instruments import CliCommand

        with pytest.raises(ValidationError, match="executable"):
            CliCommand(executable="", prompt_flag="-p")

    @pytest.mark.adversarial
    def test_huge_context_window_accepted(self):
        """Very large context windows should be accepted (future models)."""
        from mozart.core.config.instruments import ModelCapacity

        mc = ModelCapacity(
            name="future-model",
            context_window=10_000_000,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
        )
        assert mc.context_window == 10_000_000

    @pytest.mark.adversarial
    def test_unicode_names_accepted(self):
        """Unicode instrument names should work (international users)."""
        from mozart.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        profile = InstrumentProfile(
            name="模型-cli",
            display_name="模型 CLI",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="test", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        assert profile.name == "模型-cli"

    @pytest.mark.adversarial
    def test_extra_fields_ignored_by_default(self):
        """Unknown fields in YAML should not cause errors (forward compat)."""
        from mozart.core.config.instruments import ModelCapacity

        # Pydantic v2 ignores extra fields by default
        mc = ModelCapacity.model_validate({
            "name": "test",
            "context_window": 1000,
            "cost_per_1k_input": 0.0,
            "cost_per_1k_output": 0.0,
            "future_field": "should be ignored",
        })
        assert mc.name == "test"
