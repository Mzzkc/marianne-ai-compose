# Token Compression Strategies for Marianne

> **Status: Research / Not Implemented** — This document describes strategies under consideration. None have been implemented.

**Research Date:** 2026-01-05
**Priority:** High (cost/latency impact)

---

## Executive Summary

This document captures research on algorithmic token compression strategies applicable to Marianne AI Compose. The goal is reducing token usage (and thus cost/latency) while maintaining output quality.

**Key Finding:** Marianne's batch-oriented, declarative YAML model aligns naturally with prompt caching and prefix sharing strategies. The highest-impact, lowest-complexity strategy is **Anthropic's native prompt caching**, which can deliver 90% cost reduction with minimal code changes.

---

## Who This Is For

### Explicit Audience
- **Developers** running batch AI operations (code review, data processing, content generation)
- **Teams** needing cost optimization for high-volume Claude usage
- **Marianne contributors** implementing new optimization features

### Implicit Audience (P5 Recognition)
- **Marianne itself** - as a self-evolving system, Marianne may use this research to inform its own evolution decisions. The opus evolution cycles (v1→v4) demonstrate Marianne's capacity to improve its own score based on discovered patterns.

---

## Current State Analysis

### Existing Compression Patterns in Marianne

Marianne already implements basic compression thinking:

```python
# src/marianne/prompts/templating.py:222-224
if len(original_context) > 3000:
    truncation_msg = "\n\n[... original prompt truncated for brevity ...]"
    original_context = original_context[:3000] + truncation_msg
```

**Problem:** Naive truncation loses potentially important context. Smart compression would preserve semantics while reducing tokens.

### Token Flow in Marianne

```
YAML Config → PromptBuilder → [COMPRESSION POINT] → Backend → Claude API/CLI
                    ↓
            Template + Stakes + Thinking Method + Variables
                    ↓
            MZT_OPERATOR_IMPERATIVE injection (claude_cli.py:51-104)
                    ↓
            Final prompt (~1000-5000 tokens typical)
```

**Key Insight:** The MZT_OPERATOR_IMPERATIVE (53 lines, ~800 tokens) is injected into EVERY prompt. This is a prime candidate for caching.

---

## Strategy Ranking

| Rank | Strategy | Cost Savings | Latency Savings | Complexity | Marianne Fit |
|------|----------|--------------|-----------------|------------|------------|
| 1 | Anthropic Prompt Caching | 90% | 85% | Low | Excellent |
| 2 | Sheet Prefix Sharing | 30-50% | 20-40% | Medium | Excellent |
| 3 | Completion Prompt Summarization | 40-60% | 10-20% | Medium | Good |
| 4 | Semantic Deduplication | 30-40% | 5-10% | High | Moderate |
| 5 | LLMLingua/Extractive Compression | Up to 95% | Variable | High | Low |

---

## Strategy 1: Anthropic Prompt Caching (Recommended First)

### What It Is

Anthropic's native prompt caching allows marking portions of prompts as cacheable. Cached content is stored for 5-60 minutes and reused across API calls.

### Why It Fits Marianne

Marianne's batch model has high cache potential:

| Prompt Component | Per-Sheet Variation | Cache Candidate |
|------------------|---------------------|-----------------|
| MZT_OPERATOR_IMPERATIVE | None | YES (100%) |
| Template body | None | YES (100%) |
| Stakes | None | YES (100%) |
| Thinking method | None | YES (100%) |
| Variables (custom) | Rarely | YES (95%) |
| sheet_num, start_item, end_item | Always | NO |

**Estimated cacheable portion: 85-95% of tokens**

### Pricing Impact

| Operation | Cost (vs base input) |
|-----------|----------------------|
| Write to cache | 125% (one-time) |
| Read from cache | 10% |
| Uncached content | 100% |

**10-sheet job example:**
- Without caching: 10 × 1000 tokens = 10,000 tokens billed
- With caching: 1 × 1250 (write) + 9 × 100 (read) + 10 × 50 (variable) = 2,650 tokens equivalent
- **Savings: 73%**

### Implementation Plan

```python
# src/marianne/backends/anthropic_api.py (enhancement)

class AnthropicApiBackend(Backend):
    def __init__(self, ..., enable_prompt_caching: bool = True):
        self.enable_prompt_caching = enable_prompt_caching

    def _build_messages_with_caching(self, prompt: str) -> list[dict]:
        """Build messages with cache breakpoints."""
        if not self.enable_prompt_caching:
            return [{"role": "user", "content": prompt}]

        # Split prompt into cacheable and variable portions
        static_portion, variable_portion = self._split_prompt(prompt)

        return [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": static_portion,
                    "cache_control": {"type": "ephemeral"}  # 5-min TTL
                },
                {
                    "type": "text",
                    "text": variable_portion
                }
            ]
        }]
```

