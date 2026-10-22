"""Independent functional boundary checks for GH #335."""

import asyncio
import json
import os
import shutil
import sys
import uuid
from unittest.mock import patch

import pytest

from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.execution.instruments.interactive.backend import InteractiveCliBackend
from marianne.execution.instruments.interactive.tmux import TmuxControl
from tests.test_credential_env_filtering import _make_profile
from tests.test_interactive_mode import make_interactive_config


@pytest.mark.parametrize("inherit", [False, True])
async def test_exec_literal_overlay_missing_grant_and_failure(tmp_path, monkeypatch, inherit):
    monkeypatch.setenv("REVIEW_FOREIGN", "foreign")
    monkeypatch.setenv("REVIEW_ALLOWED", "parent")
    monkeypatch.delenv("REVIEW_MISSING", raising=False)
    literal = "spaces ; $(touch SHOULD_NOT_EXIST)\nquote'\"=tail"
    profile = _make_profile(
        inherit_env=inherit,
        required_env=["REVIEW_ALLOWED", "REVIEW_MISSING"],
        env={"REVIEW_ALLOWED": literal, "REVIEW_EXPANDED": "${REVIEW_FOREIGN}"},
    )
    profile.cli.command.executable = sys.executable
    profile.cli.command.prompt_flag = "-c"
    profile.cli.command.prompt_via_stdin = False
    profile.cli.command.supervise = False
    backend = PluginCliBackend(profile, working_directory=tmp_path)
    result = await backend.execute(
        "import os,json,sys; print(json.dumps({k:os.environ.get(k) for k in "
        "['REVIEW_ALLOWED','REVIEW_MISSING','REVIEW_FOREIGN','REVIEW_EXPANDED']})); "
        "sys.exit(7)",
        timeout_seconds=5,
    )
    assert not result.success
    assert result.exit_code == 7
    assert json.loads(result.stdout) == {
        "REVIEW_ALLOWED": literal, "REVIEW_MISSING": None,
        "REVIEW_FOREIGN": None, "REVIEW_EXPANDED": "foreign",
    }
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


def test_interactive_opt_out_snapshot_and_scratch_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_FOREIGN", "foreign")
    monkeypatch.setenv("MARIANNE_SCRATCH", "/stale-parent")
    profile = _make_profile(inherit_env=True)
    profile.cli.interactive = make_interactive_config()
    backend = InteractiveCliBackend(profile, working_directory=tmp_path)
    with patch.object(backend, "_scratch_env", return_value=None):
        env = backend._instrument_env(tmp_path)
    assert env["REVIEW_FOREIGN"] == "foreign"
    assert "MARIANNE_SCRATCH" not in env
    assert os.environ["MARIANNE_SCRATCH"] == "/stale-parent"
    monkeypatch.setenv("REVIEW_FOREIGN", "changed")
    assert env["REVIEW_FOREIGN"] == "foreign"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
@pytest.mark.parametrize("env", [{}, {"REVIEW_LITERAL": " ; $() ' \"\n=tail"}])
async def test_tmux_replaces_environment_with_empty_or_literal_mapping(tmp_path, env):
    tmux = TmuxControl(f"functional-review-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    out = tmp_path / "witness.json"
    code = (
        "import os,json,pathlib,time; p=pathlib.Path(__import__('sys').argv[1]); "
        "t=p.with_suffix('.tmp'); "
        "t.write_text(json.dumps(dict(os.environ))); "
        "t.replace(p); time.sleep(10)"
    )
    try:
        await tmux.new_session(
            "probe", [sys.executable, "-c", code, str(out)],
            cwd=tmp_path, width=80, height=24, env=env,
        )
        async with asyncio.timeout(5):
            while not out.exists():
                await asyncio.sleep(0.02)
        child = json.loads(out.read_text())
        # Python may synthesize LC_CTYPE during locale coercion.
        child.pop("LC_CTYPE", None)
        assert child == env
    finally:
        await tmux.kill_server()
