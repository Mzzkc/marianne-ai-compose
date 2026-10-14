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
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import urllib.error
import urllib.request
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
    name = profile.name

    if profile.execution_status == "unsupported":
        # Declared unsupported is never a runnable entry, whatever its kind
        # (Forge Inspect P2, unsupported-class-entry-reported-ready).
        detail = profile.execution_status_detail or "execution_status: unsupported"
        return False, f"Instrument '{name}' is unsupported: {detail}"

    if profile.kind == "http":
        # A served-model check for local servers only (GH #437). Remote
        # endpoints need credentials and are never probed here.
        return _local_http_model_available(profile)

    if profile.kind != "cli":
        # Native instruments have no binary to check
        return True, ""

    if profile.cli is None or profile.cli.command is None:
        return False, f"Instrument '{name}' is CLI kind but has no command configured"

    executable = profile.cli.command.executable
    if shutil.which(executable) is None:
        return False, f"Binary '{executable}' not found on PATH for instrument '{name}'"

    return True, ""


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


def _local_http_model_available(profile: InstrumentProfile) -> tuple[bool, str]:
    if profile.http is None or not profile.default_model:
        return True, ""
    served = served_local_models(profile.http.base_url)
    if served is None:
        return True, ""  # not loopback, or not answering: V215's domain
    if profile.default_model in served:
        return True, ""
    shown = ", ".join(served[:6]) + (", …" if len(served) > 6 else "")
    return False, (
        f"model '{profile.default_model}' is not served at {profile.http.base_url} "
        f"for instrument '{profile.name}' (served: {shown or 'none'}); pull it or set "
        f"instrument_config.model / the profile's default_model to one that is"
    )
