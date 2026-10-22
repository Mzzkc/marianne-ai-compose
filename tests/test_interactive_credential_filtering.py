"""Environment replacement at both interactive launch boundaries (GH #335)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marianne.execution.instruments.cli_backend import build_instrument_env
from marianne.execution.instruments.interactive.backend import InteractiveCliBackend
from marianne.execution.instruments.interactive.driver import DriverResult
from marianne.execution.instruments.interactive.tmux import TmuxControl
from tests.test_interactive_mode import FakeTmux, make_interactive_config, make_profile


@pytest.mark.parametrize("env", [{}, {"CUSTOM": "spaces ; $literal\nsecond line"}])
async def test_new_session_wraps_command_with_env_dash_i(tmp_path: Path, env) -> None:
    tmux = TmuxControl("unused-unit-probe")
    with patch.object(tmux, "_run", new_callable=AsyncMock) as run:
        await tmux.new_session(
            "probe", ["/bin/sleep", "1"], cwd=tmp_path, width=80, height=24, env=env,
        )
    args = run.call_args.args
    tail = args[args.index("--") + 1:]
    assert tail == ("env", "-i", "--", *(f"{k}={v}" for k, v in env.items()), "/bin/sleep", "1")
    assert "-e" not in args


@pytest.mark.parametrize("inherit,required", [(False, None), (False, ["ALLOWED_TOKEN"]),
                                             (True, None), (True, [])])
async def test_interactive_backend_passes_filtered_env(tmp_path, monkeypatch, inherit, required):
    monkeypatch.setenv("FOREIGN_SECRET", "foreign-probe")
    monkeypatch.setenv("ALLOWED_TOKEN", "allowed-probe")
    profile = make_profile(interactive=make_interactive_config())
    profile.cli.command.inherit_env = inherit
    profile.cli.command.required_env = required
    profile.cli.command.env = {"PROFILE_TOKEN": "${ALLOWED_TOKEN}"}
    backend = InteractiveCliBackend(profile, working_directory=tmp_path, tmux=FakeTmux())
    backend._driver.run = AsyncMock(return_value=DriverResult("completed", "done", 0))
    result = await backend.execute("probe", timeout_seconds=5)
    assert result.success
    env = backend._driver.run.call_args.kwargs["env"]
    assert env["MARIANNE_SCRATCH"] == str(tmp_path / ".marianne-scratch")
    expected = build_instrument_env(profile.cli.command)
    expected.pop("MARIANNE_SCRATCH", None)
    assert {k: v for k, v in env.items() if k != "MARIANNE_SCRATCH"} == expected
    assert ("FOREIGN_SECRET" in env) == (inherit and required is None)
    assert env["PROFILE_TOKEN"] == "allowed-probe"
