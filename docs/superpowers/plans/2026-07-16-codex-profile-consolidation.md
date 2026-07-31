# Codex Instrument Profile Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the mistaken standalone `gpt-5.6` instrument and make the complete GPT-5.6 model family available exclusively through `codex-cli`, with no Marianne-selected default model.

**Architecture:** Preserve one execution contract per tool: `codex-cli.yaml` owns invocation of the Codex executable, while its `models` list describes the models that executable can play. Delete the duplicate profile instead of retaining an alias or compatibility shim, then align current documentation and the plugin catalog with that boundary.

**Tech Stack:** Python 3.12, Pydantic instrument models, YAML built-in profiles, pytest, Typer CLI, Markdown/YAML reference documentation, Git submodule at `plugins/marianne`.

## Global Constraints

- This is a clean break: do not add an alias, shim, deprecated profile, or legacy score path for `gpt-5.6`.
- `codex-cli.default_model` must remain unset so Marianne emits no `--model` flag unless a score supplies `instrument_config.model`.
- Preserve the existing Codex CLI command, interactive, output, authentication, and error-classification configuration.
- Preserve the older Codex model entries unless current source proves one invalid.
- Keep all six GPT-5.6 model variants and their approved metadata under `codex-cli`.
- Update current reference material; do not rewrite historical plans, research, or evaluation records as though they were current docs.
- Use TDD: observe the new contract fail before modifying the built-in profiles.
- Preserve unrelated work in the original dirty checkout; perform all changes in `/home/emzi/Projects/marianne-expert-release`.

---

### Task 1: Encode and implement the instrument/model boundary

**Files:**
- Modify: `tests/test_instrument_user_journeys.py`
- Modify: `tests/test_builtin_instrument_profiles.py`
- Modify: `src/marianne/instruments/builtins/codex-cli.yaml`
- Delete: `src/marianne/instruments/builtins/gpt-5.6.yaml`

**Interfaces:**
- Consumes: `InstrumentProfileLoader.load_directory(path) -> dict[str, InstrumentProfile]` and `InstrumentProfile.model_validate(data) -> InstrumentProfile`.
- Produces: one registered Codex instrument named `codex-cli`; `profile.default_model is None`; six GPT-5.6 `InstrumentModel` entries with exact metadata; the existing `cli.command.model_flag == "--model"` override path.

- [ ] **Step 1: Write the failing discovery and clean-break tests**

In `tests/test_instrument_user_journeys.py`, remove `"gpt-5.6"` from `expected_names`, change the docstring to avoid a fixed stale count, and make the retired name explicit:

```python
    def test_builtin_profiles_all_load_successfully(self) -> None:
        """All built-in profiles parse without errors."""
        builtins_dir = (
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        profiles = InstrumentProfileLoader.load_directory(builtins_dir)

        expected_names = {
            "claude-code",
            "gemini-cli",
            "antigravity",
            "codex-cli",
            "cline-cli",
            "aider",
            "goose",
            "opencode",
            "crush",
            "cli",
            "ollama",
        }
        assert len(profiles) == len(expected_names)
        assert set(profiles) == expected_names
        assert "gpt-5.6" not in profiles
```

In `tests/test_builtin_instrument_profiles.py`, add:

```python
    def test_standalone_gpt_5_6_profile_is_removed(self) -> None:
        """GPT-5.6 is a Codex model family, not an instrument."""
        assert not (BUILTINS_DIR / "gpt-5.6.yaml").exists()
```

- [ ] **Step 2: Write the failing Codex model-family test**

Add this test to `TestProfileDetails` in `tests/test_builtin_instrument_profiles.py`:

