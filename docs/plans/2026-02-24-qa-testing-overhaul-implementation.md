# QA Testing Overhaul — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a suite of 9 Mozart score YAML files that systematically overhaul Mozart's testing infrastructure — from hollow 86% coverage to adversarial, property-based, TDD-driven testing with overnight smoke validation.

**Architecture:** Module-by-module score suite. Foundation score first (installs hypothesis, creates conftest patterns, sets coverage enforcement). Six module scores follow the same 5-stage template (audit → overhaul fan-out x3 → merge → coverage gate → commit). Integration score tests cross-module flows. Overnight score runs real conductor ops via cron.

**Tech Stack:** Mozart score YAML, Jinja2 templates, hypothesis, pytest, pytest-xdist, pytest-randomly

---

### Task 1: Create directory and qa-foundation.yaml

**Files:**
- Create: `scores/qa/qa-foundation.yaml`

**Step 1: Create the scores/qa directory**

```bash
mkdir -p scores/qa
```

**Step 2: Write qa-foundation.yaml**

This score sets up the testing infrastructure that all other scores depend on. 5 stages: dependency setup, conftest overhaul, quality markers, coverage baseline, commit.

Write the following to `scores/qa/qa-foundation.yaml`:

```yaml
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║       QA FOUNDATION                                                        ║
# ║                                                                            ║
# ║  Testing infrastructure setup: deps, fixtures, conftest, coverage config.  ║
# ║  MUST run before any other qa-* score.                                     ║
# ║                                                                            ║
# ║  Stage 1: Install hypothesis, pytest-xdist, pytest-randomly               ║
# ║  Stage 2: Create adversarial conftest + strict mock helpers                ║
# ║  Stage 3: Register test quality markers + meta-test quality gate           ║
# ║  Stage 4: Capture coverage baseline per module                             ║
# ║  Stage 5: Commit all infrastructure changes                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

name: "qa-foundation"
description: "Testing infrastructure: hypothesis, adversarial conftest, coverage enforcement, quality gate"

workspace: "/home/emzi/Projects/mozart-ai-compose/.qa-foundation-workspace"

workspace_lifecycle:
  archive_on_fresh: true
  max_archives: 3

backend:
  type: claude_cli
  skip_permissions: true
  working_directory: /home/emzi/Projects/mozart-ai-compose
  timeout_seconds: 2400
  output_format: json

cross_sheet:
  auto_capture_stdout: true
  max_output_chars: 4000
  lookback_sheets: 3

sheet:
  size: 1
  total_items: 5

  dependencies:
    2: [1]
    3: [2]
    4: [3]
    5: [4]

retry:
  max_retries: 2
  max_completion_attempts: 2
  completion_threshold_percent: 50

stale_detection:
  enabled: true
  idle_timeout_seconds: 1800

cost_limits:
  enabled: false

prompt:
  variables:
    preamble: |
      ╔══════════════════════════════════════════════════════════════════════════════╗
      ║  QA FOUNDATION — Testing Infrastructure Setup                              ║
      ╚══════════════════════════════════════════════════════════════════════════════╝

      **Project:** /home/emzi/Projects/mozart-ai-compose
      **Branch:** main (push directly)
      **Virtual env:** /home/emzi/Projects/mozart-ai-compose/.venv

      CRITICAL RULES:
      1. Read existing files before modifying them.
      2. Follow existing patterns — async, Pydantic v2, type hints everywhere.
      3. Never use `git add .` — stage files explicitly.
      4. Run tests after changes: `pytest tests/ -x -q --timeout=120`
      5. All new test code must have type hints.

      ## Test Quality Standards (enforce these everywhere)

      **MANDATORY:**
      - No `asyncio.sleep()` for test coordination — use polling loops with deadlines
      - No timing assertions < 30s — generous bounds only
      - No bare `MagicMock()` — must use `spec=RealClass` or `create_autospec()`
      - Every test function must have at least one `assert` or `pytest.raises`
      - No module-level mutable state in tests — use fixtures with teardown
      - Property-based tests for every Pydantic model (hypothesis @given)
      - Adversarial inputs for every parser

      **ANTI-PATTERNS (reject these):**
      - Tests that pass when implementation is removed (testing mocks, not behavior)
      - `assert isinstance(result, SomeType)` without checking values
      - `assert len(result) > 0` without checking content
      - Tests that mock the thing they're testing

  template: |
    {{ preamble }}

    *Stage {{ stage }}/{{ total_stages }}*

    {% if stage == 1 %}
    # Stage 1: Dependency Setup

    Add testing dependencies and configure coverage enforcement.

    **Step 1: Update pyproject.toml**

    Read `pyproject.toml` first. Then make these changes:

    1. Add to `[project.optional-dependencies].dev`:
       - `hypothesis>=6.100.0` (property-based testing)
       - `pytest-xdist>=3.5.0` (parallel test execution)
       - `pytest-randomly>=3.15.0` (detect order-dependent tests)

    2. Change `[tool.coverage.report]` `fail_under` from `0` to `80`

    3. Add to `[tool.pytest.ini_options]`:
       ```
       markers = [
           "adversarial: tests with adversarial/garbage inputs",
           "smoke: basic smoke tests for critical paths",
           "overnight: long-running tests for cron execution",
           "property_based: hypothesis property-based tests",
       ]
       ```

    **Step 2: Install**
    ```bash
    cd /home/emzi/Projects/mozart-ai-compose
    .venv/bin/pip install -e ".[dev]"
    ```

    **Step 3: Verify imports**
    ```bash
    .venv/bin/python -c "import hypothesis; import xdist; print('OK')"
    ```

    **Output to:** {{ workspace }}/01-deps.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.

    {% elif stage == 2 %}
    # Stage 2: Adversarial Conftest & Strict Mock Helpers

    Create the shared testing infrastructure that all module scores will use.

    **Step 1: Read existing conftest**

    Read `tests/conftest.py` to understand existing fixtures and patterns.

    **Step 2: Create `tests/conftest_adversarial.py`**

    This file provides:

    1. **Hypothesis profiles:**
       - `ci` profile: `max_examples=20, deadline=500ms` (fast for CI)
       - `nightly` profile: `max_examples=200, deadline=5000ms` (thorough for overnight)

    2. **Pydantic model strategies** — hypothesis strategies that generate valid AND
       invalid instances of ALL core Pydantic models. Read the actual model files to
       get the field types:
       - `src/mozart/core/config/backend.py` (BackendConfig)
       - `src/mozart/core/config/execution.py` (SheetConfig, RetryConfig, etc.)
       - `src/mozart/core/config/job.py` (JobConfig)
       - `src/mozart/core/config/orchestration.py` (ParallelConfig, etc.)
       - `src/mozart/core/config/workspace.py` (WorkspaceConfig)
       - `src/mozart/core/checkpoint.py` (CheckpointState, SheetState)

       For each model, create a `st.builds()` strategy that covers:
       - Valid instances with all required fields
       - Edge cases: empty strings, very long strings, zero/negative numbers
       - None for Optional fields

    3. **Adversarial string generators** — pytest fixtures:
       - `adversarial_strings` — list of: empty, null bytes, unicode (emoji, RTL, zero-width),
         path traversal (`../../etc/passwd`), SQL injection, XSS, very long (10KB), only whitespace
       - `adversarial_ints` — list of: 0, -1, -sys.maxsize, sys.maxsize, 1
       - `adversarial_paths` — list of: empty, `/`, `..`, absolute, with spaces, unicode, symlink targets

    4. **`strict_mock()` helper function:**
       ```python
       def strict_mock(spec_class, **kwargs):
           """Create a mock that raises on unexpected attribute access."""
           mock = MagicMock(spec=spec_class, **kwargs)
           # Prevent truthy-by-default: accessing undefined attrs raises
           return mock
       ```
       Actually, the best approach is `create_autospec(spec_class, instance=True)` which
       already does this. Create a `strict_mock` wrapper that uses `create_autospec` and
       document why in the docstring.

    **Step 3: Verify it all imports**
    ```bash
    .venv/bin/python -c "from tests.conftest_adversarial import *; print('OK')"
    ```

    **Output to:** {{ workspace }}/02-conftest.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.

    {% elif stage == 3 %}
    # Stage 3: Test Quality Gate Meta-Test

    Create a meta-test that enforces quality standards across ALL test files.

    **Step 1: Create `tests/test_quality_gate.py`**

    This test file scans the test suite itself and fails if quality standards are violated.

    Tests to include:

    1. **`test_no_asyncio_sleep_for_coordination()`**
       - Scan all `test_*.py` files for `asyncio.sleep` calls
       - ALLOW: `asyncio.sleep(0)` (yield to event loop) and inside `async def` helpers
         that are clearly polling loops (have a `while` above them)
       - FAIL: bare `await asyncio.sleep(N)` where N > 0 without a surrounding poll loop
       - Use AST parsing, not regex, for accuracy

    2. **`test_no_tight_timing_assertions()`**
       - Scan for `assert elapsed < N` or `assert.*< N.*seconds` patterns
       - FAIL if N < 30.0
       - Use regex for this one — AST is overkill

    3. **`test_all_tests_have_assertions()`**
       - Parse each test file with `ast`
       - For each `def test_*` or `async def test_*` function:
         - Walk the AST looking for `assert` statements, `pytest.raises`, or calls to
           assertion helpers (`assertEqual`, etc.)
         - FAIL if a test function has zero assertion-like nodes
       - Exclude known exceptions (list them explicitly if needed)

    4. **`test_no_bare_magicmock()`**
       - This is advisory for now (WARNING, not FAIL) since we have 714 existing instances
       - Scan for `MagicMock()` without `spec=` argument
       - Report count and location
       - FAIL only if NEW bare MagicMock instances are added (compare against baseline count)
       - Store baseline count in a constant at top of file

    5. **`test_property_based_tests_exist_for_pydantic_models()`**
       - List all Pydantic BaseModel subclasses in `src/mozart/core/config/` and
         `src/mozart/core/checkpoint.py`
       - For each, check that a `@given` test exists somewhere in `tests/`
       - This will FAIL initially (no property tests exist yet) — that's intentional.
         The module scores will add them. Mark this test with `@pytest.mark.xfail(reason="Module scores will add these")`
         until qa-core runs.

    **Step 2: Run the quality gate**
    ```bash
    .venv/bin/pytest tests/test_quality_gate.py -v --timeout=60
    ```

    Some tests may fail — that's expected. The module scores will fix the violations.
    The gate test itself must be correct and not crash.

    **Output to:** {{ workspace }}/03-quality-gate.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.

    {% elif stage == 4 %}
    # Stage 4: Coverage Baseline

    Capture per-module coverage as a baseline for tracking improvement.

    **Step 1: Run full test suite with coverage**
    ```bash
    cd /home/emzi/Projects/mozart-ai-compose
    .venv/bin/pytest tests/ -x --timeout=120 -q --cov=mozart --cov-report=json:coverage.json 2>&1 | tail -20
    ```

    **Step 2: Extract per-module coverage**

    Write a Python script that reads `coverage.json` and produces a baseline report:

    ```bash
    .venv/bin/python -c "
    import json
    with open('coverage.json') as f:
        data = json.load(f)

    # Group by module (first 2 path components after src/mozart/)
    modules = {}
    for path, info in data.get('files', {}).items():
        parts = path.replace('src/mozart/', '').split('/')
        module = parts[0] if len(parts) > 1 else 'root'
        if module not in modules:
            modules[module] = {'covered': 0, 'total': 0}
        summary = info.get('summary', {})
        modules[module]['covered'] += summary.get('covered_lines', 0)
        modules[module]['total'] += summary.get('num_statements', 0)

    print('Module Coverage Baseline')
    print('=' * 50)
    for mod in sorted(modules.keys()):
        d = modules[mod]
        pct = (d['covered'] / d['total'] * 100) if d['total'] > 0 else 0
        status = 'PASS' if pct >= 80 else 'FAIL'
        print(f'{mod:25s} {pct:5.1f}%  ({d[\"covered\"]}/{d[\"total\"]})  [{status}]')
    "
    ```

    **Step 3: Save baseline**

    Save the coverage.json as `.coverage-baseline.json` for future comparison.

    ```bash
    cp coverage.json .coverage-baseline.json
    ```

    **Step 4: Verify the fail_under change works**

    The `fail_under = 80` should now be active. Since current coverage is 86%, the
    suite should still pass. Verify:
    ```bash
    .venv/bin/pytest tests/ -x --timeout=120 -q 2>&1 | tail -5
    ```

    **Output to:** {{ workspace }}/04-baseline.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.
    Include the full module coverage table.

    {% elif stage == 5 %}
    # Stage 5: Commit Infrastructure Changes

    Stage and commit all infrastructure changes from stages 1-4.

    **Step 1: Verify everything works**
    ```bash
    cd /home/emzi/Projects/mozart-ai-compose
    .venv/bin/pytest tests/ -x --timeout=120 -q 2>&1 | tail -10
    ```

    **Step 2: Stage files selectively**
    ```bash
    git add pyproject.toml
    git add tests/conftest_adversarial.py
    git add tests/test_quality_gate.py
    git add .coverage-baseline.json
    # Stage any other files created/modified
    git diff --cached --stat
    ```

    Do NOT stage workspace files, coverage.json, or unrelated changes.

    **Step 3: Commit and push**
    ```bash
    git commit -m "test(infra): add hypothesis, adversarial conftest, quality gate

    - Add hypothesis, pytest-xdist, pytest-randomly to dev deps
    - Set coverage fail_under to 80%
    - Create conftest_adversarial.py with property-based strategies
    - Create test_quality_gate.py meta-test
    - Capture coverage baseline

    Co-Authored-By: Mozart AI Compose <noreply@mozart.ai>"

    git push
    ```

    **Output to:** {{ workspace }}/05-commit.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.

    {% endif %}

validations:
  # Stage 1: Dependencies installed
  - type: command_succeeds
    command: 'cd /home/emzi/Projects/mozart-ai-compose && .venv/bin/python -c "import hypothesis; print(\"OK\")"'
    condition: "stage >= 1"
    description: "hypothesis is importable"

  - type: command_succeeds
    command: 'grep -q "fail_under = 80" /home/emzi/Projects/mozart-ai-compose/pyproject.toml'
    condition: "stage >= 1"
    description: "fail_under is set to 80"

  # Stage 2: Adversarial conftest exists
  - type: file_exists
    path: "/home/emzi/Projects/mozart-ai-compose/tests/conftest_adversarial.py"
    condition: "stage >= 2"
    description: "Adversarial conftest exists"

  - type: command_succeeds
    command: 'cd /home/emzi/Projects/mozart-ai-compose && .venv/bin/python -c "import tests.conftest_adversarial; print(\"OK\")"'
    condition: "stage >= 2"
    description: "Adversarial conftest imports"
    stage: 2

  # Stage 3: Quality gate exists
  - type: file_exists
    path: "/home/emzi/Projects/mozart-ai-compose/tests/test_quality_gate.py"
    condition: "stage >= 3"
    description: "Quality gate test exists"

  # Stage 4: Baseline captured
  - type: file_exists
    path: "/home/emzi/Projects/mozart-ai-compose/.coverage-baseline.json"
    condition: "stage >= 4"
    description: "Coverage baseline exists"

  # Stage 5: Tests pass
  - type: command_succeeds
    command: 'cd /home/emzi/Projects/mozart-ai-compose && .venv/bin/pytest tests/ -x --timeout=120 -q --tb=no --no-header 2>&1 | tail -1 | grep -qE "[0-9]+ passed"'
    condition: "stage >= 5"
    description: "Full test suite passes"
    timeout_seconds: 900
    stage: 2
```

