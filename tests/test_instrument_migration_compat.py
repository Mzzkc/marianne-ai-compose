"""Observer tests: instrument migration backward compatibility.

These tests pin the contract that must hold throughout Phases 1-4 of the
pre-instrument execution removal atlas. They are deliberately written to
fail loudly the moment a migration step breaks backward compatibility for
existing scores or for the test-suite infrastructure.

Doctrine rule enforced (from Atlas Doctrine — Rules Ledger):

    RULE: conftest.py defaults must work throughout the migration
    SCOPE: tests/conftest.py, tests/conftest_adversarial.py
    RATIONALE: The shared test default ``backend.type = "claude_cli"`` is
      used across the entire test suite. The ``register_native_instruments()``
      bridge and the open Literal union together ensure this value remains
      valid. Breaking test infrastructure stability would cascade across
      hundreds of tests.

    RULE: Backward compatibility for existing scores is mandatory during
      Phases 1-4
    SCOPE: All score YAML files using ``backend:`` syntax, including 25+
      examples.
    RATIONALE: The instrument-plugin design explicitly chose coexistence —
      ``backend:`` is not deprecated in v1. Scores using ``backend:`` must
      continue to work alongside ``instrument:`` until Phase 4 declares
      the transition complete. The mutual exclusion validator at
      ``core/config/job.py:879`` enforces that ``backend:`` and
      ``instrument:`` cannot coexist in a single score.

    EVIDENCE: core/config/backend.py:290-291 (closed Literal), core/config/
      job.py:864-886 (mutual exclusion validator).

Coverage gaps addressed:
    - G-11: backward-compat of legacy backend.type values during Phase 2
            union opening.
    - G-12: coexistence of instrument: and backend: paths during transition.

Migration phase expectations (for future auditors):
    - TODAY (pre-Phase 2): closed Literal rejects novel strings. The
      "accepts novel strings" test is xfail.
    - AFTER Phase 2: closed Literal opens to ``str``; the xfail test flips
      to pass. The other tests must KEEP passing throughout.
    - AFTER Phase 4: register_native_instruments() bridge resolves all
      four legacy names; the fallback test must still pass.

Integration notes:
    These tests use real imports (``marianne.core.config.*``). No mocks
    are applied to the module under test, per the observer-test protocol.
"""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from marianne.core.config.backend import BackendConfig
from marianne.core.config.job import JobConfig

# Sentinel set of the four legacy backend.type strings that the atlas
# declares MUST remain valid throughout the migration.
LEGACY_TYPES: tuple[str, ...] = (
    "claude_cli",
    "anthropic_api",
    "recursive_light",
    "ollama",
)

# Doctrine rule tag embedded in failure messages so any regressor sees
# the exact constraint they broke.
_RULE_LEGACY = (
    "Atlas Doctrine RULE: conftest.py defaults must work throughout the "
    "migration — legacy backend.type values must remain valid for Phases 1-4."
)
_RULE_COEXIST = (
    "Atlas Doctrine RULE: Backward compatibility for existing scores is "
    "mandatory during Phases 1-4 — backend: and instrument: cannot both be "
    "set on a single score (mutual exclusion validator, job.py:864-886)."
)
_RULE_FALLBACKS = (
    "Atlas Doctrine RULE: register_native_instruments() must bridge legacy "
    "names until all native backends are removed — instrument_fallbacks with "
    "legacy names must resolve."
)


# ---------------------------------------------------------------------------
# Behaviour 1: claude_cli default remains valid (the conftest anchor).
# ---------------------------------------------------------------------------


class TestClaudeCliDefaultSurvives:
    """The shared ``backend.type = "claude_cli"`` conftest default must
    remain a legal value at EVERY phase of the migration, or the entire
    test suite collapses.
    """

    def test_claude_cli_default_constructs(self) -> None:
        """BackendConfig() with no args produces type='claude_cli'."""
        cfg = BackendConfig()
        assert cfg.type == "claude_cli", _RULE_LEGACY

    def test_claude_cli_explicit_constructs(self) -> None:
        """BackendConfig(type='claude_cli') is accepted."""
        cfg = BackendConfig(type="claude_cli")
        assert cfg.type == "claude_cli", _RULE_LEGACY

    def test_claude_cli_does_not_warn(self) -> None:
        """The default value must not emit a deprecation/migration warning.

        A warning on the default would render thousands of test-suite
        constructions noisy and mask real issues.
        """
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            BackendConfig(type="claude_cli")
        # Some unrelated warnings (e.g. third-party) might leak in; filter
        # for ones mentioning "backend" or "instrument" which would be
        # the migration-introduced kind.
        migration_warnings = [
            w for w in captured
            if "backend" in str(w.message).lower()
            or "instrument" in str(w.message).lower()
        ]
        assert migration_warnings == [], (
            f"{_RULE_LEGACY}\nUnexpected migration warnings: "
            f"{[str(w.message) for w in migration_warnings]}"
        )


