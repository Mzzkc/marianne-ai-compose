"""Instrument execution backends for Marianne.

This package contains config-driven backends that execute prompts through
CLI instruments defined by InstrumentProfile YAML files.

The PluginCliBackend is the generic execution engine — it reads a CliProfile
and builds commands, parses output, and classifies errors for any CLI tool
without requiring instrument-specific Python code.
"""

from marianne.execution.instruments.cli_backend import PluginCliBackend

__all__ = ["PluginCliBackend"]
