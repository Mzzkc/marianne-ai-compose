"""Registry-aware native backend creation.

Phase 1 of the backend atlas migration: all non-baton backend creation
must route through the instrument plugin system. This module provides
the thin wrapper around the registry that the daemon uses for the
semantic analyzer's backend.

The registry (``InstrumentRegistry``) validates that the requested
instrument name is known. When the name resolves to a native instrument
(``claude_cli``, ``anthropic_api``, ``ollama``, ``recursive_light``),
this helper delegates to the matching native backend class to translate
a ``BackendConfig`` into a configured ``Backend`` instance.

This module is the "instrument plugin system" entry point for native
backend creation. Callers MUST go through it rather than importing
``marianne.execution.setup.create_backend_from_config`` directly
(Doctrine RULE: "All model invocations must route through the instrument
plugin system after Phase 1").

The factory in ``execution/setup.py`` is preserved for the legacy
``backend:`` path during Phases 1-4 (Doctrine RULE: "Backward
compatibility for existing scores is mandatory during Phases 1-4").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marianne.core.logging import get_logger

if TYPE_CHECKING:
    from marianne.backends.base import Backend
    from marianne.core.config import BackendConfig
    from marianne.instruments.registry import InstrumentRegistry

_logger = get_logger("instruments.native_factory")


def create_backend_via_registry(
    registry: InstrumentRegistry,
    backend_config: BackendConfig,
) -> Backend:
    """Create a native Backend by validating against the registry first.

    The backend type (``backend_config.type``) is looked up in the registry.
    If the instrument is not registered, a ``ValueError`` is raised with the
    list of known instruments. If the instrument is registered, the matching
    native backend class is used to translate the ``BackendConfig`` into a
    ``Backend`` instance.

    This is the Phase 1 replacement for
    ``marianne.execution.setup.create_backend_from_config`` when called from
    daemon code paths. It preserves the existing backend creation semantics
    while making the registry the source of truth for which instruments
    exist.

    Args:
        registry: The instrument registry, populated with native
            instruments via ``register_native_instruments()`` and any
            config-loaded profiles.
        backend_config: Job backend configuration. ``type`` must match
            a registered instrument name.

    Returns:
        A configured ``Backend`` instance.

    Raises:
        ValueError: If ``backend_config.type`` is not registered.
    """
    instrument_name = backend_config.type
    profile = registry.get(instrument_name)
    if profile is None:
        available = ", ".join(p.name for p in registry.list_all())
        msg = (
            f"Backend type '{instrument_name}' is not a registered instrument. "
            f"Available instruments: {available}"
        )
        raise ValueError(msg)

    # Registry lookup succeeded. Delegate to the native backend class that
    # matches the instrument name. This dispatch mirrors the legacy factory
    # in ``execution/setup.py`` but is gated by the registry check above,
    # which is the point of Phase 1: the registry is authoritative for
    # which instruments exist.
    _logger.debug(
        "instruments.native_factory.resolved",
        instrument=profile.name,
        kind=profile.kind,
    )

    if instrument_name == "recursive_light":
        # Phase 4a: the native RecursiveLightBackend has been removed.
        # The registry profile for ``recursive_light`` is a stub — no live
        # YAML-profile HTTP plugin speaks the RL server's ``/api/process``
        # protocol. Callers still using ``backend.type: recursive_light``
        # must migrate to an instrument plugin.
        raise ValueError(
            "Backend type 'recursive_light' was removed in Phase 4 of the "
            "backend atlas migration. The native RecursiveLightBackend has "
            "been deleted. Migrate to the 'instrument:' path with a "
            "registered HTTP instrument profile that speaks the RL "
            "/api/process protocol."
        )
    if instrument_name == "anthropic_api":
        from marianne.backends.anthropic_api import AnthropicApiBackend

        return AnthropicApiBackend.from_config(backend_config)
    if instrument_name == "ollama":
        from marianne.backends.ollama import OllamaBackend

        return OllamaBackend.from_config(backend_config)

    # Default to claude_cli for any other registered instrument whose kind
    # is CLI. This preserves the legacy factory's fallthrough semantics
    # for the remaining native instrument.
    from marianne.backends.claude_cli import ClaudeCliBackend

    return ClaudeCliBackend.from_config(backend_config)


__all__ = ["create_backend_via_registry"]
