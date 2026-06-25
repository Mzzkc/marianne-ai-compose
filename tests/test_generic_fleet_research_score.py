"""Contracts for the generic fleet technique research score."""

from __future__ import annotations

from pathlib import Path

from marianne.core.config.job import JobConfig

SCORE_PATH = Path("scores/generic-fleet-technique-research.yaml")


def test_research_score_expanded_instruments_match_stage_intent() -> None:
    config = JobConfig.from_yaml(SCORE_PATH)

    assert config.sheet.total_items == 12
    assert config.sheet.fan_out_stage_map is not None
    assert {
        sheet: meta["stage"] for sheet, meta in config.sheet.fan_out_stage_map.items()
    } == {
        1: 1,
        2: 1,
        3: 1,
        4: 1,
        5: 1,
        6: 1,
        7: 1,
        8: 1,
        9: 2,
        10: 3,
        11: 4,
        12: 5,
    }

    assert config.sheet.instrument_map == {
        "gemini-flash": [1, 2, 3, 4, 5, 6, 7, 8],
    }
    assert config.sheet.per_sheet_instruments == {}
    assert config.sheet.per_sheet_fallbacks == {
        **{sheet: ["claude-code-glm52"] for sheet in range(1, 9)},
        **{sheet: ["gemini-flash"] for sheet in range(9, 13)},
    }

    assert set(config.sheet.descriptions) == set(range(1, 13))
