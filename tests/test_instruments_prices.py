"""``mzt instruments prices``: price drift against models.dev, builtins-only write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.instruments import prices
from marianne.instruments.loader import InstrumentProfileLoader

runner = CliRunner()

PROFILE = """\
name: codex-cli
display_name: Codex
description: test
kind: cli
capabilities: [tool_use]
default_timeout_seconds: 60
models:
  # the flagship
  - name: gpt-x
    context_window: 1000
    cost_per_1k_input: 0.006   # stale launch price
    cost_per_1k_output: 0.03
    max_output_tokens: 10
  - name: "gpt-x[1m]"
    context_window: 2000
    cost_per_1k_input: 0.004
    cost_per_1k_output: 0.02
    cost_per_1k_cache_read: 0.0004
    cost_per_1k_cache_write: 0.005
  - name: gpt-gone
    context_window: 1000
    cost_per_1k_input: 0.1
    cost_per_1k_output: 0.1
cli:
  command: {executable: codex, prompt_flag: "-p"}
  output: {format: text}
"""

SNAPSHOT = {
    "openai": {
        "models": {
            "gpt-x": {
                "cost": {"input": 4, "output": 20, "cache_read": 0.4, "cache_write": 5},
                "last_updated": "2026-09-22",
            }
        }
    },
    "zai-coding-plan": {"models": {"glm-9": {"cost": {"input": 0, "output": 0, "cache_read": 0}}}},
}


def _layers(tmp_path: Path) -> tuple[Path, Path]:
    builtins = tmp_path / "builtins"
    user = tmp_path / "user"
    builtins.mkdir()
    user.mkdir()
    (builtins / "codex-cli.yaml").write_text(PROFILE)
    return builtins, user


def _rows(builtins: Path, user: Path) -> list[prices.PriceRow]:
    base, overlays = prices.load_layers(builtins, [user])
    return prices.compare_profiles(base, SNAPSHOT, builtins_dir=builtins) + prices.compare_profiles(
        overlays, SNAPSHOT, builtins_dir=builtins
    )


def test_provider_mapping_strips_long_context_suffix_and_plan_prefix() -> None:
    assert prices.resolve_provider("codex-cli", "gpt-x[1m]") == ("openai", "gpt-x")
    assert prices.resolve_provider("claude-code", "glm-5.3[1m]") == ("zai-coding-plan", "glm-5.3")
    assert prices.resolve_provider("claude-code", "claude-sonnet-5") == (
        "anthropic",
        "claude-sonnet-5",
    )
    assert prices.resolve_provider("opencode", "zai-coding-plan/glm-5.3") == (
        "zai-coding-plan",
        "glm-5.3",
    )
    assert prices.resolve_provider("antigravity", "gemini-3.8-flash-high")[0] is None


def test_compare_reports_drift_match_and_absent(tmp_path: Path) -> None:
    builtins, user = _layers(tmp_path)
    by_model = {r.model: r for r in _rows(builtins, user)}
    drift = by_model["gpt-x"]
    assert drift.status == "drift" and drift.builtin
    assert set(drift.drifted) == set(prices.PRICE_FIELDS)
    assert drift.theirs["cost_per_1k_input"] == pytest.approx(0.004)
    assert drift.last_updated == "2026-09-22"
    assert by_model["gpt-x[1m]"].status == "match"
    assert by_model["gpt-gone"].status == "absent"


def test_unset_cache_price_is_compared_at_the_rate_the_estimate_charges() -> None:
    """A $0 plan row with no cache fields does not drift from an explicit 0."""
    from marianne.core.config.instruments import ModelCapacity

    cap = ModelCapacity(name="glm-9", context_window=10, cost_per_1k_input=0, cost_per_1k_output=0)
    row = prices._compare_row("claude-code", cap, SNAPSHOT, None, True)
    assert row.status == "match"


def test_rewrite_keeps_comments_and_inserts_cache_fields() -> None:
    new = prices.rewrite_builtin_prices(
        PROFILE,
        "gpt-x",
        {
            "cost_per_1k_input": 0.004,
            "cost_per_1k_output": 0.02,
            "cost_per_1k_cache_read": 0.0004,
            "cost_per_1k_cache_write": 0.005,
        },
    )
    assert "  # the flagship" in new
    assert "    cost_per_1k_input: 0.004   # stale launch price" in new
    block = new.split("- name: gpt-x\n")[1].split("  - name:")[0]
    assert block.index("cost_per_1k_output: 0.02") < block.index("cost_per_1k_cache_read: 0.0004")
    assert block.index("cost_per_1k_cache_read") < block.index("cost_per_1k_cache_write: 0.005")
    assert block.index("cost_per_1k_cache_write") < block.index("max_output_tokens")
    # The other rows are byte-identical.
    assert new.split('- name: "gpt-x[1m]"')[1] == PROFILE.split('- name: "gpt-x[1m]"')[1]


def test_rewritten_profile_still_loads_with_the_new_prices(tmp_path: Path) -> None:
    builtins, user = _layers(tmp_path)
    written, refused = prices.apply_drift(_rows(builtins, user), builtins_dir=builtins)
    assert [r.model for r in written] == ["gpt-x"] and refused == []
    cap = InstrumentProfileLoader.load_directories([builtins])["codex-cli"].models[0]
    assert (cap.cost_per_1k_input, cap.cost_per_1k_cache_read) == (0.004, 0.0004)
    assert all(r.status != "drift" for r in _rows(builtins, user))


def test_write_refuses_a_user_layer_file(tmp_path: Path) -> None:
    builtins, user = _layers(tmp_path)
    (user / "codex-cli.yaml").write_text(PROFILE)
    before = (user / "codex-cli.yaml").read_text()
    written, refused = prices.apply_drift(_rows(builtins, user), builtins_dir=builtins)
    assert len(written) == 1 and len(refused) == 1
    assert refused[0].source_path == (user / "codex-cli.yaml").resolve()
    assert (user / "codex-cli.yaml").read_text() == before


@pytest.fixture
def venue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    builtins, user = _layers(tmp_path)
    snap = tmp_path / "api.json"
    snap.write_text(json.dumps(SNAPSHOT))
    monkeypatch.setattr(
        "marianne.instruments.loader.profile_source_dirs",
        lambda **_: (builtins, user, tmp_path / "venue"),
    )
    return builtins, user, snap


def _json(output: str) -> dict:
    return json.loads(output[output.find("{") :])


def test_cli_reports_drift_and_exits_1(venue: tuple[Path, Path, Path]) -> None:
    _, _, snap = venue
    result = runner.invoke(app, ["instruments", "prices", "--snapshot", str(snap), "--json"])
    assert result.exit_code == 1, result.output
    data = _json(result.output)
    assert data["counts"] == {"match": 1, "drift": 1, "absent": 1, "unmapped": 0}
    assert [r["model"] for r in data["rows"]] == ["gpt-x"]
    assert data["written"] == []


def test_cli_write_updates_builtins_and_exits_0(venue: tuple[Path, Path, Path]) -> None:
    builtins, _, snap = venue
    result = runner.invoke(
        app, ["instruments", "prices", "--snapshot", str(snap), "--write", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert _json(result.output)["written"] == ["codex-cli:gpt-x"]
    assert "cost_per_1k_input: 0.004" in (builtins / "codex-cli.yaml").read_text()
    again = runner.invoke(app, ["instruments", "prices", "--snapshot", str(snap), "--json"])
    assert again.exit_code == 0 and _json(again.output)["counts"]["drift"] == 0


def test_cli_write_leaves_user_drift_and_exits_1(venue: tuple[Path, Path, Path]) -> None:
    _, user, snap = venue
    (user / "codex-cli.yaml").write_text(PROFILE)
    result = runner.invoke(
        app, ["instruments", "prices", "--snapshot", str(snap), "--write", "--json"]
    )
    assert result.exit_code == 1, result.output
    data = _json(result.output)
    assert data["written"] == ["codex-cli:gpt-x"]
    assert len(data["refused"]) == 1 and "codex-cli (" in data["refused"][0]


def test_cli_missing_snapshot_exits_2(venue: tuple[Path, Path, Path], tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["instruments", "prices", "--snapshot", str(tmp_path / "nope.json")]
    )
    assert result.exit_code == 2
    assert "cannot load models.dev prices" in result.output


def test_shipped_builtins_parse_under_the_rewriter() -> None:
    """Every priced builtin row can be located and rewritten to its own values."""
    builtins_dir = Path(prices.__file__).resolve().parent / "builtins"
    for name, prof in InstrumentProfileLoader.load_directories([builtins_dir]).items():
        path = prof._source_path
        assert path is not None
        text = path.read_text(encoding="utf-8")
        for cap in prof.models:
            same = {f: getattr(cap, f) for f in prices.PRICE_FIELDS}
            out = prices.rewrite_builtin_prices(text, cap.name, same)
            reloaded = [ln for ln in out.split("\n") if ln.strip()]
            assert len(reloaded) == len([ln for ln in text.split("\n") if ln.strip()]), (
                name,
                cap.name,
            )
