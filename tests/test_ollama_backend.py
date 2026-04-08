"""Tests for OllamaBackend.

Verifies Ollama backend functionality including:
- Basic prompt execution
- Tool translation (MCP -> Ollama format)
- Agentic loop with tool calls
- Error handling (connection, timeout, parse errors)
- Health checks
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from marianne.backends.ollama import OllamaBackend, OllamaMessage

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_httpx_response():
    """Factory for mock httpx responses."""

    def _create(
        json_data: dict,
        status_code: int = 200,
    ) -> MagicMock:
        response = MagicMock(spec=httpx.Response)
        response.status_code = status_code
        response.json.return_value = json_data
        response.raise_for_status = MagicMock()
        if status_code >= 400:
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Error", request=MagicMock(), response=response
            )
        return response

    return _create


@pytest.fixture
def sample_ollama_response():
    """Sample Ollama API response without tool calls."""
    return {
        "model": "llama3.1:8b",
        "message": {
            "role": "assistant",
            "content": "Hello! I'm happy to help you today.",
        },
        "done": True,
        "eval_count": 15,
        "prompt_eval_count": 10,
    }


@pytest.fixture
def sample_ollama_tool_response():
    """Sample Ollama API response with tool calls."""
    return {
        "model": "llama3.1:8b",
        "message": {
            "role": "assistant",
            "content": "I'll read that file for you.",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "/tmp/test.txt"}),
                    }
                }
            ],
        },
        "done": False,
        "eval_count": 20,
        "prompt_eval_count": 15,
    }


@pytest.fixture
def sample_ollama_final_response():
    """Sample Ollama API final response after tool execution."""
    return {
        "model": "llama3.1:8b",
        "message": {
            "role": "assistant",
            "content": "The file contains: Hello World",
        },
        "done": True,
        "eval_count": 10,
        "prompt_eval_count": 50,
    }


@pytest.fixture
def sample_mcp_tools():
    """Sample MCP tools for testing translation."""
    from marianne.bridge.mcp_proxy import MCPTool

    return [
        MCPTool(
            name="read_file",
            description="Read contents of a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                },
                "required": ["path"],
            },
            server_name="filesystem",
        ),
        MCPTool(
            name="write_file",
            description="Write contents to a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            server_name="filesystem",
        ),
    ]


@pytest.fixture
def ollama_backend():
    """Create OllamaBackend with default settings."""
    return OllamaBackend(
        base_url="http://localhost:11434",
        model="llama3.1:8b",
        timeout=30.0,
        num_ctx=32768,
    )


# =============================================================================
# Test OllamaMessage
# =============================================================================


class TestOllamaMessage:
    """Tests for OllamaMessage dataclass."""

    def test_to_dict_basic(self):
        """Test basic message serialization."""
        msg = OllamaMessage(role="user", content="Hello")
        assert msg.to_dict() == {"role": "user", "content": "Hello"}

    def test_to_dict_with_tool_calls(self):
        """Test message serialization with tool calls."""
        msg = OllamaMessage(
            role="assistant",
            content="I'll help",
            tool_calls=[{"function": {"name": "test"}}],
        )
        result = msg.to_dict()
        assert result["tool_calls"] == [{"function": {"name": "test"}}]

    def test_to_dict_with_tool_call_id(self):
        """Test tool response message serialization."""
        msg = OllamaMessage(
            role="tool",
            content="Result",
            tool_call_id="call_123",
        )
        result = msg.to_dict()
        assert result["tool_call_id"] == "call_123"


# =============================================================================
# Test Tool Translation
# =============================================================================


class TestToolTranslation:
    """Tests for MCP to Ollama tool schema translation."""

    def test_translate_tools_to_ollama(self, ollama_backend, sample_mcp_tools):
        """Test translation of MCP tools to Ollama format."""
        translated = ollama_backend._translate_tools_to_ollama(sample_mcp_tools)

        assert len(translated) == 2

        # Check first tool
        tool1 = translated[0]
        assert tool1["type"] == "function"
        assert tool1["function"]["name"] == "read_file"
        assert tool1["function"]["description"] == "Read contents of a file"
        assert "properties" in tool1["function"]["parameters"]

    def test_translate_empty_tools(self, ollama_backend):
        """Test translation of empty tool list."""
        translated = ollama_backend._translate_tools_to_ollama([])
        assert translated == []


# =============================================================================
# Test Tool Call Parsing
# =============================================================================


class TestToolCallParsing:
    """Tests for parsing tool calls from Ollama responses."""

    def test_parse_tool_calls_basic(self, ollama_backend):
        """Test parsing basic tool calls."""
        raw_calls = [
            {
                "id": "call_123",
                "function": {
                    "name": "read_file",
                    "arguments": {"path": "/tmp/test"},
                },
            }
        ]
        parsed = ollama_backend._parse_tool_calls(raw_calls)

        assert len(parsed) == 1
        assert parsed[0].id == "call_123"
        assert parsed[0].name == "read_file"
        assert parsed[0].arguments == {"path": "/tmp/test"}

    def test_parse_tool_calls_string_arguments(self, ollama_backend):
        """Test parsing tool calls with JSON string arguments."""
        raw_calls = [
            {
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "/tmp/test"}',
                },
            }
        ]
        parsed = ollama_backend._parse_tool_calls(raw_calls)

        assert len(parsed) == 1
        assert parsed[0].arguments == {"path": "/tmp/test"}

    def test_parse_tool_calls_generates_id(self, ollama_backend):
        """Test that missing IDs are generated."""
        raw_calls = [
            {
                "function": {
                    "name": "test_tool",
                    "arguments": {},
                },
            }
        ]
        parsed = ollama_backend._parse_tool_calls(raw_calls)

        assert len(parsed) == 1
        assert parsed[0].id.startswith("call_")

    def test_parse_tool_calls_empty(self, ollama_backend):
        """Test parsing empty tool calls list."""
        parsed = ollama_backend._parse_tool_calls([])
        assert parsed == []

    def test_parse_tool_calls_invalid_skipped(self, ollama_backend):
        """Test that invalid tool calls are skipped."""
        raw_calls = [
            {"function": {}},  # Missing name
            {"function": {"name": "valid_tool", "arguments": {}}},
        ]
        parsed = ollama_backend._parse_tool_calls(raw_calls)

        assert len(parsed) == 1
        assert parsed[0].name == "valid_tool"


# =============================================================================
# Test JSON Extraction
# =============================================================================


class TestJsonExtraction:
    """Tests for the JSON extraction fallback chain.

    _extract_json_from_text implements a 3-tier fallback:
    1. Extract from markdown code block (```json ... ```)
    2. Extract inline JSON object ({ ... })
    3. Return empty dict

    These tests cover each tier individually, the fallback transitions,
    and the integration path through _parse_tool_calls.
    """

    def test_extract_json_from_code_block(self, ollama_backend):
        """Tier 1: JSON inside markdown code block is extracted."""
        text = '```json\n{"path": "/tmp/test"}\n```'
        result = ollama_backend._extract_json_from_text(text)
        assert result == {"path": "/tmp/test"}

    def test_extract_json_from_code_block_no_language_tag(self, ollama_backend):
        """Tier 1: Code block without json language tag still works."""
        text = '```\n{"key": "value"}\n```'
        result = ollama_backend._extract_json_from_text(text)
        assert result == {"key": "value"}

    def test_extract_json_from_inline(self, ollama_backend):
        """Tier 2: Inline JSON object is extracted when no code block."""
        text = 'Here is the result: {"key": "value"}'
        result = ollama_backend._extract_json_from_text(text)
        assert result == {"key": "value"}

    def test_extract_json_returns_empty_on_failure(self, ollama_backend):
        """Tier 3: No JSON at all returns empty dict."""
        text = "This is not JSON at all"
        result = ollama_backend._extract_json_from_text(text)
        assert result == {}

    def test_code_block_with_invalid_json_falls_to_inline(self, ollama_backend):
        """Tier 1 fails (invalid JSON in code block) → falls through to tier 2."""
        # Code block has invalid JSON, but inline JSON exists after it
        text = '```json\nnot valid json\n```\nAlso here: {"fallback": true}'
        result = ollama_backend._extract_json_from_text(text)
        assert result == {"fallback": True}

    def test_code_block_and_inline_both_invalid_returns_empty(self, ollama_backend):
        """Both tier 1 and tier 2 fail → returns empty dict (tier 3)."""
        text = '```json\n{invalid\n```\nAlso {broken json'
        result = ollama_backend._extract_json_from_text(text)
        assert result == {}

    def test_nested_json_object(self, ollama_backend):
        """Tier 2: Nested JSON objects are parsed correctly."""
        text = 'Result: {"outer": {"inner": [1, 2, 3]}}'
        result = ollama_backend._extract_json_from_text(text)
        assert result == {"outer": {"inner": [1, 2, 3]}}

    def test_code_block_takes_priority_over_inline(self, ollama_backend):
        """Tier 1 (code block) takes priority even when inline JSON exists."""
        text = '{"inline": true}\n```json\n{"block": true}\n```'
        result = ollama_backend._extract_json_from_text(text)
        assert result == {"block": True}

    def test_multiline_code_block(self, ollama_backend):
        """Tier 1: Multi-line JSON in code block is extracted."""
        text = '```json\n{\n  "key1": "val1",\n  "key2": 42\n}\n```'
        result = ollama_backend._extract_json_from_text(text)
        assert result == {"key1": "val1", "key2": 42}

    def test_empty_string_returns_empty(self, ollama_backend):
        """Empty string returns empty dict."""
        result = ollama_backend._extract_json_from_text("")
        assert result == {}

    # --- Integration: _parse_tool_calls triggers extraction fallback ---

    def test_parse_tool_calls_string_args_invalid_json_triggers_extraction(
        self, ollama_backend
    ):
        """_parse_tool_calls falls back to _extract_json_from_text for non-JSON strings."""
        raw_calls = [
            {
                "function": {
                    "name": "write_file",
                    # String arguments that aren't valid JSON but contain embedded JSON
                    "arguments": 'Here is the args: {"path": "/tmp/out", "content": "hi"}',
                },
            }
        ]
        parsed = ollama_backend._parse_tool_calls(raw_calls)

        assert len(parsed) == 1
        assert parsed[0].name == "write_file"
        assert parsed[0].arguments == {"path": "/tmp/out", "content": "hi"}

    def test_parse_tool_calls_string_args_code_block_triggers_extraction(
        self, ollama_backend
    ):
        """_parse_tool_calls handles code-block-wrapped JSON string arguments."""
        raw_calls = [
            {
                "function": {
                    "name": "read_file",
                    "arguments": '```json\n{"path": "/etc/hosts"}\n```',
                },
            }
        ]
        parsed = ollama_backend._parse_tool_calls(raw_calls)

        assert len(parsed) == 1
        assert parsed[0].arguments == {"path": "/etc/hosts"}

    def test_parse_tool_calls_completely_invalid_string_args_skipped(
        self, ollama_backend
    ):
        """_parse_tool_calls with totally invalid string args skips the call (Q024)."""
        raw_calls = [
            {
                "function": {
                    "name": "some_tool",
                    "arguments": "just random text with no json whatsoever",
                },
            }
        ]
        parsed = ollama_backend._parse_tool_calls(raw_calls)

        # Tool call with unparseable args is skipped to prevent downstream
        # errors from missing arguments
        assert len(parsed) == 0


# =============================================================================
# Test Token Estimation
# =============================================================================


class TestTokenEstimation:
    """Tests for token usage estimation."""

    def test_estimate_tokens_from_response(self, ollama_backend, sample_ollama_response):
        """Test token estimation from actual counts in response."""
        input_tokens, output_tokens = ollama_backend._estimate_tokens(
            sample_ollama_response
        )

        assert input_tokens == 10
        assert output_tokens == 15

    def test_estimate_tokens_fallback(self, ollama_backend):
        """Test token estimation fallback when counts missing."""
        response = {
            "message": {"content": "Hello World"},  # 11 chars -> ~2 tokens
        }
        input_tokens, output_tokens = ollama_backend._estimate_tokens(response)

        assert input_tokens == 0
        assert output_tokens > 0


# =============================================================================
# Test Execute - Simple Completion
# =============================================================================


@pytest.mark.asyncio
class TestExecuteSimple:
    """Tests for simple (no-tool) execution."""

    async def test_execute_simple_prompt(
        self, ollama_backend, mock_httpx_response, sample_ollama_response
    ):
        """Test basic prompt execution without tools."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            return_value=mock_httpx_response(sample_ollama_response)
        )

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            result = await ollama_backend.execute("Hello, how are you?")

        assert result.success is True
        assert "Hello" in result.stdout
        assert result.model == "llama3.1:8b"

    async def test_execute_connection_error(self, ollama_backend):
        """Test handling of connection errors."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            result = await ollama_backend.execute("Test prompt")

        assert result.success is False
        assert result.error_type == "connection"
        assert "Connection" in result.stderr

    async def test_execute_timeout_error(self, ollama_backend):
        """Test handling of timeout errors."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            result = await ollama_backend.execute("Test prompt")

        assert result.success is False
        assert result.error_type == "timeout"
        assert result.exit_reason == "timeout"


