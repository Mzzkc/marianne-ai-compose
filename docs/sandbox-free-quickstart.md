# Sandbox-Free Quickstart

**Goal:** get from `pip install` to a validated, AI-generated result in ~10
minutes, on a **free-tier model**, without ever touching Marianne's sandbox.

## Why "sandbox-free"?

Marianne has an optional code-execution sandbox (`code_mode`, backed by
`bwrap`). The historical path-mismatch blocker is marked closed in project
status, but `code_mode` is still a specialized, opt-in generated-code path.
This guide keeps new composers on CLI instruments first.

The good news: the sandbox is **opt-in**. It only runs for sheets that
explicitly set a `code_mode:` block. Every **CLI instrument**
(`opencode`, `claude-code`, `goose`, `codex`, `gemini-cli`, `aider`, `crush`,
`cline-cli`) runs the model in its own process and **never touches the
sandbox**. So the entire normal workflow — the one this guide uses — is
sandbox-free today.

**Rule of thumb:** don't put `code_mode:` in first-run scores. Use CLI
instruments unless you specifically need generated-code execution.

---

## Step 1 — Install Marianne

```bash
git clone https://github.com/Mzzkc/marianne-ai-compose.git
cd marianne-ai-compose
python -m venv .venv && source .venv/bin/activate
pip install -e ".[daemon]"
mzt --version
```

## Step 2 — Pick a sandbox-free instrument

All options below avoid the sandbox. Pick **one**.

### Option A — `opencode` + OpenRouter free tier (recommended, $0, no card)

The fastest zero-cost path. Free-tier OpenRouter models cost $0 and need no
credit card.

```bash
npm install -g opencode-ai      # or: brew install opencode
opencode providers login        # choose "OpenRouter", then the free tier
```

The bundled `opencode` instrument profile
(`src/marianne/instruments/builtins/opencode.yaml`) already lists a curated set
of `:free` models — coding, reasoning, general-purpose, and fast/light tiers.
The example score below uses `google/gemma-4-31b-it:free`.

### Option B — `opencode` + local Ollama (fully offline, $0)

No network, no account. Register Ollama as a custom OpenAI-compatible provider
in `~/.config/opencode/opencode.json`:

```json
{
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "gemma2:27b": {} }
    }
  }
}
```

Then pull a model (`ollama pull gemma2:27b`) and set
`instrument_config.model: "ollama/gemma2:27b"` in your score.

### Option C — a CLI you already subscribe to

If you have a Claude Max subscription, a Google AI subscription, or a Z.AI
Coding Plan, the `claude-code`, `gemini-cli`, or `opencode` (with the
`zai-coding-plan` provider) instruments all work and are all sandbox-free.

> **First-run route:** `examples/getting-started/hello-setup.yaml` discovers a
> ready free, local, or paid instrument path and writes a resolved hello score.
> Direct `hello.yaml` remains useful once the instrument path is known, but
> current validation may warn about its raw-CLI fallback chain.
> `hello-local.yaml` (below) is the pure-offline twin: same orchestration, but
> it skips OpenRouter attempts and runs entirely on your local model.

## Step 3 — Run the curated example score

For the broad first-run path, use `hello-setup`:

```bash
mzt start
mzt run examples/getting-started/hello-setup.yaml
mzt status hello
open workspaces/hello/the-sky-library.html
```

[`examples/getting-started/hello-local.yaml`](https://github.com/Mzzkc/marianne-ai-compose/blob/main/examples/getting-started/hello-local.yaml)
runs entirely on a local Ollama model — no cloud, no account. Same orchestration
as the flagship `hello`: parallel agents write a short story, one composes an
ambient soundtrack, one writes the synthesis finale that braids the threads
together, and a deterministic tool builds the finished website and opens it.

```bash
mzt start                                          # start the conductor
mzt run examples/getting-started/hello-local.yaml  # run it
mzt status hello-local                             # watch progress
open workspaces/hello-local/the-sky-library.html   # read the result
```

To use a different free model, edit `instrument_config.model` in the score (any
`:free` model from the opencode profile, or your local `ollama/<model>`).

## Step 4 — Validate before you run (optional but recommended)

`mzt validate` checks YAML, schema, Jinja templates, paths, and regexes
**without** a running conductor:

```bash
mzt validate examples/getting-started/hello-local.yaml
```

---

## What this path deliberately avoids

| Avoided | Why |
|---|---|
| `code_mode:` in first-run scores | It is an opt-in generated-code path; CLI instruments are the safer onboarding route. |
| `claude-code` as the default | Requires a configured Claude Code installation; not a guaranteed free tier. |
| Restarting the conductor mid-run | Recovery of interrupted in-flight work is not fully reliable (KNOWN-ISSUES §3). |

See [KNOWN-ISSUES.md](../KNOWN-ISSUES.md) for the full list of alpha
limitations.