# ---------------------------------------------------------------------------
# Behaviour 2: all four legacy strings parse, and none of them warn.
# ---------------------------------------------------------------------------


class TestLegacyTypesAcceptedWithoutWarning:
    """Each of the four legacy backend.type values must parse cleanly.

    This contract must hold through Phase 2's union opening: even after
    the Literal becomes ``str``, the four legacy values are canonical
    and must not trigger the "unknown backend" warning.
    """

    @pytest.mark.parametrize("legacy_type", LEGACY_TYPES)
    def test_legacy_type_constructs(self, legacy_type: str) -> None:
        """Each of the four legacy strings is accepted."""
        cfg = BackendConfig(type=legacy_type)
        assert cfg.type == legacy_type, (
            f"{_RULE_LEGACY}\nExpected type={legacy_type!r}, got {cfg.type!r}"
        )

    @pytest.mark.parametrize("legacy_type", LEGACY_TYPES)
    def test_legacy_type_does_not_warn(self, legacy_type: str) -> None:
        """Legacy values must not produce a migration-guidance warning."""
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            BackendConfig(type=legacy_type)
        migration_warnings = [
            w for w in captured
            if "instrument:" in str(w.message)
            or "unknown backend" in str(w.message).lower()
            or "unrecognized" in str(w.message).lower()
        ]
        assert migration_warnings == [], (
            f"{_RULE_LEGACY}\nLegacy type {legacy_type!r} produced migration "
            f"warning: {[str(w.message) for w in migration_warnings]}"
        )


# ---------------------------------------------------------------------------
# Behaviour 3: Novel type strings (Phase 2 promise).
#
# PRE-Phase 2:  closed Literal REJECTS with ValidationError.  Marked xfail.
# POST-Phase 2: open str accepts with a warning pointing at ``instrument:``.
# ---------------------------------------------------------------------------


