"""A test that invokes ``mzt doctor`` must not run the operator's REA.

``mzt doctor`` runs ``rea doctor --json`` for every REA entry in the operator's
``~/.marianne/conductor.yaml`` (GH #499). On a host that configures REA, every
test that invokes the real command spawned that provider check: about 33 s on a
loaded machine against a 30 s test timeout, so the suite's verdict depended on
the host. The autouse fixture ``_no_host_rea_pool_in_doctor`` closes it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()

_OPERATOR_CONFIG = """\
mcp_pool:
  servers:
    rea:
      command: "node /opt/reverse-engineer-anything/bin/rea.mjs mcp"
"""


def test_doctor_in_a_test_never_runs_the_operators_rea(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    (home / ".marianne").mkdir(parents=True)
    (home / ".marianne" / "conductor.yaml").write_text(_OPERATOR_CONFIG)
    monkeypatch.setenv("HOME", str(home))

    with (
        patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("running", 12345),
        ),
        patch(
            "marianne.cli.commands.doctor._rea_provider_checks", return_value=[],
        ) as provider_checks,
    ):
        result = runner.invoke(app, ["doctor"])

    assert result.exception is None, result.output
    provider_checks.assert_called_once_with([])


def test_an_explicit_config_path_is_still_read(tmp_path: Path) -> None:
    from marianne.cli.commands.doctor import _rea_pool_entries

    cfg = tmp_path / "conductor.yaml"
    cfg.write_text(_OPERATOR_CONFIG)
    assert _rea_pool_entries(cfg) == [
        ("rea", "node /opt/reverse-engineer-anything/bin/rea.mjs mcp"),
    ]
