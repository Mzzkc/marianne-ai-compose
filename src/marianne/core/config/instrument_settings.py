"""Typed score-facing instrument overrides shared by all four scopes."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer


def coerce_first_output_timeout(value: Any) -> float:
    """Single enforcement point for a first_output_timeout_seconds override.

    Shared by the pydantic schema below and adapter dispatch validation so
    YAML loading and direct ``Sheet`` callers that bypass YAML cannot drift
    apart. Accepts a finite positive number (numeric strings included);
    refuses booleans, zero, negatives, nonfinite values, and garbage.
    """
    if isinstance(value, bool):
        raise ValueError(
            "first_output_timeout_seconds must be a duration, not a boolean"
        )
    bound = float(value)
    if not math.isfinite(bound) or bound <= 0:
        raise ValueError("first_output_timeout_seconds must be finite and positive")
    return bound


class InstrumentSettings(BaseModel, Mapping[str, Any]):
    """Only declared instrument knobs are accepted; unset knobs do not override."""

    # Direct JobConfig construction historically accepts private profile knobs.
    # The tolerant YAML walk reports and removes unknown keys before this model.
    model_config = ConfigDict(extra="allow")

    timeout_seconds: float | str | None = Field(
        default=None,
        description="Maximum seconds one sheet's instrument execution may run; "
        "set this at score, movement, sheet, or score-alias scope.",
    )
    first_output_timeout_seconds: float | None = Field(
        default=None, gt=0, allow_inf_nan=False,
        description="CLI first-output silence bound in seconds, capped by the sheet "
        "timeout. Omit to inherit the profile; use a finite positive number to "
        "override, or null to disable. HTTP and interactive backends ignore it.",
    )

    @field_validator("first_output_timeout_seconds", mode="before")
    @classmethod
    def _first_output_duration_not_bool(cls, value: Any) -> Any:
        # Null (disable) and omission (inherit) are not durations; only a
        # present non-null value carries the shared coercion contract.
        if value is not None:
            coerce_first_output_timeout(value)
        return value

    model: str | None = Field(
        default=None, description="Model requested from this instrument profile."
    )
    provider: str | None = Field(
        default=None, description="Reviewed provider identity for a guarded route."
    )
    interactive: bool | None = Field(
        default=None, description="Use the instrument's interactive execution mode."
    )
    interactive_max_nudges: int | None = Field(
        default=None, ge=0, description="Maximum nudges during interactive execution."
    )
    interactive_nudge_message: str | None = Field(
        default=None, description="Message sent when nudging an idle interactive instrument."
    )
    interactive_nudge_policy: Literal["static", "model"] | None = Field(
        default=None, description="Idle continuation policy; static by default."
    )
    interactive_nudge_instrument: str | None = Field(
        default=None, min_length=1, description="Headless instrument used by model continuation."
    )
    interactive_nudge_timeout_seconds: float | None = Field(
        default=None, gt=0, allow_inf_nan=False,
        description="Maximum seconds per model continuation call.",
    )
    max_tokens: int | None = Field(
        default=None, gt=0, description="Maximum response tokens requested from the instrument."
    )
    temperature: float | None = Field(
        default=None, description="Sampling temperature passed to the instrument."
    )
    response_format: str | dict[str, Any] | None = Field(
        default=None, description="Response format the instrument must enforce."
    )

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        # Scope merging must distinguish absent from explicitly set null.
        names = (name for name in type(self).model_fields if name in self.model_fields_set)
        return iter((*names, *(self.model_extra or {})))

    def __len__(self) -> int:
        return len(self.model_fields_set) + len(self.model_extra or {})

    def __getitem__(self, key: str) -> Any:
        if key in self.model_fields_set:
            return getattr(self, key)
        if key in (self.model_extra or {}):
            return (self.model_extra or {})[key]
        raise KeyError(key)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self) == dict(other)
        return super().__eq__(other)

    @model_serializer(mode="plain")
    def _serialize(self) -> dict[str, Any]:
        return {key: self[key] for key in self}
