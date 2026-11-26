"""Tests of the value-returning submit path (#561).

``run.submit_score`` validates the job id, probes the conductor, routes
``job.submit`` and polls for early failure, returning a typed ``SubmitResult``
— no printing, no ``typer.Exit``. This file needs no compiler, so it runs in
CI where ``tests/test_cli_agents_sc1c.py`` is skipped.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app

# Import the module by name: `commands.run` re-exports the command FUNCTION
# (`tests/test_adversary_m1c3.py` records the same trap).
run_module = importlib.import_module("marianne.cli.commands.run")
from marianne.cli.commands.run import SubmitStatus, submit_score  # noqa: E402

runner = CliRunner()


def _score(tmp_path: Any) -> Any:
    score = tmp_path / "s.yaml"
    score.write_text(
        "name: t\n"
        "workspace: " + str(tmp_path / "ws") + "\n"
        "sheet: {size: 1, total_items: 1}\n"
        "prompt:\n  template: hi\n"
    )
    return score


def _accepted(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
    return True, {"job_id": params.get("job_id", "x"), "status": "accepted", "message": "queued"}


# ---------------------------------------------------------------- unit: outcomes


def _submit(**kw: Any) -> Any:
    return asyncio.run(submit_score(run_module.Path("unused.yaml"), **kw))


def test_accepted_returns_the_reply_as_a_value() -> None:
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=_accepted)),
        patch("marianne.cli.commands.run.await_early_failure", new=AsyncMock(return_value=None)),
    ):
        out = _submit(job_id="job-1")
    assert out.status is SubmitStatus.ACCEPTED
    assert out.job_id == "job-1"
    assert out.response == {"job_id": "job-1", "status": "accepted", "message": "queued"}


def test_invalid_job_id_is_a_returned_refusal_and_never_reaches_the_conductor() -> None:
    called: list[str] = []

    async def route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        called.append(method)
        return True, {"job_id": "x", "status": "accepted"}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=route)),
    ):
        out = _submit(job_id="bad job id!")
    assert not called, "an invalid job id must not touch the conductor"
    assert out.status is SubmitStatus.BAD_JOB_ID
    assert "bad job id!" in out.message


def test_non_dict_route_reply_is_a_route_error_not_a_rejection() -> None:
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch(
            "marianne.daemon.detect.try_daemon_route",
            new=AsyncMock(return_value=(True, ["no"])),
        ),
    ):
        out = _submit()
    assert out.status is SubmitStatus.ROUTE_ERROR
    assert out.cause is not None


def test_rejected_returns_the_conductor_message() -> None:
    async def reject(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        return True, {"job_id": "", "status": "rejected", "message": "System under pressure"}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=reject)),
    ):
        out = _submit()
    assert out.status is SubmitStatus.REJECTED
    assert out.message == "System under pressure"


def test_early_failure_returns_early_failed_with_the_error() -> None:
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=_accepted)),
        patch(
            "marianne.cli.commands.run.await_early_failure",
            new=AsyncMock(
                return_value={
                    "job_id": "job-1",
                    "status": "failed",
                    "error_message": "Template syntax error",
                }
            ),
        ),
    ):
        out = _submit(job_id="job-1")
    assert out.status is SubmitStatus.EARLY_FAILED
    assert out.error == "Template syntax error"


def test_absent_conductor_is_not_routed() -> None:
    with patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=False)):
        out = _submit()
    assert out.status is SubmitStatus.NOT_ROUTED


def test_route_raises_are_route_errors_with_the_cause() -> None:
    from marianne.daemon.exceptions import (
        DaemonAccessDeniedError,
        DaemonProtocolError,
        DaemonUnresponsiveError,
        MethodNotFoundError,
    )

    for exception, expected in [
        (DaemonAccessDeniedError(), SubmitStatus.ROUTE_ERROR),
        (DaemonUnresponsiveError("no answer"), SubmitStatus.ROUTE_ERROR),
        (DaemonProtocolError("corrupt"), SubmitStatus.ROUTE_ERROR),
        (MethodNotFoundError("no method"), SubmitStatus.ROUTE_ERROR),
    ]:
        with (
            patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
            patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=exception)),
        ):
            out = _submit()
        assert out.status is expected, exception
        assert out.cause is exception


def test_connection_error_is_not_routed_and_logs() -> None:
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch(
            "marianne.daemon.detect.try_daemon_route",
            new=AsyncMock(side_effect=OSError("socket gone")),
        ),
    ):
        out = _submit()
    assert out.status is SubmitStatus.NOT_ROUTED


def test_params_carry_escalation_workspace_and_lock() -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    async def route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        sent.append((method, params))
        return True, {"job_id": "x", "status": "accepted"}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=route)),
        patch("marianne.cli.commands.run.await_early_failure", new=AsyncMock(return_value=None)),
    ):
        _submit(
            job_id="j-1",
            escalation=True,
            workspace=run_module.Path("/tmp/wsx"),
            require_lock=run_module.Path("/tmp/lock.yaml"),
            runtime_vars={"k": "v"},
            start_sheet=2,
        )
    method, params = sent[0]
    assert method == "job.submit"
    assert params["job_id"] == "j-1" and params["escalation"] is True
    assert params["workspace"] == str(run_module.Path("/tmp/wsx").resolve())
    assert params["required_lock"] == str(run_module.Path("/tmp/lock.yaml").resolve())
    assert params["runtime_variables"] == {"k": "v"} and params["start_sheet"] == 2


def test_submit_score_prints_nothing_on_any_path(capsys: pytest.CaptureFixture[str]) -> None:
    replies = [
        (True, {"job_id": "x", "status": "accepted", "message": "queued"}),
        (True, {"job_id": "x", "status": "pending", "message": "rate limited"}),
        (True, {"job_id": "", "status": "rejected", "message": "System under pressure"}),
        (False, None),
    ]
    for reply in replies:
        with (
            patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
            patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(return_value=reply)),
            patch(
                "marianne.cli.commands.run.await_early_failure", new=AsyncMock(return_value=None)
            ),
        ):
            _submit()
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------- run --json output


def _one_document(text: str) -> dict[str, Any]:
    """json.loads rejects trailing data, so this also proves there is nothing after the object."""
    return json.loads(text)


class TestRunJsonIsOneDocument:
    """Guards: ``mzt run --json`` stdout is one JSON document per outcome (#495).

    Green on the unmodified tree by design — the mzt callback sends logs to
    stderr before the command prints. They fail if the refactor changes
    ``mzt run``'s output.
    """

    def _invoke(self, tmp_path: Any, route: Any, *extra: str) -> Any:
        score = _score(tmp_path)
        with (
            patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
            patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=route)),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new=AsyncMock(return_value=None),
            ),
        ):
            return runner.invoke(app, ["run", str(score), "--json", *extra])

    def test_accepted_is_one_document(self, tmp_path: Any) -> None:
        result = self._invoke(tmp_path, _accepted)
        assert result.exit_code == 0, result.output
        assert _one_document(result.stdout)["status"] == "accepted"

    def test_pending_is_one_document(self, tmp_path: Any) -> None:
        async def route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
            return True, {"job_id": "x", "status": "pending", "message": "rate limited"}

        result = self._invoke(tmp_path, route)
        assert result.exit_code == 0, result.output
        assert _one_document(result.stdout)["status"] == "pending"

    def test_rejected_is_exit_1_and_one_document(self, tmp_path: Any) -> None:
        async def route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
            return True, {"job_id": "", "status": "rejected", "message": "System under pressure"}

        result = self._invoke(tmp_path, route)
        assert result.exit_code == 1
        assert _one_document(result.stdout)["status"] == "rejected"

    def test_early_failed_is_exit_1_and_one_document(self, tmp_path: Any) -> None:
        def route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
            return _accepted(method, params)

        with (
            patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
            patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=route)),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new=AsyncMock(
                    return_value={"job_id": "x", "status": "failed", "error_message": "boom"}
                ),
            ),
        ):
            score = _score(tmp_path)
            result = runner.invoke(app, ["run", str(score), "--json"])
        assert result.exit_code == 1
        assert _one_document(result.stdout)["status"] == "failed"

    def test_a_warning_during_submit_stays_off_stdout(self, tmp_path: Any) -> None:
        """D1 guard: a log line during the submit must not pollute stdout.

        Under the repo's conftest the default structlog factory prints to
        stdout, which is exactly what the old stdout capture broke on; the
        mzt callback has configured logging to stderr by the time the
        command runs, so stdout stays one document.
        """
        import structlog

        async def noisy_route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
            structlog.get_logger("test").warning("route_noise", hint="should not hit stdout")
            return _accepted(method, params)

        result = self._invoke(tmp_path, noisy_route)
        assert result.exit_code == 0, result.output
        assert _one_document(result.stdout)["status"] == "accepted"


class TestRunTextOutput:
    """Guards for ``mzt run`` in text mode (no ``--json``).

    The JSON class above proves nothing about text mode, which is where these
    three behaviours live. Each was deletable with every test green before
    these existed.
    """

    def _invoke(self, tmp_path: Any, route: Any, poll: Any = None, *extra: str) -> Any:
        score = _score(tmp_path)
        with (
            patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
            patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=route)),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new=poll or AsyncMock(return_value=None),
            ),
        ):
            return runner.invoke(app, ["run", str(score), *extra])

    def test_early_failure_names_diagnose(self, tmp_path: Any) -> None:
        """An early failure tells the operator the next step: ``mzt diagnose JOB``."""
        failed = AsyncMock(
            return_value={"job_id": "x", "status": "failed", "error_message": "boom"}
        )
        result = self._invoke(tmp_path, _accepted, failed)
        assert result.exit_code == 1
        assert "Score failed: x" in result.stdout
        assert "boom" in result.stdout
        assert "mzt diagnose x" in result.stdout

    def test_fresh_rejection_offers_clear(self, tmp_path: Any) -> None:
        """``--fresh`` on an already-running score offers ``mzt clear`` (not "wait")."""
        async def already_running(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
            return True, {"job_id": "", "status": "rejected", "message": "already running"}

        result = self._invoke(tmp_path, already_running, None, "--fresh")
        assert result.exit_code == 1
        assert "mzt clear --score <id>" in result.stdout
        assert "Or wait for it to finish" not in result.stdout

    def test_plain_rejection_offers_wait_not_clear(self, tmp_path: Any) -> None:
        """Without ``--fresh`` the same rejection offers waiting, as it always has."""
        async def already_running(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
            return True, {"job_id": "", "status": "rejected", "message": "already running"}

        result = self._invoke(tmp_path, already_running)
        assert result.exit_code == 1
        assert "Or wait for it to finish" in result.stdout
        assert "mzt clear --score <id>" not in result.stdout

    def test_rate_limit_panel_follows_the_error(self, tmp_path: Any) -> None:
        """Under pressure the rate-limit panel prints after the rejection, never before."""
        async def under_pressure(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
            if method == "job.submit":
                return True, {
                    "job_id": "", "status": "rejected", "message": "System under pressure",
                }
            return True, {"backends": {"claude-cli": {"seconds_remaining": 90.0}}}

        result = self._invoke(tmp_path, under_pressure)
        assert result.exit_code == 1
        error_at = result.stdout.find("Conductor rejected score")
        panel_at = result.stdout.find("Active rate limits")
        assert error_at != -1 and panel_at != -1, result.stdout
        assert error_at < panel_at, result.stdout


@pytest.mark.xfail(
    strict=True,
    reason="D7 (not yet filed as its own issue): mzt --log-format json installs its log "
    "handler on stdout (core/logging.py), so a WARNING logged during submit lands ahead "
    "of the --json result. The general fix (handler to stderr) flips "
    "test_json_log_lines_decode_strict_and_parse and a documented option; it needs its "
    "own owner decision. This pin fails loudly when that fix lands.",
)
def test_d7_log_format_json_run_is_one_document(tmp_path: Any) -> None:
    """D7 pin: `mzt --log-format json run SCORE --json` must be one document.

    Deterministic, and isolated from the machine. The conductor is stubbed
    with an accepting route that logs a WARNING, so the pin xfails for its
    stated reason here, on CI, and on a developer box with a live conductor
    alike — and never submits a real job. An unstubbed version read as
    `xfail` wherever no conductor answered, which proved nothing.

    The trap: configure_global_logging runs once per process, so without the
    `_log_config.configured = False` reset the handler keeps the first
    CLI-invoking test's stdout and this test passes for the wrong reason.
    Reset it before the invoke, every time.
    """
    import structlog

    import marianne.cli.helpers as helpers

    async def noisy_route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        structlog.get_logger("test").warning("route_noise", hint="D7: should not hit stdout")
        return True, {"job_id": params.get("job_id", "x"), "status": "accepted", "message": ""}

    reached: list[str] = []

    async def recording_route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        reached.append(method)
        return await noisy_route(method, params)

    helpers._log_config.configured = False
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch(
            "marianne.daemon.detect.try_daemon_route",
            new=AsyncMock(side_effect=recording_route),
        ),
        patch("marianne.cli.commands.run.await_early_failure", new=AsyncMock(return_value=None)),
    ):
        result = runner.invoke(
            app,
            ["--log-format", "json", "run", str(_score(tmp_path)), "--json"],
        )
    # The route ran, so the pin is about the log line and not about an
    # unreachable conductor.
    assert reached == ["job.submit"], result.output
    assert result.exit_code == 0, result.output
    assert _one_document(result.stdout)["status"] == "accepted"
