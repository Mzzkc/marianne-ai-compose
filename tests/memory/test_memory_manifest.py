"""The musician-facing manifest and the harness MCP config entry."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from marianne.core.config.memory import MemoryConfig
from marianne.memory.manifest import mcp_config_entry, mcp_entry_key, render_manifest
from marianne.memory.resolve import SheetPhase, resolve_memory
from marianne.memory.shim import TOOLS

USER = "zelda-the-tester"
SCOPE = "project/secret-demo"
DENY = [
    "principal", "grant", "permission", "access", "admin", "export", "scope", "session",
    "credential", "secret-demo", "person:", USER, "all", "inherit", ".sock", "/tmp",
]


def bindings() -> list:  # type: ignore[type-arg]
    c = MemoryConfig.model_validate({"backends": {
        "notes": {"system": "files", "scope": SCOPE},
        "shared": {"system": "files", "scope": "team/ops", "access": "grant:ops@3"},
    }})
    return resolve_memory(c, SheetPhase("work", 1), user=USER, run_dir=Path("/tmp/mr"))


def test_manifest_lists_six_tools_and_attachment_names_only() -> None:
    text = render_manifest(bindings())
    for tool in TOOLS:
        assert text.count(f"`{tool}`") == 1
    assert "notes" in text and "shared" in text
    lowered = text.lower()
    # "all" is a substring of ordinary words; check the others plainly
    for term in DENY:
        if term == "all":
            continue
        assert term.lower() not in lowered, term


def test_manifest_is_empty_without_bindings() -> None:
    assert render_manifest([]) == ""


def test_config_entry_shape_and_deny_list() -> None:
    b = bindings()[0]
    sock = b.socket_path("job-1", "d-1")
    key, entry = mcp_config_entry(b, sock, budget_seconds=7.5)
    assert key == mcp_entry_key(b) == "memory-notes"
    assert entry == {
        "command": sys.executable,
        "args": [
            "-m", "marianne.memory.shim", "--socket", str(sock),
            "--memory-budget-seconds", "7.5",
        ],
    }
    assert set(entry) == {"command", "args"}  # no env, no cwd
    blob = json.dumps({key: entry}).lower()
    for term in ("principal", "grant", "permission", "access", "admin", "export", "credential",
                 "session", USER, SCOPE, "person:", "token"):
        assert term.lower() not in blob, term


def test_config_entry_default_budget() -> None:
    b = bindings()[0]
    _, entry = mcp_config_entry(b, b.socket_path("j", "d"))
    assert entry["args"][-2:] == ["--memory-budget-seconds", "10.0"]
