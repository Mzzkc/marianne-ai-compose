"""Pure per-sheet resolution of the score's ``memory:`` key (spec 03, Behaviour 2-4).

``resolve_memory`` turns a validated :class:`MemoryConfig` plus a sheet's phase
identity into frozen :class:`ResolvedBinding` values. It performs no I/O: it
creates no directory, opens no socket and reads no registry. Scope grammar
errors surface as the typed ``E3`` from :func:`parse_scope`.

* ``memory:`` absent (``None``) -> ``[]``: zero gateway, zero sessions.
* ``memory:`` present with no backends -> one synthesized ``default`` binding
  (``files``, ``local:default``, every permission).
* otherwise one binding per attachment active in this sheet's phase, in
  declaration order, matched by the shared technique phase matcher.

The binding is conductor-side data. The musician never sees its principal,
permissions or scope; see :mod:`marianne.memory.manifest` for what it does see.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from marianne.core.config.memory import MemoryBackendDecl, MemoryConfig
from marianne.core.config.techniques import TechniqueConfig, TechniqueKind
from marianne.daemon.baton.techniques import filter_techniques_for_phase
from marianne.memory.access import INHERIT, parse_access
from marianne.memory.gateway.paths import planned_socket_path
from marianne.memory.model import ALL_PERMISSIONS, DEFAULT_SCOPE, parse_scope

#: Fixed namespace so attachment ids are stable across processes and machines.
ATTACHMENT_NAMESPACE = uuid.UUID("6d0a6b0e-3b5e-5a52-9c43-4d656d6f7279")
DEFAULT_ATTACHMENT = "default"


@dataclass(frozen=True)
class SheetPhase:
    """A sheet's phase identity: its movement name and/or number (either may be absent)."""

    name: str = ""
    movement: int | None = None


@dataclass(frozen=True)
class ResolvedBinding:
    """One attachment, resolved for one sheet. Conductor-side; never shown to a musician."""

    name: str
    attachment_id: str
    system: str
    scope: str
    principal: str
    access: str
    permissions: tuple[str, ...]
    recall_cap_bytes: int
    provenance: str
    lock_timeout_seconds: float
    trust: str
    run_dir: Path

    def socket_path(self, job_id: str | None, dispatch_id: str) -> Path:
        """The per-dispatch socket path (pure computation, nothing is created)."""
        return planned_socket_path(self.run_dir, job_id, dispatch_id, self.attachment_id)


def attachment_id(name: str, scope: str) -> str:
    """Deterministic UUIDv5 of ``name + "\\0" + canonical scope`` (spec 03 Interfaces)."""
    return str(uuid.uuid5(ATTACHMENT_NAMESPACE, f"{name}\0{scope}"))


def _active(decls: dict[str, MemoryBackendDecl], phase: SheetPhase) -> list[str]:
    # Reuse the shared matcher: wrap each attachment's phases as a technique declaration.
    wrapped = {
        name: TechniqueConfig(kind=TechniqueKind.PROTOCOL, phases=list(d.phases))
        for name, d in decls.items()
    }
    hit = filter_techniques_for_phase(wrapped, phase.name, movement=phase.movement)
    return [name for name in decls if name in hit]


def resolve_memory(
    config: MemoryConfig | None, phase: SheetPhase, *, user: str, run_dir: Path | str,
) -> list[ResolvedBinding]:
    if config is None:
        return []
    defaults = config.defaults
    decls = config.backends or {DEFAULT_ATTACHMENT: MemoryBackendDecl(system="files")}
    out: list[ResolvedBinding] = []
    for name in _active(decls, phase):
        d = decls[name]
        scope = parse_scope(d.scope).canonical if d.scope else DEFAULT_SCOPE.canonical
        access = parse_access(d.access)
        cap = defaults.recall_cap_bytes
        if "recall_cap_bytes" in d.caps.model_fields_set:
            cap = min(cap, d.caps.recall_cap_bytes)
        provenance = defaults.provenance
        if "provenance" in d.caps.model_fields_set and d.caps.provenance == "required":
            provenance = "required"
        out.append(ResolvedBinding(
            name=name,
            attachment_id=attachment_id(name, scope),
            system=d.system,
            scope=scope,
            principal=f"person:{user}",
            access=d.access,
            permissions=(
                tuple(p.value for p in ALL_PERMISSIONS) if access == INHERIT else ()
            ),
            recall_cap_bytes=cap,
            provenance=provenance,
            lock_timeout_seconds=defaults.lock_timeout_seconds,
            trust=d.trust,
            run_dir=Path(run_dir),
        ))
    return out


__all__ = [
    "ATTACHMENT_NAMESPACE", "ResolvedBinding", "SheetPhase", "attachment_id", "resolve_memory",
]
