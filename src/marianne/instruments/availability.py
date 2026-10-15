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

Non-CLI instruments (http, native) are assumed available if registered, with
one exception (GH #437): an HTTP profile on a loopback host that ANSWERS a
model listing but does not serve the profile's ``default_model`` is
unavailable, with the served models in the reason. A loopback server that
does not answer at all stays "available" here — reachability is V215's
domain, and a test that registers a fake loopback profile must not pay for a
network round trip.

``probe_profile`` is the ONE probe behind every command that answers "can
this instrument run here" (GH #447: ``doctor``, ``instruments list``,
``instruments check``, ``instruments classes check|write`` used to answer
it five ways). Its four states have one definition each:

- ``ready``: can run now — CLI binary on PATH, or a loopback server that
  serves the profile's model, or a native instrument.
- ``unavailable``: cannot run — binary missing, declared unsupported, no
  command, or the loopback server answers but does not serve the model.
- ``unreachable``: a loopback server that does not answer.
- ``unchecked``: a remote HTTP endpoint; never probed (needs credentials).

``check_profile_available`` keeps its ``(bool, reason)`` contract on top of
it: only ``unavailable`` is False, so validate and dispatch are unchanged.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.registry import InstrumentRegistry

_LOCAL_MODEL_PROBE_TIMEOUT_SECONDS = 2.0


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

    return check_profile_available(profile)


def check_profile_available(profile: InstrumentProfile) -> tuple[bool, str]:
    """Apply the same local availability probe when the profile is already loaded."""
    probe = probe_profile(profile)
    if probe.state == "unavailable":
        return False, probe.reason
    return True, ""


ProbeState = Literal["ready", "unavailable", "unreachable", "unchecked"]


@dataclass(frozen=True)
class ProfileProbe:
    """What one instrument profile can do on this machine, right now."""

    state: ProbeState
    reason: str = ""
    binary_path: str | None = None

    @property
    def runnable(self) -> bool:
        return self.state == "ready"


def probe_profile(profile: InstrumentProfile) -> ProfileProbe:
    """The one availability probe every command consumes (GH #447)."""
    name = profile.name

    if profile.execution_status == "unsupported":
        # Declared unsupported is never a runnable entry, whatever its kind
        # (Forge Inspect P2, unsupported-class-entry-reported-ready).
        detail = profile.execution_status_detail or "execution_status: unsupported"
        return ProfileProbe("unavailable", f"Instrument '{name}' is unsupported: {detail}")

    if profile.kind == "http":
        return _probe_http(profile)

    if profile.kind != "cli":
        # Native instruments have no binary to check
        return ProfileProbe("ready")

    if profile.cli is None or profile.cli.command is None:
        return ProfileProbe(
            "unavailable", f"Instrument '{name}' is CLI kind but has no command configured"
        )

    executable = profile.cli.command.executable
    binary_path = shutil.which(executable)
    if binary_path is None:
        return ProfileProbe(
            "unavailable", f"Binary '{executable}' not found on PATH for instrument '{name}'"
        )

    return ProfileProbe("ready", binary_path=binary_path)


def _probe_http(profile: InstrumentProfile) -> ProfileProbe:
    """A served-model check for local servers only (GH #437). Remote endpoints
    need credentials and are never probed here."""
    if profile.http is None:
        return ProfileProbe("unchecked", "no HTTP configuration")
    parsed = urlparse(profile.http.base_url)
    if not _is_loopback(parsed.hostname):
        return ProfileProbe("unchecked", f"remote endpoint {profile.http.base_url} is not probed")
    served = served_local_models(profile.http.base_url)
    if served is None:
        return ProfileProbe(
            "unreachable", f"no server answered at {profile.http.base_url}"
        )
    if not profile.default_model or profile.default_model in served:
        return ProfileProbe("ready")
    shown = ", ".join(served[:6]) + (", …" if len(served) > 6 else "")
    return ProfileProbe(
        "unavailable",
        f"model '{profile.default_model}' is not served at {profile.http.base_url} "
        f"for instrument '{profile.name}' (served: {shown or 'none'}); pull it or set "
        "instrument_config.model / the profile's default_model to one that is",
    )


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def served_local_models(base_url: str) -> list[str] | None:
    """The model ids a loopback OpenAI-compatible server lists, or None if it
    does not answer (unreachable, non-JSON, or not loopback)."""
    parsed = urlparse(base_url)
    if not _is_loopback(parsed.hostname):
        return None
    # ``base_url`` may or may not already carry the ``/v1`` prefix; the
    # OpenAI-compatible listing lives at ``<origin>/v1/models`` either way.
    url = f"{parsed.scheme}://{parsed.netloc}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=_LOCAL_MODEL_PROBE_TIMEOUT_SECONDS) as resp:
            if int(resp.status) != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (TimeoutError, OSError, urllib.error.URLError, ValueError):
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    return [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]
