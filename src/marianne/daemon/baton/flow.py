"""Deterministic loop and trigger transitions over checkpoint-owned state."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Literal

from marianne.core.checkpoint import TERMINAL_SHEET_STATUSES, SheetState, SheetStatus
from marianne.core.config.flow import (
    LoopConfig,
    SheetTriggerConfig,
    TriggerAction,
    span_bounds,
    span_range,
)
from marianne.core.expressions import ExpressionError, FileFacts, evaluate, parse_expression
from marianne.core.flow_state import FlowState, LoopRunState, TriggerChainState
from marianne.daemon.baton.events import (
    GotoRequested,
    LoopCompleted,
    LoopIterating,
    SheetTriggerFired,
)
from marianne.daemon.baton.flow_context import BatonExpressionContext


@dataclass
class FlowPlan:
    loops: dict[str, LoopConfig]
    triggers: dict[str, SheetTriggerConfig]
    variables: dict[str, Any]

    def digest(self) -> str:
        payload = repr(
            (
                [(key, value.model_dump(mode="json")) for key, value in self.loops.items()],
                [(key, value.model_dump(mode="json")) for key, value in self.triggers.items()],
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class FlowActionRequest:
    chain_id: int
    cursor: int
    sheet_num: int
    fired_epoch: int
    attempt: int
    action: TriggerAction


class FlowEngine:
    """Changes only shared sheet/flow objects; the baton persists them together."""

    def __init__(
        self,
        job_id: str,
        plan: FlowPlan,
        state: FlowState,
        event_generation: int | None = None,
    ) -> None:
        self.job_id = job_id
        self.event_generation = event_generation
        self.plan = plan
        self.state = state
        self._effects: list[FlowActionRequest] = []
        self._events: list[SheetTriggerFired | GotoRequested | LoopIterating | LoopCompleted] = []
        digest = plan.digest()
        if state.plan_digest not in (None, digest):
            state.chains.clear()
            state.loops = {
                span: run
                for span, run in state.loops.items()
                if span in plan.loops and run.index_name == plan.loops[span].index
            }
        state.plan_digest = digest
        for span, config in plan.loops.items():
            state.loops.setdefault(
                span,
                LoopRunState(span=span, index_name=config.index, iteration_started_at=time.time()),
            )
        for run in state.loops.values():
            if run.phase == "awaiting_facts":
                run.phase = "running"
                run.facts_request_id = None
        for chain in state.chains:
            if chain.phase != "ready":
                chain.phase = "ready"
                chain.attempt += 1

    def actions_for(self, sheet_num: int, outcome: str) -> list[TriggerAction]:
        matching = [
            (span_bounds(span)[1] - span_bounds(span)[0], order, trigger)
            for order, (span, trigger) in enumerate(self.plan.triggers.items())
            if sheet_num in span_range(span)
        ]
        matching.sort(key=lambda entry: (entry[0], entry[1]))
        actions: list[TriggerAction] = []
        for _, _, trigger in matching:
            actions.extend((trigger.on_success if outcome == "success" else trigger.on_fail) or [])
        return actions

    def has_on_fail(self, sheet_num: int) -> bool:
        return any(
            trigger.on_fail is not None
            for span, trigger in self.plan.triggers.items()
            if sheet_num in span_range(span)
        )

    def busy(self) -> bool:
        return bool(self.state.chains) or any(
            loop.phase == "awaiting_facts" for loop in self.state.loops.values()
        )

    def boundary_pending(self, sheets: dict[int, SheetState]) -> bool:
        return any(
            loop.phase == "running"
            and all(sheets[num].status in TERMINAL_SHEET_STATUSES for num in span_range(span))
            for span, loop in self.state.loops.items()
        )

    def on_terminal(
        self,
        sheets: dict[int, SheetState],
        sheet_num: int,
        outcome: Literal["success", "fail"] | None = None,
    ) -> bool:
        if sheet_num in self.state.goto_bypass:
            self.state.goto_bypass.remove(sheet_num)
        if sheet_num in self.state.queued_skips:
            reason = self.state.queued_skips.pop(sheet_num)
            if sheets[sheet_num].status != SheetStatus.COMPLETED:
                self._skip(sheets[sheet_num], reason)
        if outcome is not None:
            actions = self.actions_for(sheet_num, outcome)
            if actions:
                self._events.append(
                    SheetTriggerFired(
                        self.job_id,
                        sheet_num,
                        outcome,
                        tuple(actions),
                        self.state.next_chain_id,
                        sheets[sheet_num].dispatch_epoch,
                        event_generation=self.event_generation,
                    )
                )
                self.state.chains.append(
                    TriggerChainState(
                        chain_id=self.state.next_chain_id,
                        sheet_num=sheet_num,
                        outcome=outcome,
                        fired_epoch=sheets[sheet_num].dispatch_epoch,
                        actions=actions,
                    )
                )
                self.state.next_chain_id += 1
        return self.settle(sheets)

    def settle(self, sheets: dict[int, SheetState]) -> bool:
        changed = False
        while True:
            for num, reason in list(self.state.queued_skips.items()):
                if sheets[num].status in TERMINAL_SHEET_STATUSES:
                    self.state.queued_skips.pop(num)
                    if sheets[num].status != SheetStatus.COMPLETED:
                        self._skip(sheets[num], reason)
                    changed = True
            for num in list(self.state.goto_bypass):
                if sheets[num].status in TERMINAL_SHEET_STATUSES:
                    self.state.goto_bypass.remove(num)
                    changed = True
            if self.state.chains:
                chain = self.state.chains[0]
                if chain.phase != "ready":
                    return changed
                if chain.cursor == len(chain.actions):
                    self.state.chains.pop(0)
                    changed = True
                    continue
                action = chain.actions[chain.cursor]
                if action.run is not None or action.concert is not None:
                    chain.phase = "awaiting_run" if action.run is not None else "awaiting_concert"
                    self._effects.append(
                        FlowActionRequest(
                            chain_id=chain.chain_id,
                            cursor=chain.cursor,
                            sheet_num=chain.sheet_num,
                            fired_epoch=chain.fired_epoch,
                            attempt=chain.attempt,
                            action=action,
                        )
                    )
                    return True
                chain.cursor += 1
                self._apply_action(sheets, chain.sheet_num, action)
                changed = True
                continue
            ready = [
                (span_bounds(span)[1] - span_bounds(span)[0], span, run)
                for span, run in self.state.loops.items()
                if run.phase == "running"
                and all(sheets[num].status in TERMINAL_SHEET_STATUSES for num in span_range(span))
            ]
            if not ready:
                return changed
            _, span, run = min(ready, key=lambda entry: entry[0])
            self._decide_loop(sheets, span, run)
            changed = True

    def drain_effects(self) -> list[FlowActionRequest]:
        effects, self._effects = self._effects, []
        return effects

    def drain_events(
        self,
    ) -> list[SheetTriggerFired | GotoRequested | LoopIterating | LoopCompleted]:
        events, self._events = self._events, []
        return events

    def action_finished(
        self,
        sheets: dict[int, SheetState],
        chain_id: int,
        cursor: int,
        result: dict[str, Any],
    ) -> bool:
        if not self.state.chains:
            return False
        chain = self.state.chains[0]
        if (
            chain.chain_id != chain_id
            or chain.cursor != cursor
            or chain.phase not in {"awaiting_run", "awaiting_concert"}
        ):
            return False
        chain.results.append(result)
        chain.cursor += 1
        chain.phase = "ready"
        chain.attempt = 1
        self.settle(sheets)
        return True

    def _decide_loop(self, sheets: dict[int, SheetState], span: str, run: LoopRunState) -> None:
        config = self.plan.loops[span]
        members = [sheets[num] for num in span_range(span)]
        failed = any(
            sheet.status in {SheetStatus.FAILED, SheetStatus.CANCELLED}
            or (sheet.status == SheetStatus.SKIPPED and sheet.error_code is not None)
            for sheet in members
        )
        reason: str | None = None
        if failed:
            reason = "range_failed"
        elif all(sheet.status == SheetStatus.SKIPPED for sheet in members):
            reason = "skipped"
        elif config.count is not None and run.iteration >= config.count:
            reason = "count_reached"
        elif run.iteration >= config.max_iterations:
            reason = "max_iterations"
        elif (
            config.cost_limit_usd is not None
            and sum(sheet.total_cost_usd for sheet in members) - run.cost_baseline_usd
            > config.cost_limit_usd
        ):
            reason = "cost_limit_exceeded"
        elif config.until is not None:
            expr = parse_expression(config.until)
            if expr.references().files:
                # The adapter must furnish file facts off-loop before decision.
                run.phase = "awaiting_facts"
                run.facts_request_id = self.state.next_facts_id
                self.state.next_facts_id += 1
                return
            try:
                context = BatonExpressionContext(
                    sheets, self.state, self.plan.variables, span_bounds(span)[1]
                )
                if evaluate(expr, context):
                    reason = "condition_met"
            except ExpressionError as exc:
                reason = "condition_error"
                members[-1].status = SheetStatus.FAILED
                members[-1].error_message = f"Flow condition {config.until!r}: {exc}"
                members[-1].error_code = "E999"
        if reason is not None:
            run.phase = "completed"
            run.completed_reason = reason
            self._emit_loop_completed(span, run, reason)
            return
        self._iterate_loop(sheets, span, run)

    def _iterate_loop(self, sheets: dict[int, SheetState], span: str, run: LoopRunState) -> None:
        members = [sheets[num] for num in span_range(span)]
        cost = sum(sheet.total_cost_usd for sheet in members) - run.cost_baseline_usd
        uncertain = any(sheet.cost_uncertain for sheet in members)
        indices = {loop.index_name: loop.iteration for loop in self.state.loops.values()}
        for sheet in members:
            sheet.reset_for_flow("loop iteration", indices)
        outer_start, outer_end = span_bounds(span)
        for inner_span, inner in self.state.loops.items():
            inner_start, inner_end = span_bounds(inner_span)
            if inner_span != span and outer_start <= inner_start and inner_end <= outer_end:
                inner.phase = "running"
                inner.iteration = 1
                inner.completed_reason = None
                inner.cost_baseline_usd = sum(
                    sheets[num].total_cost_usd for num in span_range(inner_span)
                )
        run.iteration += 1
        run.iteration_started_at = time.time()
        self._events.append(
            LoopIterating(
                self.job_id,
                span_range(span),
                run.index_name,
                run.iteration,
                cost,
                uncertain,
                event_generation=self.event_generation,
            )
        )

    def _emit_loop_completed(self, span: str, run: LoopRunState, reason: str) -> None:
        self._events.append(
            LoopCompleted(
                self.job_id,
                span_range(span),
                run.index_name,
                run.iteration,
                reason,
                event_generation=self.event_generation,
            )
        )

    def facts_ready(
        self,
        sheets: dict[int, SheetState],
        span: str,
        request_id: int,
        files: dict[str, FileFacts],
        error: str | None,
    ) -> bool:
        run = self.state.loops.get(span)
        if run is None or run.phase != "awaiting_facts" or run.facts_request_id != request_id:
            return False
        run.facts_request_id = None
        config = self.plan.loops[span]
        if error is None and config.until is not None:
            try:
                context = BatonExpressionContext(
                    sheets, self.state, self.plan.variables, span_bounds(span)[1], files
                )
                met = evaluate(parse_expression(config.until), context)
            except ExpressionError as exc:
                error = str(exc)
            else:
                if met:
                    run.phase = "completed"
                    run.completed_reason = "condition_met"
                    self._emit_loop_completed(span, run, "condition_met")
                    return True
                run.phase = "running"
                self._iterate_loop(sheets, span, run)
                return True
        run.phase = "completed"
        run.completed_reason = "condition_error"
        self._emit_loop_completed(span, run, "condition_error")
        last = sheets[span_bounds(span)[1]]
        last.status = SheetStatus.FAILED
        last.error_message = f"Flow condition {config.until!r}: {error}"
        last.error_code = "E999"
        return True

    def _apply_action(
        self, sheets: dict[int, SheetState], source: int, action: TriggerAction
    ) -> None:
        if action.goto is not None:
            target = action.goto
            reset: list[int] = []
            skipped: list[int] = []
            if target <= source:
                for num in range(target, source + 1):
                    sheets[num].reset_for_flow("goto", self.indices_for(num))
                    reset.append(num)
            else:
                for num in range(source + 1, target):
                    sheet = sheets[num]
                    if sheet.status in TERMINAL_SHEET_STATUSES:
                        continue
                    if sheet.status in {SheetStatus.DISPATCHED, SheetStatus.IN_PROGRESS}:
                        self.state.queued_skips[num] = f"goto {source}->{target}"
                    else:
                        self._skip(sheet, f"goto {source}->{target}")
                        skipped.append(num)
                if sheets[target].status in TERMINAL_SHEET_STATUSES:
                    sheets[target].reset_for_flow("goto", self.indices_for(target))
                    reset.append(target)
            if target not in self.state.goto_bypass:
                self.state.goto_bypass.append(target)
            self._events.append(
                GotoRequested(
                    self.job_id,
                    source,
                    target,
                    "same" if target == source else "forward" if target > source else "backward",
                    tuple(reset),
                    tuple(skipped),
                    event_generation=self.event_generation,
                )
            )
        elif action.skip is not None:
            for num in span_range(action.skip):
                sheet = sheets[num]
                if sheet.status in {SheetStatus.DISPATCHED, SheetStatus.IN_PROGRESS}:
                    self.state.queued_skips[num] = f"trigger skip from {source}"
                elif sheet.status not in {SheetStatus.COMPLETED, SheetStatus.CANCELLED}:
                    self._skip(sheet, f"trigger skip from {source}")
        elif action.pause:
            self.state.pause_reason = f"trigger on sheet {source}"
        elif action.escalate:
            sheet = sheets[source]
            sheet.status = SheetStatus.FERMATA
            sheet.fermata_reason = (
                action.escalate
                if isinstance(action.escalate, str)
                else f"Trigger escalation from sheet {source}"
            )
            self.state.escalation_pause_owners.add(source)
            self.state.pause_reason = sheet.fermata_reason
        # continue is an intentional no-op.

    def indices_for(self, sheet_num: int) -> dict[str, int]:
        return {
            run.index_name: run.iteration
            for span, run in self.state.loops.items()
            if sheet_num in span_range(span) and run.phase != "completed"
        }

    @staticmethod
    def _skip(sheet: SheetState, reason: str) -> None:
        sheet.status = SheetStatus.SKIPPED
        sheet.error_code = None
        sheet.error_message = reason
        sheet.clear_dispatch_block()