**Step 3: Validate the score**

```bash
cd /home/emzi/Projects/mozart-ai-compose
mozart validate scores/qa/qa-foundation.yaml
```

Expected: no errors. Warnings about workspace parent are OK (auto-fixable).

**Step 4: Dry run**

```bash
mozart run scores/qa/qa-foundation.yaml --dry-run
```

Verify: 5 sheets, correct stage ordering, prompt templates render.

**Step 5: Commit**

```bash
git add scores/qa/qa-foundation.yaml
git commit -m "score(qa): add qa-foundation — testing infrastructure setup"
```

---

### Task 2: Create the module score template and qa-core.yaml

**Files:**
- Create: `scores/qa/qa-core.yaml`

**Step 1: Write qa-core.yaml**

This is the first module score. It follows the 5-stage template (audit → overhaul fan-out x3 → merge → coverage gate → commit) targeting `core/config/`, `core/checkpoint.py`, `core/fan_out.py`, `core/errors/`, and `prompts/`.

Write the following to `scores/qa/qa-core.yaml`:

```yaml
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║       QA CORE — Config, Checkpoint, Fan-Out, Errors, Templating            ║
# ║                                                                            ║
# ║  Adversarial testing overhaul for Mozart's data layer.                     ║
# ║                                                                            ║
# ║  Stage 1: Audit existing tests — classify strong/weak/flaky/useless        ║
# ║  Stage 2 x3: Overhaul (fix flaky | property-based | adversarial edges)    ║
# ║  Stage 3: Merge parallel work, resolve conflicts                           ║
# ║  Stage 4: Coverage gate — verify >= 80%, write targeted gap tests          ║
# ║  Stage 5: Commit all changes                                               ║
# ║                                                                            ║
# ║  Requires: qa-foundation (hypothesis installed, conftest_adversarial.py)   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

name: "qa-core"
description: "Adversarial testing overhaul for core modules: config, checkpoint, fan-out, errors, templating"

workspace: "/home/emzi/Projects/mozart-ai-compose/.qa-core-workspace"

workspace_lifecycle:
  archive_on_fresh: true
  max_archives: 3

backend:
  type: claude_cli
  skip_permissions: true
  working_directory: /home/emzi/Projects/mozart-ai-compose
  timeout_seconds: 3000
  output_format: json

cross_sheet:
  auto_capture_stdout: true
  max_output_chars: 4000
  lookback_sheets: 5

sheet:
  size: 1
  total_items: 5

  fan_out:
    2: 3

  dependencies:
    2: [1]
    3: [2]
    4: [3]
    5: [4]

parallel:
  enabled: true
  max_concurrent: 3

retry:
  max_retries: 2
  max_completion_attempts: 2
  completion_threshold_percent: 50

stale_detection:
  enabled: true
  idle_timeout_seconds: 1800

cost_limits:
  enabled: false

prompt:
  variables:
    preamble: |
      ╔══════════════════════════════════════════════════════════════════════════════╗
      ║  QA CORE — Adversarial Testing Overhaul                                    ║
      ║  Config · Checkpoint · Fan-Out · Errors · Templating                       ║
      ╚══════════════════════════════════════════════════════════════════════════════╝

      **Project:** /home/emzi/Projects/mozart-ai-compose
      **Branch:** main (push directly)
      **Virtual env:** .venv

      ## Source Scope (ONLY touch tests for these modules)
      - `src/mozart/core/config/` — all config models (backend, execution, job, learning, orchestration, workspace)
      - `src/mozart/core/checkpoint.py` — CheckpointState, SheetState
      - `src/mozart/core/fan_out.py` — fan-out expansion logic
      - `src/mozart/core/errors/` — error classifier, codes, models, parsers, signals
      - `src/mozart/prompts/` — Jinja templating

      ## Test Scope
      - `tests/test_config*.py`
      - `tests/test_checkpoint.py`
      - `tests/test_fan_out.py`
      - `tests/test_error_*.py`
      - `tests/test_templating.py`
      - New files you create in `tests/` for this module

      ## Shared Infrastructure (from qa-foundation)
      - `tests/conftest_adversarial.py` — hypothesis strategies, adversarial fixtures, strict_mock()
      - `tests/test_quality_gate.py` — quality standards reference

      CRITICAL RULES:
      1. Read actual source code before writing tests. Understand what the code does.
      2. Tests must test BEHAVIOR, not implementation. Ask: "Would this test fail if the behavior broke?"
      3. Use `from tests.conftest_adversarial import` for shared strategies.
      4. Every new test must have type hints.
      5. Run tests after every change: `pytest tests/test_config*.py tests/test_checkpoint.py tests/test_fan_out.py tests/test_error_*.py tests/test_templating.py -x -q --timeout=60`
      6. Never use `git add .` — stage files explicitly.

    overhaul_types:
      1:
        name: "Fix Flaky & Weak Tests"
        directive: |
          Your job: Find and fix every flaky and weak test in the test scope.

          **Read the audit report at {{ workspace }}/01-audit.md first.**

          For each test classified as "flaky" or "weak":

          **Flaky fixes:**
          - Replace `asyncio.sleep(N)` with polling loops:
            ```python
            deadline = asyncio.get_event_loop().time() + 10.0
            while asyncio.get_event_loop().time() < deadline:
                result = await check_condition()
                if result:
                    break
                await asyncio.sleep(0.1)
            assert result, "Condition not met within deadline"
            ```
          - Replace `assert elapsed < N` where N < 30 with `assert elapsed < 30.0`
          - Replace bare `MagicMock()` with `create_autospec(RealClass, instance=True)`

          **Weak fixes:**
          - Replace `assert isinstance(x, Type)` with actual value checks
          - Replace `assert len(x) > 0` with specific content assertions
          - Add meaningful assertions to assertion-free test functions
          - If a test is truly useless (tests nothing real), delete it and note why

          After EACH file modified, run:
          ```bash
          pytest <file> -x -q --timeout=60
          ```

      2:
        name: "Add Property-Based Tests"
        directive: |
          Your job: Write hypothesis @given tests for every Pydantic model in scope.

          **Read the audit report at {{ workspace }}/01-audit.md first.**
          **Read `tests/conftest_adversarial.py` for available strategies.**

          For each Pydantic model in the source scope, write property-based tests:

          1. **Round-trip property**: `model.model_validate(model.model_dump()) == model`
          2. **Validation rejects garbage**: `@given(st.text())` should not create valid models from random strings
          3. **Field constraints hold**: If a field has `ge=0`, hypothesis should verify negative values are rejected
          4. **JSON serialization**: `model.model_validate_json(model.model_dump_json()) == model`

          Models to cover:
          - BackendConfig, SheetConfig, RetryConfig, RateLimitConfig, ValidationConfig
          - CircuitBreakerConfig, CostLimitConfig, StaleDetectionConfig
          - ParallelConfig, JobConfig, WorkspaceConfig
          - CheckpointState, SheetState
          - ErrorCode, ClassifiedError

          Use `@pytest.mark.property_based` on all hypothesis tests.
          Use `@settings(max_examples=50)` for CI speed.

          Create new file: `tests/test_core_property_based.py`

          After writing, run:
          ```bash
          pytest tests/test_core_property_based.py -v --timeout=120
          ```

      3:
        name: "Add Adversarial Edge Case Tests"
        directive: |
          Your job: Write adversarial tests that try to break every parser and data path.

          **Read the audit report at {{ workspace }}/01-audit.md for coverage gaps.**
          **Read `tests/conftest_adversarial.py` for adversarial fixtures.**

          Target areas:

          1. **Config parsing with garbage YAML:**
             - Empty config, null config, config with only whitespace
             - Config with unknown fields (should they be ignored or rejected?)
             - Config with wrong types (string where int expected, etc.)
             - Config with circular references
             - Extremely nested configs (100 levels deep)
             - Config with unicode field names
             - Config larger than 1MB

          2. **Checkpoint corruption:**
             - Checkpoint with missing required fields
             - Checkpoint with sheet_num out of range
             - Checkpoint with status values that don't exist
             - Checkpoint saved mid-write (truncated JSON)
             - Two processes writing checkpoint simultaneously

          3. **Fan-out edge cases:**
             - Fan-out with 0 instances
             - Fan-out with 1 instance (degenerate case)
             - Fan-out with 100 instances
             - Fan-out with circular dependencies
             - Fan-out on a stage that doesn't exist
             - Fan-out with negative instance counts

          4. **Jinja template injection:**
             - Template with `{% raw %}` blocks
             - Template referencing undefined variables
             - Template with recursive includes (should fail gracefully)
             - Template with `{{ ''.__class__.__mro__ }}` (Jinja sandbox escape)
             - Template > 1MB

          5. **Error parser edge cases:**
             - Empty error output, None error output
             - Error output with only ANSI escape codes
             - Error output > 100KB
             - Error output in non-UTF8 encoding
             - Rate limit message with malformed timestamp

          Use `@pytest.mark.adversarial` on all these tests.
          Use `pytest.mark.parametrize` extensively.

          Create new file: `tests/test_core_adversarial.py`

          After writing, run:
          ```bash
          pytest tests/test_core_adversarial.py -v --timeout=120
          ```

  template: |
    {{ preamble }}

    *Stage {{ stage }}/{{ total_stages }} ({{ ((stage / total_stages) * 100) | round }}%)*
    {% if fan_count > 1 %}*Instance {{ instance }}/{{ fan_count }}*{% endif %}

    {% if stage == 1 %}
    # Stage 1: Audit Existing Tests

    Read every test file in scope and classify each test function.

    **Test files to audit:**
    ```bash
    ls tests/test_config*.py tests/test_checkpoint.py tests/test_fan_out.py tests/test_error_*.py tests/test_templating.py tests/test_config_nested.py 2>/dev/null
    ```

    **For each test file**, read it and classify every `def test_*` function as:

    - **Strong**: Tests behavior with meaningful assertions, handles edge cases, no timing deps
    - **Weak**: Uses bare MagicMock, trivial assertions (`assert True`, `isinstance` only), tests implementation not behavior
    - **Flaky**: Uses `asyncio.sleep` for coordination, tight timing (`elapsed < 5`), PID assumptions
    - **Useless**: No assertions at all, or only checks import works

    **Also measure coverage** for each source file:
    ```bash
    cd /home/emzi/Projects/mozart-ai-compose
    .venv/bin/pytest tests/test_config.py tests/test_config_nested.py tests/test_checkpoint.py tests/test_fan_out.py tests/test_error_classifier.py tests/test_error_codes.py tests/test_error_parsers.py tests/test_templating.py -x -q --cov=mozart.core --cov=mozart.prompts --cov-report=term-missing:skip-covered --timeout=120 2>&1 | tail -40
    ```

    **Output to:** {{ workspace }}/01-audit.md

    Format:
    ```markdown
    IMPLEMENTATION_COMPLETE: yes

    # Core Module Test Audit

    ## Coverage Summary
    | Source File | Coverage | Uncovered Lines |
    |-------------|----------|-----------------|

    ## Test Classification
    | Test File | Test Function | Classification | Reason |
    |-----------|---------------|----------------|--------|

    ## Summary
    - Total tests: N
    - Strong: N (X%)
    - Weak: N (X%)
    - Flaky: N (X%)
    - Useless: N (X%)

    ## Top Priority Fixes
    1. [Most impactful improvement]
    ...
    ```


    {% elif stage == 2 %}
    {% set task = overhaul_types[instance] %}

    # Stage 2: {{ task.name }}

    Read {{ workspace }}/01-audit.md for the audit results.

    {{ task.directive }}

    **PARALLEL STAGE:** You are instance {{ instance }} of {{ fan_count }}.
    - Instance 1: Fix flaky & weak tests (modifies existing test files)
    - Instance 2: Add property-based tests (creates new file)
    - Instance 3: Add adversarial edge cases (creates new file)

    If you need to modify a file another instance owns, note it in your output
    but don't touch it.

    **Output to:** {{ workspace }}/02-{{ task.name | lower | replace(" ", "-") | replace("&", "and") }}.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.


    {% elif stage == 3 %}
    # Stage 3: Merge & Conflict Resolution

    Three parallel instances just modified tests independently. Make it all work together.

    Read all reports:
    - {{ workspace }}/02-fix-flaky-and-weak-tests.md
    - {{ workspace }}/02-add-property-based-tests.md
    - {{ workspace }}/02-add-adversarial-edge-case-tests.md

    **Step 1: Check for conflicts**
    ```bash
    cd /home/emzi/Projects/mozart-ai-compose
    git diff --stat
    ```

    **Step 2: Run the full module test suite**
    ```bash
    .venv/bin/pytest tests/test_config*.py tests/test_checkpoint.py tests/test_fan_out.py tests/test_error_*.py tests/test_templating.py tests/test_core_property_based.py tests/test_core_adversarial.py -x -v --timeout=120 2>&1 | tail -30
    ```

    **Step 3: Fix any failures.** If tests from different instances conflict, resolve them.

    **Step 4: Run the quality gate to verify no flaky patterns remain**
    ```bash
    .venv/bin/pytest tests/test_quality_gate.py -v --timeout=60
    ```

    **Output to:** {{ workspace }}/03-merge.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.


    {% elif stage == 4 %}
    # Stage 4: Coverage Gate

    Verify the module hits >= 80% coverage. Write targeted tests for any gaps.

    **Step 1: Measure coverage**
    ```bash
    cd /home/emzi/Projects/mozart-ai-compose
    .venv/bin/pytest tests/test_config*.py tests/test_checkpoint.py tests/test_fan_out.py tests/test_error_*.py tests/test_templating.py tests/test_core_property_based.py tests/test_core_adversarial.py -x -q --cov=mozart.core --cov=mozart.prompts --cov-report=term-missing --timeout=120 2>&1 | tail -40
    ```

    **Step 2: If any source file is below 80%**, read the uncovered lines and write
    targeted tests that exercise those specific code paths. Add them to the appropriate
    existing test file or create a new one.

    **Step 3: Re-run coverage to verify**
    ```bash
    .venv/bin/pytest tests/test_config*.py tests/test_checkpoint.py tests/test_fan_out.py tests/test_error_*.py tests/test_templating.py tests/test_core_property_based.py tests/test_core_adversarial.py -x -q --cov=mozart.core --cov=mozart.prompts --cov-report=term-missing --timeout=120 2>&1 | tail -40
    ```

    Target: every source file >= 80%. Critical files (config parsing, checkpoint) >= 95%.

    **Output to:** {{ workspace }}/04-coverage.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.
    Include before/after coverage table.


    {% elif stage == 5 %}
    # Stage 5: Commit All Changes

    **Step 1: Run full test suite to verify nothing regressed**
    ```bash
    cd /home/emzi/Projects/mozart-ai-compose
    .venv/bin/pytest tests/ -x --timeout=120 -q 2>&1 | tail -10
    ```

    **Step 2: Stage only test files modified by this score**
    ```bash
    # List what changed
    git status --short | grep -E "test_.*\.py$"

    # Stage test files explicitly
    git add tests/test_core_property_based.py tests/test_core_adversarial.py
    # Also stage any modified existing test files
    # git add tests/test_config.py tests/test_checkpoint.py ... (only if modified)

    git diff --cached --stat
    ```

    **Step 3: Commit and push**
    ```bash
    git commit -m "test(core): adversarial overhaul — property-based + edge cases

    - Add hypothesis property-based tests for all Pydantic config models
    - Add adversarial edge case tests for config, checkpoint, fan-out, errors
    - Fix flaky patterns: replace asyncio.sleep with polling, widen timing bounds
    - Replace bare MagicMock() with create_autospec()
    - Coverage: core modules now >= 80%

    Co-Authored-By: Mozart AI Compose <noreply@mozart.ai>"

    git push
    ```

    **Output to:** {{ workspace }}/05-commit.md
    Include `IMPLEMENTATION_COMPLETE: yes` at the top.

    {% endif %}

validations:
  # Stage 1: Audit exists
  - type: file_exists
    path: "{workspace}/01-audit.md"
    condition: "stage >= 1"
    description: "Audit report exists"

  - type: content_contains
    path: "{workspace}/01-audit.md"
    pattern: "IMPLEMENTATION_COMPLETE: yes"
    condition: "stage >= 1"
    description: "Audit marked complete"

  # Stage 2: Per-instance reports
  - type: file_exists
    path: "{workspace}/02-fix-flaky-and-weak-tests.md"
    condition: "stage == 2 and instance == 1"
    description: "Flaky fix report exists"

  - type: file_exists
    path: "{workspace}/02-add-property-based-tests.md"
    condition: "stage == 2 and instance == 2"
    description: "Property-based test report exists"

  - type: file_exists
    path: "{workspace}/02-add-adversarial-edge-case-tests.md"
    condition: "stage == 2 and instance == 3"
    description: "Adversarial test report exists"

  # Stage 2: Actual test files created
  - type: file_exists
    path: "/home/emzi/Projects/mozart-ai-compose/tests/test_core_property_based.py"
    condition: "stage == 2 and instance == 2"
    description: "Property-based test file created"

  - type: file_exists
    path: "/home/emzi/Projects/mozart-ai-compose/tests/test_core_adversarial.py"
    condition: "stage == 2 and instance == 3"
    description: "Adversarial test file created"

  # Stage 2: Tests actually pass (outcome validation, not process)
  - type: command_succeeds
    command: 'cd /home/emzi/Projects/mozart-ai-compose && .venv/bin/pytest tests/test_core_property_based.py -x -q --timeout=120 --tb=no --no-header 2>&1 | tail -1 | grep -qE "[0-9]+ passed"'
    condition: "stage == 2 and instance == 2"
    description: "Property-based tests pass"
    timeout_seconds: 300

  - type: command_succeeds
    command: 'cd /home/emzi/Projects/mozart-ai-compose && .venv/bin/pytest tests/test_core_adversarial.py -x -q --timeout=120 --tb=no --no-header 2>&1 | tail -1 | grep -qE "[0-9]+ passed"'
    condition: "stage == 2 and instance == 3"
    description: "Adversarial tests pass"
    timeout_seconds: 300

  # Stage 3: Merge
  - type: command_succeeds
    command: 'cd /home/emzi/Projects/mozart-ai-compose && .venv/bin/pytest tests/test_config.py tests/test_config_nested.py tests/test_checkpoint.py tests/test_fan_out.py tests/test_error_classifier.py tests/test_error_codes.py tests/test_error_parsers.py tests/test_templating.py tests/test_core_property_based.py tests/test_core_adversarial.py -x -q --timeout=120 --tb=no --no-header 2>&1 | tail -1 | grep -qE "[0-9]+ passed"'
    condition: "stage >= 3"
    description: "All core module tests pass"
    timeout_seconds: 600

  # Stage 4: Coverage gate
  - type: command_succeeds
    command: |
      cd /home/emzi/Projects/mozart-ai-compose && .venv/bin/pytest tests/test_config.py tests/test_config_nested.py tests/test_checkpoint.py tests/test_fan_out.py tests/test_error_classifier.py tests/test_error_codes.py tests/test_error_parsers.py tests/test_templating.py tests/test_core_property_based.py tests/test_core_adversarial.py -x -q --cov=mozart.core --cov=mozart.prompts --cov-report=json:/tmp/qa-core-cov.json --timeout=120 --tb=no --no-header 2>&1 | tail -1 | grep -qE "[0-9]+ passed" && python3 -c "
      import json
      with open('/tmp/qa-core-cov.json') as f:
          data = json.load(f)
      pct = data.get('totals', {}).get('percent_covered', 0)
      assert pct >= 80, f'Coverage {pct:.1f}% < 80%'
      print(f'Coverage: {pct:.1f}% >= 80% PASSED')
      "
    condition: "stage >= 4"
    description: "Core module coverage >= 80%"
    timeout_seconds: 600
    stage: 2

  # Stage 5: Full suite
  - type: command_succeeds
    command: 'cd /home/emzi/Projects/mozart-ai-compose && .venv/bin/pytest tests/ -x --timeout=120 -q --tb=no --no-header 2>&1 | tail -1 | grep -qE "[0-9]+ passed"'
    condition: "stage >= 5"
    description: "Full test suite passes"
    timeout_seconds: 900
    stage: 2
```

