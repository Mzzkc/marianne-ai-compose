"""Instrument availability checks.

Checks whether a named instrument is available for execution.
Used by:
- ``mzt doctor`` — instrument health checks
- ``mzt validate`` — warn on unavailable instruments
- Baton dispatch path — pre-flight before execution

The check is simple:
1. Is the instrument registered in the registry?
2. If it's a CLI instrument, is the binary on PATH?
3. If it's a CLI instrument, does it have a command configured?

Non-CLI instruments (http, native) are assumed available if registered.
"""

from __future__ import annotations

import shutil

from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.registry import InstrumentRegistry


def check_instrument_available(
    name: str,
    registry: InstrumentRegistry,
) -> tuple[bool, str]:
    """Check whether an instrument is available for execution.

    Args:
        name: The instrument name to check.
        registry: The instrument registry to look up profiles.

    Returns:
        A ``(available, reason)`` tuple. When available, reason is ``""``.
        When unavailable, reason describes why (for diagnostics/logging).
    """
    profile: InstrumentProfile | None = registry.get(name)
    if profile is None:
        return False, f"Instrument '{name}' not registered (profile not found in registry)"

    if profile.kind != "cli":
        # Non-CLI instruments have no binary to check
        return True, ""

    if profile.cli is None or profile.cli.command is None:
        return False, f"Instrument '{name}' is CLI kind but has no command configured"

    executable = profile.cli.command.executable
    if shutil.which(executable) is None:
        return False, f"Binary '{executable}' not found on PATH for instrument '{name}'"

    return True, ""
