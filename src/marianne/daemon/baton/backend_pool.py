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
    interactive: bool = False,
) -> Backend:
    """Create a Backend instance from an InstrumentProfile.

    For CLI instruments, creates a PluginCliBackend — or an
    InteractiveCliBackend when ``interactive`` is requested (the profile
    must carry a verified ``cli.interactive`` block, else this raises).
    For HTTP instruments, creates the appropriate API backend.

    Args:
        profile: The instrument profile.
        working_directory: Working directory for subprocess execution.
        model: Optional model override.
        api_key: Optional API key from the keyring. When provided, the
            HTTP backend uses this key directly instead of reading from
            an environment variable.
        interactive: When True, build an interactive (tmux-session)
            backend instead of the headless CLI backend.

    Returns:
        A configured Backend instance.

    Raises:
        ValueError: interactive=True with a non-CLI profile or a profile
            lacking ``cli.interactive`` (fail-fast — surfaced as a
            dispatch failure with the structured message).
    """
    if interactive:
        if profile.kind != "cli":
            raise ValueError(
                f"Instrument '{profile.name}' is kind={profile.kind}; "
                f"interactive mode only applies to CLI instruments."
            )
        from marianne.execution.instruments.interactive import (
            InteractiveCliBackend,
        )

        interactive_backend: Backend = InteractiveCliBackend(
            profile=profile,
            working_directory=working_directory,
        )
        if model:
            interactive_backend.apply_overrides({"model": model})
        return interactive_backend

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


