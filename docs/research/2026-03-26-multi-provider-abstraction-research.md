# Multi-Provider Abstraction Research

**Purpose:** Inform the design of Marianne's instrument plugin system by analyzing how nine major AI orchestration frameworks handle multi-model/multi-provider abstraction.

**Date:** 2026-03-26

> Historical research snapshot. Descriptions of Marianne's then-current
> backends predate the profile-driven execution cleanup completed in July 2026.

---

## Executive Summary

After analyzing LiteLLM, LangChain, CrewAI, Mastra, AutoGen, Semantic Kernel, Haystack, DSPy, and Pydantic AI, five key findings emerge:

1. **The `provider/model` string format is universal.** Every framework that supports multiple providers uses it. LiteLLM pioneered it, and DSPy, CrewAI, Mastra, and Pydantic AI all adopted it. Marianne should too.

2. **OpenAI's API shape is the de facto lingua franca.** LiteLLM normalizes everything to OpenAI's chat completion format. Frameworks that don't use LiteLLM still model their interfaces after OpenAI's patterns (messages, tool_calls, streaming chunks). Fighting this is wasted energy.

3. **The hardest problem is parameter normalization, not routing.** Routing is solved (string prefix → handler). The real complexity is: what happens when you send `temperature=0.7` to a model that doesn't support it? Or `tool_choice="required"` to a provider that calls it something else? `drop_params` exists because this problem has no clean solution.

4. **Fallback across providers is rare and fragile.** Only LiteLLM (Router), Pydantic AI (FallbackModel), and Mastra have explicit cross-provider fallback. Most frameworks leave retry/fallback to the user. The ones that do it well treat it as a separate layer above the model abstraction, not inside it.

5. **Two architectural patterns dominate: proxy vs. inheritance.** LiteLLM/Mastra use the proxy pattern (one function, routes internally). LangChain/Semantic Kernel/AutoGen use inheritance (base class, override methods). The proxy pattern scales better for provider count; inheritance scales better for provider-specific features.

---

## Framework-by-Framework Analysis

### 1. LiteLLM — The Multi-Provider Proxy

**What it is:** A unified interface to 90+ LLM providers using the OpenAI chat completion format.

**Model abstraction:** Single `completion()` function. Provider determined by model string prefix (`openai/gpt-4`, `anthropic/claude-3`, `azure/gpt-4`). Internally routes through a giant if/elif chain matching `custom_llm_provider` values to provider-specific handlers.

**Configuration:** Code-based (Python kwargs) or YAML (for the proxy server). The proxy config uses a `model_list` with entries containing `model_name` (alias) and `litellm_params` (actual model, API key, base URL, RPM/TPM limits).

**Provider switching:** Change the model string. That's it. Same code, different string.

**Parameter normalization:** This is LiteLLM's core complexity. Three mechanisms:
- `get_optional_params()` — extracts provider-compatible params from the universal set
- `drop_params=True` — silently removes params unsupported by the target provider (global, per-request, or per-proxy-config)
- `additional_drop_params` — explicit list of params to drop, supports JSONPath for nested fields (e.g., `tools[*].input_examples`)
- `allowed_openai_params` — whitelist approach (inverse of drop_params)

Each provider has a dedicated config class (e.g., `BaseAnthropicMessagesConfig`) that defines its supported param set and handles translation.

**Fallback/retry:** The Router component handles this. `model_list` entries with the same `model_name` form a deployment group. The router load-balances across them and fails over when one goes down. Fallback ordering via `order` parameter. Cooldown mechanism: failed deployments get 5-second cooldown, auto-re-enabled when cooldown expires.

**Tool calling:** Normalized to OpenAI's function calling format. Each provider handler translates to/from the provider's native format internally.

**Rate limits:** Router tracks RPM/TPM per deployment. Strategies: simple-shuffle (weighted by RPM/TPM), latency-based, usage-based (via Redis), cost-based. Rate limit 429 errors trigger immediate cooldown.

**Cost tracking:** Automatic per-model cost calculation using a centralized `model_cost` dictionary. Supports token-based, character-based, time-based, and custom metrics. Hierarchical lookup: `provider/region/model` → `provider/model` → `model`. Tracks by API key, user, team, and custom tags.

