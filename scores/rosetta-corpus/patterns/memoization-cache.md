## Memoization Cache

`Status: Working` · **Source:** Dynamic programming, functional memoization (Bellman, 1957), Expedition 4. **Scale:** iteration + score-level. **Iteration:** 4.

### Core Dynamic

Workspace artifact `memo-cache.yaml` records input fingerprints and corresponding output fingerprints per stage. Before executing, the agent checks the cache: if the input fingerprint matches, reuse the cached output without re-execution. Not about caching LLM responses (infrastructure concern) — about recognizing at the ORCHESTRATION level that a stage's inputs haven't changed. Self-chaining scores that re-analyze unchanged modules are computing Fibonacci naively.

**Context invalidation:** Cache entries include a `context_hash` derived from prelude content and relevant workspace state beyond direct inputs. When the prelude changes (different instructions, updated conventions), the context hash invalidates affected entries even if input files are identical. The user-supplied `cache_check.py` script computes both input fingerprints and context hash.

### When to Use / When NOT to Use

Use for self-chaining scores where each iteration modifies only part of the workspace, concert campaigns where scores analyze overlapping inputs, or CEGAR Loops where refined modules need re-analysis but unchanged ones don't. Not when inputs change every iteration, cache management costs more than re-execution, or the context hash is too coarse (invalidating too much) or too fine (missing real invalidations).

### Marianne Score Structure

```yaml
sheets:
  - name: check-cache
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/cache_check.py --stage analysis --workspace {{ workspace }}"
  - name: analyze
    prompt: >
      Read memo-cache.yaml. Analyze ONLY files not in the cache (or with changed fingerprints).
      Update memo-cache.yaml with new entries: {file, input_hash, output_hash, context_hash, timestamp}.
    capture_files: ["memo-cache.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/memo-cache.yaml"
```

**Script dependency:** `cache_check.py` is user-supplied. Interface contract: `--stage NAME --workspace PATH`. Exits 0 if cache is valid and contains entries for the current stage. Exits 1 if cache needs rebuilding. The script computes SHA-256 fingerprints of input files and a context hash from prelude content.

### Failure Mode

Cache serves stale results because the context hash missed a relevant change (e.g., a prelude update changed the analysis criteria but not the input files). If cached results look wrong, clear the cache and re-run — the first run is no more expensive than running without memoization. Over-aggressive caching (caching everything) wastes disk and adds lookup overhead; only cache stages where re-execution is expensive.

### Composes With

CEGAR Loop (cache unchanged abstractions), Cathedral Construction (cache across cathedral iterations), Fixed-Point Iteration (cache stable regions during convergence)