```python
    def test_codex_owns_complete_gpt_5_6_family_without_default(self) -> None:
        """Codex carries GPT-5.6 metadata but lets the client choose its default."""
        path = BUILTINS_DIR / "codex-cli.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)

        assert profile.default_model is None
        assert {"vision", "thinking"} <= set(profile.capabilities)
        assert profile.cli is not None
        assert profile.cli.command.model_flag == "--model"

        expected = {
            "gpt-5.6-sol": (272_000, 0.005, 0.030, 128_000, 4),
            "gpt-5.6-sol[1m]": (1_050_000, 0.005, 0.030, 128_000, 4),
            "gpt-5.6-terra": (272_000, 0.0025, 0.015, 128_000, 6),
            "gpt-5.6-terra[1m]": (1_050_000, 0.0025, 0.015, 128_000, 6),
            "gpt-5.6-luna": (272_000, 0.001, 0.006, 128_000, 8),
            "gpt-5.6-luna[1m]": (1_050_000, 0.001, 0.006, 128_000, 8),
        }
        actual = {
            model.name: (
                model.context_window,
                model.cost_per_1k_input,
                model.cost_per_1k_output,
                model.max_output_tokens,
                model.max_concurrent,
            )
            for model in profile.models
            if model.name.startswith("gpt-5.6-")
        }
        assert actual == expected
```

- [ ] **Step 3: Run the focused tests and verify the new contract fails**

Run:

```bash
PYTHONPATH=$PWD/src uv run --no-sync pytest \
  tests/test_instrument_user_journeys.py::TestDiscoverInstruments::test_builtin_profiles_all_load_successfully \
  tests/test_builtin_instrument_profiles.py::TestBuiltinProfilesExist::test_standalone_gpt_5_6_profile_is_removed \
  tests/test_builtin_instrument_profiles.py::TestProfileDetails::test_codex_owns_complete_gpt_5_6_family_without_default \
  -v
```

Expected: FAIL because `gpt-5.6` is still discovered, `gpt-5.6.yaml` still exists, and `codex-cli` lacks the three `[1m]` entries plus the full capability/concurrency metadata.

- [ ] **Step 4: Consolidate GPT-5.6 metadata into Codex**

In `src/marianne/instruments/builtins/codex-cli.yaml`:

- add `vision` and `thinking` to `capabilities`;
- leave `default_model` absent;
- replace the three partial GPT-5.6 entries with the six exact entries asserted above;
- retain `o3` and `o4-mini`;
- replace the comment pointing to `gpt-5.6.yaml` with a comment explaining that GPT-5.6 is a model family played through Codex;
- leave every `cli:` setting unchanged.

Delete `src/marianne/instruments/builtins/gpt-5.6.yaml`.

- [ ] **Step 5: Run the focused tests and the existing model-override contract**

Run:

```bash
PYTHONPATH=$PWD/src uv run --no-sync pytest \
  tests/test_instrument_user_journeys.py \
  tests/test_builtin_instrument_profiles.py \
  tests/test_instrument_config_model_override.py::TestPluginCliBackendModelOverride::test_model_override_with_none_profile_default \
  -q
```

Expected: PASS.

- [ ] **Step 6: Commit the runtime contract**

```bash
git add tests/test_instrument_user_journeys.py \
  tests/test_builtin_instrument_profiles.py \
  src/marianne/instruments/builtins/codex-cli.yaml \
  src/marianne/instruments/builtins/gpt-5.6.yaml
git commit -m "fix: consolidate GPT-5.6 under Codex"
```

### Task 2: Align current documentation and the Marianne plugin catalog

**Files:**
- Modify: `docs/limitations.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/instrument-guide.md`
- Create: `tests/test_instrument_catalog.py`
- Modify: `plugins/marianne/docs/ref/instrument-catalog.yaml`
- Modify: `plugins/marianne/docs/ref/instrument-catalog.md`
- Modify: `plugins/marianne/docs/ref/CHANGELOG-instrument-catalog.md`

**Interfaces:**
- Consumes: the Task 1 profile contract and the catalog's existing `instruments` / `musicians` schema.
- Produces: current docs that use `codex-cli` as the instrument name, describe GPT-5.6 as its model family, and show `(instrument default)` for Codex unless a model is explicitly selected.

