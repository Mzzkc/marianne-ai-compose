"""#200: wire learning-store pattern injection into the baton dispatch path.

Sibling of #207 (failure-history): the renderer accepts ``patterns:
list[str]`` but the baton's render call never passes them, so learned
patterns from the ``GlobalLearningStore`` never reach the Musician. The
producer (``get_patterns``) and consumer (renderer) both exist; this wires
them.

Per the 4-model lab (unanimous design):
- ``BatonAdapter.__init__`` gains an optional ``learning_store`` handle
  (None-default keeps store-less paths / existing tests working), threaded
  from ``self._learning_hub.store`` at ``manager.py:467``.
- A static ``_pattern_to_str`` converts a ``PatternRecord`` to a compact
  prompt string with an effectiveness marker (✓ high / ○ moderate /
  ⚠ low — matching ``_format_patterns_section``'s legend); empty-description
  records are skipped.
- An async ``_build_learned_patterns(instrument_name)`` runs the SYNC sqlite
  ``get_patterns`` off the event loop (``asyncio.to_thread`` — the #243
  contract), filtered to the executing instrument + universal patterns,
  excluding quarantined ones. Returns None when there's no handle or nothing
  relevant so the renderer cleanly skips the layer (matching #207).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from marianne.daemon.baton.adapter import BatonAdapter
from marianne.learning.store import GlobalLearningStore
from marianne.learning.store.models import PatternRecord, QuarantineStatus


def _record(
    *,
    name: str = "p",
    description: str | None = "desc",
    effectiveness: float = 0.5,
    suggested_action: str | None = None,
) -> PatternRecord:
    now = datetime.now()
    return PatternRecord(
        id="id-" + name,
        pattern_type="validation_failure",
        pattern_name=name,
        description=description,
        occurrence_count=1,
        first_seen=now,
        last_seen=now,
        last_confirmed=now,
        led_to_success_count=0,
        led_to_failure_count=0,
        effectiveness_score=effectiveness,
        variance=0.0,
        suggested_action=suggested_action,
        context_tags=[],
        priority_score=0.5,
    )


# ── _pattern_to_str: PatternRecord → compact prompt string ────────────────


class TestPatternToStr:
    def test_high_effectiveness_marker(self) -> None:
        s = BatonAdapter._pattern_to_str(_record(effectiveness=0.85))
        assert s is not None
        assert s.startswith("[✓]")

    def test_moderate_effectiveness_marker(self) -> None:
        s = BatonAdapter._pattern_to_str(_record(effectiveness=0.5))
        assert s is not None
        assert s.startswith("[○]")

    def test_low_effectiveness_marker(self) -> None:
        s = BatonAdapter._pattern_to_str(_record(effectiveness=0.2))
        assert s is not None
        assert s.startswith("[⚠]")

    def test_includes_name_and_description(self) -> None:
        s = BatonAdapter._pattern_to_str(
            _record(name="retry-on-429", description="back off and retry")
        )
        assert s is not None
        assert "retry-on-429" in s
        assert "back off and retry" in s

    def test_includes_suggested_action_when_present(self) -> None:
        s = BatonAdapter._pattern_to_str(
            _record(suggested_action="add a sleep")
        )
        assert s is not None
        assert "→ add a sleep" in s

    def test_omits_action_when_absent(self) -> None:
        s = BatonAdapter._pattern_to_str(_record(suggested_action=None))
        assert s is not None
        assert "→" not in s

    def test_empty_description_returns_none(self) -> None:
        assert BatonAdapter._pattern_to_str(_record(description=None)) is None
        assert BatonAdapter._pattern_to_str(_record(description="")) is None


# ── _build_learned_patterns: off-loop store query → prompt strings ────────


class TestBuildLearnedPatterns:
    @pytest.mark.asyncio
    async def test_no_store_handle_returns_none(self) -> None:
        adapter = BatonAdapter()  # no learning_store
        assert await adapter._build_learned_patterns("claude-code") is None

    @pytest.mark.asyncio
    async def test_empty_store_returns_none(self, tmp_path) -> None:
        store = GlobalLearningStore(tmp_path / "learning.db")
        adapter = BatonAdapter(learning_store=store)
        assert await adapter._build_learned_patterns("claude-code") is None
        store.close()

    @pytest.mark.asyncio
    async def test_returns_strings_for_matching_instrument(
        self, tmp_path
    ) -> None:
        store = GlobalLearningStore(tmp_path / "learning.db")
        store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="prefer-explicit-paths",
            description="use absolute paths in commands",
            suggested_action="qualify every path",
            instrument_name="claude-code",
        )
        adapter = BatonAdapter(learning_store=store)
        result = await adapter._build_learned_patterns("claude-code")
        assert result is not None
        assert len(result) == 1
        assert "prefer-explicit-paths" in result[0]
        assert "use absolute paths in commands" in result[0]
        store.close()

    @pytest.mark.asyncio
    async def test_instrument_specific_pattern_not_returned_for_other(
        self, tmp_path
    ) -> None:
        store = GlobalLearningStore(tmp_path / "learning.db")
        store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="goose-only-tip",
            description="goose-specific guidance",
            instrument_name="goose",
        )
        adapter = BatonAdapter(learning_store=store)
        # include_universal=True is harmless here: this pattern is scoped to
        # 'goose', not universal (NULL), so it must not surface for 'claude-code'.
        assert await adapter._build_learned_patterns("claude-code") is None
        store.close()

    @pytest.mark.asyncio
    async def test_universal_pattern_returned_for_any_instrument(
        self, tmp_path
    ) -> None:
        store = GlobalLearningStore(tmp_path / "learning.db")
        store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="universal-tip",
            description="applies everywhere",
            instrument_name=None,  # universal
        )
        adapter = BatonAdapter(learning_store=store)
        result = await adapter._build_learned_patterns("claude-code")
        assert result is not None
        assert any("universal-tip" in s for s in result)
        store.close()

    @pytest.mark.asyncio
    async def test_quarantined_pattern_excluded(self, tmp_path) -> None:
        store = GlobalLearningStore(tmp_path / "learning.db")
        pid = store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="sketchy-pattern",
            description="not yet trusted",
            instrument_name="claude-code",
        )
        store.update_quarantine_status(pid, QuarantineStatus.QUARANTINED)
        adapter = BatonAdapter(learning_store=store)
        assert await adapter._build_learned_patterns("claude-code") is None
        store.close()
