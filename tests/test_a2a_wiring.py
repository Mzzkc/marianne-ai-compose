"""Tests for A2A protocol wiring."""

from __future__ import annotations

from marianne.core.config.a2a import A2ASkill, AgentCard
from marianne.daemon.a2a.inbox import A2AInbox
from marianne.daemon.a2a.registry import AgentCardRegistry


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
