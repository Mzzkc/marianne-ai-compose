"""Recurring-score presentation through the existing run command."""

from __future__ import annotations

from pathlib import Path

from marianne.cli.commands.run import run
from marianne.cli.output import console


def _score(path: Path, *, scheduled: bool) -> Path:
    schedule = "schedule:\n  interval: 5m\n" if scheduled else ""
    path.write_text(
        "name: display-job\n"
        "workspace: ./workspace\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        + schedule
    )
    return path


def _dry_run(path: Path) -> str:
    with console.capture() as capture:
        run(
            config_file=path,
            dry_run=True,
            start_sheet=None,
            workspace=None,
            json_output=False,
            escalation=False,
            self_healing=False,
            yes=False,
            fresh=False,
            var=None,
        )
    return capture.get()


def test_scheduled_run_panel_is_labelled_recurring(tmp_path: Path) -> None:
    assert "[recurring]" in _dry_run(_score(tmp_path / "scheduled.yaml", scheduled=True))


def test_unscheduled_run_panel_has_no_recurring_decoration(tmp_path: Path) -> None:
    assert "[recurring]" not in _dry_run(
        _score(tmp_path / "ordinary.yaml", scheduled=False)
    )
