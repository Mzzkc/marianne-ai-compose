"""The per-job scratch root (GH #460): exported to every instrument, removed at terminal."""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.core.config.workspace import (
    SCRATCH_DIR_NAME,
    SCRATCH_ENV_VAR,
    WorkspaceLifecycleConfig,
    scratch_root,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _profile(*, required_env: list[str] | None = None) -> InstrumentProfile:
    return InstrumentProfile(
        name="scratch-cli",
        display_name="Scratch CLI",
        kind="cli",
        models=[ModelCapacity(name="t", context_window=1000,
                              cost_per_1k_input=0, cost_per_1k_output=0)],
        default_model="t",
        cli=CliProfile(
            command=CliCommand(executable="python3", prompt_flag="-c",
                               prompt_via_stdin=False, required_env=required_env),
            output=CliOutputConfig(format="text"),
            errors=CliErrorConfig(success_exit_codes=[0]),
        ),
    )


def test_scratch_policy_defaults_to_remove() -> None:
    assert WorkspaceLifecycleConfig().scratch == "remove"
    assert WorkspaceLifecycleConfig(scratch="keep").scratch == "keep"
    with pytest.raises(ValueError):
        WorkspaceLifecycleConfig(scratch="archive")  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("required_env", [None, ["HOME"]])
async def test_plugin_cli_exports_scratch_root_and_creates_it(
    tmp_path: Path, required_env: list[str] | None,
) -> None:
    backend = PluginCliBackend(_profile(required_env=required_env), working_directory=tmp_path)
    result = await backend.execute(
        "import os; print(os.environ['MARIANNE_SCRATCH'])", timeout_seconds=10,
    )
    assert result.exit_code == 0, result.stderr
    assert Path(result.stdout.strip()) == tmp_path / SCRATCH_DIR_NAME
    assert (tmp_path / SCRATCH_DIR_NAME).is_dir()


@pytest.mark.asyncio
async def test_no_working_directory_exports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nested Marianne: this process is itself an instrument of an outer job.
    monkeypatch.setenv(SCRATCH_ENV_VAR, "/outer/job/.marianne-scratch")
    backend = PluginCliBackend(_profile())
    result = await backend.execute(
        "import os; print('MARIANNE_SCRATCH' in os.environ)", timeout_seconds=10,
    )
    assert result.stdout.strip() == "False"


def test_interactive_backend_env_names_the_scratch_root(tmp_path: Path) -> None:
    from marianne.execution.instruments.interactive.backend import InteractiveCliBackend

    env = InteractiveCliBackend._scratch_env(tmp_path)
    assert env == {SCRATCH_ENV_VAR: str(scratch_root(tmp_path))}
    assert scratch_root(tmp_path).is_dir()


@pytest.mark.asyncio
async def test_tmux_new_session_passes_env_entries() -> None:
    from marianne.execution.instruments.interactive.tmux import TmuxControl

    calls: list[tuple[str, ...]] = []

    class Probe(TmuxControl):
        async def _run(self, *args: str, **kwargs: object) -> tuple[int, str, str]:  # type: ignore[override]
            calls.append(args)
            return 0, "", ""

    await Probe("probe").new_session(
        "s", ["sleep", "1"], cwd=Path("/"), width=80, height=24,
        env={SCRATCH_ENV_VAR: "/w/.marianne-scratch"},
    )
    args = calls[0]
    assert args[args.index("--") + 1:] == (
        "env", "-i", "--", f"{SCRATCH_ENV_VAR}=/w/.marianne-scratch", "sleep", "1",
    )


@pytest.mark.asyncio
async def test_manager_removes_scratch_root_at_terminal_unless_kept(tmp_path: Path) -> None:
    from marianne.daemon.manager import JobManager, JobMeta

    score = tmp_path / "score.yaml"
    for policy, expect_present in (("remove", False), ("keep", True)):
        workspace = tmp_path / f"ws-{policy}"
        root = scratch_root(workspace)
        root.mkdir(parents=True)
        (root / "probe-clone").mkdir()
        score.write_text(
            "name: scratch\nworkspace: " + str(workspace) + "\n"
            "workspace_lifecycle:\n  scratch: " + policy + "\n"
            "sheet:\n  size: 1\n  total_items: 1\nprompt:\n  template: hi\n"
        )
        meta = JobMeta(job_id="j", config_path=score, workspace=workspace)
        await JobManager._remove_scratch_root(JobManager.__new__(JobManager), "j", meta)
        assert root.exists() is expect_present, policy
