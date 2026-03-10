"""Tests for prelude bug fix and JobConfig.to_yaml() serialization (CIB Cycle 1).

Tests Forge's work: top-level prelude fix (example files) and round-trip
serialization via to_yaml().
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozart.core.config import (
    InjectionCategory,
    InjectionItem,
    JobConfig,
)


class TestPreludeBugFix:
    """Tests for the top-level prelude bug fix.

    The bug: score-composer.yaml and design-review.yaml had prelude at the
    YAML root level (sibling of sheet, prompt, backend). JobConfig has no
    prelude field, so Pydantic silently dropped it. Additionally, they used
    'path:' instead of 'file:' in InjectionItem.

    Forge's fix: move prelude under sheet: and change path: to file:.
    """

    @pytest.mark.smoke
    def test_score_composer_prelude_preserved(self):
        """After fix, score-composer.yaml must have prelude under sheet."""
        config = JobConfig.from_yaml(Path("examples/score-composer.yaml"))
        assert len(config.sheet.prelude) > 0, (
            "score-composer.yaml prelude is empty — the fix didn't work or "
            "prelude is still at the wrong nesting level"
        )

    @pytest.mark.smoke
    def test_design_review_prelude_preserved(self):
        """After fix, design-review.yaml must have prelude under sheet."""
        example = Path("examples/design-review.yaml")
        if not example.exists():
            pytest.skip("design-review.yaml not found")
        config = JobConfig.from_yaml(example)
        assert len(config.sheet.prelude) > 0, (
            "design-review.yaml prelude is empty — the fix didn't work"
        )

    @pytest.mark.adversarial
    def test_injection_item_requires_file_not_path(self):
        """InjectionItem requires 'file' field — 'path' is not accepted."""
        with pytest.raises(ValidationError):
            InjectionItem.model_validate({"path": "test.md", "as": "context"})

    @pytest.mark.adversarial
    def test_injection_item_file_field_required(self):
        """InjectionItem without 'file' field raises ValidationError."""
        with pytest.raises(ValidationError):
            InjectionItem.model_validate({"as": "context"})

    @pytest.mark.adversarial
    def test_injection_item_both_path_and_file(self):
        """If both 'path' and 'file' are provided, only 'file' matters."""
        item = InjectionItem.model_validate(
            {"file": "correct.md", "path": "ignored.md", "as": "context"}
        )
        assert item.file == "correct.md"

    @pytest.mark.adversarial
    def test_top_level_prelude_silently_dropped(self):
        """Top-level prelude on JobConfig is silently ignored (extra='ignore').

        This documents the current behavior: top-level prelude is NOT supported.
        """
        config = JobConfig.model_validate({
            "name": "test",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "prelude": [{"file": "test.md", "as": "context"}],
        })
        assert config.sheet.prelude == []

    @pytest.mark.adversarial
    def test_prelude_correct_nesting_works(self):
        """Prelude under sheet: is correctly parsed."""
        config = JobConfig.model_validate({
            "name": "test",
            "sheet": {
                "size": 1,
                "total_items": 1,
                "prelude": [{"file": "context.md", "as": "context"}],
            },
            "prompt": {"template": "test"},
        })
        assert len(config.sheet.prelude) == 1
        assert config.sheet.prelude[0].file == "context.md"
        assert config.sheet.prelude[0].as_ == InjectionCategory.CONTEXT

    @pytest.mark.adversarial
    def test_prelude_uses_file_field_in_examples(self):
        """All example scores with preludes use 'file:' not 'path:'."""
        for example_path in Path("examples").glob("*.yaml"):
            with open(example_path) as f:
                raw = yaml.safe_load(f)
            if raw is None:
                continue
            sheet = raw.get("sheet", {})
            if not isinstance(sheet, dict):
                continue
            prelude_items = sheet.get("prelude", [])
            for i, item in enumerate(prelude_items):
                assert "file" in item, (
                    f"{example_path.name}: prelude item {i} uses "
                    f"'{list(item.keys())}' instead of 'file'"
                )
                assert "path" not in item, (
                    f"{example_path.name}: prelude item {i} uses 'path' "
                    f"instead of 'file' (the bug)"
                )


class TestToYamlRoundTrip:
    """Tests for JobConfig.to_yaml() round-trip serialization.

    The invariant: from_yaml_string(config.to_yaml()) must produce a
    semantically equivalent config (compared via model_dump()).
    """

    @pytest.mark.smoke
    def test_minimal_config_roundtrip(self):
        """Minimal config survives to_yaml -> from_yaml_string roundtrip."""
        original = JobConfig.model_validate({
            "name": "minimal",
            "sheet": {"size": 5, "total_items": 10},
            "prompt": {"template": "{{ sheet_num }}"},
        })
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert original.model_dump() == restored.model_dump()

    @pytest.mark.smoke
    def test_to_yaml_produces_valid_yaml(self):
        """to_yaml() output must be parseable YAML."""
        config = JobConfig.model_validate({
            "name": "yaml-validity",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
        })
        yaml_str = config.to_yaml()
        parsed = yaml.safe_load(yaml_str)
        assert isinstance(parsed, dict)
        assert parsed["name"] == "yaml-validity"

    @pytest.mark.smoke
    @pytest.mark.parametrize("example_name", [
        "simple-sheet",
        "prelude-cadenza-example",
        "cross-sheet-test",
        "quality-continuous",
        "dinner-party",
        "worldbuilder",
        "dialectic",
        "nonfiction-book",
        "api-backend",
        "strategic-plan",
    ])
    def test_example_score_roundtrip(self, example_name: str):
        """Example scores survive to_yaml -> from_yaml_string roundtrip."""
        example_path = Path(f"examples/{example_name}.yaml")
        if not example_path.exists():
            pytest.skip(f"Example {example_name}.yaml not found")
        original = JobConfig.from_yaml(example_path)
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert original.model_dump() == restored.model_dump()

    @pytest.mark.adversarial
    def test_jinja2_template_preserved(self):
        """Jinja2 {{ }} syntax in templates must survive round-trip."""
        original = JobConfig.model_validate({
            "name": "jinja-test",
            "sheet": {"size": 1, "total_items": 3},
            "prompt": {
                "template": "Process {{ sheet_num }} of {{ total_sheets }}.\n"
                            "Workspace: {{ workspace }}\n"
                            "Items: {{ start_item }}-{{ end_item }}",
            },
        })
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.prompt.template == original.prompt.template
        assert "{{ sheet_num }}" in restored.prompt.template
        assert "{{ total_sheets }}" in restored.prompt.template

    @pytest.mark.adversarial
    def test_multiline_template_preserved(self):
        """Multiline YAML template must survive round-trip."""
        template = (
            "Line 1: Process sheet {{ sheet_num }}.\n"
            "Line 2: This is a detailed instruction.\n"
            "Line 3: Write output to {{ workspace }}/result.md\n"
        )
        original = JobConfig.model_validate({
            "name": "multiline-test",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": template},
        })
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.prompt.template == original.prompt.template

    @pytest.mark.adversarial
    def test_injection_item_alias_serialized_correctly(self):
        """InjectionItem 'as_' field must serialize as 'as' in YAML output."""
        original = JobConfig.model_validate({
            "name": "alias-test",
            "sheet": {
                "size": 1,
                "total_items": 1,
                "prelude": [{"file": "context.md", "as": "context"}],
            },
            "prompt": {"template": "test"},
        })
        yaml_str = original.to_yaml()
        assert "as_:" not in yaml_str, "InjectionItem alias not applied — 'as_' in output"
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.sheet.prelude[0].as_ == InjectionCategory.CONTEXT

    @pytest.mark.adversarial
    def test_path_fields_serialized_as_strings(self):
        """Path objects must become strings in YAML output."""
        original = JobConfig.model_validate({
            "name": "path-test",
            "workspace": "/tmp/test-workspace",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
        })
        yaml_str = original.to_yaml()
        assert "!!python" not in yaml_str, "YAML output contains Python-specific tags"
        restored = JobConfig.from_yaml_string(yaml_str)
        assert isinstance(restored.workspace, Path)

    @pytest.mark.adversarial
    def test_enum_fields_serialized_as_values(self):
        """Enum fields must serialize as their string values, not enum repr."""
        original = JobConfig.model_validate({
            "name": "enum-test",
            "sheet": {
                "size": 1,
                "total_items": 1,
                "prelude": [{"file": "test.md", "as": "skill"}],
            },
            "prompt": {"template": "test"},
            "conductor": {"role": "ai"},
        })
        yaml_str = original.to_yaml()
        assert "InjectionCategory" not in yaml_str
        assert "ConductorRole" not in yaml_str
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.sheet.prelude[0].as_ == InjectionCategory.SKILL

    @pytest.mark.adversarial
    def test_none_fields_roundtrip(self):
        """Fields with None values must survive round-trip correctly."""
        original = JobConfig.model_validate({
            "name": "null-test",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
        })
        assert original.state_path is None
        assert original.bridge is None
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.state_path is None
        assert restored.bridge is None

    @pytest.mark.adversarial
    def test_exclude_defaults_produces_valid_yaml(self):
        """to_yaml(exclude_defaults=True) produces valid, parseable YAML."""
        original = JobConfig.model_validate({
            "name": "exclude-defaults",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
        })
        yaml_str = original.to_yaml(exclude_defaults=True)
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.name == "exclude-defaults"

    @pytest.mark.adversarial
    def test_special_characters_in_strings(self):
        """Strings with special characters must survive round-trip."""
        original = JobConfig.model_validate({
            "name": "special-chars",
            "description": 'Contains: colons, #hashes, [brackets], {braces}, "quotes"',
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
        })
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.description == original.description

    @pytest.mark.adversarial
    def test_unicode_in_strings(self):
        """Unicode characters must survive round-trip."""
        original = JobConfig.model_validate({
            "name": "unicode-test",
            "description": "Supports: caf\u00e9, \u2603, \u2764\ufe0f, \u65e5\u672c\u8a9e",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
        })
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.description == original.description

    @pytest.mark.adversarial
    def test_empty_sheets_roundtrip(self):
        """Config with minimal sheet (size=1, total_items=1) survives."""
        original = JobConfig.model_validate({
            "name": "min-sheets",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "{{ sheet_num }}"},
        })
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.sheet.total_sheets == 1

    @pytest.mark.adversarial
    def test_concert_config_roundtrip(self):
        """Concert/on_success sections survive round-trip."""
        original = JobConfig.model_validate({
            "name": "concert-test",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": "{workspace}/next-score.yaml",
                    "description": "Chain to next phase",
                },
            ],
        })
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert len(restored.on_success) == 1
        assert restored.on_success[0].type == "run_job"

    @pytest.mark.adversarial
    def test_validation_rules_roundtrip(self):
        """Validation rules survive round-trip."""
        original = JobConfig.model_validate({
            "name": "validations-rt",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "validations": [
                {
                    "type": "file_exists",
                    "path": "{workspace}/output.txt",
                    "description": "Output file exists",
                },
                {
                    "type": "content_contains",
                    "path": "{workspace}/log.txt",
                    "pattern": "SUCCESS",
                    "description": "Success marker",
                },
            ],
        })
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert len(restored.validations) == 2
        assert restored.validations[0].type == "file_exists"
        assert restored.validations[1].pattern == "SUCCESS"

    @pytest.mark.adversarial
    def test_fan_out_expanded_config_roundtrip(self):
        """Fan-out scores with expanded configs survive round-trip."""
        example_path = Path("examples/design-review.yaml")
        if not example_path.exists():
            pytest.skip("design-review.yaml not found")
        original = JobConfig.from_yaml(example_path)
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert original.model_dump() == restored.model_dump()

    @pytest.mark.adversarial
    def test_score_composer_roundtrip(self):
        """score-composer.yaml (with prelude fix) survives round-trip."""
        original = JobConfig.from_yaml(Path("examples/score-composer.yaml"))
        yaml_str = original.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert original.model_dump() == restored.model_dump()
        assert len(restored.sheet.prelude) == 2
