"""#245: centralize the `.marianne-state.db` magic string into one constant.

The literal was hand-constructed in 8+ path expressions across cli/, daemon/,
execution/, and core/config/ — change the filename in one place and you'd miss
the rest. Now there is a single source of truth, `STATE_DB_FILENAME`, and the
path is built as ``workspace / STATE_DB_FILENAME`` everywhere. Pure refactor:
zero behavior change (the constant's value equals the old literal).
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.constants import STATE_DB_FILENAME


def test_constant_value_matches_legacy_literal() -> None:
    # Pins the value so any future rename is a deliberate one-line change.
    assert STATE_DB_FILENAME == ".marianne-state.db"


def test_job_config_state_path_uses_constant(tmp_path: Path) -> None:
    from marianne.core.config import JobConfig

    p = tmp_path / "score.yaml"
    p.write_text(
        "name: t\nsheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: x\n"
        f"workspace: {tmp_path}\n"
        "state_backend: sqlite\n"
    )
    cfg = JobConfig.from_yaml(p)
    # The sqlite state-db path (get_state_path) is built from the constant.
    assert cfg.get_state_path() == cfg.workspace / STATE_DB_FILENAME
    assert cfg.get_state_path().name == STATE_DB_FILENAME
