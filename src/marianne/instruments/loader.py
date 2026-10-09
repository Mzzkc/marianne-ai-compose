"""Instrument profile loader.

Scans directories for YAML instrument profiles and parses them into
validated InstrumentProfile instances. This is the entry point for the
instrument plugin system — the conductor calls the loader at startup
to discover available instruments.

Loading order matters:
    1. Built-in profiles (shipped with Marianne, lowest precedence)
    2. Organization profiles (~/.marianne/instruments/)
    3. Venue profiles (.marianne/instruments/, highest precedence)

Later directories override earlier ones on name collision. This lets
venue-specific profiles customize organization-wide defaults, which in
turn customize built-in defaults.

Invalid YAML files, validation failures, and other errors are logged and
skipped — one broken profile should not prevent other instruments from
loading. This is a reliability-first design: degrade gracefully, log
clearly, continue operating.

Usage:
    from marianne.instruments.loader import InstrumentProfileLoader

    profiles = InstrumentProfileLoader.load_directories([
        Path.home() / ".marianne" / "instruments",
        Path(".marianne/instruments"),
    ])
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

from marianne.core.config.instruments import InstrumentProfile, InstrumentRouteBinding
from marianne.core.logging import get_logger

_logger = get_logger("instruments.loader")

# File extensions the loader recognizes as instrument profiles.
_YAML_EXTENSIONS = frozenset({".yaml", ".yml"})


@dataclass(frozen=True)
class ProfileLoadFailure:
    """One instrument profile file that exists but failed to load (#408).

    Carries the exact reason the loader skipped the file — the same
    reason already emitted as a WARNING by ``_load_file`` — so the
    hot-reload path can keep the previous registry entry and report
    the file instead of silently removing it (the #397 edit-accident
    class). ``path`` is resolved to match ``InstrumentProfile._source_path``.
    """

    path: Path
    reason_code: str
    reason: str


def route_identity(binding: InstrumentRouteBinding) -> tuple[str | int | None, ...]:
    """Exact route projection; observation time is not route identity.

    No normalization or optional-field wildcarding is permitted. A fresh
    capture can have a later timestamp while retaining the identical route.
    """
    return (
        binding.arm, binding.instrument, binding.kind, binding.profile_origin,
        binding.profile_file_sha256, binding.effective_model, binding.effective_provider,
        binding.model_source, binding.provider_source, binding.transport_scheme,
        binding.transport_host, binding.transport_port, binding.transport_endpoint,
    )


class InstrumentProfileLoader:
    """Loads InstrumentProfile instances from YAML files in directories.

    The loader is deliberately simple: scan a directory for YAML files,
    parse each one, validate via Pydantic, and collect the results. No
    recursion into subdirectories. No implicit file discovery magic.

    Error handling: every failure is logged with the file path and reason.
    The loader continues past failures — one broken YAML file should not
    prevent other instruments from loading.
    """

    @staticmethod
    def load_directory(directory: str | Path) -> dict[str, InstrumentProfile]:
        """Load all instrument profiles from a single directory.

        Args:
            directory: Path to scan for *.yaml and *.yml files.
                If the directory does not exist, returns empty dict.

        Returns:
            Dict of profile name → InstrumentProfile. When two files in
            the same directory define the same name, the last one
            (alphabetically by filename) wins.
        """
        profiles, _failures = InstrumentProfileLoader._scan_directory(directory)
        return profiles

    @staticmethod
    def _scan_directory(
        directory: str | Path,
    ) -> tuple[dict[str, InstrumentProfile], list[ProfileLoadFailure]]:
        """Scan one directory, reporting files that failed to load.

        Same scan/override semantics as ``load_directory``; additionally
        returns one ``ProfileLoadFailure`` per file that exists but could
        not be loaded (read/parse/validation error).
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            _logger.debug(
                "instruments_dir_not_found",
                directory=str(dir_path),
            )
            return {}, []

        profiles: dict[str, InstrumentProfile] = {}
        failures: list[ProfileLoadFailure] = []

        # Sort files alphabetically for deterministic override behavior
        yaml_files = sorted(
            f for f in dir_path.iterdir()
            if f.is_file() and f.suffix in _YAML_EXTENSIONS
        )

        for yaml_file in yaml_files:
            profile, failure = InstrumentProfileLoader._load_file_detailed(
                yaml_file
            )
            if failure is not None:
                failures.append(failure)
            if profile is not None:
                if profile.name in profiles:
                    _logger.info(
                        "instrument_name_override",
                        name=profile.name,
                        file=str(yaml_file),
                        previous_file="(same directory)",
                    )
                profiles[profile.name] = profile

        if profiles:
            _logger.info(
                "instruments_loaded",
                directory=str(dir_path),
                count=len(profiles),
                names=sorted(profiles.keys()),
            )

        return profiles, failures

    @staticmethod
    def load_directories(
        directories: list[str | Path],
    ) -> dict[str, InstrumentProfile]:
        """Load profiles from multiple directories with override semantics.

        Later directories override earlier ones on name collision. The
        intended loading order:
            1. Built-in profiles (lowest precedence)
            2. Organization profiles (~/.marianne/instruments/)
            3. Venue profiles (.marianne/instruments/, highest precedence)

        Args:
            directories: Ordered list of directories to scan. Missing
                directories are silently skipped.

        Returns:
            Merged dict of profile name → InstrumentProfile.
        """
        merged: dict[str, InstrumentProfile] = {}

        for directory in directories:
            dir_profiles = InstrumentProfileLoader.load_directory(directory)
            for name, profile in dir_profiles.items():
                if name in merged:
                    _logger.info(
                        "instrument_overridden_by_later_dir",
                        name=name,
                        directory=str(directory),
                    )
                merged[name] = profile

        _logger.info(
            "instruments_total_loaded",
            count=len(merged),
            names=sorted(merged.keys()),
        )

        return merged

    @staticmethod
    def _load_file(path: Path) -> InstrumentProfile | None:
        """Load and validate a single YAML instrument profile.

        Returns None on any error — parse failures, validation errors,
        unexpected structure. All errors are logged.
        """
        profile, _failure = InstrumentProfileLoader._load_file_detailed(path)
        return profile

    @staticmethod
    def _load_file_detailed(
        path: Path,
    ) -> tuple[InstrumentProfile | None, ProfileLoadFailure | None]:
        """Load and validate one file, returning WHY it failed.

        Returns ``(profile, None)`` on success and ``(None, failure)``
        on any error — read failures, parse failures, validation
        errors, unexpected structure. All errors are logged exactly as
        ``_load_file`` logs them; the failure record carries the same
        reason so reload callers can report it (#408 landing).
        """
        try:
            raw_bytes = path.read_bytes()
            raw_text = raw_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            _logger.warning(
                "instrument_file_read_error",
                file=str(path),
                error=str(e),
            )
            return None, ProfileLoadFailure(
                path=path.resolve(),
                reason_code="instrument_file_read_error",
                reason=str(e) or type(e).__name__,
            )

        # Parse YAML
        try:
            data: Any = yaml.safe_load(raw_text)
        except yaml.YAMLError as e:
            _logger.warning(
                "instrument_yaml_parse_error",
                file=str(path),
                error=str(e),
            )
            return None, ProfileLoadFailure(
                path=path.resolve(),
                reason_code="instrument_yaml_parse_error",
                reason=str(e) or type(e).__name__,
            )

        # Must be a dict
        if not isinstance(data, dict):
            _logger.warning(
                "instrument_yaml_not_dict",
                file=str(path),
                actual_type=type(data).__name__,
            )
            return None, ProfileLoadFailure(
                path=path.resolve(),
                reason_code="instrument_yaml_not_dict",
                reason=f"file is {type(data).__name__}, expected a mapping",
            )

        # Validate through Pydantic
        try:
            profile = InstrumentProfile.model_validate(data)
        except Exception as e:
            _logger.warning(
                "instrument_validation_error",
                file=str(path),
                error=str(e),
            )
            return None, ProfileLoadFailure(
                path=path.resolve(),
                reason_code="instrument_validation_error",
                reason=str(e) or type(e).__name__,
            )

        _logger.debug(
            "instrument_loaded",
            name=profile.name,
            kind=profile.kind,
            file=str(path),
        )

        profile._source_path = path.resolve()
        profile._source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        return profile, None


def profile_source_dirs(
    *, organization_dir: Path | None = None, venue_dir: Path | None = None,
) -> tuple[Path, Path, Path]:
    """Resolve the three instrument profile source directories (#408).

    Single source of truth for both ``load_all_profiles`` and the config
    hot-reload watcher: built-ins, organization (~/.marianne/instruments),
    venue (.marianne/instruments). Explicit arguments override the defaults
    (used by tests and by callers that recorded their boot-time dirs).
    """
    builtins_dir = Path(__file__).resolve().parent / "builtins"
    org_dir = (
        organization_dir if organization_dir is not None
        else Path.home() / ".marianne" / "instruments"
    )
    resolved_venue_dir = (
        venue_dir if venue_dir is not None else Path(".marianne") / "instruments"
    )
    return builtins_dir, org_dir, resolved_venue_dir


def load_all_profiles(
    *, organization_dir: Path | None = None, venue_dir: Path | None = None,
) -> dict[str, InstrumentProfile]:
    """Load all instrument profiles from all standard sources.

    Convenience function that encapsulates the standard loading order:
        1. Built-in YAML profiles (shipped with Marianne)
        2. Organization profiles (~/.marianne/instruments/)
        3. Venue profiles (.marianne/instruments/)

    Later sources override earlier ones on name collision.

    Returns:
        Dict of profile name → InstrumentProfile.
    """
    profiles: dict[str, InstrumentProfile] = {}

    builtins_dir, org_dir, venue_dir = profile_source_dirs(
        organization_dir=organization_dir, venue_dir=venue_dir,
    )

    yaml_profiles = InstrumentProfileLoader.load_directories(
        [builtins_dir, org_dir, venue_dir]
    )

    profiles.update(yaml_profiles)
    return profiles


def load_all_profiles_with_failures(
    *, organization_dir: Path | None = None, venue_dir: Path | None = None,
) -> tuple[dict[str, InstrumentProfile], list[ProfileLoadFailure]]:
    """``load_all_profiles`` with per-file failure reporting (#408 reload).

    Same sources and the same override semantics as
    ``load_all_profiles``, but instead of only skipping files that
    exist and fail to load, each skip is returned as a
    ``ProfileLoadFailure`` so the hot-reload path can keep the previous
    registry entry for that file and report it to the operator. Boot
    callers keep the tolerant ``load_all_profiles`` contract unchanged.

    Returns:
        Tuple of (merged profiles by name, per-file load failures).
    """
    merged: dict[str, InstrumentProfile] = {}
    failures: list[ProfileLoadFailure] = []

    builtins_dir, org_dir, venue_dir = profile_source_dirs(
        organization_dir=organization_dir, venue_dir=venue_dir,
    )

    for directory in (builtins_dir, org_dir, venue_dir):
        dir_profiles, dir_failures = InstrumentProfileLoader._scan_directory(
            directory
        )
        for name, profile in dir_profiles.items():
            if name in merged:
                _logger.info(
                    "instrument_overridden_by_later_dir",
                    name=name,
                    directory=str(directory),
                )
            merged[name] = profile
        failures.extend(dir_failures)

    _logger.info(
        "instruments_total_loaded",
        count=len(merged),
        names=sorted(merged.keys()),
    )

    return merged, failures


def verify_single_route(document: dict[str, Any], instrument: str) -> None:
    """Verify the deliberately bounded single-route score shape, not arbitrary scores."""
    if document.get("instrument") != instrument:
        raise ValueError("LOCAL_TRANSPORT_ROUTE_MISMATCH: score instrument differs")
    if document.get("movements"):
        raise ValueError("LOCAL_TRANSPORT_MOVEMENT_ARM: bound route has movements")
    if document.get("instrument_fallbacks"):
        raise ValueError("LOCAL_TRANSPORT_FALLBACK_ARM: bound route has fallbacks")
    for block in (document, document.get("sheet", {})):
        if not isinstance(block, dict):
            raise ValueError("LOCAL_TRANSPORT_UNVERIFIED: invalid sheet configuration")
        for key in ("per_sheet_instruments", "per_sheet_fallbacks", "instrument_map"):
            if block.get(key):
                raise ValueError("LOCAL_TRANSPORT_PER_SHEET_ARM: bound route has alternatives")


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        try:
            addresses = socket.getaddrinfo(host, None)
            return bool(addresses) and all(
                ipaddress.ip_address(address[4][0]).is_loopback for address in addresses
            )
        except (OSError, ValueError):
            return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def capture_resolved_instrument_route(
    instrument: str,
    instrument_config: dict[str, Any],
    *,
    now: datetime,
    arm: Literal["local", "remote"] = "local",
    organization_dir: Path | None = None,
    venue_dir: Path | None = None,
    loaded_profile: InstrumentProfile | None = None,
) -> InstrumentRouteBinding:
    """Capture the winning loader profile and exact declared request overrides.

    A loaded executor can supply its original profile to refuse stale cached
    configuration. Synchronous file/DNS work belongs in ``asyncio.to_thread``
    when called from the execution path. No profile body enters the binding.
    """
    org = (
        organization_dir if organization_dir is not None
        else Path.home() / ".marianne" / "instruments"
    )
    venue = venue_dir if venue_dir is not None else Path(".marianne") / "instruments"
    profile = load_all_profiles(organization_dir=org, venue_dir=venue).get(instrument)
    if profile is None or profile._source_path is None:
        raise ValueError("LOCAL_TRANSPORT_UNVERIFIED: instrument profile is not observable")
    origin: Literal["organization", "venue"] | None = None
    if profile._source_path.parent == venue.resolve():
        origin = "venue"
    elif profile._source_path.parent == org.resolve():
        origin = "organization"
    if arm == "local" and origin is None:
        raise ValueError("LOCAL_TRANSPORT_UNVERIFIED: local profile must be registry-visible")
    if loaded_profile is not None and (
        loaded_profile.model_dump() != profile.model_dump()
        or loaded_profile._source_path != profile._source_path
        or loaded_profile._source_sha256 != profile._source_sha256
    ):
        raise ValueError("attempt_route_drift: loaded instrument differs from captured profile")
    model = instrument_config.get("model") or profile.default_model
    if not isinstance(model, str) or not model:
        raise ValueError("route_model_undeclared: no effective model declaration")
    provider = instrument_config.get("provider")
    if provider is not None and (not isinstance(provider, str) or not provider):
        raise ValueError("LOCAL_TRANSPORT_UNVERIFIED: invalid provider declaration")
    scheme: Literal["http", "https"] | None = None
    host, port, endpoint = None, None, None
    if profile.http is not None:
        url = urlsplit(profile.http.base_url)
        if url.username is not None or url.password is not None:
            raise ValueError("LOCAL_TRANSPORT_URL_CREDENTIALS: credentials in transport URL")
        if url.scheme not in {"http", "https"}:
            raise ValueError("LOCAL_TRANSPORT_UNVERIFIED: unsupported transport scheme")
        scheme = "https" if url.scheme == "https" else "http"
        host, port, endpoint = url.hostname, url.port, profile.http.endpoint
        if arm == "local" and (
            not host or not _is_loopback_host(host)
            or "://" in endpoint or endpoint.startswith("//")
        ):
            raise ValueError("LOCAL_TRANSPORT_REMOTE_HTTP_URL: route is not loopback-relative")
    if arm == "local" and (profile.kind != "http" or profile.http is None):
        raise ValueError("LOCAL_TRANSPORT_UNVERIFIED: local route requires an HTTP profile")
    return InstrumentRouteBinding(
        arm=arm, instrument=instrument, kind=profile.kind,
        profile_origin=origin, profile_file_sha256=profile._source_sha256,
        effective_model=model, effective_provider=provider,
        model_source="score_override" if instrument_config.get("model") else "profile",
        provider_source="score_override" if provider is not None else None,
        transport_scheme=scheme, transport_host=host, transport_port=port,
        transport_endpoint=endpoint, resolved_at=now,
    )


def capture_instrument_route_binding(
    score_path: Path,
    instrument: str,
    score_digest: str,
    *,
    now: datetime,
    arm: Literal["local", "remote"] = "local",
    organization_dir: Path | None = None,
    venue_dir: Path | None = None,
) -> InstrumentRouteBinding:
    """Capture one reviewed score using the same resolver as the conductor."""
    data = score_path.read_bytes()
    document = yaml.safe_load(data)
    if not isinstance(document, dict):
        raise ValueError("LOCAL_TRANSPORT_UNVERIFIED: score is not a mapping")
    verify_single_route(document, instrument)
    overrides = document.get("instrument_config", {})
    if not isinstance(overrides, dict):
        raise ValueError("LOCAL_TRANSPORT_UNVERIFIED: invalid instrument configuration")
    binding = capture_resolved_instrument_route(
        instrument, overrides, now=now, arm=arm,
        organization_dir=organization_dir, venue_dir=venue_dir,
    )
    if not score_digest or hashlib.sha256(data).hexdigest() != score_digest:
        raise ValueError("LOCAL_TRANSPORT_SCORE_CHANGED: score bytes do not match reviewed digest")
    return binding
