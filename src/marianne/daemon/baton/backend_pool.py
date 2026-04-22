"""Backend pool — per-instrument backend instance management.

The baton dispatches sheets to execution, each requiring a Backend instance
for its assigned instrument. The BackendPool manages these instances:

- **CLI instruments** get one Backend per concurrent sheet (subprocess
  isolation — each sheet runs in its own process).
- **HTTP instruments** share a singleton Backend (httpx handles
  connection pooling and concurrency internally).

The pool tracks how many instances are in-flight per instrument, which
the baton uses to enforce per-instrument concurrency limits.

Design decisions:

- **Lazy creation** — Backend instances are created on first acquire,
  not upfront. This avoids spawning processes for instruments that
  aren't used by any sheet in the current job.
- **CLI reuse** — Released CLI backends go back into a free list. The
  next acquire for the same instrument reuses an existing instance
  rather than creating a new one. This avoids repeated subprocess
  setup for sequential sheets on the same instrument.
- **Lock-free acquire for HTTP** — HTTP singletons are created once
  and returned on every acquire. No release needed (the pool tracks
  them but doesn't recycle them).
- **Graceful close** — ``close_all()`` closes every Backend instance
  (calls ``backend.close()``). Called by the baton at job completion
  or cancellation.

See: ``docs/plans/2026-03-26-baton-design.md`` — BackendPool section.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from marianne.backends.base import Backend
from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.registry import InstrumentRegistry

if TYPE_CHECKING:
    from marianne.daemon.keyring import ApiKeyKeyring
    from marianne.daemon.pgroup import ProcessGroupManager

from marianne.core.logging import get_logger

_logger = get_logger("daemon.baton.backend_pool")


def _create_backend_for_profile(
    profile: InstrumentProfile,
    *,
    working_directory: Path | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> Backend:
    """Create a Backend instance from an InstrumentProfile.

    For CLI instruments, creates a PluginCliBackend.
    For HTTP instruments, creates the appropriate API backend.

    Falls back to the legacy ``create_backend_from_config`` for native
    instruments that don't have a profile-based constructor.

    Args:
        profile: The instrument profile.
        working_directory: Working directory for subprocess execution.
        model: Optional model override.
        api_key: Optional API key from the keyring. When provided, the
            HTTP backend uses this key directly instead of reading from
            an environment variable.

    Returns:
        A configured Backend instance.
    """
    if profile.kind == "cli":
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend(
            profile=profile,
            working_directory=working_directory,
        )
        if model:
            backend.apply_overrides({"model": model})
        return backend

    # HTTP instruments — route by HttpProfile.schema_family (Phase 3).
    # Dispatch is now driven purely off the schema_family field on
    # HttpProfile (``openai`` | ``anthropic`` | ``gemini``); there is no
    # hardcoded instrument-name check. Previously-unrecognised HTTP
    # profiles raise a structured ValueError with migration guidance.
    return _dispatch_http_profile(
        profile,
        working_directory=working_directory,
        model=model,
        api_key=api_key,
    )


def _dispatch_http_profile(
    profile: InstrumentProfile,
    *,
    working_directory: Path | None,
    model: str | None,
    api_key: str | None,
) -> Backend:
    """Dispatch an HTTP InstrumentProfile to the handler for its schema family.

    Doctrine RULE: "Generic HTTP instrument dispatch must work for all
    HttpProfile schema families". Handlers are selected by
    ``profile.http.schema_family``:

    - ``openai``   → OpenAI-compatible chat/completions (OpenRouter,
      OpenAI proper, self-hosted OpenAI-compat servers).
    - ``anthropic``→ Anthropic Messages API (routed through the native
      AnthropicApiBackend exception per Doctrine Exception Registry).
    - ``gemini``   → Google Gemini API. Translator is designed but not
      yet wired — raises a structured ``ValueError`` with migration
      guidance.

    The handler is free to use any backing implementation (native SDK,
    generic httpx, third-party library). The pool only cares that it
    returns a configured ``Backend`` (or raises a structured error).
    """
    if profile.kind != "http" or profile.http is None:
        raise ValueError(
            f"_dispatch_http_profile called with non-HTTP profile "
            f"(name={profile.name!r}, kind={profile.kind!r}). This is a "
            f"programming error in backend_pool.py."
        )

    family = profile.http.schema_family
    if family == "openai":
        return _build_openai_family_backend(
            profile,
            working_directory=working_directory,
            model=model,
            api_key=api_key,
        )
    if family == "anthropic":
        return _build_anthropic_family_backend(
            profile,
            working_directory=working_directory,
            model=model,
            api_key=api_key,
        )
    if family == "gemini":
        raise ValueError(
            f"HTTP instrument '{profile.name}' uses schema_family='gemini' "
            f"but no Gemini translator is wired yet. Migration guidance: "
            f"use a CLI instrument profile (e.g. gemini-cli) or wait for "
            f"the gemini schema-family HTTP translator to land. See "
            f"docs/research/2026-03-26-universal-instrument-api-research.md."
        )
    # Unknown schema family — raise an actionable ValueError (NOT a bare
    # deferred-implementation stub).
    raise ValueError(
        f"HTTP instrument '{profile.name}' declares schema_family="
        f"{family!r} which has no dispatch handler. "
        f"Supported HttpProfile schema families: 'openai', 'anthropic'. "
        f"('gemini' is declared but the translator is not yet wired.)"
    )


def _build_openai_family_backend(
    profile: InstrumentProfile,
    *,
    working_directory: Path | None,
    model: str | None,
    api_key: str | None,
) -> Backend:
    """Construct a Backend for schema_family='openai' profiles.

    Uses the OpenRouter backend implementation as the generic OpenAI-compat
    handler — it speaks standard OpenAI chat/completions protocol and works
    for any profile whose schema family is 'openai', including OpenRouter
    itself, OpenAI proper, and self-hosted OpenAI-compat servers.
    """
    from marianne.execution.instruments.openai_compat_backend import OpenRouterBackend

    assert profile.http is not None  # narrowed by dispatcher

    resolved_model = model or profile.default_model or "minimax/minimax-m1-80k"
    auth_env = profile.http.auth_env_var or "OPENROUTER_API_KEY"
    backend: Backend = OpenRouterBackend(
        model=resolved_model,
        api_key_env=auth_env,
        timeout_seconds=profile.default_timeout_seconds,
        base_url=profile.http.base_url,
    )
    # Inject API key from keyring if provided.
    if api_key is not None and hasattr(backend, "_api_key"):
        object.__setattr__(backend, "_api_key", api_key)
    if working_directory is not None:
        backend.working_directory = working_directory
    return backend


def _build_anthropic_family_backend(
    profile: InstrumentProfile,
    *,
    working_directory: Path | None,
    model: str | None,
    api_key: str | None,
) -> Backend:
    """Construct a Backend for schema_family='anthropic' profiles.

    Uses the native AnthropicApiBackend (Doctrine Exception Registry: the
    Anthropic SDK provides thinking/streaming/tool_use features that a
    generic HTTP POST cannot replicate, so this backend remains native).
    """
    from marianne.backends.anthropic_api import AnthropicApiBackend

    assert profile.http is not None  # narrowed by dispatcher

    resolved_model = model or profile.default_model or "claude-sonnet-4-5-20250929"
    auth_env = profile.http.auth_env_var or "ANTHROPIC_API_KEY"
    backend: Backend = AnthropicApiBackend(
        model=resolved_model,
        api_key_env=auth_env,
        timeout_seconds=profile.default_timeout_seconds,
    )
    if api_key is not None and hasattr(backend, "_api_key"):
        object.__setattr__(backend, "_api_key", api_key)
    if working_directory is not None:
        backend.working_directory = working_directory
    return backend


class BackendPool:
    """Manages Backend instances for per-sheet execution.

    The baton acquires a backend before dispatching a sheet and releases
    it after the sheet completes (or fails). The pool enforces
    per-instrument concurrency by tracking in-flight instances.

    Usage::

        pool = BackendPool(registry)

        # Dispatch a sheet
        backend = await pool.acquire("claude-code", working_directory=ws)
        try:
            result = await backend.execute(prompt)
        finally:
            await pool.release("claude-code", backend)

        # Job done
        await pool.close_all()
    """

    def __init__(
        self,
        registry: InstrumentRegistry,
        pgroup: ProcessGroupManager | None = None,
        keyring: ApiKeyKeyring | None = None,
    ) -> None:
        self._registry = registry
        self._pgroup = pgroup
        self._keyring = keyring

        # CLI instruments: free list per instrument name
        self._cli_free: dict[str, list[Backend]] = {}

        # HTTP instruments: singleton per instrument name
        self._http_singletons: dict[str, Backend] = {}

        # Tracking: how many backends are currently in-flight (acquired
        # but not yet released) per instrument.
        self._in_flight: dict[str, int] = {}

        # All backends ever created (for close_all cleanup)
        self._all_backends: list[Backend] = []

        # Protect concurrent acquire/release to avoid race conditions
        # on the free lists
        self._lock = asyncio.Lock()

        self._closed = False

    async def acquire(
        self,
        instrument_name: str,
        *,
        model: str | None = None,
        working_directory: Path | None = None,
    ) -> Backend:
        """Acquire a Backend instance for an instrument.

        For CLI instruments: returns a free instance if available,
        otherwise creates a new one. For HTTP instruments: returns
        the shared singleton (creating it on first call).

        Args:
            instrument_name: Name of the instrument (from registry).
            model: Optional model override for this execution.
            working_directory: Working directory for the backend.

        Returns:
            A Backend instance ready for execution.

        Raises:
            ValueError: If the instrument is not registered.
            RuntimeError: If the pool has been closed.
        """
        if self._closed:
            msg = "BackendPool is closed — cannot acquire new backends"
            raise RuntimeError(msg)

        profile = self._registry.get(instrument_name)
        if profile is None:
            msg = (
                f"Instrument '{instrument_name}' not found in registry. "
                f"Available: {', '.join(p.name for p in self._registry.list_all())}"
            )
            raise ValueError(msg)

        # Resolve API key from keyring for HTTP instruments before acquiring lock.
        # Key is loaded from disk, used to configure the backend, then not stored.
        api_key: str | None = None
        if profile.kind == "http" and self._keyring is not None:
            if self._keyring.has_keys(instrument_name):
                try:
                    api_key = await self._keyring.select_key(instrument_name)
                except (KeyError, FileNotFoundError, ValueError):
                    _logger.warning(
                        "backend_pool.keyring_select_failed",
                        extra={"instrument": instrument_name},
                        exc_info=True,
                    )

        async with self._lock:
            backend = self._acquire_locked(
                profile,
                model=model,
                working_directory=working_directory,
                api_key=api_key,
            )

        _logger.debug(
            "backend_pool.acquired",
            extra={
                "instrument": instrument_name,
                "in_flight": self._in_flight.get(instrument_name, 0),
                "model": model,
            },
        )
        return backend

    async def release(
        self,
        instrument_name: str,
        backend: Backend,
    ) -> None:
        """Release a Backend instance back to the pool.

        For CLI instruments: the backend goes back to the free list for
        reuse. For HTTP instruments: no-op (the singleton stays active).

        Args:
            instrument_name: The instrument name used in ``acquire()``.
            backend: The Backend instance to release.
        """
        # Clear any per-sheet overrides (model, etc.) before returning
        # the backend to the free list. Without this, a model override from
        # sheet N would silently carry over to sheet N+1 that reuses the
        # same backend instance. This was F-150's secondary bug.
        backend.clear_overrides()

        async with self._lock:
            count = self._in_flight.get(instrument_name, 0)
            self._in_flight[instrument_name] = max(0, count - 1)

            profile = self._registry.get(instrument_name)
            if profile is not None and profile.kind == "cli":
                # Return CLI backend to free list for reuse
                if instrument_name not in self._cli_free:
                    self._cli_free[instrument_name] = []
                self._cli_free[instrument_name].append(backend)

            # HTTP singletons are never "released" — they stay active

        _logger.debug(
            "backend_pool.released",
            extra={
                "instrument": instrument_name,
                "in_flight": self._in_flight.get(instrument_name, 0),
            },
        )

    def in_flight_count(self, instrument_name: str) -> int:
        """How many backends are currently acquired for this instrument.

        Used by the baton's dispatch logic to enforce per-instrument
        concurrency limits.
        """
        return self._in_flight.get(instrument_name, 0)

    def total_in_flight(self) -> int:
        """Total backends in-flight across all instruments."""
        return sum(self._in_flight.values())

    async def close_all(self) -> None:
        """Close all Backend instances and mark the pool as closed.

        Called at job completion, cancellation, or conductor shutdown.
        After this call, ``acquire()`` raises RuntimeError.
        """
        self._closed = True

        async with self._lock:
            for backend in self._all_backends:
                try:
                    await backend.close()
                except Exception:
                    _logger.warning(
                        "backend_pool.close_failed",
                        extra={"backend": backend.name},
                        exc_info=True,
                    )

            self._cli_free.clear()
            self._http_singletons.clear()
            self._in_flight.clear()

        _logger.debug(
            "backend_pool.closed",
            extra={"total_backends": len(self._all_backends)},
        )

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    def _acquire_locked(
        self,
        profile: InstrumentProfile,
        *,
        model: str | None = None,
        working_directory: Path | None = None,
        api_key: str | None = None,
    ) -> Backend:
        """Acquire under lock. Returns a Backend instance."""
        name = profile.name

        if profile.kind == "http":
            # HTTP: return existing singleton or create one
            if name not in self._http_singletons:
                backend = _create_backend_for_profile(
                    profile,
                    working_directory=working_directory,
                    model=model,
                    api_key=api_key,
                )
                self._http_singletons[name] = backend
                self._all_backends.append(backend)
            backend = self._http_singletons[name]
        else:
            # CLI: pop from free list or create new
            free_list = self._cli_free.get(name, [])
            if free_list:
                backend = free_list.pop()
                # Update working directory for reuse
                if working_directory is not None:
                    backend.working_directory = working_directory
                if model:
                    backend.apply_overrides({"model": model})
            else:
                backend = _create_backend_for_profile(
                    profile,
                    working_directory=working_directory,
                    model=model,
                )
                self._all_backends.append(backend)

        # Wire PID tracking for orphan detection when running under daemon.
        # Same pattern as JobService._setup_components — set callbacks on
        # backends that support them (PluginCliBackend, ClaudeCliBackend).
        if self._pgroup is not None:
            if hasattr(backend, "_on_process_spawned"):
                backend._on_process_spawned = self._pgroup.track_backend_pid
            if hasattr(backend, "_on_process_exited"):
                backend._on_process_exited = self._pgroup.untrack_backend_pid

        # Track in-flight
        self._in_flight[name] = self._in_flight.get(name, 0) + 1
        return backend
