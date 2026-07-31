"""Semantic analyzer — LLM-based analysis of sheet completions.

Subscribes to EventBus sheet events, sends completion context to an LLM,
and stores resulting insights as patterns in the global learning store.

The analyzer operates independently of job execution: analysis failures
never affect running jobs. Concurrency is limited by a semaphore to
control API costs.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from marianne.core.checkpoint import CheckpointState
from marianne.core.constants import SHEET_NUM_KEY
from marianne.core.logging import get_logger
from marianne.daemon.config import SemanticLearningConfig
from marianne.daemon.event_bus import EventBus
from marianne.daemon.learning_hub import LearningHub
from marianne.daemon.types import ObserverEvent
from marianne.execution.base import Backend
from marianne.learning.patterns import PatternType

_logger = get_logger("daemon.semantic_analyzer")

# Events that indicate a sheet has finished execution
_COMPLETION_EVENTS = frozenset({"sheet.completed", "sheet.failed"})

# Anomaly events from the profiler subsystem
_ANOMALY_EVENT = "monitor.anomaly"

# Valid insight categories accepted from LLM responses
_VALID_CATEGORIES = frozenset({
    "root_cause", "knowledge", "prompt_improvement",
    "anti_pattern", "effectiveness",
})


class SemanticAnalyzer:
    """Analyzes sheet completions via LLM to produce learning insights.

    Lifecycle:
        backend = create_backend_for_instrument(registry, config.instrument)
        analyzer = SemanticAnalyzer(config, backend, learning_hub, live_states)
        await analyzer.start(event_bus)
        # ... events flow, analyses run ...
        await analyzer.stop()
    """

    def __init__(
        self,
        config: SemanticLearningConfig,
        backend: Backend,
        learning_hub: LearningHub,
        live_states: dict[str, CheckpointState],
    ) -> None:
        self._config = config
        self._backend = backend
        self._learning_hub = learning_hub
        self._live_states = live_states
        self._semaphore = asyncio.Semaphore(config.max_concurrent_analyses)
        self._sub_id: str | None = None
        self._anomaly_sub_id: str | None = None
        self._pending_tasks: set[asyncio.Task[None]] = set()

    async def start(self, event_bus: EventBus) -> None:
        """Subscribe to sheet events and anomaly events on the event bus."""
        if not self._config.enabled:
            _logger.info("semantic_analyzer.disabled")
            return

        self._sub_id = event_bus.subscribe(
            callback=self._on_sheet_event,
            event_filter=lambda e: e.get("event", "") in _COMPLETION_EVENTS,
        )

        # Subscribe to monitor.anomaly events — store directly as
        # RESOURCE_ANOMALY patterns without LLM calls.
        self._anomaly_sub_id = event_bus.subscribe(
            callback=self._on_anomaly_event,
            event_filter=lambda e: e.get("event", "") == _ANOMALY_EVENT,
        )

        _logger.info(
            "semantic_analyzer.started",
            backend=self._backend.name,
            analyze_on=self._config.analyze_on,
            max_concurrent=self._config.max_concurrent_analyses,
        )

    async def stop(self, event_bus: EventBus | None = None) -> None:
        """Unsubscribe and wait for pending analyses to drain."""
        if self._sub_id is not None and event_bus is not None:
            event_bus.unsubscribe(self._sub_id)
            self._sub_id = None

        if self._anomaly_sub_id is not None and event_bus is not None:
            event_bus.unsubscribe(self._anomaly_sub_id)
            self._anomaly_sub_id = None

        # Wait for pending analysis tasks to complete
        if self._pending_tasks:
            _logger.info(
                "semantic_analyzer.draining",
                pending=len(self._pending_tasks),
            )
            done, _ = await asyncio.wait(
                self._pending_tasks,
                timeout=self._config.timeout_seconds,
            )
            # Cancel any tasks that didn't finish in time
            for task in self._pending_tasks - done:
                task.cancel()

        self._pending_tasks.clear()

        # Close the backend
        try:
            await self._backend.close()
        except (OSError, RuntimeError):
            pass

        _logger.info("semantic_analyzer.stopped")

    async def _on_sheet_event(self, event: ObserverEvent) -> None:
        """Handle a sheet completion/failure event.

        Immediately copies needed data from live state before spawning
        the analysis task to avoid races with _on_task_done() clearing
        live state.
        """
        event_type = event.get("event", "")
        job_id = event.get("job_id", "")
        sheet_num = event["sheet_num"]  # TypedDict access has correct type

        # Determine if this event outcome matches our analyze_on filter
        if event_type == "sheet.completed" and "success" not in self._config.analyze_on:
            return
        if event_type == "sheet.failed" and "failure" not in self._config.analyze_on:
            return

        # Copy sheet data from live state before it gets cleared
        sheet_data = self._extract_sheet_data(job_id, sheet_num, event)
        if sheet_data is None:
            _logger.debug(
                "semantic_analyzer.no_live_state",
                job_id=job_id,
                sheet_num=sheet_num,
            )
            return

        # Spawn analysis task with semaphore limiting
        task = asyncio.create_task(
            self._analyze_with_semaphore(job_id, sheet_num, event_type, sheet_data),
            name=f"semantic-analysis-{job_id}-s{sheet_num}",
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _extract_sheet_data(
        self,
        job_id: str,
        sheet_num: int,
        event: ObserverEvent,
    ) -> dict[str, Any] | None:
        """Extract sheet data from live state for analysis.

        Returns a snapshot of the relevant data, or None if no live
        state is available for this job.
        """
        live_state = self._live_states.get(job_id)
        if live_state is None:
            return None

        sheets = live_state.sheets or {}
        sheet_state = sheets.get(sheet_num)

        data: dict[str, Any] = {
            "job_id": job_id,
            SHEET_NUM_KEY: sheet_num,
            "event_type": event.get("event", ""),
            "event_data": event.get("data"),
        }

        if sheet_state is not None:
            data["stdout_tail"] = sheet_state.stdout_tail or ""
            data["stderr_tail"] = sheet_state.stderr_tail or ""
            data["validation_details"] = sheet_state.validation_details or []
            data["exit_code"] = sheet_state.exit_code
            data["retry_count"] = sheet_state.attempt_count
            data["duration_seconds"] = sheet_state.execution_duration_seconds
            data["status"] = sheet_state.status.value if sheet_state.status else ""
        else:
            # Minimal data from event itself
            event_data = event.get("data") or {}
            data["stdout_tail"] = event_data.get("stdout_tail", "")
            data["stderr_tail"] = ""
            data["validation_details"] = []
            data["exit_code"] = event_data.get("exit_code")
            data["retry_count"] = 0
            data["duration_seconds"] = None
            data["status"] = event.get("event", "").replace("sheet.", "")

        return data

    async def _analyze_with_semaphore(
        self,
        job_id: str,
        sheet_num: int,
        event_type: str,
        sheet_data: dict[str, Any],
    ) -> None:
        """Run analysis under the concurrency semaphore."""
        async with self._semaphore:
            await self._analyze_sheet(job_id, sheet_num, event_type, sheet_data)

    async def _analyze_sheet(
        self,
        job_id: str,
        sheet_num: int,
        event_type: str,
        sheet_data: dict[str, Any],
    ) -> None:
        """Send sheet data to LLM for analysis and store results."""
        prompt = self._build_analysis_prompt(sheet_data)

        try:
            response_text = await self._call_llm(prompt)
        except Exception:
            _logger.warning(
                "semantic_analyzer.llm_failed",
                job_id=job_id,
                sheet_num=sheet_num,
                exc_info=True,
            )
            return

        insights = self._parse_analysis_response(response_text)
        if not insights:
            _logger.debug(
                "semantic_analyzer.no_insights",
                job_id=job_id,
                sheet_num=sheet_num,
            )
            return

        # Store each insight as a pattern
        self._store_insights(job_id, sheet_num, event_type, insights)

        _logger.info(
            "semantic_analyzer.analysis_complete",
            job_id=job_id,
            sheet_num=sheet_num,
            insight_count=len(insights),
        )

    def _build_analysis_prompt(self, sheet_data: dict[str, Any]) -> str:
        """Build the LLM analysis prompt from sheet data."""
        outcome = "SUCCESS" if sheet_data.get("event_type") == "sheet.completed" else "FAILURE"
        stdout_tail = (sheet_data.get("stdout_tail") or "")[:3000]
        stderr_tail = (sheet_data.get("stderr_tail") or "")[:1000]
        validations = sheet_data.get("validation_details") or []
        retry_count = sheet_data.get("retry_count", 0)
        exit_code = sheet_data.get("exit_code")
        duration = sheet_data.get("duration_seconds")

        validation_summary = ""
        if validations:
            val_lines = []
            for v in validations:
                status = "PASS" if v.get("passed") else "FAIL"
                desc = v.get("description", "unknown")
                val_lines.append(f"  - [{status}] {desc}")
            validation_summary = "\n".join(val_lines)

        return f"""Analyze this sheet execution and provide learning insights.

