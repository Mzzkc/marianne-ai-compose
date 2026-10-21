"""Alias chains with `select` modifiers (GH #473).

An alias may bind an ordered chain of entries; `select` modifiers decide which
entry each sheet starts on, and the rest follow as that sheet's fallbacks.
Build-time modifiers (cycle, spread, cheapest, widest) are pinned here through
build_sheets; `balance` is pinned through the real dispatcher.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import hypothesis.strategies as st
import pytest
from hypothesis import given
from pydantic import ValidationError

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.core.config.job import AliasEntry, InstrumentDef, JobConfig
from marianne.core.sheet import build_sheets, order_alias_entries
from marianne.daemon.baton.adapter import _attempt_entry_config
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.state import SheetExecutionState

REVIEWER = {
    "chain": [
        {"profile": "codex-cli", "config": {"model": "gpt-6-astra"}},
        {"profile": "claude-code", "config": {"model": "claude-opus-5-5"}},
        "opencode",
    ],
}


def _config(tmp_path: Path, **fields: Any) -> JobConfig:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    base: dict[str, Any] = {
        "name": "alias-select",
        "workspace": str(ws),
        "instrument": "claude-code",
        "sheet": {"size": 1, "total_items": 3},
        "prompt": {"template": "work {{ sheet_num }}"},
    }
    base.update(fields)
    return JobConfig.model_validate(base)


def _profile(
    name: str, models: list[tuple[str, float, int]], default_model: str | None = None,
) -> InstrumentProfile:
    return InstrumentProfile(
        name=name, display_name=name, kind="cli", default_model=default_model,
        models=[
            ModelCapacity(name=m, context_window=ctx, cost_per_1k_input=cost,
                          cost_per_1k_output=cost * 5, max_output_tokens=8000)
            for m, cost, ctx in models
        ],
        cli=CliProfile(
            command=CliCommand(executable="x", prompt_flag="-p"),
            output=CliOutputConfig(format="text"),
        ),
    )


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_single_profile_unchanged(self) -> None:
        d = InstrumentDef(profile="claude-code", config={"model": "x"})
        assert d.profile == "claude-code" and d.chain is None
        assert [e.profile for e in d.entries] == ["claude-code"]
        assert d.entries[0].config.get("model") == "x"

    def test_chain_derives_profile_from_first_entry(self) -> None:
        d = InstrumentDef.model_validate(REVIEWER)
        assert d.profile == "codex-cli"
        assert [e.profile for e in d.entries] == ["codex-cli", "claude-code", "opencode"]

    def test_select_without_chain_is_refused(self) -> None:
        with pytest.raises(ValueError, match="need a `chain`"):
            InstrumentDef(profile="claude-code", select=["cycle"])

    def test_neither_profile_nor_chain_is_refused(self) -> None:
        with pytest.raises(ValueError, match="needs `profile` or `chain`"):
            InstrumentDef(config={"model": "x"})  # type: ignore[call-arg]

    def test_duplicate_entries_and_conflicting_modifiers_are_refused(self) -> None:
        with pytest.raises(ValueError, match="same entry twice"):
            InstrumentDef(chain=[AliasEntry(profile="a"), AliasEntry(profile="a")])
        with pytest.raises(ValueError, match="cycle and spread"):
            InstrumentDef.model_validate({**REVIEWER, "select": ["cycle", "spread"]})
        with pytest.raises(ValueError, match="cheapest and widest"):
            InstrumentDef.model_validate({**REVIEWER, "select": ["cheapest", "widest"]})


# ---------------------------------------------------------------------------
# build-time modifiers
# ---------------------------------------------------------------------------


class TestBuildTime:
    def test_chain_without_select_is_primary_plus_fallbacks(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path, instrument="reviewer", instruments={"reviewer": REVIEWER})
        for sheet in build_sheets(cfg):
            assert sheet.instrument_name == "codex-cli"
            assert sheet.instrument_config["model"] == "gpt-6-astra"
            assert sheet.instrument_fallbacks == ["claude-code", "opencode"]
            assert sheet.instrument_fallback_configs == [{"model": "claude-opus-5-5"}, {}]
            assert sheet.balance_chain is False

    def test_cycle_rotates_per_use_in_sheet_order(self, tmp_path: Path) -> None:
        cfg = _config(
            tmp_path, instrument="reviewer",
            instruments={"reviewer": {**REVIEWER, "select": ["cycle"]}},
            sheet={"size": 1, "total_items": 4},
        )
        sheets = build_sheets(cfg)
        assert [s.instrument_name for s in sheets] == [
            "codex-cli", "claude-code", "opencode", "codex-cli",
        ]
        # The rest of the chain follows the rotated primary, in rotated order.
        assert sheets[1].instrument_fallbacks == ["opencode", "codex-cli"]
        assert sheets[1].instrument_fallback_configs == [{}, {"model": "gpt-6-astra"}]
        assert sheets[1].instrument_config["model"] == "claude-opus-5-5"

    def test_spread_restarts_at_each_movement(self, tmp_path: Path) -> None:
        # Movement 1: one sheet. Movement 2: three voices. Movement 3: two.
        cfg = _config(
            tmp_path, instrument="reviewer",
            instruments={"reviewer": {**REVIEWER, "select": ["spread"]}},
            sheet={"size": 1, "total_items": 3, "fan_out": {2: 3, 3: 2}},
        )
        names = [s.instrument_name for s in build_sheets(cfg)]
        assert names == [
            "codex-cli",                             # movement 1, voice 1
            "codex-cli", "claude-code", "opencode",  # movement 2, three distinct
            "codex-cli", "claude-code",              # movement 3 starts over
        ]

    def test_cycle_counts_only_sheets_that_use_the_alias(self, tmp_path: Path) -> None:
        cfg = _config(
            tmp_path, instrument="claude-code",
            instruments={"reviewer": {**REVIEWER, "select": ["cycle"]}},
            sheet={
                "size": 1, "total_items": 4,
                "per_sheet_instruments": {2: "reviewer", 4: "reviewer"},
            },
        )
        sheets = build_sheets(cfg)
        assert sheets[1].instrument_name == "codex-cli"
        assert sheets[3].instrument_name == "claude-code"

    def test_cheapest_and_widest_order_by_the_profile_table(self) -> None:
        profiles = {
            "codex-cli": _profile("codex-cli", [("gpt-6-astra", 0.01, 272_000)]),
            "claude-code": _profile("claude-code", [("claude-opus-5-5", 0.005, 1_000_000)]),
            "opencode": _profile("opencode", []),  # no row: keeps authored place, after known
        }
        def _order(select: list[str]) -> list[str]:
            d = InstrumentDef.model_validate({**REVIEWER, "select": select})
            ordered = order_alias_entries(
                d, use_index=0, movement_use_index=0, profiles=profiles,
            )
            return [e.profile for e in ordered]

        assert _order(["cheapest"]) == ["claude-code", "codex-cli", "opencode"]
        assert _order(["widest"]) == ["claude-code", "codex-cli", "opencode"]

    def test_modifiers_compose_in_written_order(self) -> None:
        profiles = {
            "codex-cli": _profile("codex-cli", [("gpt-6-astra", 0.01, 272_000)]),
            "claude-code": _profile("claude-code", [("claude-opus-5-5", 0.005, 1_000_000)]),
            "opencode": _profile("opencode", [("x", 0.0, 100)], default_model="x"),
        }
        d = InstrumentDef.model_validate({**REVIEWER, "select": ["cheapest", "cycle"]})
        # cheapest → [opencode 0.0, claude-code 0.005, codex-cli 0.01]; cycle use 1
        # rotates that by one → [claude-code, codex-cli, opencode].
        names = [
            e.profile
            for e in order_alias_entries(d, use_index=1, movement_use_index=0, profiles=profiles)
        ]
        assert names == ["claude-code", "codex-cli", "opencode"], names
        # Written the other way, cycle rotates the AUTHORED order and cheapest then
        # re-sorts it, so the rotation is lost: the order written is the contract.
        d2 = InstrumentDef.model_validate({**REVIEWER, "select": ["cycle", "cheapest"]})
        names2 = [
            e.profile
            for e in order_alias_entries(d2, use_index=1, movement_use_index=0, profiles=profiles)
        ]
        assert names2 == ["opencode", "claude-code", "codex-cli"]

    def test_balance_sets_the_sheet_flag_and_keeps_authored_order(self, tmp_path: Path) -> None:
        cfg = _config(
            tmp_path, instrument="reviewer",
            instruments={"reviewer": {**REVIEWER, "select": ["balance"]}},
        )
        sheet = build_sheets(cfg)[0]
        assert sheet.balance_chain is True
        assert sheet.instrument_name == "codex-cli"

    def test_chain_tail_entries_inherit_the_alias_level_config(self, tmp_path: Path) -> None:
        cfg = _config(
            tmp_path, instrument="reviewer",
            instruments={"reviewer": {
                **REVIEWER,
                "config": {"response_format": {"type": "json_object"}},
            }},
        )
        sheet = build_sheets(cfg)[0]
        assert sheet.instrument_config["response_format"] == {"type": "json_object"}
        for fb_config in sheet.instrument_fallback_configs:
            assert fb_config["response_format"] == {"type": "json_object"}
        assert sheet.instrument_fallback_configs[0]["model"] == "claude-opus-5-5"

    def test_alias_chain_in_fallback_list_contributes_every_entry(self, tmp_path: Path) -> None:
        cfg = _config(
            tmp_path, instrument="claude-code", instrument_fallbacks=["reviewer"],
            instruments={"reviewer": REVIEWER},
        )
        sheet = build_sheets(cfg)[0]
        assert sheet.instrument_fallbacks == ["codex-cli", "claude-code", "opencode"]


# ---------------------------------------------------------------------------
# dispatch-time balance
# ---------------------------------------------------------------------------


class TestBalanceAtDispatch:
    def _state(self) -> SheetExecutionState:
        return SheetExecutionState(
            sheet_num=1, instrument_name="codex-cli", model="gpt-6-astra",
            fallback_chain=["claude-code", "opencode"],
            fallback_configs=[{"model": "claude-opus-5-5"}, {}],
            balance_chain=True,
        )

    @pytest.mark.asyncio
    async def test_idle_chain_keeps_the_primary(self) -> None:
        baton = BatonCore()
        for name in ("codex-cli", "claude-code", "opencode"):
            baton.register_instrument(name, max_concurrent=4)
        baton.register_job("j", {1: self._state()}, {})
        cb = AsyncMock()
        await dispatch_ready(baton, DispatchConfig(max_concurrent_sheets=10), cb)
        st = baton._jobs["j"].sheets[1]
        assert st.instrument_name == "codex-cli"
        assert st.fallback_chain == ["claude-code", "opencode"]
        assert not any(h.get("reason") == "balance" for h in st.instrument_fallback_history)

    @pytest.mark.asyncio
    async def test_loaded_primary_yields_to_the_least_loaded_entry(self) -> None:
        baton = BatonCore()
        for name in ("codex-cli", "claude-code", "opencode"):
            baton.register_instrument(name, max_concurrent=4)
        baton.register_job("j", {1: self._state()}, {})
        # codex-cli/gpt-6-astra is 3/4 busy elsewhere; claude-code 2/4; opencode idle.
        active = {
            ("other", 1): ("codex-cli", "gpt-6-astra"),
            ("other", 2): ("codex-cli", "gpt-6-astra"),
            ("other", 3): ("codex-cli", "gpt-6-astra"),
            ("other", 4): ("claude-code", "claude-opus-5-5"),
            ("other", 5): ("claude-code", "claude-opus-5-5"),
        }
        cfg = DispatchConfig(
            max_concurrent_sheets=10,
            instrument_concurrency={"codex-cli": 4, "claude-code": 4, "opencode": 4},
            active_executions=active,
        )
        cb = AsyncMock()
        await dispatch_ready(baton, cfg, cb)
        st = baton._jobs["j"].sheets[1]
        assert st.instrument_name == "opencode" and st.model is None
        assert st.fallback_chain == ["codex-cli", "claude-code"]
        assert st.fallback_configs == [{"model": "gpt-6-astra"}, {"model": "claude-opus-5-5"}]
        assert st.instrument_fallback_history[-1]["reason"] == "balance"
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_balance_rotates_whole_entry_configs(self) -> None:
        """The entry that runs is the entry whose config is read (#473 review)."""
        baton = BatonCore()
        for name in ("codex-cli", "claude-code", "opencode"):
            baton.register_instrument(name, max_concurrent=4)
        st = self._state()
        st.primary_config = {"model": "gpt-6-astra", "response_format": {"type": "json_object"}}
        st.fallback_configs = [{"model": "claude-opus-5-5", "timeout_seconds": 90}, {}]
        baton.register_job("j", {1: st}, {})
        # codex-cli 3/4 busy, claude-code 2/4 busy, opencode idle.
        active = {("other", i): ("codex-cli", "gpt-6-astra") for i in range(3)}
        active[("other", 3)] = ("claude-code", "claude-opus-5-5")
        active[("other", 4)] = ("claude-code", "claude-opus-5-5")
        cfg = DispatchConfig(
            max_concurrent_sheets=10, active_executions=active,
            instrument_concurrency={"codex-cli": 4, "claude-code": 4, "opencode": 4},
        )
        await dispatch_ready(baton, cfg, AsyncMock())
        st = baton._jobs["j"].sheets[1]
        assert st.instrument_name == "opencode"
        assert st.primary_config == {}
        assert _attempt_entry_config({"response_format": {"type": "json_object"}}, st) == {}
        assert st.fallback_chain == ["codex-cli", "claude-code"]
        assert st.fallback_configs == [
            {"model": "gpt-6-astra", "response_format": {"type": "json_object"}},
            {"model": "claude-opus-5-5", "timeout_seconds": 90},
        ]
        # A later advance to the displaced primary reads its whole config.
        st.advance_fallback("test")
        assert st.instrument_name == "codex-cli" and st.model == "gpt-6-astra"
        assert _attempt_entry_config({}, st)["response_format"] == {"type": "json_object"}

    def test_attempt_entry_config_without_primary_config_reads_the_sheet(self) -> None:
        st = self._state()
        assert st.primary_config is None
        assert _attempt_entry_config({"response_format": None}, st) == {"response_format": None}
        st.current_instrument_index = 1
        assert _attempt_entry_config({"response_format": None}, st) == {"model": "claude-opus-5-5"}

    @pytest.mark.asyncio
    async def test_rate_limited_entry_is_never_chosen(self) -> None:
        baton = BatonCore()
        for name in ("codex-cli", "claude-code", "opencode"):
            baton.register_instrument(name, max_concurrent=4)
        baton.register_job("j", {1: self._state()}, {})
        active = {("other", i): ("codex-cli", "gpt-6-astra") for i in range(3)}
        cfg = DispatchConfig(
            max_concurrent_sheets=10, active_executions=active,
            instrument_concurrency={"codex-cli": 4, "claude-code": 4, "opencode": 4},
            rate_limited_instruments={"opencode"},
        )
        await dispatch_ready(baton, cfg, AsyncMock())
        assert baton._jobs["j"].sheets[1].instrument_name == "claude-code"

    @pytest.mark.asyncio
    async def test_a_sheet_that_already_ran_is_not_rebalanced(self) -> None:
        baton = BatonCore()
        for name in ("codex-cli", "claude-code", "opencode"):
            baton.register_instrument(name, max_concurrent=4)
        st = self._state()
        st.attempt_history.append({"attempt": 1})
        baton.register_job("j", {1: st}, {})
        active = {("other", i): ("codex-cli", "gpt-6-astra") for i in range(3)}
        cfg = DispatchConfig(max_concurrent_sheets=10, active_executions=active,
                             instrument_concurrency={"codex-cli": 4})
        await dispatch_ready(baton, cfg, AsyncMock())
        assert baton._jobs["j"].sheets[1].instrument_name == "codex-cli"


class TestAliasEntryProperties:
    """AliasEntry accepts a bare profile name or a mapping, never anything else."""

    @given(name=st.text(min_size=1, max_size=40))
    def test_bare_name_and_mapping_build_the_same_entry(self, name: str) -> None:
        bare = AliasEntry.model_validate(name)
        mapped = AliasEntry.model_validate({"profile": name})
        assert bare.profile == name
        assert bare == mapped
        assert bare.config == mapped.config

    @given(value=st.one_of(st.integers(), st.lists(st.text()), st.just(""), st.none()))
    def test_non_name_values_are_refused(self, value: object) -> None:
        with pytest.raises(ValidationError):
            AliasEntry.model_validate(value)

    @given(extra=st.text(min_size=1, max_size=20).filter(lambda k: k not in ("profile", "config")))
    def test_unknown_keys_are_refused(self, extra: str) -> None:
        with pytest.raises(ValidationError):
            AliasEntry.model_validate({"profile": "codex-cli", extra: 1})
