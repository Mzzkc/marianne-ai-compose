# Thinking Lab — Instrument Catalog v1 Cross-Vendor Review

You are one of five independent reviewers. The other four cannot see your response. Bring your authentic perspective — your training lineage and what you actually know.

This review feeds the v1 of Marianne's instrument catalog. Marianne is an open-source-first orchestration system; the catalog is the source of truth for which models composers reach for. The composer drafted v1 from public benchmarks, vendor docs, and judgment. They know the draft is biased and incomplete. **You are being asked to push back, correct, and especially to extend.**

---

## What we want from you

### 1. Rate the models you know well

For each musician id below that you have direct experience with — or strong evidence about from public benchmarks, vendor docs, or community signal — produce ratings on these axes (1–5, where 5 is class-leading):

- `reasoning` — multi-step logic, math, formal reasoning, planning
- `code_gen` — writing new code from spec
- `code_read` — understanding existing code
- `long_context` — effective use of large input windows (not just nominal size)
- `instruction_following` — adherence to system prompt and constraints
- `format_adherence` — JSON, YAML, structured output reliability
- `tool_use` — function calling, agentic loops, MCP
- `speed_tokens_per_sec` — actual inference speed under typical load
- `cost_efficiency` — quality-per-dollar (5 = excellent value, 1 = expensive for what you get)

Plus brief prose:

- 1–3 strengths (what it shines at)
- 1–3 weaknesses (where it stumbles)
- Quirks (failure modes, context-window cliffs, prompt sensitivities, refusal patterns)
- Confidence: high / medium / low

**Skip models you don't know well.** Saying "skip" is more useful than guessing. Web search is allowed if it would let you give a confident rating instead of skipping.

### 2. Push back on the composer's likely biases

The composer (Claude Opus 4.7) almost certainly:

- Over-rated Anthropic models (self-bias)
- Under-rated models from non-US labs (training-corpus skew)
- Under-rated open-weight models (subscription-tier familiarity)
- Got specialization-model details wrong (image-gen, video-gen, speech, embeddings — not their wheelhouse)
- Missed quirks they don't personally encounter

Where you can correct any of these, do.

### 3. Extend the list — this is critical

The list below is incomplete. The composer knows it. **You probably know free-tier or open-weight models that should be in the catalog and aren't.** Add them. Specifically watch for gaps in:

- **Open-weight LLMs the composer missed** — Qwen3 family, Yi, Mistral Small/Nemo/Pixtral, Cohere Command R+, DeepSeek-Coder-V2.5, Hermes / OpenChat / NousResearch fine-tunes, Phi family, SmolLM, MiniCPM, etc.
- **Free OpenRouter or HuggingChat models not listed**
- **Reasoning models** — QwQ-32B, GLM-Zero, anything reasoning-focused
- **Multimodal** — LLaVA, Pixtral, Qwen-VL, MiniCPM-V variants
- **Speech (audio-in)** — beyond Whisper: Conformer, SeamlessM4T, Canary, Parakeet
- **Speech (speech-out)** — beyond Kokoro/ElevenLabs: F5-TTS, GPT-SoVITS, OpenVoice, XTTS-v2, Bark, Sesame, Coqui
- **Image generation** — beyond Flux/SD: Pixart, Lumina, HiDream, DeepFloyd, Ideogram, Recraft, SDXL Lightning/Turbo
- **Video generation** — beyond what's listed: Wan, CogVideoX, Stable Video Diffusion, Mochi, Pyramid Flow
- **Embeddings/Reranking** — Cohere Embed v4, Nomic Vision, Mixedbread, Stella, Jina embeddings/reranker
- **Region/lineage diversity** — Chinese (Qwen, Yi, Doubao, ERNIE, GLM siblings), French (Mistral variants), Japanese (Sarashina, Karakuri), Korean (HyperCLOVA), Arabic (Falcon, Jais)

For each addition, provide what you'd put in a catalog entry: id, provider, family, approximate context window, license / availability, modality tags (text / vision / audio-in / image-gen / etc.), a one-sentence rationale for inclusion, and confidence on whatever ratings you give.

If you don't have signal on a model worth including, list its name with `propose_for_research: true` so the next refresh can dig deeper.

### 4. Use web search where it would change your answer

If a quick web search would let you confirm a release date, pricing change, or new model availability — do it. Cite URLs in your review. Don't fabricate; cite or skip.

---

## The current list (v1)

```
bge-large-en-v1.5
bge-reranker-v2-m3
claude-haiku-4-5-20251001
claude-opus-4-7
claude-sonnet-4-6
codestral:22b
cohere-rerank-3.5
deepseek-r1
deepseek-v3
distil-whisper-large-v3
elevenlabs-v3
flux-1-dev
flux-1-pro
flux-schnell
gemini-2.5-pro
gemini-3-flash-preview
gemini-3.1-pro-preview
gpt-5
gpt-5.5
hunyuan-video
imagen-3
kokoro-tts
llama3.3:70b
ltx-video
nomic-embed-text-v1.5
o4-mini
openrouter/google/gemma-4-31b-it:free
openrouter/meta-llama/llama-4-maverick:free
openrouter/minimax/minimax-m2.5:free
openrouter/nvidia/nemotron-3-super-120b-a12b:free
openrouter/z-ai/glm-4.5-air:free
phi-4:14b
qwen2.5-coder:32b
sora-2
stable-diffusion-3.5-large
text-embedding-3-large
veo-3
voyage-3
whisper-large-v3
zai-coding-plan/glm-4.7-flash
zai-coding-plan/glm-5-turbo
```

---

## Output format

Begin your review with a YAML front-matter block identifying you (your name and family — that's already templated by the score, but the file you write should still start with `---`).

Then a fenced YAML block with this structure (omit fields you have no signal on):

```yaml
ratings:
  <musician-id>:
    reasoning: 5
    code_gen: 4
    code_read: 4
    long_context: 4
    instruction_following: 5
    format_adherence: 5
    tool_use: 5
    speed_tokens_per_sec: 2
    cost_efficiency: 1
    strengths: ["…"]
    weaknesses: ["…"]
    quirks: ["…"]
    confidence: high

push_back:
  <musician-id>: "Composer's v1 likely says X. I think Y because Z (with citation if applicable)."

skipped:
  - <musician-id>   # honest "I don't know this well enough"

new_models:
  - id: <proposed-id>
    provider: <provider>
    family: <family>
    context_window: <int>
    max_output_tokens: <int>
    license: <e.g., apache-2, mit, proprietary, free-tier-via-openrouter>
    available_via: [<instrument>]   # which instrument can run it
    modality_tags: [text|vision|audio-in|speech-out|image-gen|video-gen|embedding|reranking|multimodal]
    task_tags: [code|reasoning|review|writing|...]
    notes: "Why this should be in the catalog"
    confidence: high|medium|low
    cite: "<url or doc reference if web search informed this>"

propose_for_research:
  - <model-name>: "Why this might warrant inclusion but I'd want a refresh to dig deeper"
```

Then a brief markdown section after the YAML for any narrative observations — patterns across models, ecosystem shifts the composer should know about, framing critiques. Keep it tight.

---

## Final framing

The composer is asking for correction, not validation. The most useful thing you can do is be specific where you disagree, and additive where the list is thin. Open-weight models, non-Western ecosystems, and specialized capabilities (speech/image/video/embedding) are the most likely gaps — lean into those.

Five voices. One catalog. The divergences are the signal.
