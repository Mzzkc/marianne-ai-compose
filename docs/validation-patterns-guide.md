# Validation Patterns Guide

Validations are how Marianne verifies that each sheet accomplished its goal. They run after every sheet execution, not just at the end of a job. When validations fail, the sheet retries (up to `retry.max_retries`). When more than `completion_threshold_percent` pass, Marianne enters completion mode — sending a focused prompt telling the agent what still needs fixing.

This guide teaches validation design patterns: how to combine validation types, avoid common pitfalls, and write validations that actually verify outcomes rather than just process.

---

## Table of Contents

- [The Two Syntax Systems](#the-two-syntax-systems)
- [The Five Validation Types](#the-five-validation-types)
- [Layered Validation Pattern](#layered-validation-pattern)
- [Staged Validations](#staged-validations)
- [Conditional Validations](#conditional-validations)
- [Command Succeeds Pitfalls](#command-succeeds-pitfalls)
- [Validation Anti-Patterns](#validation-anti-patterns)
- [Validation Best Practices](#validation-best-practices)
- [Quick Start Examples](#quick-start-examples)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

---

## The Two Syntax Systems

**Critical:** Marianne uses different template syntax in prompts versus validations. This is the #1 source of broken validations.

### Prompts: Jinja2 (`{{ }}`)

```yaml
prompt:
  template: |
    Write output to {{ workspace }}/sheet{{ sheet_num }}.md
```

- Double braces: `{{ variable }}`
- Supports conditionals, loops, filters
- Engine: Jinja2

### Validations: Python Format Strings (`{}`)

```yaml
validations:
  - type: file_exists
    path: "{workspace}/sheet{sheet_num}.md"
```

- Single braces: `{variable}`
- Simple substitution only
- Engine: Python `str.format()`

### What Goes Wrong

```yaml
# WRONG — Jinja syntax in validation path
validations:
  - type: file_exists
    path: "{{ workspace }}/output.md"
    # Python .format() treats {{ as literal {

# CORRECT
validations:
  - type: file_exists
    path: "{workspace}/output.md"
```

**Rule:** Use `{{ workspace }}` in the **prompt template**. Use `{workspace}` in **validation paths and commands**.

---

## The Five Validation Types

| Type | What It Checks | Best For |
|------|---------------|----------|
| `file_exists` | File exists and is a file | Basic output verification |
| `file_modified` | File mtime changed during sheet execution | Proving the agent edited a file |
| `content_contains` | Literal substring appears in file | Structural markers (headings, completion tags) |
| `content_regex` | Regex pattern matches file content | Flexible pattern matching |
| `command_succeeds` | Shell command exits with code 0 | Tests, builds, linting, complex logic |

### Available Variables in Validations

All validation `path`, `command`, and `working_directory` fields support:

- `{workspace}` — always available
- `{sheet_num}` — always available
- `{start_item}` — always available
- `{end_item}` — always available
- `{stage}` — when fan-out is configured
- `{instance}` — when fan-out is configured

**Not available:** `total_sheets`, user variables from `prompt.variables`, `previous_outputs`. Use `command_succeeds` if you need complex logic.

---

## Layered Validation Pattern

Don't rely on a single validation type. Layer coarse checks (file exists) to fine checks (content structure, actual execution).

```yaml
validations:
  # Layer 1: File exists
  - type: file_exists
    path: "{workspace}/analysis.md"
    stage: 1
    description: "Analysis file created"

  # Layer 2: Structure correct
  - type: content_contains
    path: "{workspace}/analysis.md"
    pattern: "## Findings"
    stage: 2
    description: "Has Findings section"

  - type: content_regex
    path: "{workspace}/analysis.md"
    pattern: "(?s)## Findings.*## Recommendations"
    stage: 2
    description: "Has both Findings and Recommendations"

  # Layer 3: Substantive content
  - type: command_succeeds
    command: 'test $(wc -w < "{workspace}/analysis.md") -ge 500'
    stage: 3
    description: "Analysis has at least 500 words"
```

**Why layer?** If the file doesn't exist, stages 2 and 3 are skipped (fail-fast). Each layer verifies a different aspect: presence, structure, substance.

---

## Staged Validations

The `stage` field (1-10) controls execution order with fail-fast between stages. If any validation in stage N fails, stages N+1 and higher are skipped.

### Build Pipeline Pattern

```yaml
validations:
  # Stage 1: Syntax and style (fast)
  - type: command_succeeds
    command: 'ruff check {workspace}/src/'
    stage: 1
    description: "Lint passes"

  # Stage 2: Tests (only if lint passes)
  - type: command_succeeds
    command: 'cd {workspace} && pytest -x -q'
    stage: 2
    description: "Tests pass"

  # Stage 3: Security scan (only if tests pass)
  - type: command_succeeds
    command: 'cd {workspace} && pip-audit'
    stage: 3
    description: "No known vulnerabilities"
```

**Why stage?** Don't waste time running tests if the code doesn't even lint. Fail fast on cheap checks, then run expensive checks.

### Documentation Pipeline Pattern

```yaml
validations:
  # Stage 1: Files exist
  - type: file_exists
    path: "{workspace}/01-research.md"
    stage: 1

  # Stage 2: Structure
  - type: content_regex
    path: "{workspace}/01-research.md"
    pattern: "## .*\\n.*## "
    stage: 2
    description: "At least two sections"

  # Stage 3: Substance
  - type: command_succeeds
    command: 'test $(wc -w < "{workspace}/01-research.md") -ge 800'
    stage: 3
    description: "At least 800 words"
```

---

## Conditional Validations

The `condition` field controls when a validation applies. Supports: `>=`, `<=`, `==`, `!=`, `>`, `<`, and `and` (no `or` — use separate rules).

### Sheet-Specific Validations

```yaml
validations:
  - type: file_exists
    path: "{workspace}/01-setup.md"
    condition: "sheet_num == 1"
    description: "Setup output created"

  - type: file_exists
    path: "{workspace}/synthesis.md"
    condition: "sheet_num == 10"
    description: "Synthesis document created"
```

### Stage-Specific Validations (with Fan-Out)

```yaml
sheet:
  size: 1
  total_items: 3
  fan_out:
    2: 3

validations:
  - type: file_exists
    path: "{workspace}/01-framing.md"
    condition: "stage == 1"

  - type: file_exists
    path: "{workspace}/02-review-{instance}.md"
    condition: "stage == 2"
    description: "Review instance {instance} created"

  - type: file_exists
    path: "{workspace}/03-synthesis.md"
    condition: "stage == 3"
```

### Combined Conditions

```yaml
validations:
  # Only check first instance in stage 2
  - type: command_succeeds
    command: 'pytest {workspace}/tests/'
    condition: "stage == 2 and instance == 1"
    description: "Tests pass (run once per stage)"
```

---

## Command Succeeds Pitfalls

The `command_succeeds` validation type is powerful but has three common pitfalls.

### Pitfall 1: Pipe Exit Codes

**Problem:** Shell pipes always exit with the last command's exit code, not the first.

```yaml
# WRONG — Always passes because tail exits 0
validations:
  - type: command_succeeds
    command: 'pytest {workspace}/tests/ | tail -5'
    description: "Tests pass"
```

Even if `pytest` fails, `tail` exits 0, so the validation passes.

**Fix:** Use bash's `PIPESTATUS` array:

```yaml
# CORRECT — Checks pytest exit code, not tail
validations:
  - type: command_succeeds
    command: 'bash -c "pytest {workspace}/tests/ | tail -5; exit ${PIPESTATUS[0]}"'
    description: "Tests pass"
```

Or avoid pipes entirely:

```yaml
# ALSO CORRECT — No pipe, direct exit code
validations:
  - type: command_succeeds
    command: 'cd {workspace} && pytest tests/ -x -q'
    description: "Tests pass"
```

### Pitfall 2: Unquoted Variables with Spaces

**Problem:** If `{workspace}` contains spaces, the command breaks.

```yaml
# WRONG — Breaks if workspace = "/path/with spaces/work"
validations:
  - type: command_succeeds
    command: 'test -f {workspace}/output.md'
```

**Fix:** Always quote variables:

```yaml
# CORRECT
validations:
  - type: command_succeeds
    command: 'test -f "{workspace}/output.md"'
```

Marianne automatically shell-quotes `{workspace}` values via `shlex.quote()`, but explicit quotes in your command template ensure safety and readability.

### Pitfall 3: Default Timeout Too Short

**Problem:** `command_succeeds` defaults to 3600 seconds (1 hour). Large test suites, builds, or security scans can exceed this.

```yaml
# WRONG — Full test suite takes 90 minutes
validations:
  - type: command_succeeds
    command: 'cd {workspace} && pytest tests/ --full'
    # Timeout after 60 minutes — validation fails even though tests passed
```

**Fix:** Set `timeout_seconds` per validation:

```yaml
# CORRECT
validations:
  - type: command_succeeds
    command: 'cd {workspace} && pytest tests/ --full'
    timeout_seconds: 7200  # 2 hours
    description: "Full test suite passes"
```

**Best practice:** Measure first (`time pytest tests/`), then set timeout to `max(measured × 1.5, 900)`.

---

## Validation Anti-Patterns

### Anti-Pattern 1: File Existence Only

**Problem:** File may exist from a previous run. Validation passes even if the agent did nothing.

```yaml
# BAD — Passes with stale files
validations:
  - type: file_exists
    path: "{workspace}/output.md"
```

**Fix:** Add `file_modified` or content checks:

```yaml
# BETTER
validations:
  - type: file_modified
    path: "{workspace}/output.md"
    description: "Output file was updated this sheet"

  - type: content_contains
    path: "{workspace}/output.md"
    pattern: "SHEET_{sheet_num}_COMPLETE"
    description: "Output tagged with sheet number"
```

### Anti-Pattern 2: Process Validation, Not Outcome Validation

**The most dangerous anti-pattern** because the score appears to work.

**Problem:** Validations check whether the agent *did work* (file modified, tests pass, imports work) but not whether the agent *achieved the goal*.

**Example:** Prompt says "add pagination to the `/items` endpoint."

```yaml
# BAD — Agent can refactor nearby code, make tests pass, never add pagination
validations:
  - type: command_succeeds
    command: 'pytest {workspace}/tests/ -x -q'
  - type: file_modified
    path: "{workspace}/src/api/routes.py"
```

**The litmus test:** "Can the agent pass all my validations without achieving the stated goal?" If yes, your validations are decorative.

**Fix:** Validate the outcome directly:

```yaml
# GOOD — Actually hits the endpoint and checks pagination
validations:
  - type: command_succeeds
    command: |
      cd {workspace} && python -c "
      from app.routes import app
      from app.testing import client
      resp = client(app).get('/items?page=2&per_page=5')
      data = resp.json()
      assert 'page' in data and 'total_pages' in data, 'Missing pagination fields'
      assert len(data['items']) <= 5, 'per_page limit not enforced'
      "
    description: "Pagination endpoint returns paginated response"
```

### Anti-Pattern 3: Too Strict (Exact Prose Matching)

**Problem:** Validation breaks when the agent rephrases output.

```yaml
# BAD — Breaks on minor rephrasing
validations:
  - type: content_contains
    pattern: "The analysis shows that the total count is 42."
```

**Fix:** Match structure, not exact prose:

```yaml
# BETTER — Flexible structural match
validations:
  - type: content_regex
    pattern: "(?i)(analysis|summary).*\\btotal\\b.*\\d+"
    description: "Contains analysis with numeric total"
```

### Anti-Pattern 4: Too Broad (Meaningless Regex)

**Problem:** Regex matches anything, provides no verification.

```yaml
# BAD — Matches any non-empty file
validations:
  - type: content_regex
    pattern: ".*"
```

**Fix:** Match specific structure:

```yaml
# BETTER
validations:
  - type: content_regex
    pattern: "(?s)## Summary.*## Recommendations"
    description: "Has both Summary and Recommendations sections"
```

---

## Validation Best Practices

### 1. Every Sheet Needs Validations

No validations = sheet always "passes" = you learn nothing. Every sheet must have at least one meaningful validation.

### 2. Mirror Prompt Instructions

If your prompt says "write to X," validate X exists. If it says "include a findings section," validate that section exists.

```yaml
prompt:
  template: |
    Write your analysis to {{ workspace }}/analysis.md.
    Include these sections:
    - ## Executive Summary
    - ## Findings
    - ## Recommendations

validations:
  - type: file_exists
    path: "{workspace}/analysis.md"
  - type: content_contains
    path: "{workspace}/analysis.md"
    pattern: "## Executive Summary"
  - type: content_contains
    path: "{workspace}/analysis.md"
    pattern: "## Findings"
  - type: content_contains
    path: "{workspace}/analysis.md"
    pattern: "## Recommendations"
```

### 3. Use Staged Validation for Build Pipelines

Run fast checks first, expensive checks only if fast checks pass.

### 4. Add Descriptions

The `description` field appears in status output and completion prompts. Write clear descriptions:

```yaml
# BAD — No description
validations:
  - type: command_succeeds
    command: 'pytest tests/'

# GOOD — Clear description
validations:
  - type: command_succeeds
    command: 'pytest tests/'
    description: "All tests pass"
```

### 5. Set Appropriate Timeouts

Measure command execution time first, then set `timeout_seconds` to `measured × 1.5` (minimum 900 seconds).

### 6. Validate Outcomes, Not Process

For every goal in the prompt, ask: "Can the agent pass all validations without achieving this goal?" If yes, add a validation that directly checks the outcome.

---

## Quick Start Examples

### Example 1: Documentation Generation

```yaml
validations:
  # Layer 1: File exists
  - type: file_exists
    path: "{workspace}/api-docs.md"
    stage: 1

  # Layer 2: Structure
  - type: content_regex
    path: "{workspace}/api-docs.md"
    pattern: "## .*\\n.*## "
    stage: 2
    description: "Has multiple sections"

  # Layer 3: Substance
  - type: command_succeeds
    command: 'test $(wc -w < "{workspace}/api-docs.md") -ge 1000'
    stage: 3
    description: "At least 1000 words"

  # Layer 4: Code examples
  - type: content_regex
    path: "{workspace}/api-docs.md"
    pattern: "```(python|javascript|bash)"
    stage: 3
    description: "Contains code examples"
```

### Example 2: Code Refactoring

```yaml
# Goal: Replace raw SQL with ORM calls
validations:
  # Outcome validation — no raw SQL remains
  - type: command_succeeds
    command: '! grep -rn "execute(\\"SELECT\\|execute(\\"INSERT" {workspace}/src/'
    stage: 1
    description: "No raw SQL queries remain"

  # Process validation — tests still pass
  - type: command_succeeds
    command: 'cd {workspace} && pytest tests/ -x -q'
    stage: 2
    description: "Tests pass after refactoring"

  # Process validation — lint clean
  - type: command_succeeds
    command: 'ruff check {workspace}/src/'
    stage: 2
    description: "Code passes lint"
```

### Example 3: Multi-Stage Research

```yaml
sheet:
  size: 1
  total_items: 3
  fan_out:
    2: 3

validations:
  # Stage 1: Setup
  - type: file_exists
    path: "{workspace}/01-framing.md"
    condition: "stage == 1"

  # Stage 2: Research (fan-out)
  - type: file_exists
    path: "{workspace}/02-research-{instance}.md"
    condition: "stage == 2"

  - type: command_succeeds
    command: 'test $(wc -w < "{workspace}/02-research-{instance}.md") -ge 500'
    condition: "stage == 2"
    description: "Research has substantive content"

  # Stage 3: Synthesis
  - type: file_exists
    path: "{workspace}/03-synthesis.md"
    condition: "stage == 3"

  - type: content_regex
    path: "{workspace}/03-synthesis.md"
    pattern: "(?i)(convergence|tension|gap|emergence)"
    condition: "stage == 3"
    description: "Synthesis identifies patterns across research"
```

---

## Troubleshooting

### Validation Always Passes with Stale Files

**Problem:** `file_exists` validation passes because a file from a previous run still exists.

**Solution:** Use `file_modified` instead, or run with `--fresh` to clear workspace.

```yaml
# Replace this
validations:
  - type: file_exists
    path: "{workspace}/output.md"

# With this
validations:
  - type: file_modified
    path: "{workspace}/output.md"
    description: "Output file was updated during this sheet"
```

### Validation Fails with "File Not Found"

**Problem:** Path uses Jinja syntax in validation (double braces).

```yaml
# WRONG
validations:
  - type: file_exists
    path: "{{ workspace }}/output.md"

# CORRECT
validations:
  - type: file_exists
    path: "{workspace}/output.md"
```

### Command Succeeds Always Passes

**Problem:** Pipe exit code issue (see [Pitfall 1](#pitfall-1-pipe-exit-codes)).

**Solution:** Use `PIPESTATUS` or remove pipes.

### Command Succeeds Times Out

**Problem:** Default timeout (3600s) too short for long-running commands.

**Solution:** Set `timeout_seconds` explicitly:

```yaml
validations:
  - type: command_succeeds
    command: 'cd {workspace} && pytest tests/ --full'
    timeout_seconds: 7200  # 2 hours
```

### Validation Fails Even Though Prompt Told Agent to Create File

**Problem:** Prompt and validation have different paths (prompt says `{{ workspace }}/output.md`, validation checks `{workspace}/result.md`).

**Solution:** Ensure prompt and validation reference the same file path. Copy-paste the path between them, then adjust syntax (`{{` vs `{`).

---

## Next Steps

**Learn more validation techniques:**
- [Score Writing Guide](score-writing-guide.md) — Full validation types reference
- [Configuration Reference](configuration-reference.md) — All validation fields documented

**Explore examples with validation patterns:**
- `examples/quality-continuous.yaml` — Multi-stage validation pipeline
- `examples/issue-solver.yaml` — Outcome validation for bug fixes
- `examples/docs-generator.yaml` — Documentation structure validation

**Understand related concepts:**
- [Getting Started](getting-started.md) — Basic validation setup
- [CLI Reference](cli-reference.md) — `mzt validate` command
