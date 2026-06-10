"""#133: observer/resource data wired into retry-prompt failure evidence.

Verified scope (2026-06-11, against the baton — most of the filed issue was
runner-era stale): cascade-blind fail_fast is gone (dependency-scoped skip),
the four recovery layers are wired (#201/#361), and the rate-limit message
from the issue IS classified. What genuinely remained:

1. Observer events (file/process timeline) were recorded per-job (JSONL +
   in-memory ring) but never reached healing — the retry prompt couldn't
   say "you created 3 of the 4 required files" or "your subprocess exited
   non-zero".
2. No resource state at failure time reached healing — "memory exhausted"
   and "logic error" looked identical.

Mechanism (extends #201's evidence pattern, additive only):

- ``BatonAdapter(diagnostic_snapshot_fn=...)`` — manager-injected sync
  provider: ``job_id -> {"observer_events": [...], "resources": {...}}``
  (ObserverRecorder ring + ResourceMonitor cached memory; no I/O).
- ``_build_healing_context`` attaches the snapshot to an EXISTING failure
  context (never creates one from runtime data alone), filtering observer
  events to the failed attempt's window (dispatch-time onwards). Fail-open:
  a raising provider degrades to the #201 behavior.
- ``format_failure_evidence`` renders a bounded, untrusted-framed "runtime
  context" section AFTER the primary signals (validation-first ordering
  preserved; runtime data alone never produces a block).

The SelfHealingCoordinator path is deliberately untouched: #201's lab
demoted it to a non-gating environment-fix pre-pass, and file timelines
don't change mkdir-style remedies. The evidence prompt is where diagnosis
changes agent behavior.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from marianne.core.checkpoint import SheetState
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.prompts.failure_evidence import format_failure_evidence


def _make_sheet(workspace: Path, num: int = 1) -> Sheet:
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=workspace,
        instrument_name="claude-code",
        prompt_template="test",
        timeout_seconds=600.0,
    )


def _failed_state() -> SheetState:
    state = SheetState(sheet_num=1)
    state.error_code = "E601"
    state.error_message = "validation failed"
    return state


def _event(event: str, ts: float, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "job_id": "j1",
        "sheet_num": 1,
        "event": event,
        "data": data or {},
        "timestamp": ts,
    }


class TestHealingContextDiagnostics:
    def test_snapshot_attached_to_existing_failure_context(
        self, tmp_path: Path
    ) -> None:
        now = time.time()

        def provider(job_id: str) -> dict[str, Any]:
            return {
                "observer_events": [_event("observer.file_created", now)],
                "resources": {"memory_mb": 512.0},
            }

        adapter = BatonAdapter(diagnostic_snapshot_fn=provider)
        adapter.register_job("j1", [_make_sheet(tmp_path)], dependencies={})
        adapter._stale_dispatch_time[("j1", 1)] = now - 60.0

        ctx = adapter._build_healing_context(_failed_state(), "j1", 1)

        assert ctx is not None
        assert ctx["observer_events"] == [_event("observer.file_created", now)]
        assert ctx["resources"] == {"memory_mb": 512.0}

    def test_no_provider_preserves_201_behavior(self, tmp_path: Path) -> None:
        adapter = BatonAdapter()
        adapter.register_job("j1", [_make_sheet(tmp_path)], dependencies={})

        ctx = adapter._build_healing_context(_failed_state(), "j1", 1)

        assert ctx is not None
        assert "observer_events" not in ctx
        assert "resources" not in ctx

    def test_runtime_data_alone_never_creates_context(
        self, tmp_path: Path
    ) -> None:
        """A clean first attempt stays None even with rich runtime data."""

        def provider(job_id: str) -> dict[str, Any]:
            return {
                "observer_events": [_event("observer.file_created", time.time())],
                "resources": {"memory_mb": 512.0},
            }

        adapter = BatonAdapter(diagnostic_snapshot_fn=provider)
        adapter.register_job("j1", [_make_sheet(tmp_path)], dependencies={})

        assert adapter._build_healing_context(SheetState(sheet_num=1), "j1", 1) is None

    def test_events_before_attempt_dispatch_filtered_out(
        self, tmp_path: Path
    ) -> None:
        now = time.time()
        stale = _event("observer.file_created", now - 1000.0, {"path": "old.txt"})
        fresh = _event("observer.file_created", now, {"path": "new.txt"})

        def provider(job_id: str) -> dict[str, Any]:
            return {"observer_events": [fresh, stale], "resources": None}

        adapter = BatonAdapter(diagnostic_snapshot_fn=provider)
        adapter.register_job("j1", [_make_sheet(tmp_path)], dependencies={})
        adapter._stale_dispatch_time[("j1", 1)] = now - 60.0

        ctx = adapter._build_healing_context(_failed_state(), "j1", 1)

        assert ctx is not None
        assert ctx["observer_events"] == [fresh]

    def test_raising_provider_degrades_to_201_behavior(
        self, tmp_path: Path
    ) -> None:
        def boom(job_id: str) -> dict[str, Any]:
            raise RuntimeError("recorder gone")

        adapter = BatonAdapter(diagnostic_snapshot_fn=boom)
        adapter.register_job("j1", [_make_sheet(tmp_path)], dependencies={})

        ctx = adapter._build_healing_context(_failed_state(), "j1", 1)

        assert ctx is not None
        assert ctx["error_code"] == "E601"
        assert "observer_events" not in ctx


class TestEvidenceRuntimeSection:
    def test_runtime_context_rendered_after_primary_signals(self) -> None:
        block = format_failure_evidence(
            error_code="E601",
            error_message="boom",
            observer_events=[
                _event("observer.file_created", 1.0, {"path": "out/a.md"}),
                _event(
                    "observer.process_exited",
                    2.0,
                    {"pid": 42, "exit_code": 137},
                ),
            ],
            resources={"memory_mb": 900.5},
        )

        assert block is not None
        assert "out/a.md" in block
        assert "137" in block
        assert "900" in block
        # Primary signal (classifier line) comes before the runtime section.
        assert block.index("E601") < block.index("out/a.md")

    def test_runtime_data_alone_yields_no_block(self) -> None:
        block = format_failure_evidence(
            observer_events=[_event("observer.file_created", 1.0, {"path": "x"})],
            resources={"memory_mb": 100.0},
        )
        assert block is None

    def test_runtime_section_is_bounded(self) -> None:
        events = [
            _event("observer.file_created", float(i), {"path": f"f{i}.txt"})
            for i in range(200)
        ]
        block = format_failure_evidence(error_code="E601", observer_events=events)
        assert block is not None
        # Far fewer than all 200 paths are echoed.
        assert sum(f"f{i}.txt" in block for i in range(200)) <= 12

    def test_validation_first_ordering_preserved(self) -> None:
        block = format_failure_evidence(
            validation_details=[
                {
                    "rule_type": "command_succeeds",
                    "passed": False,
                    "failure_reason": "tests failed",
                }
            ],
            observer_events=[_event("observer.file_created", 1.0, {"path": "z.md"})],
        )
        assert block is not None
        assert block.index("tests failed") < block.index("z.md")


class TestPreambleWire:
    def test_healing_context_diagnostics_reach_the_retry_preamble(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: ctx keys flow through build_preamble into the block."""
        from marianne.prompts.preamble import build_preamble

        preamble = build_preamble(
            sheet_num=1,
            total_sheets=3,
            workspace=tmp_path,
            retry_count=1,
            healing_context={
                "error_code": "E601",
                "error_message": "boom",
                "observer_events": [
                    _event("observer.file_created", 1.0, {"path": "out/a.md"})
                ],
                "resources": {"memory_mb": 777.0},
            },
        )

        assert "out/a.md" in preamble
        assert "777" in preamble


class TestManagerInjection:
    async def test_manager_injects_diagnostic_provider(self, tmp_path: Path) -> None:
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.manager import JobManager

        config = DaemonConfig(
            max_concurrent_jobs=1,
            pid_file=tmp_path / "test.pid",
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr.start()
        try:
            adapter = mgr._baton_adapter
            assert adapter is not None
            assert adapter._diagnostic_snapshot_fn is not None
            snap = adapter._diagnostic_snapshot_fn("nonexistent-job")
            # Provider is total: returns a dict even for unknown jobs.
            assert snap is None or isinstance(snap, dict)
        finally:
            await mgr.shutdown(graceful=False)
