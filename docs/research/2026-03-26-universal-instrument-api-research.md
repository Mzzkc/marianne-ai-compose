# Universal Instrument API Research

> Historical research snapshot. Its inventory of native provider backends
> describes the pre-unification system and is not current runtime guidance.

**Date:** 2026-03-26
**Purpose:** Document HTTP API interfaces for major AI model providers to inform the design of Marianne's universal instrument plugin system.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Provider-by-Provider Reference](#provider-by-provider-reference)
3. [Universal vs Provider-Specific Features](#universal-vs-provider-specific-features)
4. [Tool/Function Calling Comparison](#toolfunction-calling-comparison)
5. [Streaming Format Comparison](#streaming-format-comparison)
6. [Rate Limit Header Comparison](#rate-limit-header-comparison)
7. [Error Response Comparison](#error-response-comparison)
8. [Design Implications for Marianne](#design-implications-for-marianne)

---

## Executive Summary

Across 10 providers, there is a strong convergence around the **OpenAI Chat Completions schema** as the de facto standard. 7 of 10 providers either implement it natively or offer an OpenAI-compatible endpoint. The two major outliers are **Anthropic** (completely different schema) and **Google Gemini** (completely different schema). **AWS Bedrock** wraps provider-native schemas in its own Converse API envelope.

**Key finding for Marianne:** A universal instrument needs exactly three translation layers:
1. **OpenAI-compatible** (covers OpenAI, Azure OpenAI, Groq, Together AI, OpenRouter, Ollama, LiteLLM)
2. **Anthropic Messages** (covers direct Anthropic API and Bedrock Claude via native InvokeModel)
3. **Google Gemini** (covers AI Studio and Vertex AI)

Plus one **envelope adapter** for AWS Bedrock's Converse API (which normalizes all models into its own schema).

---

## Provider-by-Provider Reference

### 1. OpenAI Chat Completions API

**The de facto standard that everyone else copies.**

#### Endpoint
```
POST https://api.openai.com/v1/chat/completions
```

#### Auth
```
Authorization: Bearer $OPENAI_API_KEY
```

#### Request Schema
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"},
    {"role": "tool", "content": "result", "tool_call_id": "call_abc123"}
  ],
  "temperature": 1.0,
  "top_p": 1.0,
  "max_tokens": 4096,
  "max_completion_tokens": 4096,
  "n": 1,
  "stream": false,
  "stream_options": {"include_usage": true},
  "stop": ["END"],
  "presence_penalty": 0.0,
  "frequency_penalty": 0.0,
  "logit_bias": {},
  "logprobs": false,
  "top_logprobs": null,
  "seed": null,
  "response_format": {"type": "json_schema", "json_schema": {...}},
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get weather for a location",
        "parameters": {
          "type": "object",
          "properties": {"location": {"type": "string"}},
          "required": ["location"]
        },
        "strict": true
      }
    }
  ],
  "tool_choice": "auto" | "none" | "required" | {"type": "function", "function": {"name": "get_weather"}},
  "parallel_tool_calls": true,
  "user": "user-123"
}
```

#### Response Schema
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1677858242,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello!",
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "get_weather",
              "arguments": "{\"location\": \"NYC\"}"
            }
          }
        ]
      },
      "finish_reason": "stop" | "length" | "tool_calls" | "content_filter"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30
  },
  "system_fingerprint": "fp_abc123"
}
```

#### Streaming Format
SSE with `data:` prefix, terminated by `data: [DONE]`.
```
data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}

data: [DONE]
```

#### Rate Limit Headers
```
x-ratelimit-limit-requests: 10000
x-ratelimit-limit-tokens: 1000000
x-ratelimit-remaining-requests: 9999
x-ratelimit-remaining-tokens: 999990
x-ratelimit-reset-requests: 1ms
x-ratelimit-reset-tokens: 6ms
```

#### Error Response
```json
{
  "error": {
    "message": "Rate limit exceeded",
    "type": "rate_limit_error",
    "param": null,
    "code": "rate_limit_exceeded"
  }
}
```
HTTP codes: 400 (bad request), 401 (auth), 403 (forbidden), 404 (not found), 429 (rate limit), 500 (server), 503 (overloaded).

---

### 2. Anthropic Messages API

**Completely different schema. The second most important to support natively.**

#### Endpoint
```
POST https://api.anthropic.com/v1/messages
```

#### Auth
```
x-api-key: $ANTHROPIC_API_KEY
anthropic-version: 2023-06-01
Content-Type: application/json
```
Note: Uses `x-api-key` header, NOT `Authorization: Bearer`. Also requires `anthropic-version` header.

#### Request Schema
```json
{
  "model": "claude-sonnet-4-5-20250929",
  "max_tokens": 1024,
  "system": "You are helpful." | [{"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}}],
  "messages": [
    {"role": "user", "content": "Hello" | [{"type": "text", "text": "..."}, {"type": "image", "source": {...}}]},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_abc", "name": "get_weather", "input": {"location": "NYC"}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_abc", "content": "72F"}]}
  ],
  "temperature": 1.0,
  "top_p": null,
  "top_k": null,
  "stop_sequences": [],
  "stream": false,
  "tools": [
    {
      "name": "get_weather",
      "description": "Get weather",
      "input_schema": {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"]
      }
    }
  ],
  "tool_choice": {"type": "auto" | "any" | "tool" | "none", "name": "get_weather", "disable_parallel_tool_use": false},
  "thinking": {"type": "enabled", "budget_tokens": 10000},
  "metadata": {"user_id": "user-123"},
  "cache_control": {"type": "ephemeral", "ttl": "5m" | "1h"},
  "output_config": {"format": {"type": "json_schema", "schema": {...}}},
  "service_tier": "auto" | "standard_only"
}
```

**Key schema differences from OpenAI:**
- `system` is a top-level field, not a message role
- `max_tokens` is **required** (not optional)
- Tools use `input_schema` not `parameters`
- Tools don't wrap in `{"type": "function", "function": {...}}`
- Tool results are `tool_result` content blocks inside user messages, not separate `tool` role messages
- Tool calls are `tool_use` content blocks inside assistant messages, not a `tool_calls` array
- Content is always a block array (text, image, tool_use, tool_result), not just a string
- Has `cache_control` on individual blocks for prompt caching
- Has `thinking` for extended reasoning
- Has built-in tool types (bash, text_editor, web_search, code_execution)

#### Response Schema
```json
{
  "id": "msg_01abc",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-5-20250929",
  "content": [
    {"type": "text", "text": "Let me check..."},
    {"type": "thinking", "thinking": "I should use the weather tool..."},
    {"type": "tool_use", "id": "toolu_abc", "name": "get_weather", "input": {"location": "NYC"}}
  ],
  "stop_reason": "end_turn" | "stop_sequence" | "max_tokens" | "tool_use",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 10,
    "output_tokens": 20,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  }
}
```

**Key response differences:**
- No `choices` array -- single response only (no `n` parameter)
- `content` is an array of typed blocks, not a single string
- `stop_reason` not `finish_reason`; values differ (`end_turn` vs `stop`, `tool_use` vs `tool_calls`)
- `usage` has cache-specific token fields
- No `total_tokens` field (compute it yourself)

#### Streaming Format
SSE with named `event:` types (NOT `data: [DONE]` termination):
```
event: message_start
data: {"type":"message_start","message":{"id":"msg_01","model":"...","usage":{"input_tokens":10}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":10}}

event: message_stop
data: {"type":"message_stop"}
```

**Streaming differences:** Uses typed event names (`message_start`, `content_block_delta`, etc.) instead of generic `data:` chunks. Content blocks have explicit start/stop lifecycle. Tool input streams as `input_json_delta`.

#### Rate Limit Headers
```
RateLimit-Limit-Requests: 10000
RateLimit-Limit-Tokens: 2000000
RateLimit-Remaining-Requests: 9999
RateLimit-Remaining-Tokens: 1999990
RateLimit-Reset-Requests: 2024-12-31T00:00:00Z
RateLimit-Reset-Tokens: 2024-12-31T00:00:00Z
```
Note: Uses `RateLimit-*` prefix (IETF draft standard), NOT `x-ratelimit-*` (OpenAI convention). Reset values are ISO timestamps, not durations.

#### Error Response
```json
{
  "type": "error",
  "error": {
    "type": "rate_limit_error" | "authentication_error" | "invalid_request_error" | "overloaded_error",
    "message": "Rate limit exceeded"
  }
}
```
HTTP codes: 400 (invalid request), 401 (auth), 403 (permission), 404 (not found), 429 (rate limit), 529 (overloaded -- unique code).

#### Unique Features
- **Prompt caching** with `cache_control` on any content block (5m or 1h TTL)
- **Extended thinking** with configurable budget
- **Built-in tools** (bash, text_editor, web_search, web_fetch, code_execution)
- **System prompt as top-level field** (not a message role)
- **529 Overloaded** status code (non-standard HTTP code)
- **Document/PDF content blocks** with citations
- **Server-side tool execution** (web search, code execution run on Anthropic's infra)

---

### 3. Google Gemini API (AI Studio)

**Completely different schema from both OpenAI and Anthropic.**

#### Endpoint
```
POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
POST https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent
```
Note: Streaming is a **separate endpoint**, not a request parameter.

#### Auth
API key as query parameter: `?key=$GEMINI_API_KEY`

#### Request Schema
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {"text": "Hello"},
        {"inline_data": {"mime_type": "image/jpeg", "data": "<base64>"}},
        {"functionCall": {"name": "get_weather", "args": {"location": "NYC"}}},
        {"functionResponse": {"name": "get_weather", "response": {"temp": "72F"}}}
      ]
    }
  ],
  "systemInstruction": {
    "parts": [{"text": "You are helpful."}]
  },
  "tools": [
    {
      "functionDeclarations": [
        {
          "name": "get_weather",
          "description": "Get weather",
          "parameters": {
            "type": "OBJECT",
            "properties": {"location": {"type": "STRING"}},
            "required": ["location"]
          }
        }
      ]
    }
  ],
  "toolConfig": {
    "functionCallingConfig": {
      "mode": "AUTO" | "ANY" | "NONE"
    }
  },
  "generationConfig": {
    "temperature": 1.0,
    "topP": 0.95,
    "topK": 40,
    "maxOutputTokens": 8192,
    "candidateCount": 1,
    "stopSequences": [],
    "responseMimeType": "application/json",
    "responseSchema": {...}
  },
  "safetySettings": [
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
  ],
  "cachedContent": "cachedContents/abc123"
}
```

**Key schema differences:**
- `contents` not `messages`; `parts` not `content`
- Roles are `user` and `model` (not `assistant`)
- `systemInstruction` (camelCase) as top-level field
- Tools wrapped in `functionDeclarations` array inside a tool object
- Tool config is separate from tool definitions
- Generation params wrapped in `generationConfig` object
- Safety settings are a first-class concept
- `responseMimeType` for structured output
- JSON Schema types are UPPERCASE (`OBJECT`, `STRING`)
- Function calls and responses are `parts` within messages, not separate structures

#### Response Schema
```json
{
  "candidates": [
    {
      "content": {
        "role": "model",
        "parts": [
          {"text": "Hello!"},
          {"functionCall": {"name": "get_weather", "args": {"location": "NYC"}}}
        ]
      },
      "finishReason": "STOP" | "MAX_TOKENS" | "SAFETY" | "RECITATION" | "OTHER",
      "safetyRatings": [{"category": "...", "probability": "..."}]
    }
  ],
  "usageMetadata": {
    "promptTokenCount": 10,
    "candidatesTokenCount": 20,
    "totalTokenCount": 30
  },
  "modelVersion": "gemini-2.0-flash",
  "responseId": "abc123"
}
```

#### Streaming Format
Uses `?alt=sse` query parameter on the streaming endpoint. Returns sequential `GenerateContentResponse` objects as SSE `data:` events.

#### Rate Limit Headers
Standard Google API rate limiting. No well-documented custom headers -- relies on HTTP 429 responses with `Retry-After`.

#### Error Response
Standard Google API error format:
```json
{
  "error": {
    "code": 429,
    "message": "Resource exhausted",
    "status": "RESOURCE_EXHAUSTED"
  }
}
```

#### Unique Features
- **Safety settings** with per-category thresholds (no equivalent in OpenAI/Anthropic)
- **Cached content** references for long context reuse
- **Separate streaming endpoint** instead of stream parameter
- **UPPERCASE enum values** throughout
- **`model` role** instead of `assistant`
- **camelCase** field names (not snake_case)
- **Multi-candidate responses** via `candidateCount`

---

### 3b. Google Vertex AI (Gemini)

Same schema as AI Studio with these differences:

#### Endpoint
```
POST https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{REGION}/publishers/google/models/{MODEL}:generateContent
POST https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{REGION}/publishers/google/models/{MODEL}:streamGenerateContent
```

#### Auth
OAuth 2.0 / Service Account / ADC (Application Default Credentials). NOT API key.
```
Authorization: Bearer $(gcloud auth print-access-token)
```

#### Differences from AI Studio
- Regional endpoints (us-central1, europe-west4, etc.)
- IAM-based auth instead of API key
- Same request/response schema
- Enterprise features (VPC-SC, CMEK, audit logging)
- Higher rate limits

---

### 4. Ollama API

**Local model server. Has its own native API plus partial OpenAI compatibility.**

#### Native Endpoint
```
POST http://localhost:11434/api/chat
POST http://localhost:11434/api/generate
```

#### OpenAI-Compatible Endpoint
```
POST http://localhost:11434/v1/chat/completions
```

#### Auth
None (local server).

#### Native Request Schema (`/api/chat`)
```json
{
  "model": "llama3.1:8b",
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi!", "tool_calls": [...]},
    {"role": "tool", "content": "result"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get weather",
        "parameters": {"type": "object", "properties": {...}}
      }
    }
  ],
  "stream": true,
  "format": "json" | {"type": "object", ...},
  "options": {
    "num_ctx": 32768,
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "seed": 42
  },
  "keep_alive": "5m",
  "think": true
}
```

**Key differences:**
- Generation params go in `options` sub-object, not top-level
- `num_ctx` for context window (not a concept in cloud APIs)
- `keep_alive` for model memory management
- `format` for structured output (not `response_format`)
- `think` for reasoning (model-dependent)
- Stream defaults to **true** (opposite of cloud APIs)
- Tool format matches OpenAI

#### Native Response Schema
```json
{
  "model": "llama3.1:8b",
  "created_at": "2024-01-01T00:00:00Z",
  "message": {
    "role": "assistant",
    "content": "Hello!",
    "tool_calls": [
      {
        "function": {
          "name": "get_weather",
          "arguments": {"location": "NYC"}
        }
      }
    ]
  },
  "done": true,
  "total_duration": 5000000000,
  "load_duration": 1000000000,
  "prompt_eval_count": 10,
  "prompt_eval_duration": 500000000,
  "eval_count": 20,
  "eval_duration": 3000000000
}
```

**Response differences:**
- No `choices` array -- single `message` object
- Duration in **nanoseconds** (not seconds or milliseconds)
- `prompt_eval_count`/`eval_count` instead of `prompt_tokens`/`completion_tokens`
- `done` boolean instead of `finish_reason`
- Tool call arguments may be dict or string (inconsistent across models)
- Tool calls may lack `id` field

#### Streaming Format
NDJSON (newline-delimited JSON), NOT SSE. Each line is a complete JSON object.
```
{"model":"llama3.1","created_at":"...","message":{"role":"assistant","content":"Hello"},"done":false}
{"model":"llama3.1","created_at":"...","message":{"role":"assistant","content":"!"},"done":false}
{"model":"llama3.1","created_at":"...","message":{"role":"assistant","content":""},"done":true,"eval_count":20,"prompt_eval_count":10}
```

#### Rate Limits
None (local server). Constrained by hardware.

#### Error Handling
HTTP status codes with plain text or JSON error messages. No standardized error schema.

---

### 5. OpenRouter

**Multi-provider proxy. OpenAI-compatible with routing extensions.**

#### Endpoint
```
POST https://openrouter.ai/api/v1/chat/completions
```

#### Auth
```
Authorization: Bearer $OPENROUTER_API_KEY
HTTP-Referer: https://your-app.com     (optional, for attribution)
X-OpenRouter-Title: Your App Name       (optional, for attribution)
```

#### Request Schema
Standard OpenAI schema plus these extensions:
```json
{
  "model": "anthropic/claude-sonnet-4-5",
  "messages": [...],
  "tools": [...],
  "tool_choice": "auto",
  "temperature": 1.0,
  "max_tokens": 4096,
  "stream": false,

  "models": ["anthropic/claude-sonnet-4-5", "openai/gpt-4o"],
  "route": "fallback",
  "provider": {
    "order": ["Anthropic", "Google"],
    "allow_fallbacks": true,
    "require_parameters": true
  },
  "plugins": [...],
  "prediction": {"type": "content", "content": "predicted output"},
  "user": "user-123"
}
```

**Unique request fields:**
- `models` + `route`: Multi-model fallback chain
- `provider`: Provider ordering and fallback preferences
- `plugins`: Web search, PDF parsing, context compression
- `prediction`: Speculative decoding hint
- Model names include provider prefix (`anthropic/claude-sonnet-4-5`)
- Dynamic variants: `:online`, `:nitro`, `:floor`, `:exacto` suffixes

#### Response Schema
Standard OpenAI schema plus:
```json
{
  "id": "gen-abc123",
  "choices": [...],
  "model": "anthropic/claude-sonnet-4-5",
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30,
    "cost": 0.00015,
    "is_byok": false,
    "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 0},
    "cost_details": {"upstream_inference_cost": 0.00012}
  }
}
```

**Unique response fields:**
- `native_finish_reason`: Raw provider finish reason before normalization
- `cost` in usage: Dollar cost of the request
- `is_byok`: Whether BYOK key was used
- Normalized `finish_reason` values: `stop`, `length`, `tool_calls`, `content_filter`, `error`

#### Rate Limits
Credit-based system. Check via `GET /api/v1/key`:
```json
{
  "limit": 100.0,
  "limit_remaining": 95.5,
  "usage": 4.5,
  "is_free_tier": false
}
```
Free tier: 50-1000 requests/day depending on purchased credits. DDoS protection via Cloudflare. 402 on negative balance.

#### Error Response
```json
{
  "error": {
    "code": 429,
    "message": "Rate limit exceeded",
    "metadata": {"provider_name": "Anthropic"}
  }
}
```

---

### 6. AWS Bedrock

**Two APIs: native InvokeModel (pass-through) and Converse (unified schema).**

#### Converse Endpoint
```
POST https://bedrock-runtime.{region}.amazonaws.com/model/{modelId}/converse
POST https://bedrock-runtime.{region}.amazonaws.com/model/{modelId}/converse-stream
```

#### Auth
AWS SigV4 signing. Requires IAM credentials (access key + secret key + session token).

#### Converse Request Schema
```json
{
  "messages": [
    {
      "role": "user",
      "content": [{"text": "Hello"}]
    },
    {
      "role": "assistant",
      "content": [
        {"text": "Let me check..."},
        {"toolUse": {"toolUseId": "abc", "name": "get_weather", "input": {"location": "NYC"}}}
      ]
    },
    {
      "role": "user",
      "content": [
        {"toolResult": {"toolUseId": "abc", "content": [{"json": {"temp": "72F"}}]}}
      ]
    }
  ],
  "system": [{"text": "You are helpful."}],
  "inferenceConfig": {
    "maxTokens": 1000,
    "temperature": 0.5,
    "topP": 0.9,
    "stopSequences": []
  },
  "toolConfig": {
    "tools": [
      {
        "toolSpec": {
          "name": "get_weather",
          "description": "Get weather",
          "inputSchema": {
            "json": {
              "type": "object",
              "properties": {"location": {"type": "string"}}
            }
          }
        }
      }
    ],
    "toolChoice": {"auto": {} | "any": {} | "tool": {"name": "get_weather"}}
  },
  "additionalModelRequestFields": {},
  "guardrailConfig": {"guardrailIdentifier": "abc", "guardrailVersion": "1"},
  "performanceConfig": {"latency": "optimized"},
  "serviceTier": {"type": "priority"}
}
```

**Key differences:**
- Content is always `[{"text": "..."}]` array (never bare string)
- Tool definitions use `toolSpec` wrapper with `inputSchema.json`
- Tool calls are `toolUse` blocks, results are `toolResult` blocks
- `inferenceConfig` wraps generation parameters (camelCase)
- `additionalModelRequestFields` for model-specific params
- Guardrail integration is native
- Streaming is a **separate endpoint** (`/converse-stream`)

#### Converse Response Schema
```json
{
  "output": {
    "message": {
      "role": "assistant",
      "content": [
        {"text": "The weather is..."},
        {"toolUse": {"toolUseId": "abc", "name": "get_weather", "input": {...}}}
      ]
    }
  },
  "stopReason": "end_turn" | "tool_use" | "max_tokens" | "stop_sequence" | "guardrail_intervened" | "content_filtered",
  "usage": {
    "inputTokens": 30,
    "outputTokens": 628,
    "totalTokens": 658,
    "cacheReadInputTokens": 0,
    "cacheWriteInputTokens": 0
  },
  "metrics": {
    "latencyMs": 1275
  }
}
```

#### InvokeModel API
Pass-through to native model format. For Claude models, the body IS the Anthropic Messages API schema (with `anthropic_version: "bedrock-2023-05-31"`). Response is also native Anthropic format.

#### Streaming
Converse stream returns events with base64-encoded chunks. InvokeModel stream returns native model streaming format.

#### Rate Limits
AWS service quotas per region. ThrottlingException (429) for quota exceeded. ModelNotReadyException (429) auto-retries up to 5x.

#### Error Types
| Error | HTTP Code |
|-------|-----------|
| AccessDeniedException | 403 |
| ValidationException | 400 |
| ResourceNotFoundException | 404 |
| ThrottlingException | 429 |
| ModelNotReadyException | 429 |
| ModelTimeoutException | 408 |
| ModelErrorException | 424 |
| InternalServerException | 500 |
| ServiceUnavailableException | 503 |

---

### 7. Azure OpenAI

**OpenAI schema with deployment-based routing and Microsoft auth.**

#### Endpoint
```
POST https://{resource-name}.openai.azure.com/openai/deployments/{deployment-id}/chat/completions?api-version=2024-10-21
```

#### Auth
Two options:
```
api-key: $AZURE_OPENAI_API_KEY
```
OR
```
Authorization: Bearer $AAD_TOKEN
```

#### Request Differences from OpenAI
- Model is specified by `deployment-id` in URL, NOT in request body
- `api-version` is a **required** query parameter (YYYY-MM-DD format)
- Extra `data_sources` field for RAG with Azure Search/Cosmos DB:
```json
{
  "messages": [...],
  "data_sources": [
    {
      "type": "azure_search",
      "parameters": {
        "endpoint": "https://search.windows.net/",
        "index_name": "my-index",
        "authentication": {"type": "system_assigned_managed_identity"}
      }
    }
  ]
}
```

#### Response Differences
- Content filtering results in response:
```json
{
  "prompt_filter_results": [
    {
      "content_filter_results": {
        "sexual": {"severity": "safe", "filtered": false},
        "violence": {"severity": "safe", "filtered": false},
        "hate": {"severity": "safe", "filtered": false},
        "self_harm": {"severity": "safe", "filtered": false},
        "jailbreak": {"detected": false, "filtered": false}
      }
    }
  ]
}
```
- RAG citations in message `context` field:
```json
{
  "message": {
    "content": "...",
    "context": {
      "citations": [{"content": "...", "title": "...", "url": "..."}],
      "intent": "query intent"
    }
  }
}
```

#### Rate Limit Headers
```
x-ratelimit-remaining-requests
x-ratelimit-remaining-tokens
x-ms-ratelimit-remaining-resource: ...
```

#### Error Response
```json
{
  "error": {
    "code": "429",
    "message": "Rate limit exceeded",
    "type": "rate_limit",
    "inner_error": {
      "code": "ResponsibleAIPolicyViolation",
      "content_filter_results": {...}
    }
  }
}
```

---

### 8. Together AI

**OpenAI-compatible with open-source model hosting.**

#### Endpoint
```
POST https://api.together.xyz/v1/chat/completions
```

#### Auth
```
Authorization: Bearer $TOGETHER_API_KEY
```

#### Request Extensions
Standard OpenAI schema plus:
```json
{
  "reasoning": {"enabled": true},
  "reasoning_effort": "low" | "medium" | "high",
  "safety_model": "Meta-Llama/...",
  "compliance": "hipaa",
  "context_length_exceeded_behavior": "truncate" | "error",
  "repetition_penalty": 1.1,
  "min_p": 0.05,
  "echo": true
}
```

#### Response Schema
Standard OpenAI format. Additional fields:
- `message.reasoning`: String with reasoning content
- `choice.seed`: Seed used for generation
- `warnings`: Array of warning messages

#### Streaming
Standard SSE with `data: [DONE]` termination.

#### Rate Limits
HTTP 429 on rate limit. No documented custom headers.

---

### 9. Groq

**OpenAI-compatible with speed-focused metrics.**

#### Endpoint
```
POST https://api.groq.com/openai/v1/chat/completions
```

#### Auth
```
Authorization: Bearer $GROQ_API_KEY
```

#### Request Schema
Standard OpenAI format. Notable fields:
```json
{
  "max_completion_tokens": 4096,
  "parallel_tool_calls": true,
  "reasoning_effort": "none" | "default" | "low" | "medium" | "high"
}
```

#### Response Schema
Standard OpenAI format with speed metrics:
```json
{
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30,
    "queue_time": 0.001,
    "prompt_time": 0.002,
    "completion_time": 0.005,
    "total_time": 0.008
  },
  "x_groq": {"id": "req_abc123"},
  "system_fingerprint": "fp_abc"
}
```

**Unique:** `queue_time`, `prompt_time`, `completion_time`, `total_time` in usage object. `x_groq.id` for request tracking.

#### Rate Limit Headers
```
x-ratelimit-limit-requests        (Requests Per Day)
x-ratelimit-limit-tokens           (Tokens Per Minute)
x-ratelimit-remaining-requests     (Requests Per Day remaining)
x-ratelimit-remaining-tokens       (Tokens Per Minute remaining)
x-ratelimit-reset-requests         (time until daily reset)
x-ratelimit-reset-tokens           (time until minute reset)
retry-after                        (seconds, only on 429)
```
Note: All headers sent on every response EXCEPT `retry-after` (only on 429).

#### Streaming
Standard SSE with `data: [DONE]` termination.

---

### 10. LiteLLM Proxy

**Universal proxy that normalizes everything to OpenAI format.**

#### Endpoint
```
POST http://localhost:4000/chat/completions
POST http://localhost:4000/completions
POST http://localhost:4000/embeddings
GET  http://localhost:4000/models
```

#### Auth
Virtual keys generated via `POST /key/generate`.

#### Model Name Mapping
Provider prefix notation:
```
openai/gpt-4o              -> OpenAI
anthropic/claude-sonnet-4-5  -> Anthropic
bedrock/anthropic.claude-v2 -> AWS Bedrock
azure/my-deployment         -> Azure OpenAI
vertex_ai/gemini-pro        -> Google Vertex
ollama/llama3.1             -> Ollama
together_ai/model           -> Together AI
groq/llama-3.3-70b          -> Groq
```

#### Request/Response
100% OpenAI-compatible on the client side. Translates internally to each provider's native format. Supports:
- Load balancing across deployments
- Spend tracking with virtual keys and budgets
- Fallback chains
- Custom `api_base` per model
- YAML-based configuration

**Key value:** If Marianne uses LiteLLM as a reference, the instrument plugin only needs to speak OpenAI format and delegate provider translation to LiteLLM. But that adds a dependency.

---

## Universal vs Provider-Specific Features

### Truly Universal (safe to assume across all providers)

| Feature | Universal Format |
|---------|-----------------|
| Messages array with roles | `messages: [{role, content}]` |
| System prompt | Present in all (location varies) |
| Temperature | `0.0 - 1.0` (Groq allows up to 2.0) |
| Max output tokens | Present in all (field name varies) |
| Streaming | Supported by all (format varies) |
| Tool/function calling | Supported by all (schema varies) |
| Token usage in response | Present in all (field names vary) |
| Stop sequences | Present in all |

### Provider-Specific (needs special handling)

| Feature | Providers | Notes |
|---------|-----------|-------|
| `system` as top-level field | Anthropic, Bedrock Converse, Gemini | OpenAI uses system role in messages |
| `max_tokens` required | Anthropic | Optional everywhere else |
| Prompt caching | Anthropic, Bedrock, Gemini | Different mechanisms |
| Extended thinking | Anthropic, Together AI, Groq | Different field names |
| Safety settings | Gemini, Azure | Per-category thresholds |
| Content filtering results | Azure | In response body |
| Data sources / RAG | Azure | Native Azure Search integration |
| Guardrails | Bedrock | Native guardrail config |
| Provider routing | OpenRouter | Multi-model fallback |
| Speed metrics | Groq | queue_time, prompt_time, etc. |
| Cost tracking | OpenRouter | Dollar cost in response |
| Model memory management | Ollama | keep_alive, num_ctx |
| Structured output | All | Different field names: `response_format` / `format` / `responseMimeType` |

---

## Tool/Function Calling Comparison

### Tool Definition Format

| Provider | Format |
|----------|--------|
| **OpenAI, Groq, Together, OpenRouter, Ollama** | `{"type": "function", "function": {"name", "description", "parameters"}}` |
| **Anthropic** | `{"name", "description", "input_schema"}` (no function wrapper) |
| **Gemini** | `{"functionDeclarations": [{"name", "description", "parameters"}]}` (wrapped in tool object, UPPERCASE types) |
| **Bedrock Converse** | `{"toolSpec": {"name", "description", "inputSchema": {"json": {...}}}}` |
| **Azure OpenAI** | Same as OpenAI |
| **LiteLLM** | Same as OpenAI (translated internally) |

### Tool Call in Response

| Provider | Format |
|----------|--------|
| **OpenAI family** | `message.tool_calls: [{id, type: "function", function: {name, arguments: "JSON string"}}]` |
| **Anthropic** | Content block: `{type: "tool_use", id, name, input: {object}}` (input is object, not string) |
| **Gemini** | Part: `{functionCall: {name, args: {object}}}` (args is object, not string) |
| **Bedrock Converse** | Content block: `{toolUse: {toolUseId, name, input: {object}}}` |
| **Ollama** | `message.tool_calls: [{function: {name, arguments: object|string}}]` (may lack id) |

### Tool Result Format

| Provider | Format |
|----------|--------|
| **OpenAI family** | `{role: "tool", content: "string", tool_call_id: "id"}` |
| **Anthropic** | User message content block: `{type: "tool_result", tool_use_id: "id", content: "string", is_error: bool}` |
| **Gemini** | User message part: `{functionResponse: {name: "tool", response: {object}}}` |
| **Bedrock Converse** | User message content block: `{toolResult: {toolUseId: "id", content: [{json: {...}}]}}` |

### Key Observations for Marianne's Instrument Plugin

1. **Arguments serialization differs:** OpenAI returns `arguments` as a **JSON string**. Anthropic, Gemini, and Bedrock return it as an **object**. Ollama returns either.
2. **Tool result location differs:** OpenAI uses a `tool` role message. Anthropic/Bedrock embed it as a content block in a `user` message. Gemini uses `functionResponse` parts.
3. **Tool call IDs:** OpenAI and Anthropic always provide them. Ollama sometimes doesn't. Gemini doesn't use them (matches by function name).
4. **Error signaling:** Only Anthropic has `is_error` in tool results. Others must encode errors in the content string.

---

## Streaming Format Comparison

| Provider | Format | Termination | Token Usage |
|----------|--------|-------------|-------------|
| **OpenAI, Groq, Together, OpenRouter** | SSE `data: {json}` | `data: [DONE]` | Final chunk (with `stream_options.include_usage`) |
| **Anthropic** | SSE with named `event:` types | `event: message_stop` | In `message_start` (input) and `message_delta` (output) |
| **Gemini** | SSE `data: {json}` | End of stream | In each chunk's `usageMetadata` |
| **Bedrock Converse** | Binary event stream | End of stream | In final event |
| **Ollama** | NDJSON (newline-delimited JSON) | `"done": true` in final object | In final object (`eval_count`, `prompt_eval_count`) |
| **Azure OpenAI** | SSE `data: {json}` (same as OpenAI) | `data: [DONE]` | Same as OpenAI |
| **LiteLLM** | SSE `data: {json}` (same as OpenAI) | `data: [DONE]` | Same as OpenAI |

### Streaming Implementation Notes
- **Anthropic** is the most complex: content blocks have explicit lifecycle (start/delta/stop), tool input streams as JSON deltas.
- **Ollama** is the simplest but non-standard: just NDJSON lines.
- **Bedrock** uses AWS-specific binary event stream encoding, not SSE.
- **Everyone else** follows the OpenAI SSE convention closely enough to share parsing code.

---

## Rate Limit Header Comparison

| Provider | Header Prefix | Request Limits | Token Limits | Reset Format | Retry-After |
|----------|--------------|----------------|--------------|--------------|-------------|
| **OpenAI** | `x-ratelimit-*` | Per-minute | Per-minute | Duration (e.g., `6ms`) | No |
| **Anthropic** | `RateLimit-*` (IETF) | Per-? | Per-? | ISO timestamp | No |
| **Groq** | `x-ratelimit-*` | Per-day (RPD) | Per-minute (TPM) | Duration | `retry-after` in seconds (429 only) |
| **Azure OpenAI** | `x-ratelimit-*` | Yes | Yes | Duration | `retry-after` |
| **Together AI** | Not documented | - | - | - | - |
| **OpenRouter** | Credit-based | Via `/api/v1/key` endpoint | N/A | N/A | 402 on negative balance |
| **Gemini** | Standard Google | 429 with `Retry-After` | - | - | `Retry-After` header |
| **Bedrock** | AWS service quotas | ThrottlingException | - | - | Auto-retry (SDK) |
| **Ollama** | None | N/A (local) | N/A | N/A | N/A |
| **LiteLLM** | Configurable | Via proxy config | Via proxy config | - | - |

### Key Observations
1. **Two conventions:** OpenAI's `x-ratelimit-*` (lowercase, x-prefix) vs Anthropic's `RateLimit-*` (IETF draft standard, capitalized, no x-prefix). Groq, Azure follow OpenAI's convention.
2. **Reset format differs:** OpenAI/Groq use durations. Anthropic uses ISO timestamps.
3. **Groq's quirk:** Request limits are per-DAY, token limits are per-MINUTE. Different time windows in the same header set.
4. **OpenRouter** doesn't use standard rate limit headers at all -- it's credit-based, checked via a separate API call.

---

## Error Response Comparison

| Provider | Error Wrapper | Rate Limit Code | Overload Code | Auth Code |
|----------|--------------|-----------------|---------------|-----------|
| **OpenAI** | `{"error": {"message", "type", "param", "code"}}` | 429 | 503 | 401 |
| **Anthropic** | `{"type": "error", "error": {"type", "message"}}` | 429 | **529** | 401 |
| **Gemini** | `{"error": {"code", "message", "status"}}` | 429 | 503 | 401/403 |
| **Bedrock** | AWS exception classes | 429 (ThrottlingException) | 503 | 403 (AccessDeniedException) |
| **Azure OpenAI** | `{"error": {"code", "message", "type", "inner_error"}}` | 429 | 503 | 401 |
| **Groq** | `{"error": {"message", "type", "code"}}` | 429 | 503 | 401 |
| **Together AI** | `{"error": {"type", "message", "code", "param"}}` | 429 | 503 | 401 |
| **OpenRouter** | `{"error": {"code", "message", "metadata"}}` | 429 | 503 | 401, **402** |
| **Ollama** | Varies (plain text or JSON) | N/A | N/A | N/A |
| **LiteLLM** | OpenAI format | 429 | 503 | 401 |

### Key Observations
1. **Anthropic's 529** is non-standard. The universal wrapper must map it to the internal "overloaded" category.
2. **OpenRouter's 402** (Payment Required) for negative balance is unique.
3. **Bedrock** uses typed exceptions, not JSON error bodies -- requires AWS SDK or special parsing.
4. **All providers** use 429 for rate limiting. This is the one truly universal signal.

---

## Design Implications for Marianne

### Recommended Architecture

```
Score YAML (instrument: anthropic | openai | gemini | ollama | ...)
    |
    v
