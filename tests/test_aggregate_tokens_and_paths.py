"""Tests for aggregate token counting and extract_json_path_all.

Multi-model instruments (e.g., gemini-cli with flash-lite router + main model)
report token counts under multiple keys. The `aggregate_tokens` config flag
tells the PluginCliBackend to sum ALL wildcard matches instead of returning
the first. This requires `extract_json_path_all` — a sibling to the original
`extract_json_path` that collects all matches.

TDD tests written by Harper, movement 4.
"""
from __future__ import annotations

import json

from marianne.utils.json_path import extract_json_path, extract_json_path_all


# =============================================================================
# extract_json_path_all — comprehensive tests
# =============================================================================


class TestExtractJsonPathAll:
    """Tests for extract_json_path_all — collects ALL wildcard matches."""

    def test_collects_all_wildcard_matches(self) -> None:
        """Should return all values matching a wildcard, not just the first."""
        data = {"models": {"a": {"tokens": 100}, "b": {"tokens": 200}}}
        results = extract_json_path_all(data, "models.*.tokens")
        assert sorted(results) == [100, 200]

    def test_three_models(self) -> None:
        """Three models — all three token counts returned."""
        data = {
            "stats": {
                "models": {
                    "flash-lite": {"tokens": {"prompt": 50, "candidates": 30}},
                    "flash": {"tokens": {"prompt": 150, "candidates": 200}},
                    "pro": {"tokens": {"prompt": 300, "candidates": 400}},
                }
            }
        }
        results = extract_json_path_all(
            data, "stats.models.*.tokens.prompt"
        )
        assert sorted(results) == [50, 150, 300]

    def test_single_model_returns_list_of_one(self) -> None:
        """Single model returns a list with one element."""
        data = {"models": {"main": {"tokens": 42}}}
        results = extract_json_path_all(data, "models.*.tokens")
        assert results == [42]

    def test_empty_dict_returns_empty(self) -> None:
        """Empty wildcard dict returns empty list."""
        data: dict = {"models": {}}
        results = extract_json_path_all(data, "models.*.tokens")
        assert results == []

    def test_no_wildcard_path(self) -> None:
        """Path without wildcard returns a list of one value."""
        data = {"usage": {"input_tokens": 100}}
        results = extract_json_path_all(data, "usage.input_tokens")
        assert results == [100]

    def test_missing_key_returns_empty(self) -> None:
        """Missing key in path returns empty list."""
        data = {"models": {"a": {"tokens": 100}}}
        results = extract_json_path_all(data, "models.*.missing")
        assert results == []

    def test_none_data_returns_empty(self) -> None:
        """None data returns empty list."""
        results = extract_json_path_all(None, "any.path")
        assert results == []

    def test_empty_path_returns_empty(self) -> None:
        """Empty path returns empty list."""
        results = extract_json_path_all({"key": "val"}, "")
        assert results == []

    def test_wildcard_on_non_dict_returns_empty(self) -> None:
        """Wildcard on a non-dict (e.g., list) returns empty list."""
        data = {"models": [1, 2, 3]}
        results = extract_json_path_all(data, "models.*.tokens")
        assert results == []

    def test_nested_wildcard(self) -> None:
        """Wildcard at intermediate level with deeper nested access."""
        data = {
            "models": {
                "a": {"usage": {"tokens": {"prompt": 10}}},
                "b": {"usage": {"tokens": {"prompt": 20}}},
            }
        }
        results = extract_json_path_all(
            data, "models.*.usage.tokens.prompt"
        )
        assert sorted(results) == [10, 20]

    def test_consistency_with_extract_json_path_single_model(self) -> None:
        """For single model, first match from extract_json_path equals
        the single element from extract_json_path_all."""
        data = {"models": {"main": {"tokens": 42}}}
        single = extract_json_path(data, "models.*.tokens")
        all_results = extract_json_path_all(data, "models.*.tokens")
        assert single == all_results[0]

    def test_gemini_real_structure(self) -> None:
        """Tests against the real gemini-cli output structure."""
        # Gemini uses multi-model routing: flash-lite for routing,
        # flash or pro for execution. Token counts span both.
        data = {
            "stats": {
                "models": {
                    "gemini-2.5-flash-lite-preview-06-17": {
                        "tokens": {"prompt": 1200, "candidates": 50}
                    },
                    "gemini-2.5-flash-preview-05-20": {
                        "tokens": {"prompt": 8500, "candidates": 4200}
                    },
                }
            }
        }
        input_toks = extract_json_path_all(
            data, "stats.models.*.tokens.prompt"
        )
        output_toks = extract_json_path_all(
            data, "stats.models.*.tokens.candidates"
        )
        assert sum(input_toks) == 1200 + 8500  # 9700
        assert sum(output_toks) == 50 + 4200  # 4250


