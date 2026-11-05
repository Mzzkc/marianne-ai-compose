"""`sheet.size` defaults to 1 (GH #494 follow-up).

A newcomer's first score names `total_items` and nothing else; 59 of 60
shipped examples use `size: 1`, and `fan_out`/`voices` require it. Making
the conventional value the default removes a "Field required" wall from the
first `mzt validate` without changing any score that sets it.
"""

from __future__ import annotations

from marianne.core.config import JobConfig


def _config(sheet: dict[str, int]) -> JobConfig:
    return JobConfig.model_validate(
        {"name": "first", "workspace": "./ws", "sheet": sheet, "prompt": {"template": "x"}}
    )


def test_total_items_alone_means_one_item_per_sheet() -> None:
    config = _config({"total_items": 3})
    assert config.sheet.size == 1
    assert config.sheet.total_sheets == 3


def test_an_explicit_size_still_batches() -> None:
    config = _config({"total_items": 10, "size": 5})
    assert config.sheet.size == 5
    assert config.sheet.total_sheets == 2
