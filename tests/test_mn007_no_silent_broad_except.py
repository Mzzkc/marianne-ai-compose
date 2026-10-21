"""MN-007 syntax guard for silent broad handlers.

Recognise direct diagnostics, propagation, returned exceptions, or a local
BLE001 justification. This is a conservative syntax check, not control-flow
proof: unfamiliar reporting boundaries need an explicit, reviewed reason.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
LOG_OBJECTS = {"logger", "_logger", "log", "_log", "logging"}
REPORT_HELPERS = {"output_error", "output_warning", "_send_dispatch_failure", "_record_failure"}
DEFERRED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(
        (isinstance(t, ast.Name) and t.id in {"Exception", "BaseException"})
        or (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
            and t.value.id == "builtins" and t.attr in {"Exception", "BaseException"})
        for t in types
    )


def _has_noqa_reason(handler: ast.ExceptHandler, lines: list[str]) -> bool:
    line = lines[handler.lineno - 1]
    match = re.search(r"#\s*noqa:\s*BLE001\s*(?:—|--|:)\s*(.+)", line)
    return bool(match and len(re.findall(r"[A-Za-z]+", match.group(1))) >= 2)


def _executed(node: ast.AST) -> Iterator[ast.AST]:
    # Definitions/lambdas and a nested catch do not diagnose the outer error.
    if isinstance(node, (*DEFERRED, ast.ExceptHandler)):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _executed(child)


def _uses_exception(node: ast.AST, name: str | None) -> bool:
    return name is not None and any(
        isinstance(child, ast.Name) and child.id == name for child in _executed(node)
    )


def _reports(call: ast.Call, name: str | None) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in REPORT_HELPERS or (
            func.id == "print" and _uses_exception(call, name)
        )
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in REPORT_HELPERS:
        return True
    receiver = func.value
    receiver_name = (
        receiver.id if isinstance(receiver, ast.Name)
        else receiver.attr if isinstance(receiver, ast.Attribute) else ""
    )
    return receiver_name in LOG_OBJECTS and func.attr in LOG_METHODS


def scan_file(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    silent: list[str] = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not _is_broad(handler):
            continue
        if _has_noqa_reason(handler, lines):
            continue
        nodes = [node for stmt in handler.body for node in _executed(stmt)]
        if any(
            isinstance(node, ast.Raise)
            or (isinstance(node, ast.Call) and _reports(node, handler.name))
            or (isinstance(node, (ast.Return, ast.Yield)) and _uses_exception(node, handler.name))
            for node in nodes
        ):
            continue
        silent.append(f"{path}:{handler.lineno}")
    return silent


@pytest.mark.parametrize("handler,silent", [
    ("except Exception:\n    return []", True),
    ('except:\n    return []', True),
    ('except builtins.Exception:\n    return []', True),
    ('except builtins.BaseException:\n    return []', True),
    ('except (ValueError, builtins.Exception):\n    return []', True),
    ('except Exception:\n    catalog.clear()\n    return []', True),
    ('except Exception:\n    return get_default_info()', True),
    ('except Exception as exc:\n    str(exc)\n    return []', True),
    ('except Exception:\n    def unused():\n        raise RuntimeError()\n    return []', True),
    ('except Exception:\n    class Unused:\n        def f(self):\n'
     '            logger.exception("failed")\n    return []', True),
    ('except Exception:\n    callback = lambda: logger.exception("failed")\n    return []', True),
    ('except Exception as exc:\n    error = str(exc)\n    return []', True),
    ('except Exception:\n    catalog.info()\n    return []', True),
    ('except Exception:  # noqa: E501 -- long handler line\n    return []', True),
    ('except Exception as exc:\n    print(exc)\n    return []', False),
    ('except Exception:\n    output_error("failed")\n    return []', False),
    ("except (ValueError, Exception):\n    pass", True),
    ("except BaseException:\n    return None", True),
    ("except Exception:  # noqa: BLE001\n    pass", True),
    ("except Exception as exc:\n    return []", True),
    ("except ValueError:\n    return []", False),
    ("except Exception:\n    raise", False),
    ("except Exception:\n    logger.warning('failed', exc_info=True)\n    return []", False),
    ("except Exception as exc:\n    return {'error': str(exc)}", False),
    ("except Exception:  # noqa: BLE001 — optional teardown failure\n    pass", False),
])
def test_scanner_distinguishes_diagnostics_from_silence(tmp_path, handler, silent):
    path = tmp_path / "example.py"
    source = "def example():\n    try:\n        operation()\n"
    source += "\n".join("    " + line for line in handler.splitlines()) + "\n"
    path.write_text(source)
    assert bool(scan_file(path)) is silent


def test_no_silent_broad_except_in_source():
    root = Path(__file__).resolve().parents[1] / "src" / "marianne"
    paths = sorted(root.rglob("*.py"))
    assert paths, f"No source files found at {root}"
    silent = [location for path in paths for location in scan_file(path)]
    assert not silent, (
        "Broad handlers need diagnostics or a justified fallback:\n" + "\n".join(silent)
    )
