"""Execution backends — instruments that musicians play.

Phase 4 of the backend atlas retired the native ``ClaudeCliBackend``
module from this package. ``claude_cli`` is now served by
``PluginCliBackend`` with the ``claude-code.yaml`` instrument profile.
Only the two documented exceptions (``AnthropicApiBackend`` and
``OllamaBackend``) remain as native Python backend modules under
``src/marianne/backends/``.

The legacy ``ClaudeCliBackend`` class has been relocated out of this
package to
``marianne.execution.instruments.claude_cli_legacy.ClaudeCliBackend``
as a compatibility shim for tests that exercise Claude CLI-specific
internals (``_build_command``, preamble, rate-limit classifier,
``_safe_killpg`` guard, etc.). It is NOT a native backend under
``src/marianne/backends/`` and MUST NOT be imported from here.
"""

from marianne.backends.anthropic_api import AnthropicApiBackend
from marianne.backends.base import Backend, ExecutionResult
from marianne.backends.ollama import OllamaBackend

__all__ = [
    "Backend",
    "ExecutionResult",
    "AnthropicApiBackend",
    "OllamaBackend",
]
