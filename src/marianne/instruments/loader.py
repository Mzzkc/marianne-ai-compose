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

A file in the organization or venue layer may declare ``extends: <name>``
(GH #543): the loader merges it over the named profile from the layers below
(a file may extend its own name) and validates the result as one profile.
Refusals (missing base, cycle, ``extends`` in a builtin, base not in a lower
layer) are ``ProfileLoadFailure`` records with ``instrument_extends_*`` codes.

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

import copy
import hashlib
import ipaddress
import socket
from collections.abc import Sequence
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


# PyYAML's pure-Python parser spends ~35 ms per profile; `mzt validate`
# loads every profile up to a dozen times per run (one per check that asks),
# so a 50-profile host paid ~20 s per validate and the suite's validate tests
# tripped their 30 s timeouts under load. The C parser is the same SafeLoader
# semantics ~10x faster, and a per-file memo keyed on the file's identity
# (resolved path, mtime_ns, size) makes the repeat loads free while an edited
# profile is always re-read — hot reload (#408) and `instruments check` see
# the new bytes without any invalidation call.
_SAFE_LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_FileKey = tuple[str, int, int]
_MemoValue = tuple[InstrumentProfile | None, "ProfileLoadFailure | None"]
# Plain files are keyed by their own _FileKey; a profile built with `extends`
# is keyed by the tuple of every _FileKey in its chain, so editing a BASE file
# misses the memo and the extending profile is rebuilt (#543).
_profile_memo: dict[Any, _MemoValue] = {}
_MappingValue = tuple["dict[str, Any] | None", str, "ProfileLoadFailure | None"]
_mapping_memo: dict[_FileKey, _MappingValue] = {}


def _file_key(path: Path) -> _FileKey | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), st.st_mtime_ns, st.st_size)


def clear_profile_memo() -> None:
    """Forget memoised profile loads (tests that rewrite a file in place
    within one mtime tick, or callers that want a cold read)."""
    _profile_memo.clear()
    _mapping_memo.clear()


@dataclass(frozen=True)
class _Layer:
    """One profile directory in precedence order (lowest first)."""

    directory: Path
    label: str
    builtin: bool = False


def _builtins_dir() -> Path:
    return Path(__file__).resolve().parent / "builtins"


def _make_layers(
    directories: Sequence[str | Path], labels: Sequence[str] | None = None,
) -> list[_Layer]:
    """Layers for ``directories``; a directory already listed is dropped.

    The venue default is a relative path that names the organization
    directory itself from ``$HOME`` (GH #493); scanning it twice would only
    make a profile that extends its own name look like a cycle.
    """
    builtin = _builtins_dir().resolve()
    seen: set[Path] = set()
    layers: list[_Layer] = []
    for i, d in enumerate(directories):
        path = Path(d)
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        is_builtin = resolved == builtin
        label = (
            labels[i] if labels is not None and i < len(labels)
            else ("builtin" if is_builtin else str(path))
        )
        layers.append(_Layer(path, label, is_builtin))
    return layers


def _read_mapping(path: Path) -> _MappingValue:
    """Read and YAML-parse one file to its raw mapping, memoised by file identity."""
    key = _file_key(path)
    if key is not None and key in _mapping_memo:
        return _mapping_memo[key]
    result = _read_mapping_uncached(path)
    if key is not None:
        _mapping_memo[key] = result
    return result


