"""Flow-control state persisted in the job checkpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from marianne.core.config.flow import SheetSpan, TriggerAction


class LoopRunState(BaseModel):
    span: SheetSpan = Field(description="Concrete inclusive sheet span.")
    index_name: str = Field(description="Loop index name.")
    iteration: int = Field(default=1, ge=1, description="Current one-based iteration.")
    phase: Literal["running", "awaiting_facts", "completed"] = Field(
        default="running", description="Loop decision phase."
    )
    completed_reason: str | None = Field(default=None, description="Terminal loop reason.")
    cost_baseline_usd: float = Field(default=0.0, ge=0, description="Cost at loop start.")
    iteration_started_at: float | None = Field(default=None, description="Current iteration start.")
    facts_request_id: int | None = Field(default=None, description="Outstanding facts request.")


class TriggerChainState(BaseModel):
    chain_id: int = Field(ge=1, description="Monotonic chain identifier.")
    sheet_num: int = Field(ge=1, description="Sheet that fired this chain.")
    outcome: Literal["success", "fail"] = Field(description="Attempt outcome.")
    fired_epoch: int = Field(ge=0, description="Epoch of the triggering attempt.")
    actions: list[TriggerAction] = Field(description="Ordered trigger actions.")
    cursor: int = Field(default=0, ge=0, description="Next action index.")
    phase: Literal["ready", "awaiting_run", "awaiting_concert"] = Field(
        default="ready", description="Chain execution phase."
    )
    attempt: int = Field(default=1, ge=1, description="Current action invocation count.")
    results: list[dict[str, Any]] = Field(default_factory=list, description="Action outcomes.")


class FlowState(BaseModel):
    loops: dict[SheetSpan, LoopRunState] = Field(default_factory=dict, description="Loop state.")
    chains: list[TriggerChainState] = Field(
        default_factory=list, description="Open trigger chains."
    )
    next_chain_id: int = Field(default=1, ge=1, description="Next chain identifier.")
    next_facts_id: int = Field(default=1, ge=1, description="Next file-facts request identifier.")
    queued_skips: dict[int, str] = Field(default_factory=dict, description="Pending skip reasons.")
    goto_bypass: list[int] = Field(default_factory=list, description="Dependency bypass targets.")
    pause_reason: str | None = Field(default=None, description="Durable flow pause reason.")
    plan_digest: str | None = Field(default=None, description="Compiled flow plan digest.")
