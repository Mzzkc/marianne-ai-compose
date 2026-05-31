"""#204: wire spec-corpus injection into the baton dispatch path.

Third sibling of #207 (failure-history) and #200 (learned-patterns): the
renderer accepts `spec_fragments: list[SpecFragment]` but the baton never
passes them. Unlike the first two, the producer (`SpecCorpusLoader.load`) is
ALSO never called — so #204 has a LOAD step and an INJECT step.

Per the 4-model lab (convergent design, divergences resolved against the code):
- LOAD: manager-side, off the event loop (`asyncio.to_thread`), once per job,
  before the synchronous `register_job`/`recover_job`. A testable static helper
  `JobManager._load_spec_corpus(spec, workspace)` resolves `spec_dir` against
  the job WORKSPACE (the same base `build_sheets` uses for every sheet —
  divergence-2, 3-of-4 + consistency), bundles the optional CLAUDE.md read into
  the same thread hop, and returns a populated frozen `SpecCorpusConfig` copy
  (or None when `spec_dir` is empty). A configured-but-missing dir raises
  `SpecCorpusError` (caller fails the job loudly — correctness > reliability).
- INJECT: the adapter stores the populated spec + `spec_tags` per job (mirroring
  `_job_cross_sheet`), and a PURE `_build_spec_fragments(job_id, sheet_num)`
  helper (mirroring `_build_failure_history`) filters by per-sheet tags. An
  untagged sheet gets ALL fragments — the documented contract (job.py:237-240,
  divergence-1, 3-of-4 + the spec). Empty → None so the renderer no-ops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.core.config.spec import SpecCorpusConfig, SpecFragment
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.manager import JobManager
from marianne.spec.loader import SpecCorpusError


def _sheet(num: int = 1, instrument: str = "claude-code") -> Sheet:
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=Path("/tmp/test-ws"),
        instrument_name=instrument,
        prompt_template="test",
        timeout_seconds=60.0,
    )


# ── JobManager._load_spec_corpus: off-loop load + resolve + policy ────────


class TestLoadSpecCorpus:
    @pytest.mark.asyncio
    async def test_empty_spec_dir_returns_none(self, tmp_path: Path) -> None:
        spec = SpecCorpusConfig(spec_dir="")
        assert await JobManager._load_spec_corpus(spec, tmp_path) is None

    @pytest.mark.asyncio
    async def test_loads_tagged_fragments_from_dir(self, tmp_path: Path) -> None:
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "goals.yaml").write_text(
            "name: goals\ncontent: Build it right.\ntags: [goals]\n"
        )
        (spec_dir / "safety.md").write_text("# Safety\nBe careful.")
        spec = SpecCorpusConfig(spec_dir="spec")
        result = await JobManager._load_spec_corpus(spec, tmp_path)
        assert result is not None
        names = {f.name for f in result.fragments}
        assert names == {"goals", "safety"}
        goals = next(f for f in result.fragments if f.name == "goals")
        assert goals.tags == ["goals"]

    @pytest.mark.asyncio
    async def test_include_claude_md_present(self, tmp_path: Path) -> None:
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "x.md").write_text("body")
        (tmp_path / "CLAUDE.md").write_text("# Instructions\nDo good work.")
        spec = SpecCorpusConfig(spec_dir="spec", include_claude_md=True)
        result = await JobManager._load_spec_corpus(spec, tmp_path)
        assert result is not None
        assert any(f.name == "claude_md" for f in result.fragments)

    @pytest.mark.asyncio
    async def test_include_claude_md_absent_is_silent(self, tmp_path: Path) -> None:
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "x.md").write_text("body")
        spec = SpecCorpusConfig(spec_dir="spec", include_claude_md=True)
        result = await JobManager._load_spec_corpus(spec, tmp_path)
        assert result is not None
        assert all(f.name != "claude_md" for f in result.fragments)

    @pytest.mark.asyncio
    async def test_missing_dir_raises(self, tmp_path: Path) -> None:
        spec = SpecCorpusConfig(spec_dir="nonexistent")
        with pytest.raises(SpecCorpusError):
            await JobManager._load_spec_corpus(spec, tmp_path)

    @pytest.mark.asyncio
    async def test_absolute_spec_dir_overrides_workspace(
        self, tmp_path: Path
    ) -> None:
        # An absolute spec_dir must override the workspace base.
        real = tmp_path / "elsewhere"
        real.mkdir()
        (real / "a.md").write_text("x")
        spec = SpecCorpusConfig(spec_dir=str(real))
        result = await JobManager._load_spec_corpus(spec, tmp_path / "ws")
        assert result is not None
        assert {f.name for f in result.fragments} == {"a"}


# ── adapter._build_spec_fragments: pure per-sheet tag filter ──────────────


def _spec(*frags: SpecFragment) -> SpecCorpusConfig:
    return SpecCorpusConfig(fragments=list(frags))


class TestBuildSpecFragments:
    def test_unknown_job_returns_none(self) -> None:
        adapter = BatonAdapter()
        assert adapter._build_spec_fragments("nope", 1) is None

    def test_tagged_sheet_filters(self) -> None:
        adapter = BatonAdapter()
        adapter._job_spec_config["j"] = _spec(
            SpecFragment(name="a", content="a", tags=["goals"]),
            SpecFragment(name="b", content="b", tags=["safety"]),
        )
        adapter._job_spec_tags["j"] = {1: ["goals"]}
        result = adapter._build_spec_fragments("j", 1)
        assert result is not None
        assert [f.name for f in result] == ["a"]

    def test_untagged_sheet_gets_all(self) -> None:
        # Documented contract (job.py:237-240): no spec_tags entry → all.
        adapter = BatonAdapter()
        adapter._job_spec_config["j"] = _spec(
            SpecFragment(name="a", content="a", tags=["goals"]),
            SpecFragment(name="b", content="b", tags=["safety"]),
        )
        adapter._job_spec_tags["j"] = {1: ["goals"]}  # sheet 2 has no entry
        result = adapter._build_spec_fragments("j", 2)
        assert result is not None
        assert {f.name for f in result} == {"a", "b"}

    def test_empty_filter_result_returns_none(self) -> None:
        adapter = BatonAdapter()
        adapter._job_spec_config["j"] = _spec(
            SpecFragment(name="a", content="a", tags=["goals"]),
        )
        adapter._job_spec_tags["j"] = {1: ["nonexistent"]}
        assert adapter._build_spec_fragments("j", 1) is None


# ── register_job / recover_job store spec; deregister cleans up ───────────


class TestRegisterAndRecoverStoreSpec:
    def test_register_job_stores_spec(self) -> None:
        adapter = BatonAdapter()
        spec = _spec(SpecFragment(name="a", content="a", tags=["goals"]))
        adapter.register_job(
            "j", [_sheet(1)], {}, spec_config=spec, spec_tags={1: ["goals"]}
        )
        assert adapter._job_spec_config.get("j") is spec
        assert adapter._job_spec_tags.get("j") == {1: ["goals"]}

    def test_register_job_without_spec_stores_nothing(self) -> None:
        adapter = BatonAdapter()
        adapter.register_job("j", [_sheet(1)], {})
        assert "j" not in adapter._job_spec_config
        assert "j" not in adapter._job_spec_tags

    def test_empty_fragments_not_stored(self) -> None:
        adapter = BatonAdapter()
        adapter.register_job("j", [_sheet(1)], {}, spec_config=_spec())
        assert "j" not in adapter._job_spec_config

    def test_deregister_cleans_up_spec(self) -> None:
        adapter = BatonAdapter()
        spec = _spec(SpecFragment(name="a", content="a", tags=["goals"]))
        adapter.register_job(
            "j", [_sheet(1)], {}, spec_config=spec, spec_tags={1: ["goals"]}
        )
        adapter.deregister_job("j")
        assert "j" not in adapter._job_spec_config
        assert "j" not in adapter._job_spec_tags

    def test_recover_job_stores_spec(self) -> None:
        adapter = BatonAdapter()
        spec = _spec(SpecFragment(name="a", content="a", tags=["goals"]))
        checkpoint = CheckpointState(job_id="j", job_name="j", total_sheets=1)
        adapter.recover_job(
            "j",
            [_sheet(1)],
            {},
            checkpoint,
            spec_config=spec,
            spec_tags={1: ["goals"]},
        )
        assert adapter._job_spec_config.get("j") is spec
        assert adapter._job_spec_tags.get("j") == {1: ["goals"]}