InstrumentRegistry
    |
    v
InstrumentPlugin (Protocol)
    |-- OpenAICompatiblePlugin  (covers: OpenAI, Azure, Groq, Together, OpenRouter, LiteLLM)
    |-- AnthropicPlugin         (covers: direct Anthropic API)
    |-- GeminiPlugin            (covers: AI Studio, Vertex AI)
    |-- BedrockPlugin           (covers: all Bedrock models via Converse API)
    |-- OllamaPlugin            (covers: local Ollama, can use native or OpenAI-compat endpoint)
    |
    v
UniversalRequest / UniversalResponse (internal Marianne types)
```

### The Universal Request Type Should Include

```python
@dataclass
class UniversalRequest:
    model: str
    messages: list[Message]          # Normalized message format
    system: str | None               # Extracted from messages or top-level
    max_tokens: int
    temperature: float | None
    top_p: float | None
    stop_sequences: list[str]
    tools: list[ToolDefinition]      # Normalized tool format
    tool_choice: ToolChoice
    stream: bool
    response_format: ResponseFormat | None
    # Provider-specific passthrough
    extra: dict[str, Any]
```

### The Universal Response Type Should Include

```python
@dataclass
class UniversalResponse:
    content: str                     # Extracted text content
    tool_calls: list[ToolCall]       # Normalized tool calls
    stop_reason: StopReason          # Normalized enum
    usage: TokenUsage                # Normalized token counts
    model: str                       # Actual model used
    raw_response: Any                # Provider-specific raw response
