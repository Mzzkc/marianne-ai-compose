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

from pathlib import Path
from typing import Any

import yaml

from marianne.core.config.instruments import InstrumentProfile
from marianne.core.logging import get_logger

_logger = get_logger("instruments.loader")

# File extensions the loader recognizes as instrument profiles.
_YAML_EXTENSIONS = frozenset({".yaml", ".yml"})


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
        dir_path = Path(directory)
        if not dir_path.is_dir():
            _logger.debug(
                "instruments_dir_not_found",
                directory=str(dir_path),
            )
            return {}

        profiles: dict[str, InstrumentProfile] = {}

        # Sort files alphabetically for deterministic override behavior
        yaml_files = sorted(
            f for f in dir_path.iterdir()
            if f.is_file() and f.suffix in _YAML_EXTENSIONS
        )

        for yaml_file in yaml_files:
            profile = InstrumentProfileLoader._load_file(yaml_file)
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

        return profiles

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
        try:
            raw_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            _logger.warning(
                "instrument_file_read_error",
                file=str(path),
                error=str(e),
            )
            return None

        # Parse YAML
        try:
            data: Any = yaml.safe_load(raw_text)
        except yaml.YAMLError as e:
            _logger.warning(
                "instrument_yaml_parse_error",
                file=str(path),
                error=str(e),
            )
            return None

        # Must be a dict
        if not isinstance(data, dict):
            _logger.warning(
                "instrument_yaml_not_dict",
                file=str(path),
                actual_type=type(data).__name__,
            )
            return None

        # Validate through Pydantic
        try:
            profile = InstrumentProfile.model_validate(data)
        except Exception as e:
            _logger.warning(
                "instrument_validation_error",
                file=str(path),
                error=str(e),
            )
            return None

        _logger.debug(
            "instrument_loaded",
            name=profile.name,
            kind=profile.kind,
            file=str(path),
        )

        return profile


def load_all_profiles() -> dict[str, InstrumentProfile]:
    """Load all instrument profiles from all standard sources.

    Convenience function that encapsulates the standard loading order:
        1. Native instruments (4 built-in backends)
        2. Built-in YAML profiles (shipped with Marianne)
        3. Organization profiles (~/.marianne/instruments/)
        4. Venue profiles (.marianne/instruments/)

    Later sources override earlier ones on name collision.

    Returns:
        Dict of profile name → InstrumentProfile.
    """
    from marianne.instruments.registry import InstrumentRegistry, register_native_instruments

    registry = InstrumentRegistry()
    register_native_instruments(registry)

    profiles: dict[str, InstrumentProfile] = {
        p.name: p for p in registry.list_all()
    }

    builtins_dir = Path(__file__).resolve().parent / "builtins"
    org_dir = Path.home() / ".marianne" / "instruments"
    venue_dir = Path(".marianne") / "instruments"

    yaml_profiles = InstrumentProfileLoader.load_directories(
        [builtins_dir, org_dir, venue_dir]
    )

    profiles.update(yaml_profiles)
    return profiles
