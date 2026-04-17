"""Compose command — ``mzt compose``.

The compose system is not yet implemented. Score generation from agent
configs is handled by the standalone composition compiler via ``mzt compile``.
"""

from __future__ import annotations

import typer

from ..output import console


def compose() -> None:
    """Compile semantic agent definitions into scores.

    The compose system is not yet implemented. Use ``mzt compile`` for
    score generation from agent configs.
    """
    console.print(
        "\n[yellow]The compose system is not yet implemented.[/yellow]\n"
        "Use [bold]mzt compile[/bold] for score generation from agent configs.\n"
    )
    raise typer.Exit(0)


__all__ = ["compose"]