**Streaming:** Unified `stream=True` flag. Returns OpenAI-format chunks regardless of provider. Async iteration via `__anext__()`.

**Plugin model:** No formal plugin system. Adding a provider means adding a new elif branch in `completion()`, a new provider module, a config class, and entries in the model cost map. LiteLLM's JSON provider registry (`JSONProviderRegistry`) allows some dynamic registration.

**Error handling:** Maps all provider errors to OpenAI exception types. Existing OpenAI error handlers work out of the box.

**Key insight for Marianne:** LiteLLM's `drop_params` is a pragmatic admission that universal parameter normalization is impossible. The question isn't "how do we translate all params" but "how do we gracefully handle params that don't translate." Marianne needs an equivalent mechanism.

---

### 2. LangChain — The Inheritance Framework

**Model abstraction:** `BaseChatModel` base class extending `BaseLanguageModel[AIMessage]`. Implements the Runnable protocol for composability. Each provider is a separate class inheriting from `BaseChatModel`.

**Required interface:** Providers must implement:
- `_generate()` — core synchronous generation (messages → ChatResult)
- `_llm_type` property — identifier string
- Optional: `_stream()`, `_agenerate()`, `_astream()` for streaming/async

**Configuration:** Code-based instantiation. Each provider class has its own constructor params. No unified config format.

**Provider switching:** Swap the class instantiation. Different import, different constructor args.

**Parameter normalization:** Minimal. Each provider handles its own params. `ModelProfile` objects allow capability declarations, but parameter translation is per-provider.

**Tool calling:** `bind_tools()` method — each provider overrides it. `with_structured_output()` wraps tool binding with output parsers (Pydantic schemas → provider-native format). Not all providers support it.

**Plugin model:** Separate packages per provider (`langchain-openai`, `langchain-anthropic`, etc.). 1000+ integrations across the ecosystem. Community providers live in `langchain-community`. Adding a provider = publish a new package implementing `BaseChatModel`.

**Streaming:** `_should_stream()` determines capability. Providers yield `ChatGenerationChunk` objects. Fallback to synchronous `_generate()` when streaming unavailable.

**Key insight for Marianne:** LangChain's per-provider package model is the most maintainable long-term, but it trades away the "change a string to switch providers" ergonomic. Marianne's YAML-driven model means we want the proxy pattern for users but can use inheritance internally.

---

### 3. CrewAI — The Pragmatic Hybrid

**Model abstraction:** Two tiers. Native SDK integrations for major providers (OpenAI, Anthropic, Google, Azure, Bedrock). Everything else through LiteLLM.

**Configuration:** Three formats:
- Environment variables: `MODEL=model-id`
- YAML: `llm: provider/model-id` per agent
- Python: `LLM(model="model-id", temperature=0.7)`

**Multi-model teams:** Different agents can use different providers. Each agent's `llm` field specifies its model independently.

**Tool calling:** Provider-native. OpenAI uses function calling, Claude uses native tool use. No universal abstraction — leverages each provider's strengths.

**Key insight for Marianne:** CrewAI's two-tier approach (native for majors, LiteLLM for the long tail) is pragmatic. Marianne could do the same: hand-crafted backends for Claude CLI, Anthropic API, and OpenAI-compatible, with a LiteLLM passthrough for everything else.

---

### 4. Mastra — The Model Router

**Model abstraction:** String-based model specification (`provider/model-name`). Model Router provides access to 94+ providers and 3388+ models.

**Architecture:** Direct routing for major providers (Anthropic, Google, OpenAI) using official APIs. OpenAI-compatible endpoints for the broader ecosystem. This dual approach gives lowest latency for majors and maximum compatibility for others.

**Configuration:** TypeScript-native. Model strings with full IDE autocomplete. API keys from environment variables.

**Dynamic registry:** Fetches available models from models.dev and OpenRouter at runtime. New models appear in IDE autocomplete without package updates.

**Fallback:** Automatic model fallbacks built into the router.

**Schema compatibility:** Uses compatibility layers to normalize tool schemas across providers.

**Key insight for Marianne:** Mastra's dynamic registry is compelling — a model catalog that stays current without code changes. Marianne's instrument profiles could work similarly: a registry of known models with capabilities, updated independently of the core code.

