"""Author-facing loop and trigger configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


@dataclass(frozen=True)
class FlowIssue:
    """One independently actionable flow configuration error."""

    check: str
    span: str | None
    message: str
    hint: str | None = None


class FlowConfigError(ValueError):
    """Carries all flow violations through Pydantic to the S3 renderer."""

    def __init__(self, issues: tuple[FlowIssue, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


_SPAN_RE = re.compile(r"([0-9]+)(?:-([0-9]+))?\Z")


def canonical_span(value: Any) -> str:
    """Normalize inclusive sheet spans, rejecting ambiguous spelling."""
    if isinstance(value, bool):
        raise ValueError("a boolean is not a sheet span")
    if isinstance(value, int):
        if value < 1:
            raise ValueError("sheet span must start at 1 or above")
        return str(value)
    if not isinstance(value, str):
        raise ValueError("sheet span must be an integer or a string 'N' or 'N-M'")
    match = _SPAN_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid sheet span {value!r}: use N or N-M with ASCII digits, no spaces")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start < 1 or end < start:
        raise ValueError(f"invalid sheet span {value!r}: need 1 <= N <= M")
    return str(start) if start == end else f"{start}-{end}"


SheetSpan = Annotated[str, BeforeValidator(canonical_span)]
SkipTarget = SheetSpan


def span_bounds(span: str) -> tuple[int, int]:
    start, _, end = span.partition("-")
    return int(start), int(end or start)


def span_range(span: str) -> range:
    start, end = span_bounds(span)
    return range(start, end + 1)


class LoopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    until: str | None = Field(
        default=None, description="S1 expression checked after each iteration."
    )
    count: int | None = Field(
        default=None, ge=1, description="Maximum iteration count; exact count when until is absent."
    )
    max_iterations: int = Field(
        default=50, ge=1, le=10_000, description="Absolute iteration safety cap."
    )
    cost_limit_usd: float | None = Field(
        default=None, gt=0, description="Maximum accumulated span cost in USD."
    )
    index: str = Field(description="Unique lowercase loop index available inside the span.")


class ConcertTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: str = Field(description="Child score path, relative to the parent score directory.")
    inherit_workspace: bool = Field(default=True, description="Use the parent workspace.")
    fresh: bool = Field(default=False, description="Start the child without prior state.")


class RunTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(description="Shell command executed in a bounded process group.")
    working_directory: str | None = Field(
        default=None, description="Directory; defaults to job workspace."
    )
    timeout_seconds: float = Field(
        default=300.0, gt=0, le=3600, description="Maximum command duration."
    )


class TriggerAction(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    goto: int | None = Field(default=None, ge=1, description="Jump to a sheet or fan-out stage.")
    pause: bool | None = Field(default=None, description="Pause the job durably.")
    escalate: str | bool | None = Field(default=None, description="Ask for a composer decision.")
    concert: str | ConcertTrigger | None = Field(
        default=None, description="Submit a child score without waiting for it."
    )
    run: str | RunTrigger | None = Field(
        default=None, description="Run a bounded shell command off the baton loop."
    )
    skip: SkipTarget | None = Field(
        default=None, description="Mark this sheet span deliberately skipped."
    )
    continue_: bool | None = Field(default=None, alias="continue", description="Explicit no-op.")

    @model_validator(mode="after")
    def exactly_one(self) -> TriggerAction:
        chosen = [
            name
            for name in ("goto", "pause", "escalate", "concert", "run", "skip", "continue_")
            if getattr(self, name) is not None
        ]
        if len(chosen) != 1:
            raise ValueError(f"each trigger action sets exactly one field; got {chosen}")
        if self.pause is False or self.continue_ is False or self.escalate is False:
            raise ValueError("pause/continue/escalate: false does nothing")
        return self


class SheetTriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_success: list[TriggerAction] | None = Field(
        default=None, description="Actions after a successful attempt."
    )
    on_fail: list[TriggerAction] | None = Field(
        default=None, description="Failure handler replacing retry, fallback, and healing."
    )
