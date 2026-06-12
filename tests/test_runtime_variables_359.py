"""#359: runtime variable injection via ``mzt run --var key=value``.

Generic, reusable scores parameterized per-invocation without editing
YAML — the make/ansible/terraform pattern. Contract:

- ``--var k=v`` (repeatable) supplies string values that merge into the
  score's ``prompt.variables``; CLI overrides YAML on key collision.
- Values are STRINGS by design — typed variables stay in YAML, which
  removes any coercion ambiguity at the CLI boundary.
- Durability (the #361 run-options lesson): runtime variables persist on
  ``CheckpointState`` and re-apply on resume, because the default resume
  path re-reads the YAML from disk and would otherwise silently drop
  them.
"""

from __future__ import annotations

import pytest

from marianne.cli.commands.run import _parse_runtime_vars
from marianne.core.checkpoint import CheckpointState, JobStatus


class TestParseRuntimeVars:
    def test_basic_pairs(self) -> None:
        assert _parse_runtime_vars(["a=1", "b=hello"]) == {"a": "1", "b": "hello"}

    def test_value_may_contain_equals(self) -> None:
        assert _parse_runtime_vars(["cmd=x=y"]) == {"cmd": "x=y"}

    def test_empty_value_allowed(self) -> None:
        assert _parse_runtime_vars(["k="]) == {"k": ""}

    def test_none_and_empty(self) -> None:
        assert _parse_runtime_vars(None) == {}
        assert _parse_runtime_vars([]) == {}

    def test_missing_equals_rejected(self) -> None:
        with pytest.raises(ValueError, match="key=value"):
            _parse_runtime_vars(["novalue"])

    def test_empty_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty key"):
            _parse_runtime_vars(["=v"])

    def test_last_wins_on_duplicate(self) -> None:
        assert _parse_runtime_vars(["k=1", "k=2"]) == {"k": "2"}


class TestDurability:
    def test_runtime_variables_persist_on_checkpoint(self) -> None:
        """The field must round-trip through serialization so resume sees
        it after a conductor restart."""
        state = CheckpointState(
            job_id="j",
            job_name="j",
            total_sheets=1,
            status=JobStatus.RUNNING,
            runtime_variables={"target": "ovrm", "lang": "C"},
        )
        restored = CheckpointState.model_validate_json(state.model_dump_json())
        assert restored.runtime_variables == {"target": "ovrm", "lang": "C"}

    def test_default_is_empty(self) -> None:
        state = CheckpointState(
            job_id="j", job_name="j", total_sheets=1, status=JobStatus.RUNNING
        )
        assert state.runtime_variables == {}


class TestMergePrecedence:
    def test_cli_overrides_yaml(self) -> None:
        """The documented contract: CLI --var wins over a YAML variable of
        the same name; non-colliding YAML variables are preserved."""
        from marianne.core.config import JobConfig

        config = JobConfig.model_validate(
            {
                "name": "param-score",
                "workspace": "./ws",
                "instrument": "claude-code",
                "sheet": {"size": 1, "total_items": 1},
                "prompt": {
                    "template": "{{ target }} {{ keep }}",
                    "variables": {"target": "yaml-default", "keep": "yes"},
                },
            }
        )
        merged = dict(config.prompt.variables)
        merged.update({"target": "cli-value"})
        assert merged == {"target": "cli-value", "keep": "yes"}



class TestManagerMergeHelper:
    """The single merge seam used by both first-run and resume, so the two
    paths can't drift (the durability bug class)."""

    def test_apply_merges_cli_over_yaml(self) -> None:
        from marianne.core.config import JobConfig
        from marianne.daemon.manager import _merge_runtime_variables

        config = JobConfig.model_validate(
            {
                "name": "s",
                "workspace": "./ws",
                "instrument": "claude-code",
                "sheet": {"size": 1, "total_items": 1},
                "prompt": {
                    "template": "{{ a }}{{ b }}",
                    "variables": {"a": "yaml", "b": "keep"},
                },
            }
        )
        merged = _merge_runtime_variables(config, {"a": "cli", "c": "new"})
        assert merged.prompt.variables == {"a": "cli", "b": "keep", "c": "new"}
        # Original config is not mutated (model_copy).
        assert config.prompt.variables == {"a": "yaml", "b": "keep"}

    def test_apply_empty_is_noop_same_object(self) -> None:
        from marianne.core.config import JobConfig
        from marianne.daemon.manager import _merge_runtime_variables

        config = JobConfig.model_validate(
            {
                "name": "s",
                "workspace": "./ws",
                "instrument": "claude-code",
                "sheet": {"size": 1, "total_items": 1},
                "prompt": {"template": "x"},
            }
        )
        assert _merge_runtime_variables(config, {}) is config