---

### 5. AutoGen (Microsoft) — Per-Agent Model Clients

**Model abstraction:** `ChatCompletionClient` protocol interface. Providers implement `create()` returning `CreateResult` with content, usage, and metadata.

**Configuration:** Code-based. Each provider is a different client class:
```python
client = OpenAIChatCompletionClient(model="gpt-4", api_key="...")
client = AnthropicChatCompletionClient(model="claude-3", api_key="...")
```

**Capability metadata:** `model_info` dict declares capabilities (vision, function_calling, json_output). This enables runtime feature detection.

**Tool calling:** Normalized through `convert_tools()` → `ChatCompletionToolParam`. Tool names sanitized via `normalize_name()` (invalid chars → underscores, max 64 chars). Streaming tool calls accumulated from fragmented deltas.

**Cost tracking:** Dual-tracking: `_total_usage` and `_actual_usage` accumulators. Granular token counting: 3 tokens per message envelope, vision tokens via tile-based model, tool tokens parsed from function definitions.

**Key insight for Marianne:** AutoGen's `model_info` capability metadata is exactly what Marianne needs for `InstrumentProfile`. Declaring capabilities upfront (supports_tools, supports_vision, max_context_tokens) lets the orchestrator make routing decisions without trial-and-error.

---

### 6. Semantic Kernel (Microsoft) — Service Registry

**Model abstraction:** Each provider is a "Connector" package (`Microsoft.SemanticKernel.Connectors.OpenAI`, etc.). Services are registered into a Kernel via builder pattern.

**Configuration:** Code-based registration. Each connector has provider-specific constructor args. Services get a `serviceId` for targeting specific backends within a kernel.

**Provider switching:** Register multiple services with different IDs. Select by ID at runtime. Also supports OpenAI-compatible endpoints as fallback for unknown providers.

**Tool calling:** Function calling is a first-class concern. SK considers it "the most important" capability — models without it can't call existing code. Tools are exposed as Kernel Functions.

**Key insight for Marianne:** SK's `serviceId` pattern — registering multiple backends and selecting by ID — maps cleanly to Marianne's instrument concept. A score could declare instruments by name, and the conductor resolves names to configured backends.

---

### 7. Haystack (deepset) — Pipeline Components

**Model abstraction:** "Generators" are pipeline components. Each provider has its own Generator class (`OpenAIChatGenerator`, `AnthropicChatGenerator`). 40+ generator implementations.

**Configuration:** Per-generator constructor params. Generators are plugged into Pipeline graphs.

**Provider switching:** Swap the generator component in the pipeline. Same pipeline topology, different generator class.

**Tool calling:** Unified `Tool` abstraction with JSON schema for parameters. `ToolInvoker` component executes tool calls. `@tool` decorator and `create_tool_from_function` auto-generate schemas from Python type hints. Works across generators that support the `tools` parameter, but not all generators support it.

**Key insight for Marianne:** Haystack's separation of Tool definition (schema) from Tool invocation (ToolInvoker) from Model (Generator) is clean. Marianne could similarly separate tool/technique declaration from execution from backend routing.

---

### 8. DSPy — Programmatic Model Interface

**Model abstraction:** `dspy.LM()` constructor with `provider/model-name` format. Single interface, internally uses LiteLLM for provider routing.

**Configuration:** Code-based:
```python
lm = dspy.LM('openai/gpt-4o-mini', api_key='...')
dspy.configure(lm=lm)
```

**Provider switching:** Change the model string. `dspy.context()` manager for thread-safe scoped switching:
```python
with dspy.context(lm=dspy.LM('anthropic/claude-3')):
    # This block uses Claude
```

**Provider-specific params:** Prefixed to avoid conflicts: `vertex_credentials`, `vertex_project`, `vertex_location`.

**Tool calling:** `dspy.Tool` wraps Python functions, auto-generates JSON schemas in LiteLLM/OpenAI function calling format. Native function calling when model supports it (auto-detected via `litellm.supports_function_calling()`). Falls back to text-based parsing for models without native support.

