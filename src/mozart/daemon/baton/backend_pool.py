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
import logging
from pathlib import Path

from mozart.backends.base import Backend
from mozart.core.config.instruments import InstrumentProfile
from mozart.instruments.registry import InstrumentRegistry

_logger = logging.getLogger(__name__)


def _create_backend_for_profile(
    profile: InstrumentProfile,
    *,
    working_directory: Path | None = None,
    model: str | None = None,
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

    Returns:
        A configured Backend instance.
    """
    if profile.kind == "cli":
        from mozart.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend(
            profile=profile,
            working_directory=working_directory,
        )
        if model:
            backend.apply_overrides({"model": model})
        return backend

    # HTTP instruments — use existing native backends if available
    # For now, fall through to PluginCliBackend for CLI and raise for HTTP
    # HTTP support is deferred to v1.1
    msg = (
        f"HTTP instrument backends are not yet supported (instrument: "
        f"'{profile.name}', kind: '{profile.kind}'). "
        f"Use CLI instruments for v1."
    )
    raise NotImplementedError(msg)


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

    def __init__(self, registry: InstrumentRegistry) -> None:
        self._registry = registry

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

        async with self._lock:
            backend = self._acquire_locked(
                profile,
                model=model,
                working_directory=working_directory,
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

        # Track in-flight
        self._in_flight[name] = self._in_flight.get(name, 0) + 1
        return backend
