"""Judgment-client configuration (#203).

Score-level, additive config for the automated FERMATA decider. The
judgment client (``daemon/judgment.py``) evaluates a sheet paused in
FERMATA and may produce the resolution decision the composer would
otherwise make via ``mzt resolve`` — alongside the composer, never
replacing them.

Safety posture (from the 4-model design lab, 2026-06-11, unanimous on
the load-bearing points):

- OFF by default; only meaningful when the run has escalation enabled
  (no FERMATA → nothing to judge).
- ``accept`` is NEVER allowed by default — it records failed work as
  SUCCESS, and the failure evidence the judge reads is repo/agent-
  controlled text that can lobby for it. The composer can still accept
  via ``mzt resolve``.
- ``skip`` is also excluded by default: it changes dependency behavior
  (dependents proceed without this sheet's work) and the judge lacks
  task-semantics to know a sheet is optional.
- A durable per-sheet judgment cap (default 1) terminates judge-retry
  loops; when the cap is reached the judge defers to the composer.
- Low confidence → defer (the sheet stays in plain FERMATA).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JudgmentDecision = Literal["retry", "skip", "accept", "fail"]


class JudgmentConfig(BaseModel):
    """Automated FERMATA decision-making for this score (#203)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable the AI judgment client for this score's FERMATA "
        "sheets. Requires escalation to be enabled on the run "
        "(--escalation/--self-healing); without FERMATA there is nothing "
        "to judge. Default off — composer resolution is the default.",
    )
    instrument: str = Field(
        default="claude-code",
        description="Instrument (CLI) invoked as the judge. Must be a "
        "registered instrument profile. Subscription/free models only.",
    )
    allowed_decisions: list[JudgmentDecision] = Field(
        default_factory=lambda: list[JudgmentDecision](("retry", "fail")),
        description="Decisions the judge may issue autonomously. 'accept' "
        "(records failed work as SUCCESS) and 'skip' (dependents proceed "
        "without this sheet) are excluded by default; enabling them is an "
        "explicit, informed choice. Anything else the judge wants becomes "
        "a defer (sheet stays in FERMATA for the composer).",
    )
    min_confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Judge decisions below this confidence defer to the "
        "composer instead of resolving.",
    )
    max_judgments_per_sheet: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Durable per-sheet cap on judge-issued resolutions "
        "(persisted on SheetState, survives restart). Terminates "
        "judge-retry loops: at the cap, the judge defers to the composer.",
    )
    timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=600,
        description="Per-judgment LLM invocation timeout. On timeout the "
        "sheet stays in FERMATA for the composer (fail-open).",
    )


__all__ = ["JudgmentConfig", "JudgmentDecision"]
