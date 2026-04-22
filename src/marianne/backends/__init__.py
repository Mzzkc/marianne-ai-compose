"""Execution backends — instruments that musicians play."""

from marianne.backends.anthropic_api import AnthropicApiBackend
from marianne.backends.base import Backend, ExecutionResult
from marianne.backends.claude_cli import ClaudeCliBackend
from marianne.backends.ollama import OllamaBackend
from marianne.backends.openrouter import OpenRouterBackend

__all__ = [
    "Backend",
    "ExecutionResult",
    "ClaudeCliBackend",
    "AnthropicApiBackend",
    "OllamaBackend",
    "OpenRouterBackend",
]
