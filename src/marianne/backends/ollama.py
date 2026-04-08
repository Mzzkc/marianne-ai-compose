"""Ollama backend for local model execution with MCP tool support.

Enables Marianne to use local Ollama models with translated MCP tool schemas.
Implements the Backend protocol with an agentic loop for multi-turn tool calling.

Architecture Decision: ADR-001 specifies MCPProxyService as in-process subprocess
manager, not a separate proxy process. This backend integrates with it for tool
execution during the agentic loop.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import httpx

from marianne.backends.base import Backend, ExecutionResult, HttpxClientMixin
from marianne.core.logging import get_logger
from marianne.utils.time import utc_now

if TYPE_CHECKING:
    from marianne.bridge.mcp_proxy import MCPProxyService, MCPTool, ToolResult
    from marianne.core.config import BackendConfig

_logger = get_logger("backend.ollama")


# ---------------------------------------------------------------------------
# Ollama API TypedDicts — typed contracts for request/response shapes
# ---------------------------------------------------------------------------


class OllamaFunctionDef(TypedDict):
    """Function definition within an Ollama tool."""

    name: str
    description: str
    parameters: dict[str, Any]


class OllamaToolDef(TypedDict):
    """Ollama tool definition (OpenAI-style function calling format)."""

    type: str  # Always "function"
    function: OllamaFunctionDef


class OllamaRawFunction(TypedDict, total=False):
    """Raw function payload inside a tool call from Ollama response."""

    name: str
    arguments: dict[str, Any] | str


class OllamaRawToolCall(TypedDict, total=False):
    """Raw tool call object from Ollama chat response."""

    id: str
    function: OllamaRawFunction


class OllamaChatResponse(TypedDict, total=False):
    """Top-level Ollama /api/chat response shape."""

    message: dict[str, Any]  # Contains role, content, tool_calls
    done: bool
    prompt_eval_count: int
    eval_count: int


class OllamaChatOptions(TypedDict, total=False):
    """Options sub-object for Ollama chat requests."""

    num_ctx: int


class OllamaChatRequest(TypedDict, total=False):
    """Request payload for Ollama /api/chat endpoint."""

    model: str
    messages: list[dict[str, Any]]
    tools: list[OllamaToolDef]
    stream: bool
    options: OllamaChatOptions
    keep_alive: str


@dataclass
class ToolCall:
    """Represents a tool call from the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class OllamaMessage:
    """A message in the Ollama conversation format."""

    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None  # For tool response messages

    def to_dict(self) -> dict[str, Any]:
        """Convert to Ollama API format."""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg


