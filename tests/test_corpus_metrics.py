"""Volume is a measurable condition, not an assertion of training readiness."""

from marianne.corpus.metrics import corpus_stats
from marianne.corpus.schema import TrainingExample


def example(kind="in_repo", category=None, rationale=None):
    return TrainingExample(
        instruction="Compose",
        output="name: sample\n",
        rationale=rationale,
        provenance={"source_path": "score.yaml", "source_kind": kind, "category": category},
    )


def test_counts_source_category_and_annotations():
    stats = corpus_stats(
        [
            example(category="creative", rationale="A human reason"),
            example("telemetry"),
            example(category="engineering"),
        ]
    )
    assert stats.total_examples == 3
    assert stats.by_source_kind == {"in_repo": 2, "telemetry": 1}
    assert stats.by_category == {"creative": 1, "engineering": 1}
    assert stats.annotated == 1
    assert stats.target_volume == 200
    assert not stats.meets_target


def test_target_boundary_and_empty_corpus():
    assert not corpus_stats([example()] * 199).meets_target
    assert corpus_stats([example()] * 200).meets_target
    assert corpus_stats([]).total_examples == 0
    assert corpus_stats([]).by_source_kind == {}