- [ ] **Step 1: Add a maintained-reference regression assertion**

Create `tests/test_instrument_catalog.py`:

```python
"""Contracts for Marianne's authoritative instrument catalog."""

from pathlib import Path

import yaml

CATALOG_PATH = (
    Path(__file__).parent.parent
    / "plugins"
    / "marianne"
    / "docs"
    / "ref"
    / "instrument-catalog.yaml"
)


def test_current_catalog_places_gpt_5_6_under_codex() -> None:
    """GPT-5.6 is a Codex model family, never a separate instrument."""
    catalog = yaml.safe_load(CATALOG_PATH.read_text())
    codex = catalog["instruments"]["codex-cli"]
    expected = {
        "gpt-5.6-sol",
        "gpt-5.6-sol[1m]",
        "gpt-5.6-terra",
        "gpt-5.6-terra[1m]",
        "gpt-5.6-luna",
        "gpt-5.6-luna[1m]",
    }
    assert expected <= set(codex["runs_models"])
    assert "gpt-5.6" not in catalog["instruments"]
    assert "thinking" in codex["capabilities"]
```

Use the test file's existing root constant and YAML import names if they differ.

- [ ] **Step 2: Run the catalog regression and verify it fails**

Run:

```bash
PYTHONPATH=$PWD/src uv run --no-sync pytest \
  tests/test_instrument_catalog.py::test_current_catalog_places_gpt_5_6_under_codex \
  -v
```

Expected: FAIL because the catalog still lists older Codex models and omits GPT-5.6 plus `thinking`.

- [ ] **Step 3: Update maintained parent documentation**

Apply these exact semantic changes:

- `docs/limitations.md`: change “Twelve instruments” to “Eleven instruments” and remove `gpt-5.6` from the built-in list.
- `docs/cli-reference.md`: show `codex-cli` with `(instrument default)` and change the sample summary to `11 instruments configured`.
- `docs/instrument-guide.md`: after the score example, add an explicit Codex selection example:

```yaml
instrument: codex-cli
instrument_config:
  model: gpt-5.6-luna
```

State that omitting `instrument_config.model` leaves the model selection to the installed Codex client; Marianne does not default Codex to Luna, Terra, or Sol.

- [ ] **Step 4: Update the plugin catalog**

In `plugins/marianne/docs/ref/instrument-catalog.yaml`:

- change the Codex capabilities to include `thinking`;
- replace its `runs_models` list with all six GPT-5.6 variants followed by the retained older entries;
- update the note to say GPT-5.6 is selected as a model through Codex and no separate GPT-5.6 instrument exists;
- add musician metadata for Sol, Terra, and Luna using the approved base context, output, and per-million-token prices (`5/30`, `2.5/15`, `1/6`) with `available_via: [codex-cli]`.

In `plugins/marianne/docs/ref/instrument-catalog.md`:

- describe `codex-cli` as the route to GPT-5.6;
- replace the current OpenAI summary table's leading entry with Sol, Terra, and Luna rows;
- retain older-model rows as historical/current catalog entries rather than calling GPT-5.5 the Codex default.

Prepend a dated entry to
`plugins/marianne/docs/ref/CHANGELOG-instrument-catalog.md` recording that the
standalone `gpt-5.6` instrument was rejected, the six variants were placed
under `codex-cli`, Codex's default remains client-selected, and the new model
facts came from the 2026-07-15 verified source set.

- [ ] **Step 5: Run catalog parsing and plugin tests**

Run:

```bash
python - <<'PY'
from pathlib import Path
import yaml

path = Path("plugins/marianne/docs/ref/instrument-catalog.yaml")
catalog = yaml.safe_load(path.read_text())
assert "gpt-5.6" not in catalog["instruments"]
assert catalog["instruments"]["codex-cli"]["runs_models"][0] == "gpt-5.6-sol"
print("catalog ok")
PY

PYTHONPATH=$PWD/src uv run --no-sync pytest \
  tests/test_instrument_catalog.py -q
```