**Step 2: Validate and dry-run**

```bash
mozart validate scores/qa/qa-core.yaml
mozart run scores/qa/qa-core.yaml --dry-run
```

**Step 3: Commit**

```bash
git add scores/qa/qa-core.yaml
git commit -m "score(qa): add qa-core — adversarial testing for core modules"
```

---

### Task 3: Create qa-execution.yaml

**Files:**
- Create: `scores/qa/qa-execution.yaml`

**Step 1: Write qa-execution.yaml**

Same 5-stage template as qa-core, but targeting `execution/runner/` and `execution/escalation.py`.

The key differences from qa-core:
- **Source scope:** `src/mozart/execution/runner/` (base, sheet, lifecycle, recovery, cost, isolation, patterns, models), `src/mozart/execution/escalation.py`
- **Test scope:** `tests/test_runner*.py`, `tests/test_lifecycle.py`, `tests/test_recovery_mixin.py`, `tests/test_sheet_execution*.py`, `tests/test_escalation_learning.py`, `tests/test_runner_cost.py`, `tests/test_runner_pause*.py`
- **Property-based targets:** RunnerConfig models, sheet execution state machines
- **Adversarial targets:** Broken backends (timeout, crash, garbage output), concurrent sheet execution, partial state recovery, escalation with impossible conditions, cost tracking with NaN/Inf values