**Key insight for Marianne:** DSPy's `dspy.context()` scoped switching is elegant for multi-model workflows. Marianne's sheet-level instrument overrides serve the same purpose but in YAML form. The fallback from native tool calling to text-based parsing is important — Marianne needs graceful degradation for instruments without tool support.

---

### 9. Pydantic AI — Type-Safe Model Abstraction

**Model abstraction:** Three-tier: Models (vendor SDK wrappers), Providers (auth/connection), Profiles (request construction rules). Shorthand format: `"openai:gpt-4o"`.

**Configuration:** Per-model instantiation with `ModelSettings` (temperature, max_tokens, etc.). Each model can have independent settings.

**Fallback:** `FallbackModel` — chains multiple models tried in sequence:
```python
fallback = FallbackModel(openai_model, anthropic_model)
```
Two trigger modes:
- **Exception-based:** Triggers on `ModelAPIError` (4xx/5xx). Default behavior.
- **Response-based:** Custom predicates check response content (finish_reason, content quality). Non-streaming only.

Combined handlers allow mixing exception types, exception handlers, and response predicates.

**Important design note:** Validation errors don't trigger fallback — they use retry. Provider SDK retries can delay fallback activation; recommend `max_retries=0` on inner models.

**Tool calling:** Built-in tool support. Profiles handle provider-specific JSON schema restrictions for tools.

**Key insight for Marianne:** Pydantic AI's FallbackModel with response-based triggering is the most sophisticated fallback pattern found. Marianne could use this for instrument failover: not just "did the API error" but "did the output meet quality thresholds."

---

## Cross-Framework Comparison Matrix

| Dimension | LiteLLM | LangChain | CrewAI | Mastra | AutoGen | Semantic Kernel | Haystack | DSPy | Pydantic AI |
|---|---|---|---|---|---|---|---|---|---|
| **Pattern** | Proxy | Inheritance | Hybrid | Proxy | Inheritance | Registry | Components | Proxy (via LiteLLM) | Inheritance |
| **Config format** | Code + YAML | Code | Code + YAML | Code | Code | Code | Code | Code | Code |
| **Provider string** | `provider/model` | N/A (class) | `provider/model` | `provider/model` | N/A (class) | N/A (class) | N/A (class) | `provider/model` | `provider:model` |
| **Drop params** | Yes (first-class) | No | Via LiteLLM | Unknown | No | No | No | Via LiteLLM | No |
| **Cross-provider fallback** | Router | No | No | Yes | No | No | No | No | FallbackModel |
| **Tool normalization** | OpenAI format | bind_tools() | Provider-native | Schema compat | convert_tools() | Kernel Functions | Tool + ToolInvoker | dspy.Tool + LiteLLM | Profiles |
| **Rate limit handling** | RPM/TPM tracking | No | No | Unknown | No | No | No | No | No |
| **Cost tracking** | Comprehensive | No | No | Unknown | Token counting | No | No | No | No |
| **Streaming unified** | Yes | Yes (chunks) | Provider-dependent | Yes | Yes (delta accumulation) | Partial (no Java) | Provider-dependent | Via LiteLLM | Yes |
| **Plugin extensibility** | Provider modules | Separate packages | LiteLLM for long tail | Dynamic registry | Client classes | Connector packages | Generator classes | Via LiteLLM | Model classes |

---

## Key Patterns and Anti-Patterns

### Patterns That Work

**1. The `provider/model` string as the universal identifier.**
Every proxy-pattern framework uses it. It's simple, greppable, and YAML-friendly. Separator varies (slash vs colon) but the concept is universal. Marianne should adopt `provider/model` for instrument specification in scores.

**2. Capability metadata declared upfront.**
AutoGen's `model_info`, LangChain's `ModelProfile`, Mastra's dynamic model registry — all declare what a model can do (tools, vision, streaming, structured output) before you try to use it. This prevents runtime surprises. Marianne's `InstrumentProfile` should include: `supports_tools`, `supports_streaming`, `max_context_tokens`, `supports_structured_output`, `cost_per_input_token`, `cost_per_output_token`.

**3. Separation of concerns: routing vs. parameter translation vs. fallback.**
LiteLLM keeps these as distinct layers:
- Routing: model string → provider handler
- Translation: universal params → provider-specific params (with drop_params as escape hatch)
- Fallback: Router layer above completion()