Expected: `catalog ok`; catalog contract test PASS.

- [ ] **Step 6: Commit plugin documentation, then record the submodule pointer**

```bash
git -C plugins/marianne add \
  docs/ref/instrument-catalog.yaml \
  docs/ref/instrument-catalog.md \
  docs/ref/CHANGELOG-instrument-catalog.md
git -C plugins/marianne commit -m "docs: place GPT-5.6 under Codex"

git add docs/limitations.md docs/cli-reference.md docs/instrument-guide.md \
  tests/test_instrument_catalog.py plugins/marianne
git commit -m "docs: align Codex instrument references"
```

### Task 3: Prove the clean break across source, CLI, and full suites

**Files:**
- Verify: `src/marianne/instruments/builtins/`
- Verify: `docs/`
- Verify: `plugins/marianne/docs/ref/`
- Modify only if verification exposes a current, non-historical stale reference.

**Interfaces:**
- Consumes: the runtime and documentation contracts from Tasks 1 and 2.
- Produces: release evidence that the standalone instrument is absent while explicit GPT-5.6 selection through Codex remains operational.

- [ ] **Step 1: Verify source binding and profile discovery**

Run:

```bash
PYTHONPATH=$PWD/src uv run --no-sync python - <<'PY'
from pathlib import Path
import marianne
from marianne.instruments.loader import load_all_profiles

root = Path.cwd().resolve()
source = Path(marianne.__file__).resolve()
assert source.is_relative_to(root / "src"), source
profiles = load_all_profiles()
assert "gpt-5.6" not in profiles
codex = profiles["codex-cli"]
assert codex.default_model is None
assert len([m for m in codex.models if m.name.startswith("gpt-5.6-")]) == 6
print(source)
print("profile discovery ok")
PY
```

Expected: candidate worktree source path followed by `profile discovery ok`.

- [ ] **Step 2: Verify current references contain no standalone instrument usage**

Run:

```bash
rg -n \
  'instrument:[[:space:]]*gpt-5\\.6|`gpt-5\\.6` instrument|name:[[:space:]]*gpt-5\\.6' \
  src tests docs examples scores plugins/marianne \
  -g '!docs/plans/**' \
  -g '!docs/research/**' \
  -g '!docs/handoffs/**' \
  -g '!docs/superpowers/specs/**' \
  -g '!docs/superpowers/plans/**'
```

Expected: no matches.

- [ ] **Step 3: Verify the CLI surface**

Run:

```bash
PYTHONPATH=$PWD/src uv run --no-sync mzt instruments list
PYTHONPATH=$PWD/src uv run --no-sync mzt instruments check codex-cli
```

Expected: `codex-cli` is listed with `(instrument default)`; `gpt-5.6` is not listed as an instrument; Codex readiness reflects the installed binary and auth state without a profile error.

- [ ] **Step 4: Run focused instrument tests**

Run:

```bash
PYTHONPATH=$PWD/src uv run --no-sync pytest \
  tests/test_instrument_user_journeys.py \
  tests/test_builtin_instrument_profiles.py \
  tests/test_cli_instruments.py \
  tests/test_instrument_config_model_override.py \
  tests/test_tokens.py \
  -q
```

Expected: PASS.

- [ ] **Step 5: Run the full repository suite**

Run:

```bash
PYTHONPATH=$PWD/src uv run --no-sync pytest -q
```

Expected: the full suite PASS with no unexpected skips or collection failures.

- [ ] **Step 6: Inspect the final diff and commit any verification-only correction**

Run:

```bash
git diff --check
git status --short
git -C plugins/marianne status --short
git log --oneline --decorate -5
git -C plugins/marianne log --oneline --decorate -3
```

Expected: no whitespace errors; parent and plugin worktrees clean; commits show the design, implementation, plugin catalog, and documentation alignment.
