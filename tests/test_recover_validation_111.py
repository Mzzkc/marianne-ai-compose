"""#111: `mzt recover` must validate state through CheckpointState before writing.

The daemon DB is the (de facto, and increasingly the sole) source of truth for
job state. `recover` hand-mutates the checkpoint dict and wrote it back with raw
`json.dumps` + sqlite3 — bypassing `CheckpointState` validation. A malformed
mutation would silently poison the registry and crash the conductor on the next
resume (`model_validate`). `_validated_checkpoint_json` validates first and
re-serializes canonically, so recover can never write state the conductor will
fail to load.
"""

from __future__ import annotations

import json

import pytest

from marianne.cli.commands.recover import _validated_checkpoint_json
from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus


class TestValidatedCheckpointJson:
    def test_valid_checkpoint_roundtrips(self) -> None:
        state = CheckpointState(job_id="j1", job_name="t", total_sheets=1)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.PENDING)
        out = _validated_checkpoint_json(state.model_dump(mode="json"))
        # Output is canonical and re-loadable as a CheckpointState.
        restored = CheckpointState.model_validate(json.loads(out))
        assert restored.job_id == "j1"
        assert restored.sheets[1].status == SheetStatus.PENDING

    def test_invalid_checkpoint_raises(self) -> None:
        # Missing the required job_name → must be refused, not written.
        with pytest.raises(ValueError):
            _validated_checkpoint_json({"job_id": "j1"})

    def test_bogus_sheet_status_raises(self) -> None:
        state = CheckpointState(job_id="j1", job_name="t", total_sheets=1)
        bad = state.model_dump(mode="json")
        bad["sheets"] = {"1": {"sheet_num": 1, "status": "not_a_real_status"}}
        with pytest.raises(ValueError):
            _validated_checkpoint_json(bad)

    def test_canonicalizes_unknown_keys_out(self) -> None:
        # A stray legacy key must not survive into the written state.
        state = CheckpointState(job_id="j1", job_name="t", total_sheets=1)
        d = state.model_dump(mode="json")
        d["some_legacy_field"] = "x"
        out = _validated_checkpoint_json(d)
        assert "some_legacy_field" not in json.loads(out)