def create_backend_for_instrument(
    registry: InstrumentRegistry,
    instrument_name: str,
    *,
    model: str | None = None,
    working_directory: Path | None = None,
) -> Backend:
    """Create a one-off Backend for a registered instrument by name.

    The single creation path for daemon services that need an LLM backend
    outside the sheet-dispatch pool (semantic analyzer, judgment client).
    Routes through the same profile dispatch as the BackendPool, so service
    backends and musician backends can never drift.

    Service calls are always HEADLESS: their callers parse structured
    output from ``result.stdout``, which an interactive session cannot
    provide (its stdout is a rendered screen).

    Raises:
        ValueError: If the instrument is not registered.
    """
    profile = registry.get(instrument_name)
    if profile is None:
        raise ValueError(
            f"Instrument '{instrument_name}' not found in registry. "
            f"Available: {', '.join(p.name for p in registry.list_all())}"
        )
    return _create_backend_for_profile(
        profile,
        working_directory=working_directory,
        model=model,
        interactive=False,
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

        # #171 hot-reload generation. invalidate() bumps it and clears the
        # free lists; backends are tagged with the generation they were
        # built in, so an in-flight backend built before a reload is
        # dropped (not reused stale) when released.
        self._generation: int = 0

        # Protect concurrent acquire/release to avoid race conditions
        # on the free lists
        self._lock = asyncio.Lock()

        self._closed = False

    @staticmethod
    def _profile_default_interactive(profile: InstrumentProfile) -> bool:
        """Whether this profile runs interactively when the score is silent.

        Interactive is the standard execution mode for instruments with
        verified interactive support (a ``cli.interactive`` block) unless
        the profile opts out via ``enabled_by_default: false``. Profiles
        without verified support always default to headless.
        """
        return (
            profile.kind == "cli"
            and profile.cli is not None
            and profile.cli.interactive is not None
            and profile.cli.interactive.enabled_by_default
        )

    async def acquire(
        self,
        instrument_name: str,
        *,
        model: str | None = None,
        working_directory: Path | None = None,
        interactive: bool | None = None,
    ) -> Backend:
        """Acquire a Backend instance for an instrument.

        For CLI instruments: returns a free instance if available,
        otherwise creates a new one. For HTTP instruments: returns
        the shared singleton (creating it on first call).

        Args:
            instrument_name: Name of the instrument (from registry).
            model: Optional model override for this execution.
            working_directory: Working directory for the backend.
            interactive: Tri-state execution-mode request. None (default)
                = use the profile's default — interactive when the profile
                carries verified interactive support, headless otherwise.
                True = force interactive (error if unsupported). False =
                force headless. Interactive instances live in their own
                free list — they never cross-pollinate with headless ones.

        Returns:
            A Backend instance ready for execution.

        Raises:
            ValueError: If the instrument is not registered, or interactive
                was explicitly requested for an instrument without verified
                interactive support.
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

        # Resolve the tri-state: score silence → the profile's default.
        resolved_interactive = (
            self._profile_default_interactive(profile)
            if interactive is None
            else interactive
        )

        async with self._lock:
            backend = self._acquire_locked(
                profile,
                model=model,
                working_directory=working_directory,
                api_key=api_key,
                interactive=resolved_interactive,
            )

        _logger.debug(
            "backend_pool.acquired",
            extra={
                "instrument": instrument_name,
                "in_flight": self._in_flight.get(instrument_name, 0),
                "model": model,
                "interactive": resolved_interactive,
                "interactive_requested": interactive,
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
        # For interactive backends this also resets the per-sheet driver
        # knobs and the attempt identity, so free-list reuse is clean.
        backend.clear_overrides()

        async with self._lock:
            count = self._in_flight.get(instrument_name, 0)
            self._in_flight[instrument_name] = max(0, count - 1)

            profile = self._registry.get(instrument_name)
            stale = (
                getattr(backend, "_pool_generation", self._generation)
                != self._generation
            )
            if profile is not None and profile.kind == "cli" and not stale:
                # Return CLI backend to free list for reuse. Interactive
                # instances live under their own key — a headless acquire
                # must never receive a session-driving backend or vice versa.
                free_key = self._free_list_key(instrument_name, backend)
                if free_key not in self._cli_free:
                    self._cli_free[free_key] = []
                self._cli_free[free_key].append(backend)
            # A stale (pre-reload) backend is dropped, not re-listed (#171);
            # it remains in _all_backends for close_all on shutdown.

            # HTTP singletons are never "released" — they stay active

        _logger.debug(
            "backend_pool.released",
            extra={
                "instrument": instrument_name,
                "in_flight": self._in_flight.get(instrument_name, 0),
            },
        )

    def invalidate(self) -> None:
        """Drop cached backends so the next acquire rebuilds from the
        current registry profiles (#171 hot-reload).

        Idle backends (free lists, HTTP singletons) are dropped now;
        in-flight backends finish on their loaded profile and are dropped
        on release via the generation check. Synchronous + lock-free: it
        only bumps a counter and clears dicts, so it is safe to call from
        the SIGHUP handler.
        """
        self._generation += 1
        self._cli_free.clear()
        self._http_singletons.clear()
        _logger.info(
            "backend_pool.invalidated",
            extra={"generation": self._generation},
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

    @staticmethod
    def _free_list_key(instrument_name: str, backend: Backend) -> str:
        """Free-list key — interactive instances are pooled separately."""
        from marianne.execution.instruments.interactive import (
            InteractiveCliBackend,
        )

        if isinstance(backend, InteractiveCliBackend):
            return f"{instrument_name}:interactive"
        return instrument_name

    def _acquire_locked(
        self,
        profile: InstrumentProfile,
        *,
        model: str | None = None,
        working_directory: Path | None = None,
        api_key: str | None = None,
        interactive: bool = False,
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
                backend._pool_generation = self._generation  # type: ignore[attr-defined]
                self._all_backends.append(backend)
            backend = self._http_singletons[name]
        else:
            # CLI: pop from free list or create new. Interactive and
            # headless instances use distinct keys.
            free_key = f"{name}:interactive" if interactive else name
            free_list = self._cli_free.get(free_key, [])
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
                    interactive=interactive,
                )
                backend._pool_generation = self._generation  # type: ignore[attr-defined]
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
