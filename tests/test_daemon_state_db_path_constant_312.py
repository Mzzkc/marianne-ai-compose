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


_CORE_FORBIDDEN = ("marianne.daemon", "marianne.execution", "marianne.cli", "marianne.ipc")


def _core_boundary_offenders(root: Path, source_by_path: dict[Path, str]) -> list[str]:
    """Every import of a forbidden package under ``root``: literal ``import``,
    absolute ``from … import``, RELATIVE ``from ..daemon import`` (resolved
    against the file's package), and literal-string ``importlib.import_module(…)``
    (Axiom P2-axiom-core-import-census-dynamic-gap: the first census missed the
    last two)."""
    import ast

    offenders: list[str] = []
    for path, text in sorted(source_by_path.items()):
        tree = ast.parse(text)
        rel = path.relative_to(root)
        package_parts = ["marianne", "core", *rel.parent.parts]
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    modules = [node.module or ""]
                else:
                    base = package_parts[: len(package_parts) - (node.level - 1)]
                    modules = [".".join([*base, *( [node.module] if node.module else [])])]
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                modules = [node.args[0].value]
            for module in modules:
                if module.startswith(_CORE_FORBIDDEN):
                    offenders.append(f"{rel}:{node.lineno} imports {module}")
    return offenders


def test_core_package_never_imports_daemon_execution_or_cli() -> None:
    """GH #414: ``core/`` is the bottom layer. Function-level imports count too —
    the original defect was a deferred ``from marianne.daemon.clone import …``
    inside ``core/constants.py``."""
    import marianne.core as core

    root = Path(core.__file__).parent
    sources = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.py")}
    offenders = _core_boundary_offenders(root, sources)
    assert not offenders, "\n".join(offenders)


def test_core_boundary_census_catches_relative_and_dynamic_imports(tmp_path: Path) -> None:
    """Mutation control for the census itself (Axiom P2): a core module that
    reaches daemon/ by a relative import or by ``importlib.import_module`` with a
    literal string MUST be an offender. The first census passed both."""
    root = tmp_path / "core"
    (root / "config").mkdir(parents=True)
    sources = {
        root / "config" / "sneaky.py": (
            "import importlib\n"
            "from ...daemon import clone\n"
            "def f():\n    return importlib.import_module('marianne.daemon.manager')\n"
        ),
        root / "clean.py": "from marianne.core import constants\nimport json\n",
    }
    offenders = _core_boundary_offenders(root, sources)
    assert offenders == [
        "config/sneaky.py:2 imports marianne.daemon",
        "config/sneaky.py:4 imports marianne.daemon.manager",
    ]
