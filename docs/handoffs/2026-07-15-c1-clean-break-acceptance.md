# C1 clean-break acceptance

## Accepted decision

C1 is an intentional breaking cleanup. Marianne has no compatibility audience
beyond its composer, who explicitly rejected legacy aliases and asked that
maintained examples be updated instead. The accepted boundary is:

- no `marianne.backends` package;
- no provider-specific Python executor classes;
- no built-in `anthropic_api`, `recursive_light`, or `claude_cli` profiles or
  compatibility aliases;
- Claude access through the `claude-code` agent harness;
- Ollama retained only as a useful YAML profile over its OpenAI-compatible API;
- unsupported wire schemas rejected at profile validation rather than silently
  routed through a provider special case.

This candidate is based on `79995feead02c4acbae772f4ac8ff3f29726dda5`
on `codex/marianne-c1-acting` and was committed as
`0cc3e675a9cad4b607ba83b4dd6d3a55c77617ff`. The reproducible commit-range
diff, excluding this report, is:

```text
5627dcbe4bed20d7c907eb33c6c98e9035347fe577511ae64e8e7c55a2abc57e
```

Generated with:

```bash
git diff --binary \
  79995feead02c4acbae772f4ac8ff3f29726dda5..0cc3e675a9cad4b607ba83b4dd6d3a55c77617ff \
  -- . \
  ':(exclude)docs/handoffs/2026-07-15-c1-clean-break-acceptance.md' \
  | sha256sum
```

The earlier pre-commit working-tree hash omitted untracked additions because
`git diff HEAD` does not include untracked files. The live Marianne Expert
identified that provenance defect; this commit-range hash replaces it.

## Implementation

The shared execution contract moved to `src/marianne/execution/base.py`, and
all production/test consumers now import it there. `backend_pool.py` constructs
the shared OpenAI-compatible HTTP executor for supported HTTP profiles. The
executor accepts a profile-selected base URL, endpoint, model, and optional
authentication environment variable without branching on provider names.

`src/marianne/instruments/builtins/ollama.yaml` selects
`http://localhost:11434/v1/chat/completions`, `schema_family: openai`, default
model `llama3.1:8b`, and no required key. Its capability claim is deliberately
limited to structured output; the deleted bespoke `/api/chat` MCP/tool loop is
not implied by this profile.

The Anthropic SDK dependency and lock entries are removed. Maintained source
defaults, templates, examples, scores, and current user documentation now use
current CLI profiles or Ollama. The Rosetta submodule and explicitly labelled
historical research/design snapshots retain period-accurate names. The earlier
compatibility-oriented acceptance plan is marked historical and superseded.

The plugin integration independently removed `anthropic_api` and
`recursive_light` from its catalog, routes Claude fallbacks through current CLI
profiles, and describes Ollama as OpenAI-compatible. That integration is commit
`f8e744d` in the plugin worktree and parent pointer commit `d0ea40e`.

## Test disposition

Deleting obsolete behavior includes deleting tests whose only contract was
that obsolete behavior. Retaining them would encode the rejected architecture.

| Deleted file | Disposition | Reason / replacement |
|---|---|---|
| `tests/test_anthropic_api_backend.py` | retired | Direct built-in Anthropic API executor removed. |
| `tests/test_backends.py` | retired | Native provider-class implementation tests removed with those classes. |
| `tests/test_ollama_backend.py` | retired | Bespoke Ollama `/api/chat` and translated MCP loop removed. |
| `tests/test_instrument_registry_bridge.py` | retired | Legacy/native-name bridge removed. |
| `tests/test_native_instrument_bridge.py` | migrated | Generic registry behavior moved to `tests/test_instrument_registry.py`. |

Retained behavior is tested in `tests/test_instrument_registry.py` and
`tests/test_profile_driven_http_backends.py`, plus the migrated HTTP dispatch,
built-in profile, CLI, daemon, configuration, cost, token, and user-journey
tests. Unsupported `anthropic` and `gemini` schema declarations have explicit
rejection tests. The removal is therefore a contract change, not a coverage
hole.

## Verification

Candidate-source provenance was established before testing:

```text
/home/emzi/workspaces/marianne-c1-acting/src/marianne/__init__.py
/home/emzi/workspaces/marianne-c1-acting/src/marianne/execution/base.py
```

The authoritative suite was run exactly once at a time with:

```bash
PYTHONPATH="$PWD/src" \
UV_CACHE_DIR=/tmp/marianne-c1-independent-cache \
uv run --no-sync pytest -q
```

Observed result:

```text
10739 passed, 70 skipped, 17 xfailed, 6 xpassed, 24 warnings in 122.74s
```

Additional gates passed:

- `uv lock --check` resolved 97 packages;
- all changed YAML parsed successfully;
- `git diff --check` passed;
- `src/marianne/backends` does not exist;
- no provider executor class or `marianne.backends` import remains;
- maintained examples/templates contain none of the rejected names;
- `mzt instruments check ollama` resolves the generic
  `http://localhost:11434/v1/chat/completions` endpoint;
- `mzt instruments check anthropic_api` returns `Unknown instrument`, as the
  breaking contract requires.

## Live acting acceptance

Marianne job `final-score` completed in one attempt in 7m35s with all eight
validations passing. The injected Marianne Expert reviewed exact commit
`0cc3e67`, proved candidate import provenance, ran 37 focused tests, verified
the compatibility/test disposition, and issued PASS without changing source.
The score then ran its candidate-bound full-suite validation successfully. Its
report is at
`/home/emzi/workspaces/marianne-expert-c1-final-20260715/final-review.md`.
