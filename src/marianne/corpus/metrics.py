"""Volume and category counts for the composition corpus's collection stage."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from marianne.corpus.schema import TrainingExample


class CorpusStats(BaseModel):
    """Volume sufficiency alone does not establish readiness for training."""

    TARGET_VOLUME: ClassVar[int] = 200

    model_config = ConfigDict(extra="forbid")

    total_examples: int
    by_source_kind: dict[str, int]
    by_category: dict[str, int]
    annotated: int
    target_volume: int = TARGET_VOLUME
    meets_target: bool


def corpus_stats(examples: Iterable[TrainingExample]) -> CorpusStats:
    """Count supplied examples; callers deduplicate before measuring volume."""
    records = list(examples)
    return CorpusStats(
        total_examples=len(records),
        by_source_kind=dict(sorted(Counter(ex.provenance.source_kind for ex in records).items())),
        by_category=dict(
            sorted(
                Counter(
                    ex.provenance.category for ex in records if ex.provenance.category is not None
                ).items()
            )
        ),
        annotated=sum(ex.rationale is not None for ex in records),
        meets_target=len(records) >= CorpusStats.TARGET_VOLUME,
    )
