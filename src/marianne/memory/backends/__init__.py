"""Shipped memory backends. Each implements ``memory-backend/1.1``.

``backend_factory(system)`` is the one lookup from a score's ``system:`` name
to the callable that builds that backend from an attachment's configuration.
The files factory takes the resolved location mapping; the cam factory takes
the attachment's ``settings:``. The conductor's memory gate selects the backend through it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from marianne.memory.backend_protocol import MemoryBackend
from marianne.memory.backends.cam import cam_backend_factory
from marianne.memory.backends.files import files_backend_factory
from marianne.memory.errors import SchemaViolation

BackendFactory = Callable[[Mapping[str, Any]], MemoryBackend]

_FACTORIES: Mapping[str, BackendFactory] = {
    "files": files_backend_factory,
    "cam": cam_backend_factory,
}

#: Names accepted by :func:`backend_factory`, in registration order.
BACKEND_SYSTEMS: tuple[str, ...] = tuple(_FACTORIES)


def backend_factory(system: str) -> BackendFactory:
    """The factory for ``system``, or E7 ``system_unknown`` naming the legal set."""
    try:
        return _FACTORIES[system]
    except KeyError:
        raise SchemaViolation(
            f"memory backend system `{system}` is not shipped; accepted: "
            f"{', '.join(BACKEND_SYSTEMS)}",
            subtype="system_unknown",
        ) from None


__all__ = ["BACKEND_SYSTEMS", "BackendFactory", "backend_factory"]