## Execution Context
- Outcome: {outcome}
- Exit code: {exit_code}
- Duration: {duration}s
- Retry count: {retry_count}
- Job: {sheet_data.get('job_id', 'unknown')}
- Sheet: {sheet_data.get('sheet_num', '?')}

## Validation Results
{validation_summary or '(no validation details)'}

## Output (last 3000 chars)
{stdout_tail or '(no output captured)'}

## Errors (last 1000 chars)
{stderr_tail or '(no errors)'}

## Analysis Questions
Answer each question concisely:
1. Why did this sheet {outcome.lower()}? What was the root cause?
2. What knowledge should be carried forward to future executions of similar sheets?
3. Are there prompt improvements that could increase the success rate?
4. Are there any anti-patterns visible in the output that should be avoided?
5. How effective was the prompt at guiding the execution?

## Response Format
Return a JSON array of insight objects with these fields:
- "insight": A concise description (1-2 sentences)
- "category": One of "root_cause", "knowledge",
  "prompt_improvement", "anti_pattern", "effectiveness"
- "confidence": A float between 0.0 and 1.0

Example:
[
  {{"insight": "Workspace not created before file ops",
    "category": "root_cause", "confidence": 0.9}},
  {{"insight": "Verify workspace exists before writes",
    "category": "knowledge", "confidence": 0.85}}
]
"""

    async def _call_llm(self, prompt: str) -> str:
        """Call the configured backend with the analysis prompt."""
        start = time.monotonic()
        result = await self._backend.execute(
            prompt, timeout_seconds=self._config.timeout_seconds,
        )
        duration = time.monotonic() - start

        if not result.success:
            raise RuntimeError(
                f"Backend execution failed (exit_code={result.exit_code}): "
                f"{result.error_message or result.stderr[:200]}"
            )

        _logger.debug(
            "semantic_analyzer.llm_response",
            duration_seconds=round(duration, 2),
            response_length=len(result.stdout),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

        return result.stdout

    def _parse_analysis_response(self, response_text: str) -> list[dict[str, Any]]:
        """Parse the LLM response into a list of insights.

        Handles various response formats: bare JSON array, markdown-wrapped
        JSON, or response with surrounding text.
        """
        text = response_text.strip()

        # Try to extract JSON from markdown code blocks
        if "```" in text:
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        # Find the JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []

        json_text = text[start:end + 1]

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            _logger.debug(
                "semantic_analyzer.json_parse_failed",
                text_preview=json_text[:200],
            )
            return []

        if not isinstance(parsed, list):
            return []

        # Validate each insight
        insights = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            insight_text = item.get("insight")
            category = item.get("category")
            confidence = item.get("confidence", 0.5)
            if not insight_text or not isinstance(insight_text, str):
                continue
            if category not in _VALID_CATEGORIES:
                continue
            if not isinstance(confidence, (int, float)):
                confidence = 0.5
            confidence = max(0.0, min(1.0, float(confidence)))
            insights.append({
                "insight": insight_text,
                "category": category,
                "confidence": confidence,
            })

        return insights

    def _on_anomaly_event(self, event: ObserverEvent) -> None:
        """Handle a monitor.anomaly event — store directly as RESOURCE_ANOMALY.

        No LLM call is made: anomalies are already classified by the
        AnomalyDetector.  We simply record them as patterns in the
        learning store with appropriate context tags.
        """
        if not self._learning_hub.is_running:
            return

        job_id = event.get("job_id", "")
        sheet_num = event.get(SHEET_NUM_KEY, 0)
        data = event.get("data") or {}

        anomaly_type = data.get("anomaly_type", "unknown")
        severity = data.get("severity", "medium")
        description = data.get("description", "Resource anomaly detected")
        pid = data.get("pid")

        pattern_name = f"[{anomaly_type}] {description[:100]}"
        context_tags = [
            f"anomaly_type:{anomaly_type}",
            f"severity:{severity}",
        ]
        if job_id:
            context_tags.append(f"job:{job_id}")
        if sheet_num:
            context_tags.append(f"sheet:{sheet_num}")
        if pid:
            context_tags.append(f"pid:{pid}")

        try:
            store = self._learning_hub.store
            store.record_pattern(
                pattern_type=PatternType.RESOURCE_ANOMALY.value,
                pattern_name=pattern_name,
                description=description,
                context_tags=context_tags,
            )
            _logger.debug(
                "semantic_analyzer.anomaly_stored",
                anomaly_type=anomaly_type,
                severity=severity,
                job_id=job_id,
            )
        except Exception:
            _logger.warning(
                "semantic_analyzer.anomaly_store_failed",
                exc_info=True,
            )

    def _store_insights(
        self,
        job_id: str,
        sheet_num: int,
        event_type: str,
        insights: list[dict[str, Any]],
    ) -> None:
        """Store parsed insights as patterns in the learning store."""
        if not self._learning_hub.is_running:
            _logger.warning("semantic_analyzer.store_not_running")
            return

        store = self._learning_hub.store
        outcome_tag = "success" if event_type == "sheet.completed" else "failure"

        for insight in insights:
            pattern_name = f"[{insight['category']}] {insight['insight'][:100]}"
            store.record_pattern(
                pattern_type=PatternType.SEMANTIC_INSIGHT.value,
                pattern_name=pattern_name,
                description=insight["insight"],
                context_tags=[
                    f"category:{insight['category']}",
                    f"outcome:{outcome_tag}",
                    f"job:{job_id}",
                    f"sheet:{sheet_num}",
                ],
                suggested_action=insight["insight"] if insight["category"] in (
                    "prompt_improvement", "anti_pattern", "knowledge"
                ) else None,
            )


__all__ = ["SemanticAnalyzer"]
