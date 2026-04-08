"""Tests for marianne.utils.json_path — lightweight JSON dot-path extractor.

Used by the instrument plugin system to extract result text, error messages,
and token counts from CLI instrument output (JSON/JSONL formats).

Supported syntax:
- key              — top-level key
- key.subkey       — nested access
- key[0]           — array index
- key.*            — wildcard: iterate all values, return first match
- key.*.subkey     — wildcard with nested access
"""

from __future__ import annotations

import pytest


class TestExtractJsonPath:
    """Tests for extract_json_path utility."""

    def test_top_level_key(self):
        """Extract a top-level key."""
        from marianne.utils.json_path import extract_json_path

        data = {"result": "Hello, world!"}
        assert extract_json_path(data, "result") == "Hello, world!"

    def test_nested_key(self):
        """Extract a nested key via dot notation."""
        from marianne.utils.json_path import extract_json_path

        data = {"error": {"message": "Rate limited", "code": 429}}
        assert extract_json_path(data, "error.message") == "Rate limited"
        assert extract_json_path(data, "error.code") == 429

    def test_array_index(self):
        """Extract array element by index."""
        from marianne.utils.json_path import extract_json_path

        data = {"items": ["a", "b", "c"]}
        assert extract_json_path(data, "items[0]") == "a"
        assert extract_json_path(data, "items[2]") == "c"

    def test_wildcard_returns_first_match(self):
        """Wildcard iterates all values, returns first match."""
        from marianne.utils.json_path import extract_json_path

        data = {"models": {"gpt-4": {"tokens": 100}, "gpt-3": {"tokens": 50}}}
        result = extract_json_path(data, "models.*.tokens")
        # Should return first match (dict ordering preserved in Python 3.7+)
        assert result in (100, 50)

    def test_wildcard_with_nested(self):
        """Wildcard with nested access after the wildcard."""
        from marianne.utils.json_path import extract_json_path

        data = {
            "stats": {
                "models": {
                    "main": {"tokens": {"prompt": 150, "candidates": 200}},
                }
            }
        }
        result = extract_json_path(data, "stats.models.*.tokens.prompt")
        assert result == 150

    def test_missing_key_returns_none(self):
        """Missing keys return None, not raise."""
        from marianne.utils.json_path import extract_json_path

        data = {"result": "ok"}
        assert extract_json_path(data, "missing") is None
        assert extract_json_path(data, "result.nested") is None

    def test_missing_nested_returns_none(self):
        """Missing nested keys in deep paths return None."""
        from marianne.utils.json_path import extract_json_path

        data = {"a": {"b": {"c": 1}}}
        assert extract_json_path(data, "a.b.d") is None
        assert extract_json_path(data, "a.x.c") is None

    def test_array_index_out_of_bounds_returns_none(self):
        """Out-of-bounds array index returns None."""
        from marianne.utils.json_path import extract_json_path

        data = {"items": ["a"]}
        assert extract_json_path(data, "items[5]") is None

    def test_empty_path(self):
        """Empty path returns None."""
        from marianne.utils.json_path import extract_json_path

        data = {"key": "value"}
        assert extract_json_path(data, "") is None

    def test_deeply_nested(self):
        """Works with deeply nested structures."""
        from marianne.utils.json_path import extract_json_path

        data = {"a": {"b": {"c": {"d": {"e": 42}}}}}
        assert extract_json_path(data, "a.b.c.d.e") == 42

    def test_none_data_returns_none(self):
        """None input data returns None."""
        from marianne.utils.json_path import extract_json_path

        assert extract_json_path(None, "key") is None  # type: ignore[arg-type]

    def test_non_dict_data_returns_none(self):
        """Non-dict data returns None for dot-path access."""
        from marianne.utils.json_path import extract_json_path

        assert extract_json_path("string", "key") is None  # type: ignore[arg-type]
        assert extract_json_path(42, "key") is None  # type: ignore[arg-type]

    @pytest.mark.adversarial
    def test_wildcard_on_non_dict_returns_none(self):
        """Wildcard on a non-dict value returns None."""
        from marianne.utils.json_path import extract_json_path

        data = {"items": [1, 2, 3]}
        assert extract_json_path(data, "items.*.value") is None

    @pytest.mark.adversarial
    def test_array_index_on_non_list_returns_none(self):
        """Array index on a non-list value returns None."""
        from marianne.utils.json_path import extract_json_path

        data = {"key": "value"}
        assert extract_json_path(data, "key[0]") is None

    @pytest.mark.adversarial
    def test_wildcard_all_returns_all_matches(self):
        """extract_json_path_all returns all wildcard matches."""
        from marianne.utils.json_path import extract_json_path_all

        data = {"models": {"a": {"tokens": 100}, "b": {"tokens": 200}}}
        results = extract_json_path_all(data, "models.*.tokens")
        assert set(results) == {100, 200}