Marianne should mirror this: InstrumentRegistry (routing) → Backend (translation) → Conductor (fallback/retry).

**4. Provider-specific config classes.**
LiteLLM's per-provider config classes (e.g., `BaseAnthropicMessagesConfig`) that define supported params are the cleanest approach to parameter normalization. Better than giant mapping tables.

**5. Tool schemas from Python type hints.**
Haystack's `@tool` decorator and DSPy's `dspy.Tool` both auto-generate JSON schemas from function signatures. Marianne's technique definitions could do the same for MCP tools.

### Anti-Patterns to Avoid

**1. Giant if/elif chains for provider routing.**
LiteLLM's `completion()` function is a 2000+ line if/elif chain. It works but is unmaintainable. Use a registry/dispatcher pattern instead.

**2. Assuming all providers support all features.**
Haystack's tool support is incomplete ("not all Chat Generators currently support tools"). Marianne must treat capability detection as a first-class concern, not an afterthought.

**3. Tight coupling between orchestration and provider specifics.**
CrewAI's "use each provider's native tool calling" approach means orchestration code must know about provider capabilities. Keep provider knowledge in the backend/instrument layer, not in the runner.

**4. Ignoring the "long tail" of providers.**
Mastra and CrewAI both handle this by routing unknown providers through OpenAI-compatible endpoints. Marianne should support an "openai-compatible" backend type that works with any provider exposing the OpenAI chat completions API.

**5. Conflating retry with fallback.**
Pydantic AI explicitly separates them: validation errors → retry (same model), API errors → fallback (different model). Marianne should too.

---

## Common Failure Modes in Multi-Provider Abstraction

1. **Parameter drift.** Providers add new params (e.g., `reasoning_effort`, `thinking`). The abstraction layer lags behind. LiteLLM mitigates this with frequent releases; others fall behind.

2. **Schema incompatibility.** Tool calling schemas differ subtly between providers (required fields, nesting depth, enum handling). Normalization is never perfect.

3. **Streaming format divergence.** Chunk formats, finish reasons, and usage reporting vary. Delta accumulation for tool calls is especially tricky (AutoGen handles this with indexed fragment accumulation).

4. **Cost data staleness.** Model pricing changes. LiteLLM's cost map needs constant updates. Any cost tracking system needs a mechanism for updates independent of code releases.

5. **Rate limit semantics vary.** Some providers return 429, others return 529, others return 200 with an error body. Some have per-minute limits, others per-day. Normalization requires provider-specific detection logic (which Marianne already has in `ErrorClassifier`).

6. **Authentication diversity.** API keys, OAuth tokens, AWS credentials, service accounts — every provider is different. The abstraction must support pluggable auth without exposing it in the universal interface.

---

## Recommendations for Marianne's Instrument Plugin System

### Architecture

```
Score YAML (instrument: "anthropic/claude-sonnet-4-20250514")
    ↓
InstrumentRegistry (resolves name → InstrumentProfile + Backend)
    ↓
Backend (implements execute(), translates params, handles auth)
    ↓
Conductor (manages fallback, rate limits, cost tracking across backends)
```

### Specific Recommendations

1. **Adopt `provider/model` string format in scores.** Use `/` separator (matches LiteLLM, DSPy, CrewAI conventions).

2. **Build InstrumentProfile as a Pydantic model** with declared capabilities: `supports_tools`, `supports_streaming`, `max_context_tokens`, `supports_structured_output`, `cost_per_input_token`, `cost_per_output_token`, `supported_params`. The runner checks capabilities before attempting features.

3. **Implement `drop_params` equivalent.** When a score specifies `temperature: 0.7` but the target instrument doesn't support it, either drop silently (configurable) or warn. Per-instrument `supported_params` set makes this deterministic.

4. **Three-tier backend strategy:**
   - **Hand-crafted backends** for Claude CLI, Anthropic API (current)
   - **OpenAI-compatible backend** for any provider with OpenAI-format API (covers 80% of the long tail)
   - **LiteLLM passthrough backend** for everything else (if user has litellm installed)

