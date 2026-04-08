"""Instrument registry — the central lookup for all available instruments.

Combines native instruments (Marianne's 4 built-in backends) with config-loaded
profiles from YAML files. The conductor creates a registry at startup, populates
it with native instruments, then loads profiles from directories.

Native instruments map Marianne's existing Backend subclasses to InstrumentProfile
metadata. This lets the new instrument-based lookup coexist with the existing
backend system — scores using ``backend:`` continue to work unchanged, while
scores using ``instrument:`` resolve through this registry.

The registry is a simple dict wrapper. No caching, no lazy loading, no magic.
Instruments are registered once at startup and looked up by name thereafter.

Usage:
    from marianne.instruments.registry import InstrumentRegistry, register_native_instruments

    registry = InstrumentRegistry()
    register_native_instruments(registry)
    # ... load profiles from directories ...

    profile = registry.get("gemini-cli")
"""

from __future__ import annotations

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    HttpProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.core.logging import get_logger

_logger = get_logger("instruments.registry")


class InstrumentRegistry:
    """Central registry for all available instruments.

    A simple name-to-profile mapping with registration, lookup, and
    listing operations. Thread safety is not needed — the registry is
    populated at conductor startup before any concurrent access.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, InstrumentProfile] = {}

    def register(
        self,
        profile: InstrumentProfile,
        *,
        override: bool = False,
    ) -> None:
        """Register an instrument profile.

        Args:
            profile: The InstrumentProfile to register.
            override: If True, replace an existing profile with the same
                name. If False (default), raise ValueError on collision.

        Raises:
            ValueError: If a profile with the same name is already
                registered and override is False.
        """
        if profile.name in self._profiles and not override:
            raise ValueError(
                f"Instrument '{profile.name}' is already registered. "
                f"Use override=True to replace it."
            )
        self._profiles[profile.name] = profile
        _logger.debug(
            "instrument_registered",
            name=profile.name,
            kind=profile.kind,
            override=override,
        )

    def get(self, name: str) -> InstrumentProfile | None:
        """Look up an instrument by name.

        Args:
            name: The instrument name (e.g. 'claude_cli', 'gemini-cli').

        Returns:
            The InstrumentProfile, or None if not found.
        """
        return self._profiles.get(name)

    def list_all(self) -> list[InstrumentProfile]:
        """Return all registered profiles, sorted by name."""
        return sorted(self._profiles.values(), key=lambda p: p.name)

    def __len__(self) -> int:
        return len(self._profiles)

    def __contains__(self, name: str) -> bool:
        return name in self._profiles


# ---------------------------------------------------------------------------
# Native instrument bridge
# ---------------------------------------------------------------------------


def _claude_cli_profile() -> InstrumentProfile:
    """Create an InstrumentProfile describing the native Claude CLI backend.

    This is metadata only — the actual execution still goes through
    ClaudeCliBackend. The profile lets the registry look up Claude CLI
    by name and report its capabilities.
    """
    return InstrumentProfile(
        name="claude_cli",
        display_name="Claude Code",
        description=(
            "Anthropic's Claude Code CLI — the reference instrument. "
            "Full tool use, file editing, shell access, MCP, and session resume."
        ),
        kind="cli",
        capabilities={
            "tool_use",
            "file_editing",
            "shell_access",
            "mcp",
            "structured_output",
            "session_resume",
            "streaming",
            "thinking",
        },
        default_model="claude-sonnet-4-5-20250929",
        default_timeout_seconds=1800.0,
        models=[
            ModelCapacity(
                name="claude-sonnet-4-5-20250929",
                context_window=200_000,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
                max_output_tokens=16384,
            ),
            ModelCapacity(
                name="claude-opus-4-20250514",
                context_window=200_000,
                cost_per_1k_input=0.015,
                cost_per_1k_output=0.075,
                max_output_tokens=16384,
            ),
            ModelCapacity(
                name="claude-haiku-4-5-20251001",
                context_window=200_000,
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.005,
                max_output_tokens=8192,
            ),
        ],
        cli=CliProfile(
            command=CliCommand(
                executable="claude",
                prompt_flag="-p",
                model_flag="--model",
                auto_approve_flag="--dangerously-skip-permissions",
                output_format_flag="--output-format",
                output_format_value="json",
                system_prompt_flag="--system-prompt",
                allowed_tools_flag="--allowedTools",
                mcp_config_flag="--mcp-config",
            ),
            output=CliOutputConfig(
                format="json",
                result_path="result",
                input_tokens_path="usage.input_tokens",
                output_tokens_path="usage.output_tokens",
            ),
        ),
    )


def _anthropic_api_profile() -> InstrumentProfile:
    """Create an InstrumentProfile describing the native Anthropic API backend."""
    return InstrumentProfile(
        name="anthropic_api",
        display_name="Anthropic API",
        description=(
            "Direct Anthropic Messages API — for structured generation, "
            "high throughput, and fine-grained control over model parameters."
        ),
        kind="http",
        capabilities={
            "structured_output",
            "streaming",
            "thinking",
            "vision",
        },
        default_model="claude-sonnet-4-5-20250929",
        default_timeout_seconds=1800.0,
        models=[
            ModelCapacity(
                name="claude-sonnet-4-5-20250929",
                context_window=200_000,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
                max_output_tokens=16384,
            ),
            ModelCapacity(
                name="claude-opus-4-20250514",
                context_window=200_000,
                cost_per_1k_input=0.015,
                cost_per_1k_output=0.075,
                max_output_tokens=16384,
            ),
        ],
        http=HttpProfile(
            base_url="https://api.anthropic.com",
            endpoint="/v1/messages",
            schema_family="anthropic",
            auth_env_var="ANTHROPIC_API_KEY",
        ),
    )


def _ollama_profile() -> InstrumentProfile:
    """Create an InstrumentProfile describing the native Ollama backend."""
    return InstrumentProfile(
        name="ollama",
        display_name="Ollama",
        description=(
            "Local model execution via Ollama — free, private, offline. "
            "Supports tool calling with the Marianne-Ollama bridge."
        ),
        kind="http",
        capabilities={
            "tool_use",
            "structured_output",
        },
        default_model="llama3.1:8b",
        default_timeout_seconds=300.0,
        models=[
            ModelCapacity(
                name="llama3.1:8b",
                context_window=32768,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            ),
        ],
        http=HttpProfile(
            base_url="http://localhost:11434",
            endpoint="/api/chat",
            schema_family="openai",
        ),
    )


def _recursive_light_profile() -> InstrumentProfile:
    """Create an InstrumentProfile describing the native Recursive Light backend."""
    return InstrumentProfile(
        name="recursive_light",
        display_name="Recursive Light",
        description=(
            "Recursive Light Framework — TDF-aligned processing with "
            "dual-LLM confidence scoring and domain activations."
        ),
        kind="http",
        capabilities={
            "structured_output",
        },
        default_timeout_seconds=30.0,
        http=HttpProfile(
            base_url="http://localhost:8080",
            endpoint="/process",
            schema_family="openai",
        ),
    )


def register_native_instruments(registry: InstrumentRegistry) -> None:
    """Register Marianne's 4 built-in backends as named instruments.

    This bridges the native backend system (BackendConfig → Backend) with
    the new instrument system (InstrumentProfile → registry lookup). After
    calling this, the registry contains profiles for claude_cli, anthropic_api,
    ollama, and recursive_light.

    Native instruments are registered first, before config-loaded profiles.
    Config profiles can override them with override=True if needed.

    Args:
        registry: The registry to populate.
    """
    for factory in [
        _claude_cli_profile,
        _anthropic_api_profile,
        _ollama_profile,
        _recursive_light_profile,
    ]:
        profile = factory()
        registry.register(profile)
        _logger.info(
            "native_instrument_registered",
            name=profile.name,
            kind=profile.kind,
        )

    _logger.info(
        "native_instruments_complete",
        count=4,
    )