# =============================================================================
# Test Execute - Agentic Loop
# =============================================================================


@pytest.mark.asyncio
class TestAgenticLoop:
    """Tests for the agentic loop with tool calling."""

    async def test_agentic_loop_single_iteration(
        self,
        ollama_backend,
        mock_httpx_response,
        sample_ollama_response,
    ):
        """Test agentic loop that completes in one iteration."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            return_value=mock_httpx_response(sample_ollama_response)
        )

        messages = [OllamaMessage(role="user", content="Hi")]
        tools = [{"type": "function", "function": {"name": "test"}}]

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            result = await ollama_backend._agentic_loop(messages, tools)

        assert result.success is True
        assert result.stdout == "Hello! I'm happy to help you today."

    async def test_agentic_loop_with_tool_call(
        self,
        ollama_backend,
        mock_httpx_response,
        sample_ollama_tool_response,
        sample_ollama_final_response,
    ):
        """Test agentic loop that executes a tool."""
        from marianne.bridge.mcp_proxy import ContentBlock, MCPProxyService, ToolResult

        # Mock MCP proxy
        mock_proxy = AsyncMock(spec=MCPProxyService)
        mock_proxy.execute_tool = AsyncMock(
            return_value=ToolResult(
                content=[ContentBlock(type="text", text="Hello World")],
                is_error=False,
            )
        )
        ollama_backend.mcp_proxy = mock_proxy

        # Mock HTTP client - first call returns tool call, second returns final
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            side_effect=[
                mock_httpx_response(sample_ollama_tool_response),
                mock_httpx_response(sample_ollama_final_response),
            ]
        )

        messages = [OllamaMessage(role="user", content="Read /tmp/test.txt")]
        tools = [{"type": "function", "function": {"name": "read_file"}}]

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            result = await ollama_backend._agentic_loop(messages, tools)

        assert result.success is True
        # Should have called execute_tool
        mock_proxy.execute_tool.assert_called_once()

    async def test_agentic_loop_max_iterations(
        self,
        ollama_backend,
        mock_httpx_response,
        sample_ollama_tool_response,
    ):
        """Test agentic loop respects max iterations."""
        from marianne.bridge.mcp_proxy import ContentBlock, MCPProxyService, ToolResult

        # Set low max iterations
        ollama_backend.max_tool_iterations = 2

        # Mock MCP proxy
        mock_proxy = AsyncMock(spec=MCPProxyService)
        mock_proxy.execute_tool = AsyncMock(
            return_value=ToolResult(
                content=[ContentBlock(type="text", text="Result")],
                is_error=False,
            )
        )
        ollama_backend.mcp_proxy = mock_proxy

        # Always return tool calls (never done)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            return_value=mock_httpx_response(sample_ollama_tool_response)
        )

        messages = [OllamaMessage(role="user", content="Test")]
        tools = [{"type": "function", "function": {"name": "read_file"}}]

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            await ollama_backend._agentic_loop(messages, tools)

        # Should stop after max_iterations
        assert mock_client.post.call_count == 2


# =============================================================================
# Test Health Check
# =============================================================================


@pytest.mark.asyncio
class TestHealthCheck:
    """Tests for health check functionality."""

    async def test_health_check_success(self, ollama_backend, mock_httpx_response):
        """Test successful health check."""
        tags_response = {
            "models": [
                {"name": "llama3.1:8b", "size": 4000000000},
                {"name": "mistral:7b", "size": 3000000000},
            ]
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_httpx_response(tags_response))

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            result = await ollama_backend.health_check()

        assert result is True

    async def test_health_check_model_not_found(
        self, ollama_backend, mock_httpx_response
    ):
        """Test health check when model isn't available."""
        tags_response = {
            "models": [
                {"name": "mistral:7b", "size": 3000000000},
            ]
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_httpx_response(tags_response))

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            result = await ollama_backend.health_check()

        assert result is False

    async def test_health_check_connection_error(self, ollama_backend):
        """Test health check handles connection errors."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            result = await ollama_backend.health_check()

        assert result is False


# =============================================================================
# Test Backend Properties
# =============================================================================


class TestBackendProperties:
    """Tests for backend properties and configuration."""

    def test_name_property(self, ollama_backend):
        """Test name property includes model."""
        assert ollama_backend.name == "ollama:llama3.1:8b"

    def test_from_config(self):
        """Test backend creation from config."""
        from marianne.core.config import BackendConfig

        config = BackendConfig(
            type="ollama",
            ollama={"base_url": "http://custom:11434", "model": "qwen3-coder"},
        )

        backend = OllamaBackend.from_config(config)

        assert backend.base_url == "http://custom:11434"
        assert backend.model == "qwen3-coder"


# =============================================================================
# Test Close
# =============================================================================


@pytest.mark.asyncio
class TestClose:
    """Tests for resource cleanup."""

    async def test_close_client(self, ollama_backend):
        """Test that close properly cleans up HTTP client."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()

        ollama_backend._client = mock_client
        await ollama_backend.close()

        mock_client.aclose.assert_called_once()
        assert ollama_backend._client is None

    async def test_close_no_client(self, ollama_backend):
        """Test that close handles no client gracefully."""
        ollama_backend._client = None
        await ollama_backend.close()  # Should not raise


