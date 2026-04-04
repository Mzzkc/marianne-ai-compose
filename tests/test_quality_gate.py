"""Meta-test that enforces quality standards across ALL test files.

Scans the test suite itself and fails if quality standards are violated.
This is a test quality gate — it validates the tests, not the code.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TESTS_DIR: Path = Path(__file__).parent
SRC_DIR: Path = TESTS_DIR.parent / "src"
CONFIG_DIR: Path = SRC_DIR / "mozart" / "core" / "config"
CHECKPOINT_FILE: Path = SRC_DIR / "mozart" / "core" / "checkpoint.py"

# Baseline counts as of 2026-04-01. Quality gate tests fail only if NEW
# violations are added above these baselines.
BARE_MAGICMOCK_BASELINE: int = 1227
ASYNCIO_SLEEP_BASELINE: int = 137
ASSERTION_LESS_TEST_BASELINE: int = 115


def _collect_test_files() -> list[Path]:
    """Return all test_*.py files in the tests directory."""
    return sorted(
        p for p in TESTS_DIR.iterdir()
        if p.name.startswith("test_") and p.name.endswith(".py")
    )


def _parse_file(path: Path) -> Optional[ast.Module]:
    """Parse a Python file into an AST, returning None on failure."""
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None


# ---------------------------------------------------------------------------
# 1. No asyncio.sleep for coordination
# ---------------------------------------------------------------------------


class _SleepChecker(ast.NodeVisitor):
    """AST visitor that finds problematic asyncio.sleep calls.

    Allowed:
    - asyncio.sleep(0) — yield to event loop
    - asyncio.sleep(...) inside a polling loop (while above it)

    Flagged:
    - bare ``await asyncio.sleep(N)`` where N > 0 without a surrounding
      while loop
    """

    def __init__(self) -> None:
        self.violations: list[tuple[str, int]] = []
        self._in_while: int = 0
        self._filename: str = ""

    def check_file(self, path: Path) -> list[tuple[str, int]]:
        tree = _parse_file(path)
        if tree is None:
            return []
        self._filename = path.name
        self.violations = []
        self._in_while = 0
        self.visit(tree)
        return list(self.violations)

    def visit_While(self, node: ast.While) -> None:
        self._in_while += 1
        self.generic_visit(node)
        self._in_while -= 1

    def visit_Await(self, node: ast.Await) -> None:
        value = node.value
        if isinstance(value, ast.Call):
            func = value.func
            is_asyncio_sleep = False
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                if isinstance(func.value, ast.Name) and func.value.id == "asyncio":
                    is_asyncio_sleep = True
            if is_asyncio_sleep:
                # Check if the argument is 0
                args = value.args
                is_zero = False
                if args:
                    arg = args[0]
                    if isinstance(arg, ast.Constant) and arg.value == 0:
                        is_zero = True
                if not is_zero and self._in_while == 0:
                    self.violations.append((self._filename, node.lineno))
        self.generic_visit(node)


def test_no_asyncio_sleep_for_coordination() -> None:
    """Fail if NEW asyncio.sleep calls outside polling loops are added."""
    checker = _SleepChecker()
    all_violations: list[tuple[str, int]] = []

    for test_file in _collect_test_files():
        violations = checker.check_file(test_file)
        all_violations.extend(violations)

    current_count = len(all_violations)

    if current_count > ASYNCIO_SLEEP_BASELINE:
        new_count = current_count - ASYNCIO_SLEEP_BASELINE
        msg_lines = [
            f"asyncio.sleep() violations increased: {current_count} "
            f"(baseline: {ASYNCIO_SLEEP_BASELINE}, +{new_count} new)",
            "",
            "New asyncio.sleep() calls outside polling loops:",
        ]
        for filename, lineno in all_violations[-new_count:]:
            msg_lines.append(f"  {filename}:{lineno}")
        msg_lines.append(
            "\nFix: use asyncio.sleep(0) for yielding, or wrap in a "
            "while-loop polling pattern."
        )
        pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# 2. No tight timing assertions
# ---------------------------------------------------------------------------

# Matches patterns like "assert elapsed < 5.0" in non-comment lines.
# Captures the numeric bound.
_TIGHT_TIMING_RE = re.compile(
    r"assert\s+\w*elapsed\w*\s*<\s*(\d+(?:\.\d+)?)"
)


def test_no_tight_timing_assertions() -> None:
    """Scan for timing assertions with bounds < 30 seconds."""
    violations: list[tuple[str, int, float]] = []

    for test_file in _collect_test_files():
        for lineno, line in enumerate(
            test_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            m = _TIGHT_TIMING_RE.search(line)
            if m:
                bound = float(m.group(1))
                if bound < 30.0:
                    violations.append((test_file.name, lineno, bound))

    if violations:
        msg_lines = [
            f"Found {len(violations)} tight timing assertion(s) (bound < 30s):"
        ]
        for filename, lineno, bound in violations:
            msg_lines.append(f"  {filename}:{lineno} — assert elapsed < {bound}")
        msg_lines.append(
            "\nFix: use generous bounds (>= 30s) for timing assertions. "
            "Performance benchmarks belong in a separate suite."
        )
        pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# 3. All tests have assertions
# ---------------------------------------------------------------------------


def _has_assertion(node: ast.AST) -> bool:
    """Check whether an AST subtree contains an assert-like construct."""
    for child in ast.walk(node):
        # assert statement
        if isinstance(child, ast.Assert):
            return True
        # pytest.raises context manager or call
        if isinstance(child, ast.Attribute) and child.attr == "raises":
            return True
        # unittest-style assertEqual, assertTrue, etc.
        if isinstance(child, ast.Attribute) and child.attr.startswith("assert"):
            return True
        # Direct call to assert_ helpers
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id.startswith("assert"):
                return True
    return False


def test_all_tests_have_assertions() -> None:
    """Fail if NEW test functions without assertions are added."""
    violations: list[tuple[str, str, int]] = []

    for test_file in _collect_test_files():
        # Skip ourselves to avoid meta-recursion issues
        if test_file.name == "test_quality_gate.py":
            continue
        tree = _parse_file(test_file)
        if tree is None:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    if not _has_assertion(node):
                        violations.append(
                            (test_file.name, node.name, node.lineno)
                        )

    current_count = len(violations)

    if current_count > ASSERTION_LESS_TEST_BASELINE:
        new_count = current_count - ASSERTION_LESS_TEST_BASELINE
        msg_lines = [
            f"Assertion-less test count increased: {current_count} "
            f"(baseline: {ASSERTION_LESS_TEST_BASELINE}, +{new_count} new)",
            "",
            "New test functions without assertions:",
        ]
        for filename, funcname, lineno in violations[-new_count:]:
            msg_lines.append(f"  {filename}:{lineno} — {funcname}")
        msg_lines.append(
            "\nFix: every test must have at least one assert statement, "
            "pytest.raises, or assertion helper call."
        )
        pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# 4. No bare MagicMock (advisory with baseline)
# ---------------------------------------------------------------------------


def _count_bare_magicmock(path: Path) -> list[tuple[str, int]]:
    """Find MagicMock() calls without spec= or spec_set= in a file."""
    tree = _parse_file(path)
    if tree is None:
        return []

    results: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name == "MagicMock":
            has_spec = any(
                kw.arg in ("spec", "spec_set") for kw in node.keywords
            )
            if not has_spec:
                results.append((path.name, node.lineno))
    return results


def test_no_bare_magicmock() -> None:
    """Fail if NEW bare MagicMock() instances are added above baseline."""
    all_instances: list[tuple[str, int]] = []

    for test_file in _collect_test_files():
        all_instances.extend(_count_bare_magicmock(test_file))

    current_count = len(all_instances)

    if current_count > BARE_MAGICMOCK_BASELINE:
        new_count = current_count - BARE_MAGICMOCK_BASELINE
        msg_lines = [
            f"Bare MagicMock() count increased: {current_count} "
            f"(baseline: {BARE_MAGICMOCK_BASELINE}, +{new_count} new)",
            "",
            "New bare MagicMock() instances (use spec= instead):",
        ]
        # Show all locations (can't diff, show all)
        for filename, lineno in all_instances[-new_count:]:
            msg_lines.append(f"  {filename}:{lineno}")
        msg_lines.append(
            "\nFix: use MagicMock(spec=RealClass) or create_autospec(RealClass)."
        )
        pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# 5. Property-based tests exist for Pydantic models
# ---------------------------------------------------------------------------


def _find_pydantic_models() -> list[str]:
    """List all BaseModel subclass names in config/ and checkpoint.py."""
    model_names: list[str] = []
    search_paths: list[Path] = []

    for f in CONFIG_DIR.iterdir():
        if f.suffix == ".py":
            search_paths.append(f)
    search_paths.append(CHECKPOINT_FILE)

    for path in search_paths:
        tree = _parse_file(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_name = ""
                    if isinstance(base, ast.Name):
                        base_name = base.id
                    elif isinstance(base, ast.Attribute):
                        base_name = base.attr
                    if base_name == "BaseModel":
                        model_names.append(node.name)

    return sorted(set(model_names))


def _find_given_tested_models() -> set[str]:
    """Find model names that appear in @given-decorated test functions."""
    tested: set[str] = set()

    for test_file in _collect_test_files():
        source = test_file.read_text(encoding="utf-8")
        # Quick check before parsing
        if "@given" not in source:
            continue
        tree = _parse_file(test_file)
        if tree is None:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_given = False
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    func = decorator.func
                    if isinstance(func, ast.Name) and func.id == "given":
                        has_given = True
                    elif isinstance(func, ast.Attribute) and func.attr == "given":
                        has_given = True
                elif isinstance(decorator, ast.Name) and decorator.id == "given":
                    has_given = True
                elif isinstance(decorator, ast.Attribute) and decorator.attr == "given":
                    has_given = True
            if has_given:
                # Extract model names from the function body and name
                func_source = ast.dump(node)
                for model_name in _find_pydantic_models():
                    if model_name.lower() in node.name.lower() or model_name in func_source:
                        tested.add(model_name)

    return tested


def test_property_based_tests_exist_for_pydantic_models() -> None:
    """Each Pydantic BaseModel should have a @given property-based test."""
    all_models = _find_pydantic_models()
    tested_models = _find_given_tested_models()
    untested = sorted(set(all_models) - tested_models)

    if untested:
        msg_lines = [
            f"Found {len(untested)} Pydantic model(s) without @given "
            f"property-based tests (out of {len(all_models)} total):"
        ]
        for model_name in untested:
            msg_lines.append(f"  - {model_name}")
        msg_lines.append(
            "\nFix: add hypothesis @given tests using strategies from "
            "conftest_adversarial.py."
        )
        pytest.fail("\n".join(msg_lines))