5. **InstrumentRegistry as config-driven dispatcher.** YAML config maps instrument names to backend types + credentials:
   ```yaml
   instruments:
     anthropic/claude-sonnet-4-20250514:
       backend: anthropic-api
       api_key_env: ANTHROPIC_API_KEY
       max_context: 200000
       supports_tools: true
     openai/gpt-4o:
       backend: openai-compatible
       api_key_env: OPENAI_API_KEY
       base_url: https://api.openai.com/v1
       supports_tools: true
     ollama/llama3:
       backend: ollama
       base_url: http://localhost:11434
       supports_tools: false
   ```

6. **Fallback at the conductor level, not the backend level.** Following Pydantic AI's pattern: exception-based (API errors → try next instrument) and response-based (output quality check → try next instrument). Configure per-sheet:
   ```yaml
   sheets:
     - name: critical-analysis
       instrument: anthropic/claude-sonnet-4-20250514
       fallback:
         - openai/gpt-4o
         - ollama/llama3
       fallback_on: [api_error, rate_limit, quality_threshold]
   ```

7. **Cost tracking in ExecutionResult.** Marianne already has `input_tokens` and `output_tokens`. Add `cost_usd` calculated from InstrumentProfile pricing. Aggregate at the job level.

8. **Don't build a model cost database.** LiteLLM maintains one; Marianne shouldn't duplicate it. If LiteLLM is available, use its cost data. Otherwise, let users declare costs in instrument profiles.

---

## What Marianne Already Has Right

Reviewing `src/marianne/backends/base.py`:

- **`Backend` ABC with `execute()` + `health_check()`** — clean abstract interface, similar to LangChain's `BaseChatModel` but simpler
- **`ExecutionResult` with token tracking** — already captures `input_tokens`, `output_tokens`, `model`, `rate_limited`
- **`apply_overrides()` / `clear_overrides()`** — per-sheet parameter overrides already exist, solving the "different params per execution" problem
- **`_detect_rate_limit()` using ErrorClassifier** — centralized rate limit detection, ahead of most frameworks
- **`HttpxClientMixin`** — shared HTTP client lifecycle, good for API-based backends
- **Four backends already** — `ClaudeCliBackend`, `AnthropicApiBackend`, `OllamaBackend`, `RecursiveLightBackend`

The gap is not in the backend abstraction (which is solid) but in the **registry/routing layer** (no InstrumentRegistry), **capability metadata** (no InstrumentProfile), and **cross-provider fallback** (conductor retries same backend, doesn't fail over to different backend).

---

## Sources

- [LiteLLM Documentation](https://docs.litellm.ai/docs/)
- [LiteLLM Streaming + Async](https://docs.litellm.ai/docs/completion/stream)
- [LiteLLM Routing](https://docs.litellm.ai/docs/routing)
- [LiteLLM drop_params](https://docs.litellm.ai/docs/completion/drop_params)
- [LangChain Provider Integrations](https://docs.langchain.com/oss/python/integrations/providers/overview)
- [CrewAI LLM Configuration](https://docs.crewai.com/concepts/llms)
- [Mastra Model Router Blog](https://mastra.ai/blog/model-router)
- [AutoGen Models Tutorial](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/models.html)
- [Semantic Kernel Chat Completion](https://learn.microsoft.com/en-us/semantic-kernel/concepts/ai-services/chat-completion/)
- [Haystack Generators](https://docs.haystack.deepset.ai/docs/generators)
- [Haystack Tool Abstraction](https://docs.haystack.deepset.ai/docs/tool)
- [DSPy Language Models](https://dspy.ai/learn/programming/language_models/)
- [DSPy Tool Integration](https://deepwiki.com/stanfordnlp/dspy/3.3-tool-integration-and-react-agents)
- [Pydantic AI Models](https://ai.pydantic.dev/models/)
- [LiteLLM GitHub - main.py](https://github.com/BerriAI/litellm/blob/main/litellm/main.py)
- [LiteLLM GitHub - provider routing](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/get_llm_provider_logic.py)
- [AutoGen GitHub - OpenAI client](https://github.com/microsoft/autogen/blob/main/python/packages/autogen-ext/src/autogen_ext/models/openai/_openai_client.py)