class TestNovelTypeAcceptedWithWarning:
    """Phase 2 opens the Literal union to arbitrary strings.

    After Phase 2, a novel ``backend.type`` value must parse but should
    emit a non-fatal warning suggesting the user adopt the
    ``instrument:`` path instead. Until Phase 2 lands the closed Literal
    keeps this strict.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Blocked by Phase 2: BackendConfig.type is still a closed "
            "Literal. Atlas Doctrine RULE: BackendConfig.type must accept "
            "arbitrary strings after Phase 2. Flip this test to expected-"
            "pass once the union opens at core/config/backend.py:290-291."
        ),
    )
    def test_novel_type_is_accepted(self) -> None:
        """A made-up backend type string must parse without raising."""
        cfg = BackendConfig(type="custom_cli")
        assert cfg.type == "custom_cli"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Blocked by Phase 2: warning validator that suggests the "
            "instrument: path has not yet been added. Atlas Doctrine RULE: "
            "BackendConfig.type must accept arbitrary strings after Phase 2 "
            "with a warning pointing at instrument:."
        ),
    )
    def test_novel_type_emits_instrument_suggestion(self) -> None:
        """After Phase 2, novel types must nudge users toward instrument:."""
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            BackendConfig(type="custom_cli")
        messages = [str(w.message) for w in captured]
        # At least one warning should guide the user to the instrument path.
        assert any("instrument" in m.lower() for m in messages), (
            f"Expected an 'instrument:' migration hint in warnings, got: "
            f"{messages}"
        )

    def test_novel_type_is_currently_rejected(self) -> None:
        """Today, the closed Literal rejects unknown strings.

        This is the regression guard that trips IF someone opens the
        union WITHOUT adding the warning validator required by Phase 2.
        Once Phase 2 lands fully, this test is expected to be deleted
        (and the two xfails above should flip to passing).
        """
        with pytest.raises(ValidationError):
            BackendConfig(type="custom_cli")


# ---------------------------------------------------------------------------
# Behaviour 4: Mutual-exclusion validator at job.py:864-886 still bites.
# ---------------------------------------------------------------------------


class TestBackendInstrumentMutualExclusion:
    """Mutual exclusion between ``instrument:`` and explicit
    ``backend.type`` must continue to be enforced at the JobConfig layer.

    The validator only fires when backend.type is NON-default
    ("claude_cli" is the default and coexists with instrument:).
    """

    def test_rejects_both_set_non_default(self) -> None:
        """instrument + backend.type=anthropic_api must be rejected."""
        with pytest.raises(ValueError, match="Cannot specify both"):
            JobConfig(
                name="mutual-excl-reject",
                workspace="./workspaces/test",
                instrument="gemini-cli",
                backend={"type": "anthropic_api"},
                sheet={"size": 1, "total_items": 1},
                prompt={"template": "hi"},
            )

    def test_rejects_both_set_recursive_light(self) -> None:
        """Any non-default backend.type value triggers the validator."""
        with pytest.raises(ValueError, match="Cannot specify both"):
            JobConfig(
                name="mutual-excl-rl",
                workspace="./workspaces/test",
                instrument="gemini-cli",
                backend={"type": "recursive_light"},
                sheet={"size": 1, "total_items": 1},
                prompt={"template": "hi"},
            )

    def test_allows_instrument_only(self) -> None:
        """instrument: alone with default backend must succeed."""
        cfg = JobConfig(
            name="instrument-only",
            workspace="./workspaces/test",
            instrument="gemini-cli",
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hi"},
        )
        assert cfg.instrument == "gemini-cli", _RULE_COEXIST
        # backend field always has a default; that is intentional.
        assert cfg.backend.type == "claude_cli"

    def test_allows_backend_only(self) -> None:
        """backend: with explicit non-default and no instrument: succeeds."""
        cfg = JobConfig(
            name="backend-only",
            workspace="./workspaces/test",
            backend={"type": "anthropic_api"},
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hi"},
        )
        assert cfg.instrument is None, _RULE_COEXIST
        assert cfg.backend.type == "anthropic_api", _RULE_COEXIST

    def test_allows_instrument_with_default_backend(self) -> None:
        """instrument: plus the implicit default backend is OK.

        ``backend`` is always present with defaults; the mutual-exclusion
        validator only fires when the user explicitly raises backend.type
        above its default.
        """
        cfg = JobConfig(
            name="inst-plus-default-bk",
            workspace="./workspaces/test",
            instrument="gemini-cli",
            backend={"type": "claude_cli"},
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hi"},
        )
        assert cfg.instrument == "gemini-cli"
        assert cfg.backend.type == "claude_cli"


# ---------------------------------------------------------------------------
# Behaviour 5: instrument_fallbacks: [anthropic_api] still constructs.
# ---------------------------------------------------------------------------


class TestInstrumentFallbacksAcceptsLegacyNames:
    """25+ example scores pass ``instrument_fallbacks: [anthropic_api]``.

    This contract is load-bearing: removing it breaks every example and
    the transition bridge (registry.py:277-304) that resolves legacy
    names to instrument profiles.
    """

    def test_single_legacy_fallback(self) -> None:
        """A single legacy name in the fallback chain is accepted."""
        cfg = JobConfig(
            name="fb-single",
            workspace="./workspaces/test",
            instrument_fallbacks=["anthropic_api"],
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hi"},
        )
        assert cfg.instrument_fallbacks == ["anthropic_api"], _RULE_FALLBACKS

    def test_multiple_legacy_fallbacks(self) -> None:
        """All four legacy names accepted in combination."""
        cfg = JobConfig(
            name="fb-multi",
            workspace="./workspaces/test",
            instrument_fallbacks=list(LEGACY_TYPES),
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hi"},
        )
        assert cfg.instrument_fallbacks == list(LEGACY_TYPES), _RULE_FALLBACKS

    def test_fallbacks_coexist_with_primary_instrument(self) -> None:
        """Primary instrument + legacy-named fallbacks — the common shape
        used by 25+ examples.
        """
        cfg = JobConfig(
            name="fb-with-primary",
            workspace="./workspaces/test",
            instrument="gemini-cli",
            instrument_fallbacks=["anthropic_api", "claude_cli"],
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hi"},
        )
        assert cfg.instrument == "gemini-cli"
        assert cfg.instrument_fallbacks == ["anthropic_api", "claude_cli"], (
            _RULE_FALLBACKS
        )

    def test_fallbacks_default_empty(self) -> None:
        """Omitting instrument_fallbacks yields an empty list (no surprise
        mutation of defaults).
        """
        cfg = JobConfig(
            name="fb-absent",
            workspace="./workspaces/test",
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hi"},
        )
        assert cfg.instrument_fallbacks == [], _RULE_FALLBACKS