class OllamaBackend(HttpxClientMixin, Backend):
    """Backend for Ollama model execution with tool translation.

    Implements the Backend protocol for local Ollama models. Supports:
    - MCP tool schema translation to Ollama function format
    - Multi-turn agentic loop for tool calling
    - Health checks via /api/tags endpoint

    Example usage:
        backend = OllamaBackend(
            base_url="http://localhost:11434",
            model="llama3.1:8b",
        )
        result = await backend.execute("Write a hello world function")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        timeout: float = 300.0,
        num_ctx: int = 32768,
        keep_alive: str = "5m",
        max_tool_iterations: int = 10,
        mcp_proxy: MCPProxyService | None = None,
    ) -> None:
        """Initialize Ollama backend.

        Args:
            base_url: Ollama server URL (default: http://localhost:11434)
            model: Model to use (must support tool calling)
            timeout: Request timeout in seconds
            num_ctx: Context window size (recommend >= 32768 for Claude Code tools)
            keep_alive: Keep model loaded duration (e.g., "5m", "1h")
            max_tool_iterations: Maximum tool call iterations per execution
            mcp_proxy: Optional MCPProxyService for tool execution
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.max_tool_iterations = max_tool_iterations
        self.mcp_proxy = mcp_proxy
        self._working_directory: Path | None = None
        self._preamble: str | None = None
        self._prompt_extensions: list[str] = []

        # HTTP client lifecycle via shared mixin
        self._init_httpx_mixin(self.base_url, self.timeout, connect_timeout=10.0)

    @classmethod
    def from_config(cls, config: BackendConfig) -> OllamaBackend:
        """Create backend from configuration.

        Args:
            config: Backend configuration with ollama settings

        Returns:
            Configured OllamaBackend instance
        """
        ollama_cfg = config.ollama
        return cls(
            base_url=ollama_cfg.base_url,
            model=ollama_cfg.model,
            timeout=ollama_cfg.timeout_seconds,
            num_ctx=ollama_cfg.num_ctx,
            keep_alive=ollama_cfg.keep_alive,
            max_tool_iterations=ollama_cfg.max_tool_iterations,
        )

    @property
    def name(self) -> str:
        """Human-readable backend name."""
        return f"ollama:{self.model}"

    def set_preamble(self, preamble: str | None) -> None:
        """Set the dynamic preamble for the next execution."""
        self._preamble = preamble

    def set_prompt_extensions(self, extensions: list[str]) -> None:
        """Set prompt extensions for the next execution."""
        self._prompt_extensions = [e for e in extensions if e.strip()]

    async def execute(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt and return the result.

        Runs the agentic loop if tools are available via MCPProxyService,
        otherwise performs a simple completion.

        Args:
            prompt: The prompt to send to Ollama
            timeout_seconds: Per-call timeout override. Ollama uses the httpx
                client-level timeout from ``__init__``; per-call override is
                logged but not enforced.

        Returns:
            ExecutionResult with output and metadata
        """
        if timeout_seconds is not None:
            _logger.debug(
                "timeout_override_ignored",
                backend="ollama",
                requested=timeout_seconds,
                actual=self.timeout,
            )
        start_time = time.monotonic()
        started_at = utc_now()

        _logger.debug(
            "ollama_execute_start",
            model=self.model,
            prompt_length=len(prompt),
            has_mcp_proxy=self.mcp_proxy is not None,
        )

        try:
            # Build initial messages, injecting preamble/extensions
            if self._preamble or self._prompt_extensions:
                parts: list[str] = []
                if self._preamble:
                    parts.append(self._preamble)
                parts.append(prompt)
                if self._prompt_extensions:
                    parts.append("\n".join(self._prompt_extensions))
                messages = [OllamaMessage(role="user", content="\n".join(parts))]
            else:
                messages = [OllamaMessage(role="user", content=prompt)]

            # Get tools if MCP proxy is available
            tools: list[OllamaToolDef] = []
            mcp_degraded: str | None = None
            if self.mcp_proxy:
                try:
                    mcp_tools = await self.mcp_proxy.list_tools()
                    tools = self._translate_tools_to_ollama(mcp_tools)
                    _logger.debug("tools_loaded", tool_count=len(tools))
                except (OSError, ConnectionError, TimeoutError, httpx.HTTPError) as e:
                    mcp_degraded = (
                        f"[MCP DEGRADED] Tool loading failed ({type(e).__name__}: {e}); "
                        "running in non-agentic mode. "
                        "Check MCP server connectivity and configuration."
                    )
                    _logger.warning(
                        "mcp_tool_load_failed.falling_back_to_non_agentic",
                        error=str(e),
                        error_type=type(e).__name__,
                        hint=mcp_degraded,
                    )

            # Run agentic loop if tools available, else simple completion
            if tools:
                result = await self._agentic_loop(messages, tools)
            else:
                result = await self._simple_completion(messages)

            duration = time.monotonic() - start_time
            result.duration_seconds = duration
            result.started_at = started_at
            result.model = self.model

            # Surface MCP degradation in result so callers can detect it
            if mcp_degraded:
                result.stderr = (
                    f"{result.stderr}\n{mcp_degraded}" if result.stderr
                    else mcp_degraded
                )
                if not result.error_message:
                    result.error_message = mcp_degraded

            _logger.info(
                "ollama_execute_complete",
                success=result.success,
                duration_seconds=duration,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

            return result

        except httpx.ConnectError as e:
            duration = time.monotonic() - start_time
            _logger.error("ollama_connection_error", error=str(e))
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Connection error: {e}",
                duration_seconds=duration,
                started_at=started_at,
                error_type="connection",
                error_message=str(e),
                model=self.model,
            )

        except httpx.TimeoutException as e:
            duration = time.monotonic() - start_time
            _logger.error("ollama_timeout", error=str(e))
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Timeout: {e}",
                duration_seconds=duration,
                started_at=started_at,
                exit_reason="timeout",
                error_type="timeout",
                error_message=str(e),
                model=self.model,
            )

        except Exception as e:
            duration = time.monotonic() - start_time
            _logger.exception("ollama_execute_error", error=str(e))
            raise

    async def _simple_completion(
        self, messages: list[OllamaMessage]
    ) -> ExecutionResult:
        """Perform a simple completion without tools.

        Args:
            messages: Conversation messages

        Returns:
            ExecutionResult with model response
        """
        client = await self._get_client()

        payload = {
            "model": self.model,
            "messages": [msg.to_dict() for msg in messages],
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
            },
            "keep_alive": self.keep_alive,
        }

        response = await client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

        content = data.get("message", {}).get("content", "")
        input_tokens, output_tokens = self._estimate_tokens(data)

        return ExecutionResult(
            success=True,
            stdout=content,
            stderr="",
            duration_seconds=0.0,  # Will be set by caller
            exit_code=0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _agentic_loop(
        self,
        messages: list[OllamaMessage],
        tools: list[OllamaToolDef],
    ) -> ExecutionResult:
        """Run multi-turn agentic loop with tool calling.

        Continues until model returns done without tool_calls or max iterations.

        Args:
            messages: Initial conversation messages
            tools: Translated tool schemas for Ollama

        Returns:
            ExecutionResult with combined output from all turns
        """
        client = await self._get_client()
        total_input_tokens = 0
        total_output_tokens = 0
        all_outputs: list[str] = []
        iteration = 0

        for iteration in range(1, self.max_tool_iterations + 1):
            _logger.debug(
                "agentic_loop_iteration",
                iteration=iteration,
                message_count=len(messages),
            )

            payload = {
                "model": self.model,
                "messages": [msg.to_dict() for msg in messages],
                "tools": tools,
                "stream": False,
                "options": {
                    "num_ctx": self.num_ctx,
                },
                "keep_alive": self.keep_alive,
            }

            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

            # Track tokens
            input_est, output_est = self._estimate_tokens(data)
            total_input_tokens += input_est
            total_output_tokens += output_est

            # Parse response
            msg = data.get("message", {})
            content = msg.get("content", "")
            tool_calls_raw = msg.get("tool_calls", [])

            if content:
                all_outputs.append(content)

            # If no tool calls, we're done
            if not tool_calls_raw:
                _logger.debug("agentic_loop_complete", iterations=iteration)
                break

            # Parse and execute tool calls
            tool_calls = self._parse_tool_calls(tool_calls_raw)
            if not tool_calls:
                _logger.warning("no_valid_tool_calls_parsed")
                break

            # Add assistant message with tool calls to history
            messages.append(
                OllamaMessage(
                    role="assistant",
                    content=content,
                    tool_calls=tool_calls_raw,
                )
            )

            # Execute each tool call
            for tc in tool_calls:
                tool_result = await self._execute_tool_call(tc)
                result_text = self._format_tool_result(tool_result)

                # Add tool response to messages
                messages.append(
                    OllamaMessage(
                        role="tool",
                        content=result_text,
                        tool_call_id=tc.id,
                    )
                )
                _logger.debug(
                    "tool_executed",
                    tool_name=tc.name,
                    result_length=len(result_text),
                )
        else:
            # Loop completed without break — max iterations exhausted
            _logger.warning(
                "agentic_loop_max_iterations",
                max_iterations=self.max_tool_iterations,
            )

        combined_output = "\n".join(all_outputs)

        return ExecutionResult(
            success=True,
            stdout=combined_output,
            stderr="",
            duration_seconds=0.0,  # Will be set by caller
            exit_code=0,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

    async def _execute_tool_call(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call via MCP proxy.

        Args:
            tool_call: The tool call to execute

        Returns:
            ToolResult from MCP server
        """
        from marianne.bridge.mcp_proxy import ContentBlock, ToolResult

        if not self.mcp_proxy:
            return ToolResult(
                content=[ContentBlock(type="text", text="No MCP proxy configured")],
                is_error=True,
            )

        try:
            return await self.mcp_proxy.execute_tool(
                tool_call.name, tool_call.arguments
            )
        except (OSError, TimeoutError, ValueError, RuntimeError) as e:
            _logger.warning(
                "tool_execution_failed",
                tool_name=tool_call.name,
                error=str(e),
                exc_info=True,
            )
            return ToolResult(
                content=[ContentBlock(type="text", text=f"Error: {e}")],
                is_error=True,
            )

    def _format_tool_result(self, result: ToolResult) -> str:
        """Format tool result for message content.

        Args:
            result: Tool execution result

        Returns:
            Formatted string for conversation
        """
        parts = []
        for block in result.content:
            if block.type == "text" and block.text:
                parts.append(block.text)
            elif block.type == "image" and block.data:
                parts.append(f"[Image: {block.mime_type or 'image'}]")
            elif block.type == "resource" and block.uri:
                parts.append(f"[Resource: {block.uri}]")

        text = "\n".join(parts)
        if result.is_error:
            return f"[Tool Error]\n{text}"
        return text

    def _translate_tools_to_ollama(self, mcp_tools: list[MCPTool]) -> list[OllamaToolDef]:
        """Translate MCP tool schemas to Ollama function format.

        Ollama uses OpenAI-style function calling format:
        {
            "type": "function",
            "function": {
                "name": "tool_name",
                "description": "...",
                "parameters": { ... json schema ... }
            }
        }

        Args:
            mcp_tools: List of MCPTool objects from proxy

        Returns:
            List of Ollama-compatible tool definitions
        """
        ollama_tools: list[OllamaToolDef] = []
        for tool in mcp_tools:
            ollama_tool: OllamaToolDef = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            }
            ollama_tools.append(ollama_tool)
        return ollama_tools

    def _parse_tool_calls(self, raw_calls: list[OllamaRawToolCall]) -> list[ToolCall]:
        """Parse tool calls from Ollama response.

        Handles both standard format and edge cases like missing IDs.

        Args:
            raw_calls: Raw tool_calls from Ollama response

        Returns:
            List of parsed ToolCall objects
        """
        tool_calls = []
        for idx, raw in enumerate(raw_calls):
            try:
                # Ollama uses function.name/arguments format
                func = raw.get("function", {})
                name = func.get("name")
                if not name:
                    continue

                # Parse arguments - may be string or dict
                args = func.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = self._extract_json_from_text(args)
                        if not args:
                            # Skip tool call with empty args from failed extraction
                            # to avoid downstream errors from missing arguments (Q024)
                            _logger.warning(
                                "tool_call_skipped_empty_args",
                                tool_name=name,
                                index=idx,
                            )
                            continue

                # Generate ID if not provided (Ollama doesn't always provide)
                call_id = raw.get("id") or f"call_{uuid.uuid4().hex[:8]}"

                tool_calls.append(ToolCall(id=call_id, name=name, arguments=args))

            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
                _logger.warning(
                    "tool_call_parse_error", error=str(e), index=idx, raw=str(raw),
                )

        return tool_calls

    def _extract_json_from_text(self, text: str) -> dict[str, Any]:
        """Attempt to extract JSON from text that may have markdown formatting.

        Tries code-block extraction first, then raw JSON object detection.
        Logs a warning on parse failures for diagnostics.

        Args:
            text: Text that may contain JSON.

        Returns:
            Extracted JSON dict, or empty dict if no valid JSON found.
        """
        # Try to extract from code blocks
        code_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if code_block:
            try:
                result: dict[str, Any] = json.loads(code_block.group(1))
                return result
            except json.JSONDecodeError as e:
                _logger.debug("json_code_block_parse_failed", error=str(e))

        # Try to find JSON object directly
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            try:
                parsed: dict[str, Any] = json.loads(json_match.group(0))
                return parsed
            except json.JSONDecodeError as e:
                _logger.debug("json_direct_parse_failed", error=str(e))

        if text.strip():
            _logger.warning(
                "json_extraction_failed",
                text_length=len(text),
                text_preview=text[:200],
            )
        return {}

    def _estimate_tokens(self, response: OllamaChatResponse) -> tuple[int, int]:
        """Estimate token usage from Ollama response.

        Ollama provides eval_count and prompt_eval_count in some responses.
        Falls back to character-based estimation.

        Args:
            response: Ollama API response

        Returns:
            Tuple of (input_tokens, output_tokens)
        """
        # Try to get actual counts from response
        input_tokens = response.get("prompt_eval_count", 0)
        output_tokens = response.get("eval_count", 0)

        # Fallback: estimate from content length (~4 chars per token)
        if not output_tokens:
            content = response.get("message", {}).get("content", "")
            output_tokens = len(content) // 4

        return input_tokens, output_tokens

    async def health_check(self) -> bool:
        """Check if Ollama is available and model is loaded.

        Uses /api/tags to verify Ollama is running and configured model exists.

        Returns:
            True if healthy, False otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get("/api/tags", timeout=10.0)

            if response.status_code != 200:
                _logger.warning(
                    "ollama_health_check_failed",
                    status_code=response.status_code,
                )
                return False

            data = response.json()
            models = data.get("models", [])

            # Check if our model is available
            model_base = self.model.split(":")[0]
            available = any(
                entry.get("name", "").startswith(model_base)
                for entry in models
            )

            if not available:
                _logger.warning(
                    "ollama_model_not_found",
                    model=self.model,
                    available_models=[entry.get("name") for entry in models],
                )

            return available

        except (httpx.HTTPError, OSError, ValueError) as e:
            _logger.warning("ollama_health_check_error", error=str(e), exc_info=True)
            return False

    async def close(self) -> None:
        """Close HTTP client and release resources."""
        await self._close_httpx_client()

