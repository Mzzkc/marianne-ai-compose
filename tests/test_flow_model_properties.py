"""Generated valid flow declarations and evidence survive checkpoint encoding."""

from hypothesis import given
from hypothesis import strategies as st

from marianne.core.checkpoint import FlowAttemptRecord, InstrumentIdentity, SheetStatus
from marianne.core.config.flow import (
    ConcertTrigger,
    LoopConfig,
    RunTrigger,
    SheetTriggerConfig,
    TriggerAction,
)


@given(
    passes=st.integers(min_value=1, max_value=100),
    target=st.integers(min_value=1, max_value=100),
    name=st.text(alphabet="abcxyz012", min_size=1, max_size=10),
)
def test_flow_models_round_trip(passes: int, target: int, name: str) -> None:
    loop = LoopConfig(count=passes, index="pass_no")
    child = ConcertTrigger(score=f"{name}.yaml")
    command = RunTrigger(command=f"printf '%s' '{name}'", timeout_seconds=60)
    action = TriggerAction(goto=target)
    triggers = SheetTriggerConfig(on_fail=[action])
    identity = InstrumentIdentity(name=name, model=None)
    record = FlowAttemptRecord(
        epoch=passes, cause="loop iteration", status=SheetStatus.COMPLETED,
        loop_indices={"pass_no": passes},
    )

    for model in (loop, child, command, action, triggers, identity, record):
        assert type(model).model_validate_json(model.model_dump_json()) == model
    assert triggers.on_fail is not None and triggers.on_fail[0].goto == target
    assert record.loop_indices["pass_no"] == passes
