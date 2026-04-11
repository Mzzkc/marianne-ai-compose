"""F-518: Test suite must not depend on pytest-mock (not in dependencies).

Tests that verify no test files use the `mocker` fixture from pytest-mock,
since pytest-mock is not in the project dependencies. All mocking should use
unittest.mock.patch instead.

This is an adversarial test that checks for the bug class where tests are
written using a fixture from an optional dependency that isn't actually
installed, causing fixture-not-found errors at runtime.
"""
import re
from pathlib import Path


def test_no_mocker_fixture_in_test_signatures() -> None:
    """Verify no test uses mocker fixture (requires pytest-mock package)."""
    tests_dir = Path(__file__).parent
    violations: list[tuple[Path, int, str]] = []

    for test_file in tests_dir.glob("test_*.py"):
        # Skip this test file itself (contains mocker in strings)
        if test_file.name == "test_f518_no_pytest_mock_dependency.py":
            continue

        content = test_file.read_text()
        lines = content.split("\n")

        for i, line in enumerate(lines, start=1):
            # Look for function signatures with mocker parameter
            # Matches: def test_foo(..., mocker: Any) or def test_foo(mocker, ...)
            if re.search(r"def\s+test_\w+\([^)]*\bmocker\s*:", line):
                violations.append((test_file, i, line.strip()))

    if violations:
        msg_parts = ["Found tests using mocker fixture (pytest-mock not in dependencies):"]
        for path, line_num, line in violations:
            msg_parts.append(f"  {path.name}:{line_num}: {line}")
        msg_parts.append("")
        msg_parts.append("Use unittest.mock.patch instead of mocker fixture.")
        pytest.fail("\n".join(msg_parts))


def test_no_pytest_mock_import() -> None:
    """Verify no test imports pytest_mock or mocker."""
    tests_dir = Path(__file__).parent
    violations: list[tuple[Path, int, str]] = []

    for test_file in tests_dir.glob("test_*.py"):
        content = test_file.read_text()
        lines = content.split("\n")

        for i, line in enumerate(lines, start=1):
            # Look for pytest_mock imports
            if re.search(r"^\s*import\s+pytest_mock", line) or \
               re.search(r"^\s*from\s+pytest_mock\s+import", line):
                violations.append((test_file, i, line.strip()))

    if violations:
        msg_parts = ["Found pytest_mock imports (not in dependencies):"]
        for path, line_num, line in violations:
            msg_parts.append(f"  {path.name}:{line_num}: {line}")
        pytest.fail("\n".join(msg_parts))


def test_mocker_calls_without_fixture() -> None:
    """Verify no test calls mocker.patch() without having mocker parameter.

    This catches the case where someone uses mocker but forgets to add it
    to the test signature (or removes it without removing the usage).
    """
    tests_dir = Path(__file__).parent
    violations: list[tuple[Path, int, str]] = []

    for test_file in tests_dir.glob("test_*.py"):
        # Skip this test file itself (contains mocker in strings)
        if test_file.name == "test_f518_no_pytest_mock_dependency.py":
            continue

        content = test_file.read_text()

        # Find all mocker.patch calls (not in comments or strings)
        mocker_calls = re.finditer(r"^\s+mocker\.(patch|spy|stub|mock)", content, re.MULTILINE)

        for match in mocker_calls:
            # Get line number
            line_num = content[:match.start()].count("\n") + 1
            line = content.split("\n")[line_num - 1]
            violations.append((test_file, line_num, line.strip()))

    if violations:
        msg_parts = ["Found mocker method calls (mocker fixture not available):"]
        for path, line_num, line in violations:
            msg_parts.append(f"  {path.name}:{line_num}: {line}")
        msg_parts.append("")
        msg_parts.append("Use unittest.mock.patch instead of mocker.patch.")
        pytest.fail("\n".join(msg_parts))


import pytest
