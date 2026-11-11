"""What a sheet's prompt and harness receive about memory.

Two pure renderers. Neither emits access vocabulary, a principal, a scope or a
filesystem path: the musician learns the six tools and the attachment names,
and the harness gets a command line that names one socket. The socket is the
session, so nothing here can carry authority.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

from marianne.memory.resolve import ResolvedBinding
from marianne.memory.shim import DEFAULT_BUDGET_SECONDS

TOOL_LINES: tuple[tuple[str, str], ...] = (
    ("declare", "record a fact, with the evidence it rests on"),
    ("recall", "search memory; each answer names its source and how to resume a cut-off page"),
    ("verify", "re-check a fact against its evidence before relying on it"),
    ("bind", "tie a verified fact to the action that depends on it"),
    ("forget", "retire a fact, giving a reason"),
    ("fetch", "read a byte range of an oversized item that recall pointed to"),
)

SHIM_MODULE = "marianne.memory.shim"


def mcp_entry_key(binding: ResolvedBinding) -> str:
    """The MCP server key for one attachment's shim."""
    return f"memory-{binding.name}"


def render_manifest(bindings: Sequence[ResolvedBinding]) -> str:
    """The markdown block for a sheet's prompt; empty when no attachment is active."""
    if not bindings:
        return ""
    lines = ["## Memory Available This Phase", ""]
    lines.append("Tools (the same six on every memory server):")
    lines += [f"- `{name}`: {desc}" for name, desc in TOOL_LINES]
    lines.append("")
    lines.append("Memory servers:")
    lines += [f"- {b.name} (server `{mcp_entry_key(b)}`)" for b in bindings]
    lines.append("")
    return "\n".join(lines)


def mcp_config_entry(
    binding: ResolvedBinding, socket_path: Any, *,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
) -> tuple[str, dict[str, Any]]:
    """``(entry key, entry)`` launching the shim against one socket; nothing else."""
    return mcp_entry_key(binding), {
        "command": sys.executable,
        "args": [
            "-m", SHIM_MODULE, "--socket", str(socket_path),
            "--memory-budget-seconds", str(float(budget_seconds)),
        ],
    }


__all__ = ["TOOL_LINES", "mcp_config_entry", "mcp_entry_key", "render_manifest"]
