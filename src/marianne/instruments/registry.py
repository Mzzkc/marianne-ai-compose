"""Instrument registry — lookup storage for config-loaded profiles."""

from __future__ import annotations

from marianne.core.config.instruments import InstrumentProfile
from marianne.core.logging import get_logger

_logger = get_logger("instruments.registry")


class InstrumentRegistry:
    """Mutable name-to-profile registry populated by profile loaders."""

    def __init__(self) -> None:
        self._profiles: dict[str, InstrumentProfile] = {}

    def register(self, profile: InstrumentProfile, *, override: bool = False) -> None:
        """Register one profile, rejecting accidental name collisions."""
        if profile.name in self._profiles and not override:
            raise ValueError(
                f"Instrument '{profile.name}' is already registered. "
                "Use override=True to replace it."
            )
        self._profiles[profile.name] = profile
        _logger.debug(
            "instrument_registered", name=profile.name, kind=profile.kind, override=override,
        )

    def replace_all(self, profiles: dict[str, InstrumentProfile]) -> None:
        """Replace contents in place during a profile reload."""
        self._profiles = dict(profiles)
        _logger.info("instrument_registry_reloaded", count=len(self._profiles))

    def get(self, name: str) -> InstrumentProfile | None:
        """Return a profile by score-visible name, or ``None``."""
        return self._profiles.get(name)

    def list_all(self) -> list[InstrumentProfile]:
        """Return all profiles in stable name order."""
        return sorted(self._profiles.values(), key=lambda profile: profile.name)

    def __len__(self) -> int:
        return len(self._profiles)

    def __contains__(self, name: str) -> bool:
        return name in self._profiles
