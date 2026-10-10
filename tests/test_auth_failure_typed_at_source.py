"""AUTH_FAILURE requires a backend-typed cause (Blueprint capability-classes Inspect F1).

Same rule as #418's §5.2 narrowing for ENOENT: the route-level verdict comes from
the backend, never from free text in an agent's stderr. Blueprint's probe
``n2_auth_agent_text.py`` showed 4/4 agent-text cases abandoning the class's
preferred entry after ONE attempt with reason ``auth_failure``; with classes, every
``instrument: strong`` has a chain, so each misfire moved work to another provider
and reported a credential failure that never happened.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.core.config.flow import SheetTriggerConfig, TriggerAction
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import sheets_to_execution_states
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import _classify_error
from marianne.execution.base import ExecutionResult

AGENT_TEXT = {
    "shell-permission": "bash: line 1: ./scripts/deploy.sh: Permission denied",
    "git-publickey": "git@github.com: Permission denied (publickey).",
    "test-403": "FAILED tests/test_api.py::test_admin - assert 403 == 200",
    "traceback-line-403": 'File "app/handler.py", line 403, in run\nKeyError: \'user\'',
}


async def _drive(stderr: str, *, error_type: str | None, on_fail: bool):
    er = ExecutionResult(
        success=False, stdout="", stderr=stderr, exit_code=1,
        exit_reason="completed", duration_seconds=2.0, error_type=error_type,
    )
    cls = _classify_error(er)
    sheet = Sheet(
        num=1, movement=1, voice_count=1, workspace=Path("/tmp/auth-typed-ws"),
        instrument_name="claude-code", instrument_fallbacks=["codex-cli"],
        instrument_fallback_configs=[{}],
    )
    states = sheets_to_execution_states([sheet], max_retries=3)
    cp = CheckpointState(job_id="j", job_name="j", total_sheets=1, sheets=states)
    core = BatonCore()
    triggers = {"1": SheetTriggerConfig(on_fail=[TriggerAction(goto=1)])} if on_fail else None
    core.register_job("j", cp.sheets, {}, flow_state=cp.flow, triggers=triggers)
    st = cp.sheets[1]
    await core.handle_event(SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="claude-code", attempt=1,
        dispatch_epoch=st.dispatch_epoch, execution_success=False, exit_code=1,
        error_classification=cls.classification, error_message=cls.message,
        error_code=cls.error_code,
    ))
    return cls, st, [type(e).__name__ for e in core.drain_flow_events()]


@pytest.mark.parametrize("label", sorted(AGENT_TEXT))
@pytest.mark.parametrize("on_fail", [False, True])
async def test_agent_auth_text_stays_on_its_entry(label: str, on_fail: bool) -> None:
    cls, st, flow = await _drive(AGENT_TEXT[label], error_type=None, on_fail=on_fail)
    assert cls.classification == "EXECUTION_ERROR"
    assert cls.error_code == "E502"  # diagnosis kept
    assert st.instrument_name == "claude-code"
    assert [h.get("reason") for h in st.instrument_fallback_history] == []
    if on_fail:
        assert st.status.value != "retry_scheduled"  # the author's handler ran
    else:
        assert st.status.value == "retry_scheduled"


@pytest.mark.parametrize("error_type", ["auth", "authentication"])
async def test_backend_typed_auth_advances_the_chain(error_type: str) -> None:
    cls, st, flow = await _drive("HTTP 401 Unauthorized", error_type=error_type, on_fail=True)
    assert cls.classification == "AUTH_FAILURE"
    assert st.instrument_name == "codex-cli"
    assert [h.get("reason") for h in st.instrument_fallback_history] == ["auth_failure"]
    assert flow == []  # D-I2: chain before on_fail