def _read_mapping_uncached(path: Path) -> _MappingValue:
    try:
        raw_bytes = path.read_bytes()
        raw_text = raw_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, "", ProfileLoadFailure(
            path=path.resolve(), reason_code="instrument_file_read_error",
            reason=str(e) or type(e).__name__,
        )
    sha = hashlib.sha256(raw_bytes).hexdigest()
    try:
        data: Any = yaml.load(raw_text, Loader=_SAFE_LOADER)  # noqa: S506 - SafeLoader variant
    except yaml.YAMLError as e:
        return None, sha, ProfileLoadFailure(
            path=path.resolve(), reason_code="instrument_yaml_parse_error",
            reason=str(e) or type(e).__name__,
        )
    if not isinstance(data, dict):
        return None, sha, ProfileLoadFailure(
            path=path.resolve(), reason_code="instrument_yaml_not_dict",
            reason=f"file is {type(data).__name__}, expected a mapping",
        )
    return data, sha, None


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Overlay wins: mappings merge, scalars and lists replace, ``None`` clears."""
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _effective_name(data: dict[str, Any]) -> str | None:
    name = data.get("name", data.get("extends"))
    return name if isinstance(name, str) and name else None


def _layer_files(layer: _Layer) -> dict[str, Path]:
    """Profile name -> file in one layer; the last file alphabetically wins."""
    out: dict[str, Path] = {}
    if not layer.directory.is_dir():
        return out
    for f in sorted(
        f for f in layer.directory.iterdir()
        if f.is_file() and f.suffix in _YAML_EXTENSIONS
    ):
        data, _sha, failure = _read_mapping(f)
        if failure is not None or data is None:
            continue
        name = _effective_name(data)
        if name is not None:
            out[name] = f
    return out


class _ExtendsError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass
class _Resolved:
    data: dict[str, Any]
    chain: list[tuple[Path, _FileKey | None, str]]  # (file, key, sha), own file first
    base: tuple[str, str] | None  # (base name, base layer label) of the immediate base


def _resolve_raw(
    path: Path, layers: Sequence[_Layer], idx: int, stack: tuple[Path, ...],
) -> _Resolved:
    """Merge ``path`` (in ``layers[idx]``) over its ``extends`` chain.

    Raises ``_ExtendsError`` with a typed code for each refusal.
    """
    data, sha, failure = _read_mapping(path)
    if failure is not None or data is None:
        assert failure is not None
        raise _ExtendsError(failure.reason_code, failure.reason)
    own = (path.resolve(), _file_key(path), sha)
    if "extends" not in data:
        return _Resolved(data, [own], None)

    layer = layers[idx]
    base_name = data["extends"]
    if not isinstance(base_name, str) or not base_name.strip():
        raise _ExtendsError(
            "instrument_extends_invalid", "extends must be a non-empty profile name",
        )
    if layer.builtin:
        raise _ExtendsError(
            "instrument_extends_in_builtin",
            "extends is not allowed in a builtin profile; builtins are the base layer",
        )
    if path.resolve() in stack:
        raise _ExtendsError(
            "instrument_extends_cycle",
            f"extends cycle through {path.name} ({base_name!r})",
        )
    stack = (*stack, path.resolve())

    for j in range(idx - 1, -1, -1):
        base_file = _layer_files(layers[j]).get(base_name)
        if base_file is None:
            continue
        if base_file.resolve() in stack:
            raise _ExtendsError(
                "instrument_extends_cycle",
                f"extends cycle: {base_file.name} is already being resolved",
            )
        try:
            base = _resolve_raw(base_file, layers, j, stack)
        except _ExtendsError as e:
            raise _ExtendsError(
                e.code, f"base profile {base_name!r} ({layers[j].label}) cannot load: {e.reason}",
            ) from None
        overlay = {k: v for k, v in data.items() if k != "extends"}
        merged = _deep_merge(base.data, overlay)
        merged.setdefault("name", base_name)
        return _Resolved(merged, [own, *base.chain], (base_name, layers[j].label))

    elsewhere = [
        lay.label for lay in layers[idx:]
        if any(p != path for p in [_layer_files(lay).get(base_name)] if p is not None)
    ]
    if elsewhere:
        raise _ExtendsError(
            "instrument_extends_not_below",
            f"extends {base_name!r} names a profile only in the same or a higher layer "
            f"({', '.join(elsewhere)}); a file can extend only a profile from a layer below "
            f"its own ({layer.label})",
        )
    raise _ExtendsError(
        "instrument_extends_missing_base",
        f"extends {base_name!r}: no profile of that name in any layer below {layer.label}",
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
        *,
        layers: Sequence[_Layer] | None = None,
        layer_index: int = 0,
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
                yaml_file, layers=layers, layer_index=layer_index,
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
        *,
        lower_layers: Sequence[str | Path] = (),
        labels: Sequence[str] | None = None,
    ) -> dict[str, InstrumentProfile]:
        """Load profiles from multiple directories with override semantics.

        Later directories override earlier ones on name collision. The
        intended loading order:
            1. Built-in profiles (lowest precedence)
            2. Organization profiles (~/.marianne/instruments/)
            3. Venue profiles (.marianne/instruments/, highest precedence)

        A file may declare ``extends: <name>`` (#543): it is merged over the
        named profile from the layers below it. ``lower_layers`` names
        directories that sit below ``directories`` and are consulted only to
        resolve ``extends``; their profiles are not returned.

        Args:
            directories: Ordered list of directories to scan. Missing
                directories are silently skipped.
            lower_layers: Directories below ``directories``, for ``extends``.
            labels: Layer names for ``directories`` (messages and metadata).

        Returns:
            Merged dict of profile name → InstrumentProfile.
        """
        merged: dict[str, InstrumentProfile] = {}
        lower = _make_layers(lower_layers)
        own = _make_layers(
            directories, labels,
        )
        # A directory listed below is not rescanned above.
        lower_resolved = {lay.directory.resolve() for lay in lower}
        own = [lay for lay in own if lay.directory.resolve() not in lower_resolved]
        layers = [*lower, *own]

        for i, layer in enumerate(own):
            dir_profiles, _failures = InstrumentProfileLoader._scan_directory(
                layer.directory, layers=layers, layer_index=len(lower) + i,
            )
            for name, profile in dir_profiles.items():
                if name in merged:
                    _logger.info(
                        "instrument_overridden_by_later_dir",
                        name=name,
                        directory=str(layer.directory),
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
        *,
        layers: Sequence[_Layer] | None = None,
        layer_index: int = 0,
    ) -> tuple[InstrumentProfile | None, ProfileLoadFailure | None]:
        """Memoised ``_load_file_uncached``: the same bytes (path, mtime,
        size) parse once per process; a changed file is parsed afresh.

        A file that declares ``extends`` (#543) is resolved against ``layers``
        below ``layer_index`` and memoised under every file key in its chain,
        so editing the base file rebuilds it.
        """
        key = _file_key(path)
        if key is not None:
            hit = _profile_memo.get(key)
            if hit is not None:
                return hit
        data, _sha, _failure = _read_mapping(path)
        if data is not None and "extends" in data:
            return InstrumentProfileLoader._load_extending(
                path, layers if layers is not None else [], layer_index,
            )
        result = InstrumentProfileLoader._load_file_uncached(path)
        if key is not None:
            _profile_memo[key] = result
        return result

    @staticmethod
    def _load_extending(
        path: Path, layers: Sequence[_Layer], layer_index: int,
    ) -> tuple[InstrumentProfile | None, ProfileLoadFailure | None]:
        """Build one profile from a file that declares ``extends`` (#543)."""
        if not layers:
            layers = [_Layer(path.parent, "layer")]
            layer_index = 0
            note = " (no lower layer was supplied)"
        else:
            note = ""
        try:
            resolved = _resolve_raw(path, layers, layer_index, ())
        except _ExtendsError as e:
            _logger.warning(
                "instrument_extends_error", file=str(path), code=e.code, error=e.reason,
            )
            return None, ProfileLoadFailure(
                path=path.resolve(), reason_code=e.code, reason=e.reason + note,
            )
        memo_key = ("extends", *(k for _p, k, _s in resolved.chain))
        if None not in memo_key:
            hit = _profile_memo.get(memo_key)
            if hit is not None:
                return hit
        digest = hashlib.sha256(
            "\n".join(s for _p, _k, s in resolved.chain).encode("ascii"),
        ).hexdigest()
        result = InstrumentProfileLoader._validate_mapping(
            resolved.data, path, digest,
        )
        profile = result[0]
        if profile is not None and resolved.base is not None:
            profile._extends = resolved.base
        if None not in memo_key:
            _profile_memo[memo_key] = result
        return result

    @staticmethod
    def _load_file_uncached(
        path: Path,
    ) -> tuple[InstrumentProfile | None, ProfileLoadFailure | None]:
        """Load and validate one file, returning WHY it failed.

        Returns ``(profile, None)`` on success and ``(None, failure)``
        on any error — read failures, parse failures, validation
        errors, unexpected structure. All errors are logged exactly as
        ``_load_file`` logs them; the failure record carries the same
        reason so reload callers can report it (#408 landing).
        """
        data, sha, failure = _read_mapping_uncached(path)
        if failure is not None or data is None:
            _logger.warning(
                failure.reason_code if failure else "instrument_file_read_error",
                file=str(path),
                error=failure.reason if failure else "",
            )
            return None, failure
        return InstrumentProfileLoader._validate_mapping(data, path, sha)

    @staticmethod
    def _validate_mapping(
        data: dict[str, Any], path: Path, sha256: str,
    ) -> tuple[InstrumentProfile | None, ProfileLoadFailure | None]:
        """Validate one raw mapping through Pydantic and stamp loader metadata."""
        try:
            profile = InstrumentProfile.model_validate(data)
        except Exception as e:  # noqa: BLE001 — structured logger reports instrument_validation_error
            _logger.warning(
                "instrument_validation_error",
                file=str(path),
                error=str(e),
            )
            reason_code = "instrument_validation_error"
            if "extra_forbidden" in str(e):
                # The file names a key this installed schema does not
                # have: the file is not broken, the running code is older
                # than the file (an editable install that moved on, or a
                # profile written for a newer release). GH #450.
                reason_code = "instrument_schema_stale"
            return None, ProfileLoadFailure(
                path=path.resolve(),
                reason_code=reason_code,
                reason=str(e) or type(e).__name__,
            )

        _logger.debug(
            "instrument_loaded",
            name=profile.name,
            kind=profile.kind,
            file=str(path),
        )

        profile._source_path = path.resolve()
        profile._source_sha256 = sha256
        return profile, None


_LAYER_LABELS = ("builtin", "organization", "venue")


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
        [builtins_dir, org_dir, venue_dir], labels=_LAYER_LABELS,
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

    layers = _make_layers([builtins_dir, org_dir, venue_dir], _LAYER_LABELS)
    for i, layer in enumerate(layers):
        directory = layer.directory
        dir_profiles, dir_failures = InstrumentProfileLoader._scan_directory(
            directory, layers=layers, layer_index=i,
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
    # GH #493: the venue default is a RELATIVE path, so from $HOME it names
    # the organization directory itself. Classify the organization layer
    # first; a profile is "venue" only when its directory is a venue
    # directory that is not also the organization one, so the same bytes
    # yield the same origin from every working directory.
    origin: Literal["organization", "venue"] | None = None
    source_dir = profile._source_path.parent
    if source_dir == org.resolve():
        origin = "organization"
    elif source_dir == venue.resolve():
        origin = "venue"
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