The YAML follows the exact same structure as qa-core — identical stages, fan-out, dependencies, validations — but with the module-specific scope swapped in the preamble and overhaul_types variables.

Copy the qa-core.yaml structure, changing:
- `name: "qa-execution"`
- `description:` to match execution scope
- `workspace:` to `.qa-execution-workspace`
- Preamble source/test scope sections
- `overhaul_types` directives to target execution-specific adversarial scenarios
- Validation commands to run execution test files
- Coverage target to `--cov=mozart.execution`
- New test file names: `test_execution_property_based.py`, `test_execution_adversarial.py`

**Step 2: Validate, dry-run, commit**

```bash
mozart validate scores/qa/qa-execution.yaml
git add scores/qa/qa-execution.yaml
git commit -m "score(qa): add qa-execution — adversarial testing for runner/execution"
```

---

### Task 4: Create qa-daemon.yaml

**Files:**
- Create: `scores/qa/qa-daemon.yaml`

**Step 1: Write qa-daemon.yaml**

This is the biggest module score. The daemon has 20+ modules.

Same 5-stage template, but:
- **Source scope:** `src/mozart/daemon/` (all modules: manager, scheduler, IPC, event bus, health, rate coordinator, backpressure, learning hub, semantic analyzer, pgroup, monitor, registry, config, process, detect, output, types, profiler/*, etc.)
- **Test scope:** All `tests/test_daemon_*.py` files
- **Property-based targets:** IPC protocol messages (JSON-RPC), scheduler priority queues, event bus subscriptions, daemon config
- **Adversarial targets:**
  - IPC with malformed JSON, truncated messages, messages > 1MB
  - Scheduler with 0/negative priorities, more jobs than semaphore slots
  - Event bus with concurrent publish/subscribe from 100 coroutines
  - Health check when conductor is shutting down
  - Rate coordinator with NaN/Inf rate limits
  - Manager with duplicate job IDs, jobs submitted after shutdown
  - Process group management with zombie processes

Same structure as qa-core with module-specific swaps.
- New test files: `test_daemon_property_based.py`, `test_daemon_adversarial.py`
- Coverage: `--cov=mozart.daemon`

**Step 2: Validate, dry-run, commit**

```bash
mozart validate scores/qa/qa-daemon.yaml
git add scores/qa/qa-daemon.yaml
git commit -m "score(qa): add qa-daemon — adversarial testing for daemon modules"
```

---

### Task 5: Create qa-cli.yaml

**Files:**
- Create: `scores/qa/qa-cli.yaml`

Same 5-stage template targeting:
- **Source scope:** `src/mozart/cli/` (commands/, helpers.py, output.py, __init__.py)
- **Test scope:** `tests/test_cli*.py`, `tests/test_conductor_*.py`
- **Property-based targets:** CLI argument parsing, output rendering data models
- **Adversarial targets:**
  - CLI with garbage arguments (unicode, very long, null bytes)
  - Output rendering with huge data (10K sheets, 100K log lines)
  - Output rendering with empty data (no sheets, no jobs)
  - Pause/resume race conditions (pause while pausing, resume while running)
  - Conductor commands when no conductor is running
  - Status command with corrupted state files
  - Config command with invalid YAML

New test files: `test_cli_property_based.py`, `test_cli_adversarial.py`
Coverage: `--cov=mozart.cli`

**Step 2: Validate, dry-run, commit**

```bash
mozart validate scores/qa/qa-cli.yaml
git add scores/qa/qa-cli.yaml
git commit -m "score(qa): add qa-cli — adversarial testing for CLI commands"
```

---

### Task 6: Create qa-dashboard.yaml

**Files:**
- Create: `scores/qa/qa-dashboard.yaml`

Same 5-stage template targeting:
- **Source scope:** `src/mozart/dashboard/` (routes/, services/, auth, static, templates)
- **Test scope:** `tests/test_dashboard*.py`
- **Property-based targets:** Dashboard API response models, auth token formats
- **Adversarial targets:**
  - SSE with dropped connections mid-stream
  - Auth with expired/malformed/missing tokens
  - Rate limiting under concurrent load (100 simultaneous requests)
  - XSS in job names, sheet descriptions, error messages
  - CSRF on state-changing endpoints
  - Job control with nonexistent job IDs
  - Stream artifacts with extremely large files
  - Dashboard pages with no data (empty state)

New test files: `test_dashboard_property_based.py`, `test_dashboard_adversarial.py`
Coverage: `--cov=mozart.dashboard`

**Step 2: Validate, dry-run, commit**

```bash
mozart validate scores/qa/qa-dashboard.yaml
git add scores/qa/qa-dashboard.yaml
git commit -m "score(qa): add qa-dashboard — adversarial testing for web dashboard"
```

---

### Task 7: Create qa-validation.yaml

**Files:**
- Create: `scores/qa/qa-validation.yaml`

Same 5-stage template targeting:
- **Source scope:** `src/mozart/validation/` (checks/, base.py, rendering.py, reporter.py, runner.py), `src/mozart/healing/` (coordinator.py, remedies/)
- **Test scope:** `tests/test_validation*.py`, `tests/test_healing.py`
- **Property-based targets:** Validation rule configs, healing remedy configs
- **Adversarial targets:**
  - Validation with circular/catastrophic regex (ReDoS)
  - Validation with malformed YAML that hangs the parser
  - All remedies failing simultaneously
  - Healing coordinator when workspace is read-only
  - Validation runner with timeout of 0 seconds
  - Content regex with binary file input
  - file_exists validation with symlink loops
  - command_succeeds with command that produces 1GB output

New test files: `test_validation_property_based.py`, `test_validation_adversarial.py`
Coverage: `--cov=mozart.validation --cov=mozart.healing`

**Step 2: Validate, dry-run, commit**

```bash
mozart validate scores/qa/qa-validation.yaml
git add scores/qa/qa-validation.yaml
git commit -m "score(qa): add qa-validation — adversarial testing for validation/healing"
```

---

### Task 8: Create qa-integration.yaml

**Files:**
- Create: `scores/qa/qa-integration.yaml`

This score is different from the module scores. It tests cross-module interactions with minimal mocking.

```yaml
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║       QA INTEGRATION — Cross-Module End-to-End Tests                       ║
# ║                                                                            ║
# ║  Tests real code paths across module boundaries. Minimal mocking.          ║
# ║                                                                            ║
# ║  Stage 1: Identify integration gaps from module score coverage reports     ║
# ║  Stage 2 x3: Write integration tests (config→runner, IPC→manager, e2e)   ║
# ║  Stage 3: Merge and verify all integration tests pass                      ║
# ║  Stage 4: Full suite regression check                                      ║
# ║  Stage 5: Commit                                                           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
```

**Integration test targets:**
- Instance 1: **Config → Execution pipeline** — Parse real YAML, validate, create runner, execute sheet with mock backend, verify checkpoint saved correctly. Test the full config→validate→runner→execute→checkpoint chain.
- Instance 2: **IPC → Manager → Event Bus** — Start mock IPC server, submit job via client, verify manager receives it, verify events propagate through event bus. Test the full IPC→manager→runner→event chain.
- Instance 3: **End-to-end workflows** — Fan-out expansion → parallel execution → fan-in; error → retry → self-healing → retry; prelude/cadenza injection → template rendering → backend; pause mid-execution → checkpoint → resume.

New test file: `tests/test_cross_module_integration.py`

Same 5-stage structure with fan-out x3.

**Step 2: Validate, dry-run, commit**

```bash
mozart validate scores/qa/qa-integration.yaml
git add scores/qa/qa-integration.yaml
git commit -m "score(qa): add qa-integration — cross-module e2e tests"
```

---

### Task 9: Create qa-overnight.yaml

**Files:**
- Create: `scores/qa/qa-overnight.yaml`

This score is fundamentally different — it tests against a REAL running conductor. Designed for overnight cron execution.

```yaml
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║       QA OVERNIGHT — Long-Running Smoke Tests                              ║
# ║                                                                            ║
# ║  Tests Mozart end-to-end against a real conductor. Designed for cron.      ║
# ║                                                                            ║
# ║  Stage 1: Start conductor, verify health                                   ║
# ║  Stage 2: Submit basic job, wait for completion                            ║
# ║  Stage 3: Pause/resume cycle                                               ║
# ║  Stage 4: Concurrent job submission                                        ║
# ║  Stage 5: Error recovery with retries                                      ║
# ║  Stage 6: Crash recovery (kill -9, restart, resume)                        ║
# ║  Stage 7: Cleanup and orphan detection                                     ║
# ║  Stage 8: Generate report and notify on failure                            ║
# ║                                                                            ║
# ║  Total runtime: ~55 minutes                                                ║
# ║  Cron: 0 2 * * * mozart run scores/qa/qa-overnight.yaml                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
```

Key differences from module scores:
- 8 sequential stages, no fan-out
- Each stage runs REAL Mozart CLI commands (not pytest)
- Timeouts are much longer (stages 2-6 need 10-15 min each)
- Validations check actual Mozart state (job status, process lists)
- Stage 1 starts a real conductor; Stage 7 stops it
- Stage 8 generates an HTML/markdown report and triggers desktop notification on failure

Uses a dedicated workspace with date-stamped archives for overnight history.

The prompts for this score are more prescriptive than module scores because the agent needs to run specific Mozart commands in specific order (start conductor → submit job → poll status → verify outcome).

**Step 2: Validate, dry-run, commit**

```bash
mozart validate scores/qa/qa-overnight.yaml
git add scores/qa/qa-overnight.yaml
git commit -m "score(qa): add qa-overnight — long-running smoke tests for cron"
```

---

### Task 10: Final validation and push

**Files:**
- Verify: all 9 files in `scores/qa/`

**Step 1: Validate all scores**

```bash
for f in scores/qa/qa-*.yaml; do
  echo "=== Validating $f ==="
  mozart validate "$f" || echo "FAILED: $f"
done
```

**Step 2: Dry-run the foundation score**

```bash
mozart run scores/qa/qa-foundation.yaml --dry-run
```

**Step 3: Verify git status**

```bash
git status
git log --oneline -10
```

**Step 4: Push all commits**

```bash
git push
```

**Step 5: Commit the implementation plan**

```bash
git add docs/plans/2026-02-24-qa-testing-overhaul-implementation.md
git commit -m "docs: QA testing overhaul implementation plan"
git push
```

---

## Execution Notes

**Running order:**
1. `qa-foundation` first (always)
2. Module scores in any order — can run as concert for parallelism
3. `qa-integration` after module scores complete
4. `qa-overnight` separately, via cron

**Running as a concert:**
```bash
# Foundation first
mozart run scores/qa/qa-foundation.yaml

# Then all module scores in parallel
mozart run scores/qa/qa-core.yaml &
mozart run scores/qa/qa-execution.yaml &
mozart run scores/qa/qa-daemon.yaml &
mozart run scores/qa/qa-cli.yaml &
mozart run scores/qa/qa-dashboard.yaml &
mozart run scores/qa/qa-validation.yaml &
wait

# Then integration
mozart run scores/qa/qa-integration.yaml

# Overnight via cron
crontab -e
# Add: 0 2 * * * cd /home/emzi/Projects/mozart-ai-compose && mozart run scores/qa/qa-overnight.yaml
```

**Expected cost:** $60-90 for the full suite. Module scores can be re-run individually when their module changes.