### Config Addition

```yaml
# New config option
backend:
  type: anthropic_api
  prompt_caching: true
  cache_ttl: "5m"  # or "1h" for longer jobs
```

### References

- [Anthropic Prompt Caching Docs](https://docs.claude.com/en/docs/build-with-claude/prompt-caching)
- [Anthropic Announcement](https://www.anthropic.com/news/prompt-caching)
- [Token Saving Updates](https://www.anthropic.com/news/token-saving-updates)

---

## Strategy 2: Sheet Prefix Sharing

### What It Is

When processing multiple sheets, identify the common prompt prefix and optimize how it's sent to the model. This is related to but distinct from API-level caching—it's about structuring prompts for optimal KV cache reuse at inference time.

### Why It Fits Marianne

All sheets in a Marianne job share:
- Same system instructions
- Same template structure
- Same stakes/thinking method
- Only sheet numbers vary

### Implementation Approach

```python
# src/marianne/execution/runner.py (enhancement)

class JobRunner:
    def _extract_common_prefix(self, prompts: list[str]) -> tuple[str, list[str]]:
        """Extract common prefix from all sheet prompts.

        Returns:
            (common_prefix, list_of_suffixes)
        """
        if not prompts:
            return "", prompts

        # Find longest common prefix
        prefix = prompts[0]
        for prompt in prompts[1:]:
            while not prompt.startswith(prefix):
                prefix = prefix[:-1]
                if not prefix:
                    return "", prompts

        suffixes = [p[len(prefix):] for p in prompts]
        return prefix, suffixes
```

### Batch API Optimization

When using the Anthropic API, batch multiple sheet requests:

```python
# Batch request structure
requests = [
    {
        "custom_id": f"sheet-{i}",
        "params": {
            "messages": [{"role": "user", "content": suffix}],
            "system": common_prefix,  # Shared across batch
        }
    }
    for i, suffix in enumerate(suffixes)
]
```

### References

- [BatchLLM Paper](https://arxiv.org/abs/2412.03594) - 1.3x-10.8x throughput improvement
- [SGLang RadixAttention](https://lmsys.org/blog/2024-01-17-sglang/) - KV cache reuse patterns

---

## Strategy 3: Completion Prompt Summarization

### What It Is

Replace naive truncation with intelligent summarization when building completion prompts for partial failures.

### Current Problem

```python
# Current naive approach (templating.py:222)
if len(original_context) > 3000:
    original_context = original_context[:3000] + "[truncated]"
```

This loses potentially crucial context from the end of the prompt.

### Proposed Solution

```python
# Enhanced approach
async def _summarize_context(self, original: str, max_tokens: int = 500) -> str:
    """Use small model to summarize long context."""
    if len(original) < 3000:
        return original

    # Use Haiku for cost-efficient summarization
    summary = await self.summary_backend.execute(
        f"Summarize this task context in {max_tokens} tokens, "
        f"preserving key requirements and constraints:\n\n{original}"
    )
    return summary.stdout
```

### Adaptive Focus Memory (AFM) Pattern

Research shows a three-tier fidelity approach works well:

| Fidelity | When to Use | Compression |
|----------|-------------|-------------|
| FULL | Recent/critical content | 0% |
| COMPRESSED | Important but not critical | 50-70% |
| PLACEHOLDER | Old/tangential content | 90%+ |

### Implementation Hooks

```python
# src/marianne/prompts/compression.py (new file)

from enum import Enum
from dataclasses import dataclass

class Fidelity(Enum):
    FULL = "full"
    COMPRESSED = "compressed"
    PLACEHOLDER = "placeholder"

@dataclass
class ContentBlock:
    content: str
    fidelity: Fidelity
    importance: float  # 0.0-1.0

class ContextCompressor:
    """Compress context using adaptive fidelity levels."""

    def compress(
        self,
        blocks: list[ContentBlock],
        target_tokens: int
    ) -> str:
        """Compress blocks to fit target token budget."""
        # Sort by importance
        sorted_blocks = sorted(blocks, key=lambda b: b.importance, reverse=True)

        result = []
        current_tokens = 0

        for block in sorted_blocks:
            block_tokens = self._estimate_tokens(block.content)

            if current_tokens + block_tokens <= target_tokens:
                result.append(block.content)
                current_tokens += block_tokens
            elif block.fidelity != Fidelity.PLACEHOLDER:
                # Try to compress
                compressed = self._compress_block(block, target_tokens - current_tokens)
                result.append(compressed)
                current_tokens += self._estimate_tokens(compressed)

        return "\n\n".join(result)
```

### References

- [Adaptive Focus Memory](https://arxiv.org/html/2511.12712)
- [Recursive Summarization for Dialogue](https://www.sciencedirect.com/science/article/abs/pii/S0925231225008653)
- [LLM Chat History Summarization Guide](https://mem0.ai/blog/llm-chat-history-summarization-guide-2025)

---

## Strategy 4: Semantic Deduplication

### What It Is

Detect and remove semantically redundant content before sending to the model.

### Where Redundancy Occurs in Marianne

| Source | Example | Typical Redundancy |
|--------|---------|-------------------|
| Learned patterns | Similar patterns from multiple sheets | 20-40% |
| Stakes + template | Repeated themes/instructions | 10-20% |
| Completion prompts | Original task echoed in failed validations | 30-50% |

### Implementation Approach

```python
# src/marianne/prompts/deduplication.py (new file)

from typing import Protocol
import hashlib

class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class SemanticDeduplicator:
    """Remove semantically redundant content."""

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        similarity_threshold: float = 0.85
    ):
        self.embeddings = embeddings
        self.threshold = similarity_threshold

    def deduplicate(self, segments: list[str]) -> list[str]:
        """Remove redundant segments using cosine similarity."""
        if len(segments) <= 1:
            return segments

        # Get embeddings
        vectors = self.embeddings.embed(segments)

        # Keep first occurrence, skip similar ones
        kept = [segments[0]]
        kept_vectors = [vectors[0]]

        for i, (segment, vector) in enumerate(zip(segments[1:], vectors[1:])):
            if not self._is_redundant(vector, kept_vectors):
                kept.append(segment)
                kept_vectors.append(vector)

        return kept

    def _is_redundant(
        self,
        vector: list[float],
        existing: list[list[float]]
    ) -> bool:
        """Check if vector is too similar to any existing."""
        for existing_vec in existing:
            similarity = self._cosine_similarity(vector, existing_vec)
            if similarity > self.threshold:
                return True
        return False
```

### Simpler Alternative: MinHash

For environments without embedding models, MinHash provides fast approximate deduplication:

```python
from datasketch import MinHash, MinHashLSH

def minhash_deduplicate(segments: list[str], threshold: float = 0.5) -> list[str]:
    """Deduplicate using MinHash LSH."""
    lsh = MinHashLSH(threshold=threshold, num_perm=128)
    kept = []

    for i, segment in enumerate(segments):
        mh = MinHash(num_perm=128)
        for word in segment.split():
            mh.update(word.encode('utf8'))

        # Check for similar existing segments
        result = lsh.query(mh)
        if not result:
            lsh.insert(f"seg_{i}", mh)
            kept.append(segment)

    return kept
```

### References

- [Context Window Efficiency Guide](https://dev.to/siddhantkcode/the-engineering-guide-to-context-window-efficiency-202b)
- [Data Deduplication at Trillion Scale](https://zilliz.com/blog/data-deduplication-at-trillion-scale-solve-the-biggest-bottleneck-of-llm-training)

---

## Strategy 5: LLMLingua / Extractive Compression

### What It Is

Use a smaller language model (GPT-2 Small, LLaMA-7B) to identify and remove non-essential tokens while preserving meaning.

### The LLMLingua Family

| Version | Focus | Compression | Speed |
|---------|-------|-------------|-------|
| LLMLingua v1 | General prompts | Up to 20x | Baseline |
| LongLLMLingua | Long contexts | 4-6x (quality focus) | Baseline |
| LLMLingua-2 | Speed + accuracy | 3-6x | 3-6x faster |

### Why Lower Priority for Marianne

| Consideration | Impact |
|---------------|--------|
| Requires external model | Added complexity, latency |
| Best for RAG/documents | Marianne prompts are structured, not documents |
| Diminishing returns | Prompt caching already gives 90% savings |
| Dependency management | Additional Python packages |

### If Implementing

```python
# src/marianne/prompts/llmlingua.py (optional)

try:
    from llmlingua import PromptCompressor
    LLMLINGUA_AVAILABLE = True
except ImportError:
    LLMLINGUA_AVAILABLE = False

class LLMLinguaCompressor:
    """Optional LLMLingua integration for aggressive compression."""

    def __init__(self, model_name: str = "microsoft/llmlingua-2-xlm-roberta-large"):
        if not LLMLINGUA_AVAILABLE:
            raise ImportError("llmlingua not installed. Run: pip install llmlingua")

        self.compressor = PromptCompressor(model_name=model_name)

    def compress(
        self,
        prompt: str,
        target_ratio: float = 0.5,
        force_tokens: list[str] | None = None
    ) -> str:
        """Compress prompt to target ratio.

        Args:
            prompt: Original prompt text
            target_ratio: Target compression (0.5 = 50% of original)
            force_tokens: Tokens that must be preserved
        """
        result = self.compressor.compress_prompt(
            prompt,
            rate=target_ratio,
            force_tokens=force_tokens or [],
        )
        return result["compressed_prompt"]
```

### References

- [Microsoft LLMLingua](https://github.com/microsoft/LLMLingua) - EMNLP'23, ACL'24
- [LongLLMLingua Paper](https://arxiv.org/abs/2310.06839)
- [PCToolkit](https://arxiv.org/abs/2403.17411) - Unified compression toolkit

---

## Implementation Roadmap

### Phase 1: Anthropic Prompt Caching (Low Effort, High Impact)

**Effort:** 1-2 days
**Impact:** 70-90% cost reduction for API backend users

Tasks:
1. Add `prompt_caching` config option to `BackendConfig`
2. Implement `_build_messages_with_caching()` in `AnthropicApiBackend`
3. Add cache metrics to execution result (cache_hit, tokens_saved)
4. Update outcome tracking to record caching effectiveness
5. Document in README

### Phase 2: Smart Completion Summarization (Medium Effort, Medium Impact)

**Effort:** 2-3 days
**Impact:** 40-60% reduction in completion prompt tokens

Tasks:
1. Create `src/marianne/prompts/compression.py` module
2. Implement `ContextCompressor` with fidelity levels
3. Replace naive truncation in `build_completion_prompt()`
4. Add summarization backend option (Haiku for cost)
5. A/B test completion success rates

### Phase 3: Prefix Optimization (Medium Effort, Variable Impact)

**Effort:** 3-5 days
**Impact:** 20-40% latency reduction for multi-sheet jobs

Tasks:
1. Implement `_extract_common_prefix()` in JobRunner
2. Restructure prompt building to separate static/variable
3. Optimize batch API calls to share system prompts
4. Add prefix caching metrics

### Phase 4: Optional Advanced Compression (High Effort, Diminishing Returns)

**Effort:** 1-2 weeks
**Impact:** Additional 10-30% on top of previous phases

Tasks:
1. Add optional `llmlingua` dependency
2. Create `SemanticDeduplicator` protocol
3. Implement MinHash fallback for no-embedding environments
4. Add compression config section to YAML

---

## Metrics to Track

When implementing compression, track these metrics:

```python
@dataclass
class CompressionMetrics:
    """Metrics for compression effectiveness."""

    original_tokens: int
    compressed_tokens: int
    compression_ratio: float  # compressed / original

    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate: float = 0.0

    tokens_saved: int = 0
    estimated_cost_saved_usd: float = 0.0

    compression_latency_ms: float = 0.0
    quality_score: float | None = None  # If validated
```

Add to outcome recording:

```yaml
# In .marianne-outcomes.json
{
  "job_id": "my-job",
  "sheet_num": 1,
  "compression": {
    "strategy": "prompt_caching",
    "original_tokens": 4500,
    "compressed_tokens": 500,
    "cache_hit": true,
    "tokens_saved": 4000
  }
}
```

---

## TDF Analysis Summary

| Domain | Key Insight |
|--------|-------------|
| **COMP** | Clear integration points exist (PromptBuilder, Backend). Prompt caching requires API backend, not CLI. |
| **SCI** | Benchmarks show 90% savings achievable. RAG systems report 30-40% redundancy. |
| **CULT** | Anthropic's caching is Claude-native (no external dependencies). DSPy philosophy differs (signatures vs prompts). |
| **EXP** | Prompt caching "feels right" for Marianne's declarative model. LLMLingua feels like overkill. |
| **META** | This research itself demonstrates P5—Marianne documenting how to improve Marianne. |

---

## References

### Academic Papers
- [LLMLingua (EMNLP'23)](https://arxiv.org/abs/2310.05736)
- [LongLLMLingua (ACL'24)](https://arxiv.org/abs/2310.06839)
- [BatchLLM](https://arxiv.org/abs/2412.03594)
- [PCToolkit](https://arxiv.org/abs/2403.17411)
- [Adaptive Focus Memory](https://arxiv.org/html/2511.12712)

### Industry Documentation
- [Anthropic Prompt Caching](https://docs.claude.com/en/docs/build-with-claude/prompt-caching)
- [Microsoft LLMLingua GitHub](https://github.com/microsoft/LLMLingua)
- [DSPy Framework](https://dspy.ai/)

### Practical Guides
- [Token Compression Guide](https://medium.com/@yashpaddalwar/token-compression-how-to-slash-your-llm-costs-by-80-without-sacrificing-quality-bfd79daf7c7c)
- [DataCamp Prompt Compression Tutorial](https://www.datacamp.com/tutorial/prompt-compression)
- [FreeCodeCamp Compression Guide](https://www.freecodecamp.org/news/how-to-compress-your-prompts-and-reduce-llm-costs/)

---

*Document created: 2026-01-05*
*Research conducted by: Claude (via Marianne parallel session)*
*For use by: Marianne evolution cycles, human contributors*
