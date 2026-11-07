"""Two concurrent fan-out musicians exchange mail through real CLI subprocesses."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest
import structlog.testing

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager
from marianne.daemon.types import JobRequest
from marianne.execution.base import ExecutionResult
from marianne.execution.instruments.cli_backend import PluginCliBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("delay_retention", [False, True])
async def test_concurrent_fan_out_mailbox_exchange(tmp_path, monkeypatch, delay_retention):
    workspace = tmp_path / "workspace"
    stub = tmp_path / "musician.py"
    stub.write_text('''
import json, os, subprocess, sys, time
from pathlib import Path
sheet = int(os.environ["MARIANNE_SHEET"])
other = 3 - sheet
body = "PRIVATE-NOTE-FROM-" + str(sheet)
cli = [sys.executable, "-m", "marianne", "mail"]
sent = subprocess.run(cli + ["send", str(other), body], capture_output=True, text=True)
assert sent.returncode == 0, sent.stderr
end = time.monotonic() + 12
while time.monotonic() < end:
    received = subprocess.run(cli + ["read"], capture_output=True, text=True)
    assert received.returncode == 0, received.stderr
    messages = [json.loads(line) for line in received.stdout.splitlines()]
    if messages:
        assert messages[0]["sender"] == other
        assert messages[0]["body"] == "PRIVATE-NOTE-FROM-" + str(other)
        Path(sys.argv[1], f"received-{sheet}.json").write_text(json.dumps(messages))
        print("delivered")
        break
    time.sleep(0.05)
else:
    raise AssertionError("No reply from concurrent sibling")
''')
    profile = InstrumentProfile(
        name="mailbox-stub", display_name="Mailbox Stub", kind="cli",
        models=[ModelCapacity(name="stub", context_window=10000,
                              cost_per_1k_input=0, cost_per_1k_output=0)],
        default_model="stub",
        cli=CliProfile(
            command=CliCommand(executable=sys.executable, prompt_flag="-c"),
            output=CliOutputConfig(format="text"),
        ),
    )
    import marianne.instruments.loader as loader

    real_profiles = loader.load_all_profiles

    def profiles(**kwargs):
        return {**real_profiles(**kwargs), profile.name: profile}

    monkeypatch.setattr(loader, "load_all_profiles", profiles)
    active = set()
    max_active = 0

    async def execute(self, prompt, *, timeout_seconds=None, request=None):
        nonlocal max_active
        assert request is not None
        sheet = request.sheet_num
        assert "## Mailbox" in prompt
        assert f"{3 - sheet}: Sheet {3 - sheet}" in prompt
        active.add(sheet)
        max_active = max(max_active, len(active))
        env = self._scratch_env(self._build_env(), sheet_num=sheet)
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(stub), str(tmp_path),
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=18)
            return ExecutionResult(
                success=process.returncode == 0, exit_code=process.returncode or 0,
                stdout=stdout.decode(), stderr=stderr.decode(), duration_seconds=0.1,
            )
        finally:
            active.discard(sheet)
            if process.returncode is None:
                process.kill()
                await process.wait()

    monkeypatch.setattr(PluginCliBackend, "execute", execute)
    config = DaemonConfig(
        state_db_path=tmp_path / "registry.db", max_concurrent_sheets=2,
        learning={"enabled": False}, observer={"enabled": False},
        orchestra={"enabled": False},
    )
    score = tmp_path / "score.yaml"
    score.write_text(f'''
name: mailbox-exchange
workspace: {workspace}
instrument: mailbox-stub
sheet:
  total_items: 1
  fan_out: {{1: 2}}
parallel:
  enabled: true
  max_concurrent: 2
retry:
  max_retries: 0
prompt:
  template: Exchange a note with your sibling.
''')
    manager = JobManager(config)
    retention_ready = asyncio.Event()
    if delay_retention:
        record_traffic = manager._record_mailbox_traffic

        async def delayed_record_traffic(job_id, workspace):
            await retention_ready.wait()
            await record_traffic(job_id, workspace)

        monkeypatch.setattr(manager, "_record_mailbox_traffic", delayed_record_traffic)
    with structlog.testing.capture_logs() as logs:
        try:
            await manager.start()
            response = await manager.submit_job(JobRequest(config_path=score))
            job_task = manager._jobs[response.job_id]
            deadline = asyncio.get_running_loop().time() + 23
            while asyncio.get_running_loop().time() < deadline:
                jobs = await manager.list_jobs()
                job = next(item for item in jobs if item["job_id"] == response.job_id)
                if job["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.05)
            assert job["status"] == "completed", (job, logs[-20:])
            retention_ready.set()
            # Baton status is terminal before managed-task mailbox retention
            # and scratch cleanup settle. Join that task before inspecting them.
            await asyncio.wait_for(asyncio.shield(job_task), timeout=5)
            assert max_active == 2
            for sheet in (1, 2):
                received = json.loads((tmp_path / f"received-{sheet}.json").read_text())
                assert received[0]["sender"] == 3 - sheet
                assert received[0]["body"] == f"PRIVATE-NOTE-FROM-{3 - sheet}"
            sends = [entry for entry in logs if entry["event"] == "mailbox.send"]
            assert {(e["sender"], e["recipient"]) for e in sends} == {(1, 2), (2, 1)}
            assert len(sends) == 2
            assert all(e["bytes"] == len("PRIVATE-NOTE-FROM-1") for e in sends)
            assert "PRIVATE-NOTE" not in json.dumps(sends)
            summary = [entry for entry in logs if entry["event"] == "mailbox.traffic"]
            assert len(summary) == 1
            assert "PRIVATE-NOTE" not in json.dumps(summary)
            assert "PRIVATE-NOTE" not in json.dumps(logs, default=str)
            saved = json.loads(await manager._registry.load_checkpoint(response.job_id))
            assert len(saved["mailbox_traffic"]["sends"]) == 2
            assert "PRIVATE-NOTE" not in json.dumps(saved["mailbox_traffic"])
            assert not (workspace / ".marianne-scratch").exists()
        finally:
            await manager.shutdown(graceful=False)