```

### Translation Priorities

1. **Messages:** Normalize system prompt handling (role vs top-level field)
2. **Tools:** Normalize between OpenAI wrapper format, Anthropic flat format, Gemini functionDeclarations, Bedrock toolSpec
3. **Tool calls:** Normalize arguments (string vs object), IDs (present vs absent), location (tool_calls array vs content blocks vs parts)
4. **Tool results:** Normalize between tool role, user content blocks, and functionResponse parts
5. **Streaming:** Normalize between SSE variants, NDJSON, and binary streams
6. **Rate limits:** Normalize between `x-ratelimit-*`, `RateLimit-*`, and credit-based systems
7. **Errors:** Map all error codes/types to Marianne's internal ErrorCategory enum

### What Marianne Already Does Right

Looking at `src/marianne/backends/`:
- `AnthropicApiBackend` handles direct Anthropic API correctly
- `OllamaBackend` handles native Ollama API with tool translation
- `HttpxClientMixin` provides shared HTTP client lifecycle
- `ExecutionResult` already has `rate_limited`, `error_type`, `input_tokens`, `output_tokens`
- Error classification is centralized in `ErrorClassifier`

### What Needs to Change

1. **Backend.execute() signature** is prompt-only (`str`). The universal plugin needs structured input (messages, tools, system prompt).
2. **Tool handling** is currently only in OllamaBackend. Needs to be in the universal layer.
3. **Streaming** is not implemented in any HTTP backend. All use synchronous request/response.
4. **Rate limit header parsing** is not implemented. Only rate limit detection from error text/codes.
5. **The OpenAI-compatible family** is missing entirely -- no OpenAI, Groq, Together, or OpenRouter backend.
6. **Response normalization** happens ad-hoc in each backend. Needs a shared translation layer.

### Minimum Viable Plugin System

For v1, support these five plugin types:
1. **OpenAI-compatible** -- one plugin covering OpenAI, Azure, Groq, Together, OpenRouter by changing base_url + auth headers
2. **Anthropic** -- already mostly built
3. **Gemini** -- new, different enough to need its own plugin
4. **Bedrock Converse** -- new, wraps all Bedrock models
5. **Ollama** -- already built, just needs Protocol alignment

The OpenAI-compatible plugin alone covers 6+ providers with just config changes (base URL, auth header, model name). This is the highest-leverage implementation.