# =============================================================================
# Test Preamble and Prompt Extensions
# =============================================================================


@pytest.mark.asyncio
class TestOllamaPreamble:
    """Tests for preamble and prompt extension injection in Ollama backend."""

    async def test_preamble_prepended_to_user_message(
        self, ollama_backend, mock_httpx_response, sample_ollama_response
    ):
        """set_preamble() content is prepended to the user message."""
        ollama_backend.set_preamble("<marianne-preamble>Identity</marianne-preamble>")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            return_value=mock_httpx_response(sample_ollama_response)
        )

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            await ollama_backend.execute("Do the task")

        # Inspect the messages sent to the API
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        user_content = payload["messages"][0]["content"]

        assert user_content.startswith("<marianne-preamble>")
        assert "Do the task" in user_content

    async def test_extensions_appended_to_user_message(
        self, ollama_backend, mock_httpx_response, sample_ollama_response
    ):
        """set_prompt_extensions() content is appended to the user message."""
        ollama_backend.set_prompt_extensions(["Extension A"])

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            return_value=mock_httpx_response(sample_ollama_response)
        )

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            await ollama_backend.execute("Do the task")

        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        user_content = payload["messages"][0]["content"]

        assert "Extension A" in user_content
        assert user_content.index("Do the task") < user_content.index("Extension A")

    async def test_preamble_and_extensions_together(
        self, ollama_backend, mock_httpx_response, sample_ollama_response
    ):
        """Preamble + prompt + extensions in correct order."""
        ollama_backend.set_preamble("<marianne-preamble>Preamble</marianne-preamble>")
        ollama_backend.set_prompt_extensions(["Extension"])

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            return_value=mock_httpx_response(sample_ollama_response)
        )

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            await ollama_backend.execute("My prompt")

        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        user_content = payload["messages"][0]["content"]

        assert user_content.index("Preamble") < user_content.index("My prompt")
        assert user_content.index("My prompt") < user_content.index("Extension")

    async def test_no_injection_when_neither_set(
        self, ollama_backend, mock_httpx_response, sample_ollama_response
    ):
        """Without preamble or extensions, prompt is sent unchanged."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            return_value=mock_httpx_response(sample_ollama_response)
        )

        with patch.object(ollama_backend, "_get_client", return_value=mock_client):
            await ollama_backend.execute("Plain prompt")

        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        user_content = payload["messages"][0]["content"]

        assert user_content == "Plain prompt"

    def test_set_preamble_stores_value(self, ollama_backend):
        """set_preamble() stores the preamble."""
        ollama_backend.set_preamble("test")
        assert ollama_backend._preamble == "test"

    def test_set_preamble_none_clears(self, ollama_backend):
        """set_preamble(None) clears the preamble."""
        ollama_backend.set_preamble("test")
        ollama_backend.set_preamble(None)
        assert ollama_backend._preamble is None

    def test_set_prompt_extensions_filters_empty(self, ollama_backend):
        """set_prompt_extensions() filters empty strings."""
        ollama_backend.set_prompt_extensions(["Valid", "", "  ", "Also valid"])
        assert ollama_backend._prompt_extensions == ["Valid", "Also valid"]


