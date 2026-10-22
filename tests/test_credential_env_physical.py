"""Real child-process witnesses for CLI environment isolation (GH #335)."""

import asyncio
import json
import os
import shutil
import sys
import uuid

import pytest

from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.execution.instruments.interactive.backend import InteractiveCliBackend
from marianne.execution.instruments.interactive.tmux import TmuxControl
from tests.test_credential_env_filtering import _make_profile
from tests.test_interactive_mode import make_interactive_config

_PROBE = "FOREIGN_SECRET_PROBE"
_SERVER_PROBE = "SERVER_ONLY_SECRET_PROBE"
_CASES = [(False, None, False), (True, None, True), (False, [_PROBE], True), (True, [], False)]


@pytest.mark.parametrize("inherit,required,visible", _CASES)
@pytest.mark.parametrize("supervise", [False, True])
async def test_exec_physical_child_env(
    tmp_path, monkeypatch, inherit, required, visible, supervise,
):
    monkeypatch.setenv(_PROBE, "fake-foreign-credential")
    profile = _make_profile(inherit_env=inherit, required_env=required)
    profile.cli.command.executable = sys.executable
    profile.cli.command.prompt_flag = "-c"
    profile.cli.command.prompt_via_stdin = False
    profile.cli.command.supervise = supervise
    backend = PluginCliBackend(profile, working_directory=tmp_path)
    result = await backend.execute(
        f"import os,json; print(json.dumps(os.environ.get({_PROBE!r})))",
        timeout_seconds=10,
    )
    assert result.success, result.stderr
    assert json.loads(result.stdout) == ("fake-foreign-credential" if visible else None)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
@pytest.mark.parametrize("inherit,required,visible", _CASES)
async def test_tmux_physical_child_env_and_process_identity(
    tmp_path, monkeypatch, inherit, required, visible,
):
    monkeypatch.setenv(_PROBE, "fake-foreign-credential")
    profile = _make_profile(inherit_env=inherit, required_env=required)
    profile.cli.interactive = make_interactive_config()
    # A private server owned by this test only. Never touch Marianne's server.
    tmux = TmuxControl(f"env-probe-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    backend = InteractiveCliBackend(profile, working_directory=tmp_path, tmux=tmux)
    out = tmp_path / "child.json"
    probe = (
        "import os,json,pathlib,time; p=pathlib.Path(__import__('sys').argv[1]); "
        "t=p.with_suffix('.tmp'); "
        f"t.write_text(json.dumps({{'credential':os.environ.get({_PROBE!r}),"
        f"'server_credential':os.environ.get({_SERVER_PROBE!r}),"
        "'pid':os.getpid(),'pgid':os.getpgrp()})); "
        "t.replace(p); time.sleep(20)"
    )
    try:
        # Keep the test server alive while planting a server-only credential.
        await tmux.new_session("keeper", ["sleep", "20"], cwd=tmp_path, width=80, height=24)
        await tmux._run("set-environment", "-g", _SERVER_PROBE, "server-fake-secret")
        await tmux.new_session(
            "probe", [sys.executable, "-c", probe, str(out)], cwd=tmp_path,
            width=80, height=24, env=backend._instrument_env(tmp_path),
        )
        deadline = asyncio.get_running_loop().time() + 5
        while not out.exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.03)
        assert out.exists(), "tmux child failed to produce its environment witness"
        child = json.loads(out.read_text())
        assert child["credential"] == ("fake-foreign-credential" if visible else None)
        assert child["server_credential"] is None
        assert child["pid"] == child["pgid"] == await tmux.pane_pid("probe")
    finally:
        await tmux.kill_server()