# =============================================================================
# PluginCliBackend aggregate_tokens integration
# =============================================================================


class TestAggregateTokensInBackend:
    """Test aggregate_tokens flag in PluginCliBackend._parse_output."""

    def _make_backend(
        self,
        *,
        aggregate: bool = False,
        input_tokens_path: str = "usage.input_tokens",
        output_tokens_path: str = "usage.output_tokens",
    ):
        """Create a PluginCliBackend with aggregate_tokens configured."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliErrorConfig,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
            ModelCapacity,
        )
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = InstrumentProfile(
            name="test-instrument",
            display_name="Test Instrument",
            kind="cli",
            models=[
                ModelCapacity(
                    name="test-model",
                    context_window=128000,
                    cost_per_1k_input=0.01,
                    cost_per_1k_output=0.03,
                ),
            ],
            default_model="test-model",
            cli=CliProfile(
                command=CliCommand(
                    executable="echo",
                    prompt_flag="-p",
                ),
                output=CliOutputConfig(
                    format="json",
                    result_path="response",
                    input_tokens_path=input_tokens_path,
                    output_tokens_path=output_tokens_path,
                    aggregate_tokens=aggregate,
                ),
                errors=CliErrorConfig(
                    success_exit_codes=[0],
                ),
            ),
        )
        return PluginCliBackend(profile)

    def test_aggregate_false_returns_first_match(self) -> None:
        """Without aggregate, wildcard returns first match only."""
        backend = self._make_backend(
            aggregate=False,
            input_tokens_path="stats.models.*.tokens.prompt",
            output_tokens_path="stats.models.*.tokens.candidates",
        )
        stdout = json.dumps({
            "stats": {
                "models": {
                    "router": {"tokens": {"prompt": 100, "candidates": 20}},
                    "main": {"tokens": {"prompt": 500, "candidates": 300}},
                }
            }
        })
        result = backend._parse_output(stdout, "", exit_code=0)
        # First match only — depends on dict ordering (router first)
        assert result.input_tokens == 100
        assert result.output_tokens == 20

    def test_aggregate_true_sums_all_matches(self) -> None:
        """With aggregate=True, all wildcard matches are summed."""
        backend = self._make_backend(
            aggregate=True,
            input_tokens_path="stats.models.*.tokens.prompt",
            output_tokens_path="stats.models.*.tokens.candidates",
        )
        stdout = json.dumps({
            "stats": {
                "models": {
                    "router": {"tokens": {"prompt": 100, "candidates": 20}},
                    "main": {"tokens": {"prompt": 500, "candidates": 300}},
                }
            }
        })
        result = backend._parse_output(stdout, "", exit_code=0)
        assert result.input_tokens == 600  # 100 + 500
        assert result.output_tokens == 320  # 20 + 300

    def test_aggregate_true_no_matches_returns_none(self) -> None:
        """With aggregate=True, zero matches returns None (not 0)."""
        backend = self._make_backend(
            aggregate=True,
            input_tokens_path="stats.models.*.tokens.prompt",
            output_tokens_path="stats.models.*.tokens.candidates",
        )
        stdout = json.dumps({"response": "hello"})
        result = backend._parse_output(stdout, "", exit_code=0)
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_aggregate_true_single_model(self) -> None:
        """With aggregate=True and single model, same as single value."""
        backend = self._make_backend(
            aggregate=True,
            input_tokens_path="stats.models.*.tokens.prompt",
            output_tokens_path="stats.models.*.tokens.candidates",
        )
        stdout = json.dumps({
            "stats": {
                "models": {
                    "main": {"tokens": {"prompt": 500, "candidates": 300}},
                }
            }
        })
        result = backend._parse_output(stdout, "", exit_code=0)
        assert result.input_tokens == 500
        assert result.output_tokens == 300

    def test_aggregate_false_with_direct_path(self) -> None:
        """Non-wildcard paths work the same regardless of aggregate flag."""
        backend = self._make_backend(
            aggregate=False,
            input_tokens_path="usage.input_tokens",
            output_tokens_path="usage.output_tokens",
        )
        stdout = json.dumps({
            "usage": {"input_tokens": 150, "output_tokens": 200}
        })
        result = backend._parse_output(stdout, "", exit_code=0)
        assert result.input_tokens == 150
        assert result.output_tokens == 200

    def test_aggregate_true_gemini_real_output(self) -> None:
        """Simulates real gemini-cli multi-model JSON output."""
        backend = self._make_backend(
            aggregate=True,
            input_tokens_path="stats.models.*.tokens.prompt",
            output_tokens_path="stats.models.*.tokens.candidates",
        )
        # Real gemini-cli structure with flash-lite router + flash main
        stdout = json.dumps({
            "response": "Here is the analysis...",
            "stats": {
                "models": {
                    "gemini-2.5-flash-lite-preview-06-17": {
                        "tokens": {"prompt": 1200, "candidates": 50}
                    },
                    "gemini-2.5-flash-preview-05-20": {
                        "tokens": {"prompt": 8500, "candidates": 4200}
                    },
                }
            }
        })
        result = backend._parse_output(stdout, "", exit_code=0)
        assert result.input_tokens == 9700  # 1200 + 8500
        assert result.output_tokens == 4250  # 50 + 4200
        assert result.success is True


# =============================================================================
# CliOutputConfig model tests
# =============================================================================


class TestCliOutputConfigAggregateTokens:
    """Test the aggregate_tokens field on the Pydantic model."""

    def test_default_is_false(self) -> None:
        """aggregate_tokens defaults to False for backward compat."""
        from marianne.core.config.instruments import CliOutputConfig
        config = CliOutputConfig(format="json")
        assert config.aggregate_tokens is False

    def test_can_set_true(self) -> None:
        """aggregate_tokens can be set to True."""
        from marianne.core.config.instruments import CliOutputConfig
        config = CliOutputConfig(format="json", aggregate_tokens=True)
        assert config.aggregate_tokens is True

    def test_survives_serialization(self) -> None:
        """aggregate_tokens round-trips through dict serialization."""
        from marianne.core.config.instruments import CliOutputConfig
        config = CliOutputConfig(format="json", aggregate_tokens=True)
        data = config.model_dump()
        restored = CliOutputConfig.model_validate(data)
        assert restored.aggregate_tokens is True

    def test_loaded_from_yaml_dict(self) -> None:
        """aggregate_tokens loads correctly from YAML-like dict."""
        from marianne.core.config.instruments import CliOutputConfig
        data = {
            "format": "json",
            "aggregate_tokens": True,
            "input_tokens_path": "stats.models.*.tokens.prompt",
            "output_tokens_path": "stats.models.*.tokens.candidates",
        }
        config = CliOutputConfig.model_validate(data)
        assert config.aggregate_tokens is True
        assert config.input_tokens_path == "stats.models.*.tokens.prompt"
