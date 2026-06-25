"""Technique router — classifies agent output and routes to handlers.

After a musician executes a sheet through a backend, the technique router
inspects the output to determine what kind of result was produced and
routes it to the appropriate handler:

- **prose** — standard text output, no special routing needed
- **code_block** — executable code that should run in a sandbox
- **tool_call** — MCP tool invocation request, preserved for downstream routing
- **a2a_request** — inter-agent task delegation, route through event bus

For MCP-native instruments (claude-code, gemini-cli), MCP access can happen
through the instrument's native MCP support when the conductor supplies a valid
MCP config. The router itself only classifies and extracts requests; it does
not execute MCP calls.

Classification uses pattern matching on the output text — no LLM calls.
The patterns are conservative: when in doubt, classify as prose (the safe
default that doesn't trigger any special routing).

See: design spec sections 8.2 (Code Mode) and 8.3 (Technique Router)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from marianne.core.logging import get_logger

_logger = get_logger("daemon.technique_router")


class OutputKind(str, Enum):
    """Classification of agent output for routing decisions."""

    PROSE = "prose"
    """Standard text output — no special routing."""

    CODE_BLOCK = "code_block"
    """Executable code in markdown fences — route to sandbox."""

    TOOL_CALL = "tool_call"
    """MCP tool invocation — route to shared MCP pool."""

    A2A_REQUEST = "a2a_request"
    """Inter-agent task delegation — route through event bus."""


@dataclass(frozen=True)
class ClassifiedOutput:
    """Result of classifying agent output.

    Contains the classification and extracted content for routing.
    For code blocks, ``code_blocks`` contains the extracted code.
    For tool calls, ``tool_calls`` contains the parsed invocations.
    For A2A, ``a2a_requests`` contains parsed delegation requests.
    """

    kind: OutputKind
    raw_output: str
    code_blocks: list[CodeBlock] = field(default_factory=list)
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    a2a_requests: list[A2ARoutingRequest] = field(default_factory=list)


@dataclass(frozen=True)
class CodeBlock:
    """An extracted code block from agent output.

    Attributes:
        language: The language tag from the code fence (e.g., "python").
        code: The code content between the fences.
    """

    language: str
    code: str


@dataclass(frozen=True)
class ToolCallRequest:
    """A parsed MCP tool call from agent output.

    Attributes:
        server: The MCP server name (e.g., "github", "filesystem").
        method: The method to invoke (e.g., "list_issues", "read_file").
        arguments: Parsed arguments as key-value pairs.
    """

    server: str
    method: str
    arguments: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class A2ARoutingRequest:
    """A parsed A2A task delegation from agent output.

    Attributes:
        target_agent: The agent to delegate to.
        task_description: What needs to be done.
        context: Additional context for the task.
    """

    target_agent: str
    task_description: str
    context: dict[str, str] = field(default_factory=dict)


# =============================================================================
# Patterns for classification
# =============================================================================

# Markdown code fences: ```language\n...\n```
_CODE_FENCE_PATTERN = re.compile(
    r"```(\w*)\s*\n(.*?)```",
    re.DOTALL,
)

# Executable code languages (conservative list)
_EXECUTABLE_LANGUAGES = frozenset({
    "python", "py",
    "javascript", "js",
    "typescript", "ts",
    "bash", "sh", "shell",
})

# A2A delegation pattern: delegate_to(agent_name, "description")
# or structured: @delegate agent_name: description
_A2A_DELEGATE_PATTERN = re.compile(
    r"@delegate\s+(\w+)\s*:\s*(.+?)(?:\n|$)",
    re.MULTILINE,
)

# Tool call pattern: @tool server.method(args)
# Matches patterns like: @tool github.list_issues(state="open")
_TOOL_CALL_PATTERN = re.compile(
    r"@tool\s+(\w+)\.(\w+)\(([^)]*)\)",
    re.MULTILINE,
)

# Alternative tool call: JSON-like structured output
# {"tool": "server.method", "arguments": {...}}
_JSON_TOOL_PATTERN = re.compile(
    r'\{\s*"tool"\s*:\s*"(\w+)\.(\w+)"',
    re.MULTILINE,
)


class TechniqueRouter:
    """Classifies agent output and determines routing.

    The router inspects output text and applies pattern matching to
    determine whether the output contains executable code, tool calls,
    A2A requests, or is plain prose.

    Classification priority (first match wins):
    1. A2A requests — checked first because they're explicit directives
    2. Tool calls — explicit tool invocations
    3. Code blocks — executable code in fences
    4. Prose — default fallback

    Usage::

        router = TechniqueRouter()
        result = router.classify(agent_output)

        match result.kind:
            case OutputKind.CODE_BLOCK:
                for block in result.code_blocks:
                    sandbox.execute(block.code)
            case OutputKind.TOOL_CALL:
                for call in result.tool_calls:
                    downstream_route(call.server, call.method, call.arguments)
            case OutputKind.A2A_REQUEST:
                for req in result.a2a_requests:
                    event_bus.submit_a2a_task(req)
            case OutputKind.PROSE:
                pass  # standard output
    """

    def classify(self, output: str) -> ClassifiedOutput:
        """Classify agent output for routing.

        Args:
            output: Raw text output from the musician/backend.

        Returns:
            ClassifiedOutput with kind and extracted content.
        """
        if not output or not output.strip():
            return ClassifiedOutput(kind=OutputKind.PROSE, raw_output=output or "")

        # Check for A2A delegation requests
        a2a_requests = self._extract_a2a_requests(output)
        if a2a_requests:
            _logger.debug(
                "technique_router.classified",
                extra={"kind": "a2a_request", "count": len(a2a_requests)},
            )
            return ClassifiedOutput(
                kind=OutputKind.A2A_REQUEST,
                raw_output=output,
                a2a_requests=a2a_requests,
            )

        # Check for tool calls
        tool_calls = self._extract_tool_calls(output)
        if tool_calls:
            _logger.debug(
                "technique_router.classified",
                extra={"kind": "tool_call", "count": len(tool_calls)},
            )
            return ClassifiedOutput(
                kind=OutputKind.TOOL_CALL,
                raw_output=output,
                tool_calls=tool_calls,
            )

        # Check for executable code blocks
        code_blocks = self._extract_code_blocks(output)
        if code_blocks:
            _logger.debug(
                "technique_router.classified",
                extra={"kind": "code_block", "count": len(code_blocks)},
            )
            return ClassifiedOutput(
                kind=OutputKind.CODE_BLOCK,
                raw_output=output,
                code_blocks=code_blocks,
            )

        # Default: prose
        return ClassifiedOutput(kind=OutputKind.PROSE, raw_output=output)

    def _extract_code_blocks(self, output: str) -> list[CodeBlock]:
        """Extract executable code blocks from markdown fences.

        Only returns blocks with recognized executable language tags.
        Blocks without a language tag or with non-executable languages
        (markdown, yaml, json, etc.) are treated as documentation, not code.
        """
        blocks: list[CodeBlock] = []
        for match in _CODE_FENCE_PATTERN.finditer(output):
            lang = match.group(1).lower().strip()
            code = match.group(2).strip()
            if lang in _EXECUTABLE_LANGUAGES and code:
                blocks.append(CodeBlock(language=lang, code=code))
        return blocks

    def _extract_tool_calls(self, output: str) -> list[ToolCallRequest]:
        """Extract tool call invocations from agent output.

        Recognizes two patterns:
        1. @tool server.method(arg1="val1", arg2="val2")
        2. {"tool": "server.method", ...} (JSON-like)
        """
        calls: list[ToolCallRequest] = []

        # Pattern 1: @tool directives
        for match in _TOOL_CALL_PATTERN.finditer(output):
            server = match.group(1)
            method = match.group(2)
            args_str = match.group(3).strip()
            arguments = self._parse_tool_args(args_str)
            calls.append(ToolCallRequest(
                server=server, method=method, arguments=arguments,
            ))

        # Pattern 2: JSON-like tool calls (only if no @tool found)
        if not calls:
            for match in _JSON_TOOL_PATTERN.finditer(output):
                server = match.group(1)
                method = match.group(2)
                calls.append(ToolCallRequest(
                    server=server, method=method,
                ))

        return calls

    def _extract_a2a_requests(self, output: str) -> list[A2ARoutingRequest]:
        """Extract A2A delegation requests from agent output.

        Recognizes: @delegate agent_name: task description
        """
        requests: list[A2ARoutingRequest] = []
        for match in _A2A_DELEGATE_PATTERN.finditer(output):
            target = match.group(1)
            description = match.group(2).strip()
            requests.append(A2ARoutingRequest(
                target_agent=target,
                task_description=description,
            ))
        return requests

    @staticmethod
    def _parse_tool_args(args_str: str) -> dict[str, str]:
        """Parse simple key=value argument strings.

        Handles: arg1="val1", arg2="val2"
        Not a full parser — covers the common case for free-model output.
        """
        if not args_str:
            return {}

        args: dict[str, str] = {}
        # Simple key="value" or key='value' pairs
        for pair_match in re.finditer(
            r'(\w+)\s*=\s*["\']([^"\']*)["\']', args_str
        ):
            args[pair_match.group(1)] = pair_match.group(2)
        return args
