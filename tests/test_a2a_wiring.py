"""Tests for A2A protocol wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from marianne.backends.base import ExecutionResult
from marianne.core.config.a2a import A2ASkill, AgentCard
from marianne.core.config.job import PromptConfig
from marianne.core.config.techniques import TechniqueConfig, TechniqueKind
from marianne.core.sheet import Sheet
from marianne.daemon.a2a.inbox import A2AInbox, A2ATaskStatus
from marianne.daemon.a2a.registry import AgentCardRegistry
from marianne.daemon.baton.events import SheetAttemptResult, ShutdownRequested


def _sheet(workspace: Path, num: int = 1, movement: int = 1) -> Sheet:
    return Sheet(
        num=num,
        movement=movement,
        voice=None,
        voice_count=1,
        instrument_name="test",
        workspace=workspace,
        prompt_template="Do the work",
        validations=[],
        timeout_seconds=30.0,
    )


def _a2a_techniques() -> dict[str, TechniqueConfig]:
    return _a2a_techniques_for_phases(["all"])


def _a2a_techniques_for_phases(phases: list[str]) -> dict[str, TechniqueConfig]:
    return {
        "a2a": TechniqueConfig(
            kind=TechniqueKind.PROTOCOL,
            phases=phases,
        )
    }


class _EventBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _FakeBackend:
    def __init__(self, pool: _FakeBackendPool, stdout: str) -> None:
        self._pool = pool
        self.stdout = stdout
        self.preamble: str | None = None
        self.output_callback: Any = None

    def set_preamble(self, preamble: str | None) -> None:
        self.preamble = preamble

    def set_output_callback(self, callback: Any) -> None:
        self.output_callback = callback

    def clear_overrides(self) -> None:
        pass

    async def execute(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        self._pool.prompts.append(prompt)
        return ExecutionResult(
            success=True,
            stdout=self.stdout,
            stderr="",
            duration_seconds=0.01,
            exit_code=0,
            model="fake",
            input_tokens=1,
            output_tokens=1,
        )


class _FakeBackendPool:
    def __init__(self, outputs: str | list[str]) -> None:
        self.outputs = [outputs] if isinstance(outputs, str) else list(outputs)
        self.prompts: list[str] = []
        self._registry = None

    async def acquire(self, *args: Any, **kwargs: Any) -> _FakeBackend:
        stdout = self.outputs.pop(0) if self.outputs else "done"
        return _FakeBackend(self, stdout)

    async def release(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def close_all(self) -> None:
        pass


class TestJobConfigAgentCard:
    def _m(self, **kw: object) -> dict:
        d: dict = {
            "name": "t",
            "workspace": "/tmp/t",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "x"},
        }
        d.update(kw)
        return d

    def test_agent_card_default_none(self) -> None:
        from marianne.core.config.job import JobConfig

        assert JobConfig.model_validate(self._m()).agent_card is None

    def test_agent_card_from_yaml_dict(self) -> None:
        from marianne.core.config.job import JobConfig

        c = JobConfig.model_validate(
            self._m(
                agent_card={
                    "name": "canyon",
                    "description": "Arch",
                    "skills": [{"id": "r", "description": "d"}],
                }
            )
        )
        assert c.agent_card is not None and c.agent_card.name == "canyon"

    def test_agent_card_from_model(self) -> None:
        from marianne.core.config.job import JobConfig

        d = self._m()
        d["agent_card"] = AgentCard(name="forge", description="Builder").model_dump()
        assert JobConfig.model_validate(d).agent_card is not None


class TestAgentCardLifecycle:
    def test_register_card_on_job_start(self) -> None:
        reg = AgentCardRegistry()
        card = AgentCard(
            name="canyon", description="Arch", skills=[A2ASkill(id="r", description="d")]
        )
        reg.register("j1", card)
        assert reg.count == 1 and reg.get("j1") is card

    def test_deregister_card_on_job_complete(self) -> None:
        reg = AgentCardRegistry()
        card = AgentCard(name="canyon", description="A")
        reg.register("j1", card)
        assert reg.deregister("j1") is card and reg.count == 0

    def test_deregister_card_on_job_cancel(self) -> None:
        reg = AgentCardRegistry()
        card = AgentCard(name="forge", description="B")
        reg.register("j2", card)
        assert reg.deregister("j2") is card

    def test_no_card_no_registration(self) -> None:
        assert AgentCardRegistry().count == 0


class TestA2AInboxPromptInjection:
    def test_pending_task_appears_in_prompt(self) -> None:
        inbox = A2AInbox(job_id="j1", agent_name="canyon")
        inbox.submit_task(source_job_id="jf", source_agent="forge", description="Review layout")
        ctx = inbox.render_pending_context()
        assert "forge" in ctx and "Review layout" in ctx

    def test_multiple_pending_tasks_all_appear(self) -> None:
        inbox = A2AInbox(job_id="j1", agent_name="canyon")
        inbox.submit_task(source_job_id="jf", source_agent="forge", description="T1")
        inbox.submit_task(source_job_id="js", source_agent="sentinel", description="T2")
        ctx = inbox.render_pending_context()
        assert "T1" in ctx and "T2" in ctx

    def test_no_pending_tasks_empty_context(self) -> None:
        assert len(A2AInbox(job_id="j1", agent_name="c").render_pending_context()) < 50

    def test_no_inbox_no_change_to_prompt(self) -> None:
        inboxes: dict[str, A2AInbox] = {}
        assert inboxes.get("unknown") is None

    def test_inbox_context_injected_into_prompt_rendering(self) -> None:
        inbox = A2AInbox(job_id="j1", agent_name="canyon")
        inbox.submit_task(
            source_job_id="js", source_agent="sentinel", description="Security review"
        )
        ctx = inbox.render_pending_context()
        assert len(ctx) > 0 and "sentinel" in ctx


class TestBatonAdapterA2AWiring:
    def test_adapter_stores_a2a_inbox(self) -> None:
        inboxes: dict[str, A2AInbox] = {}
        inboxes["j1"] = A2AInbox(job_id="j1", agent_name="c")
        assert inboxes["j1"] is not None

    def test_adapter_no_inbox_returns_none(self) -> None:
        assert dict[str, A2AInbox]().get("x") is None  # type: ignore[misc]

    def test_adapter_removes_inbox_on_deregister(self) -> None:
        inboxes: dict[str, A2AInbox] = {"j1": A2AInbox(job_id="j1", agent_name="t")}
        del inboxes["j1"]
        assert "j1" not in inboxes

    def test_register_job_with_agent_card_creates_runtime_inbox(
        self,
        tmp_path: Path,
    ) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        adapter.register_job(
            "canyon-job",
            [_sheet(tmp_path)],
            {1: []},
            agent_card=AgentCard(name="canyon", description="Arch"),
            techniques=_a2a_techniques(),
        )

        inbox = adapter.get_a2a_inbox("canyon-job")
        assert inbox is not None
        assert inbox.agent_name == "canyon"

    @pytest.mark.asyncio
    async def test_sheet_attempt_result_routes_delegate_to_target_inbox(
        self,
        tmp_path: Path,
    ) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        event_bus = _EventBus()
        adapter = BatonAdapter(event_bus=event_bus)
        adapter.register_job(
            "forge-job",
            [_sheet(tmp_path / "forge")],
            {1: []},
            agent_card=AgentCard(name="forge", description="Builder"),
            techniques=_a2a_techniques(),
        )
        adapter.register_job(
            "canyon-job",
            [_sheet(tmp_path / "canyon")],
            {1: []},
            agent_card=AgentCard(name="canyon", description="Arch"),
            techniques=_a2a_techniques(),
        )

        await adapter._route_a2a_requests(
            SheetAttemptResult(
                job_id="forge-job",
                sheet_num=1,
                instrument_name="test",
                attempt=1,
                validation_pass_rate=100.0,
                a2a_requests=[
                    {
                        "target_agent": "canyon",
                        "task_description": "Review module X boundaries",
                        "context": {"path": "src/module_x.py"},
                    }
                ],
            )
        )

        inbox = adapter.get_a2a_inbox("canyon-job")
        assert inbox is not None
        pending = inbox.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0].source_agent == "forge"
        assert pending[0].description == "Review module X boundaries"
        assert pending[0].context["source_job_id"] == "forge-job"
        assert pending[0].context["source_sheet_num"] == 1
        assert [event["event"] for event in event_bus.events] == [
            "baton.a2a.task.submitted",
            "baton.a2a.task.routed",
        ]

    @pytest.mark.asyncio
    async def test_render_a2a_context_accepts_pending_tasks(
        self,
        tmp_path: Path,
    ) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        adapter.register_job(
            "forge-job",
            [_sheet(tmp_path / "forge")],
            {1: []},
            agent_card=AgentCard(name="forge", description="Builder"),
            techniques=_a2a_techniques(),
        )
        adapter.register_job(
            "canyon-job",
            [_sheet(tmp_path / "canyon")],
            {1: []},
            agent_card=AgentCard(name="canyon", description="Arch"),
            techniques=_a2a_techniques(),
        )
        await adapter._route_a2a_requests(
            SheetAttemptResult(
                job_id="forge-job",
                sheet_num=1,
                instrument_name="test",
                attempt=1,
                validation_pass_rate=100.0,
                a2a_requests=[
                    {
                        "target_agent": "canyon",
                        "task_description": "Review module X boundaries",
                        "context": {},
                    }
                ],
            )
        )

        context = adapter._render_a2a_context_for_dispatch("canyon-job")

        assert context is not None
        assert "A2A Inbox" in context
        assert "Review module X boundaries" in context
        inbox = adapter.get_a2a_inbox("canyon-job")
        assert inbox is not None
        assert inbox.pending_count == 0
        task = next(iter(inbox.to_dict()["tasks"].values()))
        assert task["status"] == A2ATaskStatus.ACCEPTED.value

    @pytest.mark.asyncio
    async def test_baton_run_loop_routes_between_two_agents_and_injects_prompt(
        self,
        tmp_path: Path,
    ) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(max_concurrent_sheets=1)
        pool = _FakeBackendPool(
            [
                "@delegate canyon: Review module X boundaries",
                "canyon handled delegated task",
            ]
        )
        adapter.set_backend_pool(pool)
        prompt_config = PromptConfig(template="Agent sheet {{ sheet_num }}")
        adapter.register_job(
            "forge-job",
            [_sheet(tmp_path / "forge")],
            {1: []},
            agent_card=AgentCard(name="forge", description="Builder"),
            techniques=_a2a_techniques(),
            prompt_config=prompt_config,
        )
        adapter.register_job(
            "canyon-job",
            [_sheet(tmp_path / "canyon")],
            {1: []},
            agent_card=AgentCard(name="canyon", description="Arch"),
            techniques=_a2a_techniques(),
            prompt_config=prompt_config,
        )

        run_task = asyncio.create_task(adapter.run())
        try:
            assert await asyncio.wait_for(
                adapter.wait_for_completion("forge-job"), timeout=3.0
            )
            assert await asyncio.wait_for(
                adapter.wait_for_completion("canyon-job"), timeout=3.0
            )
            inbox = adapter.get_a2a_inbox("canyon-job")
            assert inbox is not None
            assert inbox.pending_count == 0
            assert len(pool.prompts) == 2
            assert "A2A Inbox" not in pool.prompts[0]
            assert "A2A Inbox" in pool.prompts[1]
            assert "Review module X boundaries" in pool.prompts[1]
            assert "from forge" in pool.prompts[1]
        finally:
            await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
            await asyncio.wait_for(run_task, timeout=3.0)

    @pytest.mark.asyncio
    async def test_delegate_output_ignored_when_source_sheet_not_a2a_enabled(
        self,
        tmp_path: Path,
    ) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(max_concurrent_sheets=1)
        pool = _FakeBackendPool(
            [
                "@delegate canyon: Review module X boundaries",
                "canyon work without delegated task",
            ]
        )
        adapter.set_backend_pool(pool)
        prompt_config = PromptConfig(template="Agent sheet {{ sheet_num }}")
        adapter.register_job(
            "forge-job",
            [_sheet(tmp_path / "forge")],
            {1: []},
            agent_card=AgentCard(name="forge", description="Builder"),
            techniques=_a2a_techniques_for_phases(["2"]),
            prompt_config=prompt_config,
        )
        adapter.register_job(
            "canyon-job",
            [_sheet(tmp_path / "canyon")],
            {1: []},
            agent_card=AgentCard(name="canyon", description="Arch"),
            techniques=_a2a_techniques(),
            prompt_config=prompt_config,
        )

        run_task = asyncio.create_task(adapter.run())
        try:
            assert await asyncio.wait_for(
                adapter.wait_for_completion("forge-job"), timeout=3.0
            )
            assert await asyncio.wait_for(
                adapter.wait_for_completion("canyon-job"), timeout=3.0
            )
            inbox = adapter.get_a2a_inbox("canyon-job")
            assert inbox is not None
            assert inbox.pending_count == 0
            assert len(pool.prompts) == 2
            assert "A2A Inbox" not in pool.prompts[1]
        finally:
            await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
            await asyncio.wait_for(run_task, timeout=3.0)

    @pytest.mark.asyncio
    async def test_target_inbox_waits_for_explicit_a2a_check_sheet(
        self,
        tmp_path: Path,
    ) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(max_concurrent_sheets=1)
        pool = _FakeBackendPool(
            [
                "@delegate canyon: Review module X boundaries",
                "canyon normal work",
                "canyon a2a check",
            ]
        )
        adapter.set_backend_pool(pool)
        prompt_config = PromptConfig(template="Agent sheet {{ sheet_num }}")
        adapter.register_job(
            "forge-job",
            [_sheet(tmp_path / "forge", movement=1)],
            {1: []},
            agent_card=AgentCard(name="forge", description="Builder"),
            techniques=_a2a_techniques(),
            prompt_config=prompt_config,
        )
        adapter.register_job(
            "canyon-job",
            [
                _sheet(tmp_path / "canyon", num=1, movement=1),
                _sheet(tmp_path / "canyon", num=2, movement=2),
            ],
            {1: [], 2: [1]},
            agent_card=AgentCard(name="canyon", description="Arch"),
            techniques=_a2a_techniques_for_phases(["2"]),
            prompt_config=prompt_config,
        )

        run_task = asyncio.create_task(adapter.run())
        try:
            assert await asyncio.wait_for(
                adapter.wait_for_completion("forge-job"), timeout=3.0
            )
            assert await asyncio.wait_for(
                adapter.wait_for_completion("canyon-job"), timeout=3.0
            )
            assert len(pool.prompts) == 3
            assert "A2A Inbox" not in pool.prompts[1]
            assert "A2A Inbox" in pool.prompts[2]
            assert "Review module X boundaries" in pool.prompts[2]
            inbox = adapter.get_a2a_inbox("canyon-job")
            assert inbox is not None
            assert inbox.pending_count == 0
        finally:
            await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
            await asyncio.wait_for(run_task, timeout=3.0)
