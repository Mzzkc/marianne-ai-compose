"""Versioned composition examples retain source and annotation boundaries."""

import pytest
from pydantic import ValidationError

from marianne.corpus.schema import CORPUS_SCHEMA_VERSION, CorpusProvenance, TrainingExample


def test_record_round_trips_without_inventing_rationale():
    record = TrainingExample(
        instruction="Compose a score",
        output="name: sample\n",
        provenance=CorpusProvenance(source_path="examples/sample.yaml", source_kind="in_repo"),
    )
    assert TrainingExample.model_validate_json(record.model_dump_json()) == record
    assert record.rationale is None
    assert record.input is None
    assert record.provenance.schema_version == CORPUS_SCHEMA_VERSION == 1


@pytest.mark.parametrize("extra", [{"surprise": True}, {"schema_version": 2}])
def test_provenance_rejects_unknown_fields_and_versions(extra):
    with pytest.raises(ValidationError):
        CorpusProvenance(source_path="sample.yaml", source_kind="in_repo", **extra)


def test_training_example_requires_provenance_and_rejects_extras():
    with pytest.raises(ValidationError):
        TrainingExample(instruction="Compose", output="name: score")
    with pytest.raises(ValidationError):
        TrainingExample(
            instruction="Compose",
            output="name: score",
            surprise=True,
            provenance={"source_path": "score.yaml", "source_kind": "in_repo"},
        )
