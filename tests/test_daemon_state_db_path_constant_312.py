"""#312: centralize the ``~/.marianne/daemon-state.db`` magic path into one constant.

The literal was hand-constructed in 4 separate path expressions across
daemon/config.py (the reserved ``state_db_path`` default), daemon/process.py
(the reserved-field warning baseline), cli/helpers.py, and
cli/commands/recover.py (the two functional conductor-down readers). Relocating
the daemon registry DB meant a coordinated 4-site edit, and the CLI fallback
readers could silently drift from the config default. Now there is a single
source of truth, ``DAEMON_STATE_DB_PATH``.

Pure refactor: zero behavior change (the constant's value equals the old
literal). The config-driven *override* of this path remains a deliberately
deferred feature — ``state_db_path`` is documented as reserved/not-yet-wired
and continues to log a warning when set; this change only deduplicates the
literal, it does not implement override resolution.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.constants import DAEMON_STATE_DB_PATH


def test_constant_value_matches_legacy_literal() -> None:
    # Pins the value so any future relocation is a deliberate one-line change.
    assert Path("~/.marianne/daemon-state.db") == DAEMON_STATE_DB_PATH


def test_daemon_config_default_uses_constant() -> None:
    from marianne.daemon.config import DaemonConfig

    cfg = DaemonConfig()
    assert cfg.state_db_path == DAEMON_STATE_DB_PATH


def test_recover_db_path_uses_constant() -> None:
    from marianne.cli.commands.recover import _get_db_path

    assert _get_db_path() == DAEMON_STATE_DB_PATH.expanduser()


def test_recover_db_path_follows_active_clone(monkeypatch) -> None:
    """GH #401: with --conductor-clone active, offline readers open the clone's DB."""
    from marianne.cli.commands.recover import _get_db_path
    from marianne.daemon.clone import (
        active_registry_db_path,
        resolve_clone_paths,
        set_clone_name,
    )

    set_clone_name("pin401")
    try:
        expected = resolve_clone_paths("pin401").state_db.expanduser()
        assert _get_db_path() == expected
        assert active_registry_db_path() == expected
    finally:
        set_clone_name(None)
    assert _get_db_path() == DAEMON_STATE_DB_PATH.expanduser()


def test_core_package_never_imports_daemon_execution_or_cli() -> None:
    """GH #414: ``core/`` is the bottom layer. Function-level imports count too —
    the original defect was a deferred ``from marianne.daemon.clone import …``
    inside ``core/constants.py``."""
    import ast

    import marianne.core as core

    root = Path(core.__file__).parent
    forbidden = ("marianne.daemon", "marianne.execution", "marianne.cli", "marianne.ipc")
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.startswith(forbidden):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno} imports {module}")
    assert not offenders, "\n".join(offenders)
