"""The 24x7-trader mock broker satisfies the suite's broker contract.

Runs ``_scripts/broker_mock.py`` as a subprocess, the way every score calls
``$BROKER_CMD``, against ``_techniques/broker.md``: all eight verbs, the JSON
shapes ``broker_paper.sh`` emits, the exit behaviour for unknown verbs and bad
arguments, idempotency by ``--client-order-id``, deterministic fills from a
fixture, and the suite's own callers (``execute_slate.sh``, ``benchmark_spy.sh``).
No network, no credentials.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SUITE = Path(__file__).resolve().parents[1] / "examples" / "finance" / "24x7-trader"
MOCK = SUITE / "_scripts" / "broker_mock.py"
CONTRACT = SUITE / "_techniques" / "broker.md"

BALANCE_KEYS = {"cash", "equity", "buying_power", "currency"}
POSITION_KEYS = {
    "ticker", "qty", "avg_entry", "current_price", "market_value", "unrealized_pnl", "side",
}
QUOTE_KEYS = {"ticker", "bid", "ask", "last", "volume", "ts"}
ORDER_KEYS = {"status", "id", "ticker", "side", "qty", "type"}
LIST_KEYS = {"id", "ticker", "side", "qty", "status", "ts"}


class Mock:
    """One state directory, one fixture; each call is a fresh process."""

    def __init__(self, tmp: Path, prices: dict[str, Any] | None = None) -> None:
        self.state = tmp / "state"
        self.prices_path = tmp / "prices.json"
        self.set_prices(prices if prices is not None else {"AAPL": 200, "SPY": 500, "MSFT": 400})
        self.env = {
            "PATH": "/usr/bin:/bin",
            "MOCK_BROKER_STATE": str(self.state),
            "MOCK_BROKER_PRICES": str(self.prices_path),
            "MOCK_BROKER_NOW": "2026-11-17T09:30:00Z",
        }

    def set_prices(self, prices: dict[str, Any]) -> None:
        self.prices_path.write_text(json.dumps(prices))

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(MOCK), *args], env=self.env, capture_output=True, text=True,
            timeout=30, check=False,
        )

    def ok(self, *args: str) -> Any:
        proc = self.run(*args)
        assert proc.returncode == 0, f"{args}: {proc.stderr}"
        return json.loads(proc.stdout)


@pytest.fixture
def broker(tmp_path: Path) -> Mock:
    return Mock(tmp_path)


def test_contract_verbs_all_implemented() -> None:
    """Every verb in the contract table is dispatched; none is silently missing."""
    verbs = set(re.findall(r"^\| `(\w+)` \|", CONTRACT.read_text(), re.M))
    assert len(verbs) == 8, verbs
    source = MOCK.read_text()
    for verb in verbs:
        assert f'"{verb}": v_{verb}' in source, verb


def test_every_score_call_is_handled(broker: Mock) -> None:
    """Each broker verb the scores and scripts invoke is accepted by the mock."""
    called: set[str] = set()
    for path in [*SUITE.glob("*.yaml"), *(SUITE / "_scripts").glob("*.sh")]:
        if path.name == "broker_paper.sh":
            continue
        text = path.read_text()
        called |= set(re.findall(r'"\$BROKER" (\w+)', text))
        called |= set(re.findall(r"`broker (\w+)", text))
    assert called >= {
        "auth_check", "get_balance", "get_positions", "get_quote",
        "place_order", "set_stop", "cancel_stop", "list_orders",
    }
    probes = {
        "auth_check": [], "get_balance": [], "get_positions": [],
        "get_quote": ["AAPL"], "list_orders": ["--status", "open"],
        "cancel_stop": ["AAPL"],
    }
    for verb in sorted(called - {"place_order", "set_stop"}):
        assert broker.run(verb, *probes[verb]).returncode == 0, verb
    # market-open / midday SELL spellings and the slate's flag order
    broker.ok("place_order", "AAPL", "BUY", "10", "market", "--client-order-id", "a1")
    broker.ok("set_stop", "AAPL", "190")
    broker.ok("place_order", "AAPL", "SELL", "2", "limit", "--limit-price", "250")
    broker.ok("place_order", "AAPL", "SELL", "2", "limit", "260")  # 5th positional


def test_auth_check_balance_and_positions_on_fresh_account(broker: Mock) -> None:
    auth = broker.ok("auth_check")
    assert (auth["authenticated"], auth["mode"]) == (True, "paper")
    assert isinstance(auth["account_id"], str) and auth["account_id"]
    bal = broker.ok("get_balance")
    assert set(bal) == BALANCE_KEYS
    assert bal["cash"] == bal["equity"] == bal["buying_power"] == 100000
    assert bal["currency"] == "USD"
    assert broker.ok("get_positions") == []


def test_get_quote_fixture_shapes(broker: Mock) -> None:
    quote = broker.ok("get_quote", "aapl")
    assert set(quote) == QUOTE_KEYS
    assert quote["ticker"] == "AAPL" and quote["last"] == 200
    assert quote["ts"] == "2026-11-17T09:30:00Z"
    broker.set_prices({"AAPL": {"last": 10, "bid": 9.5, "ask": 10.5, "volume": 7}})
    rich = broker.ok("get_quote", "AAPL")
    assert (rich["bid"], rich["ask"], rich["last"], rich["volume"]) == (9.5, 10.5, 10, 7)


def test_default_fixture_when_no_prices_given(tmp_path: Path) -> None:
    b = Mock(tmp_path)
    del b.env["MOCK_BROKER_PRICES"]
    assert b.ok("get_quote", "SPY")["last"] == 500


def test_market_buy_fills_deterministically_and_updates_book(broker: Mock) -> None:
    order = broker.ok("place_order", "AAPL", "BUY", "10", "market", "--client-order-id", "c1")
    assert set(order) >= ORDER_KEYS
    assert order["status"] == "filled" and order["id"]
    assert (order["ticker"], order["side"], order["qty"], order["type"]) == (
        "AAPL", "buy", 10, "market")
    assert order["client_order_id"] == "c1"

    (pos,) = broker.ok("get_positions")
    assert set(pos) == POSITION_KEYS
    assert (pos["ticker"], pos["qty"], pos["avg_entry"], pos["side"]) == ("AAPL", 10, 200, "long")
    assert pos["market_value"] == 2000 and pos["unrealized_pnl"] == 0

    bal = broker.ok("get_balance")
    assert bal["cash"] == 98000 and bal["equity"] == 100000

    broker.set_prices({"AAPL": 210, "SPY": 500})  # the market moves between calls
    (pos,) = broker.ok("get_positions")
    assert pos["current_price"] == 210 and pos["unrealized_pnl"] == 100
    assert broker.ok("get_balance")["equity"] == 100100


def test_state_persists_in_the_state_file(broker: Mock) -> None:
    broker.ok("place_order", "MSFT", "BUY", "5", "market")
    state = json.loads((broker.state / "state.json").read_text())
    assert state["cash"] == 98000
    assert state["positions"]["MSFT"]["qty"] == 5
    assert state["orders"][0]["status"] == "filled"


def test_sell_reduces_position_and_closes_it(broker: Mock) -> None:
    broker.ok("place_order", "AAPL", "BUY", "10", "market")
    broker.ok("place_order", "AAPL", "SELL", "4", "market")
    (pos,) = broker.ok("get_positions")
    assert pos["qty"] == 6
    broker.ok("place_order", "AAPL", "SELL", "6", "market")
    assert broker.ok("get_positions") == []
    assert broker.ok("get_balance")["cash"] == 100000


def test_limit_order_rests_then_fills_when_fixture_crosses(broker: Mock) -> None:
    order = broker.ok("place_order", "AAPL", "BUY", "10", "limit", "--limit-price", "190")
    assert order["status"] == "new" and order["type"] == "limit"
    assert broker.ok("get_positions") == []
    assert [o["id"] for o in broker.ok("list_orders")] == [order["id"]]
    broker.set_prices({"AAPL": 185})
    (pos,) = broker.ok("get_positions")
    assert pos["qty"] == 10 and pos["avg_entry"] == 185
    assert broker.ok("list_orders") == []
    assert [o["id"] for o in broker.ok("list_orders", "--status", "filled")] == [order["id"]]


def test_set_stop_cancel_stop_and_trigger(broker: Mock) -> None:
    broker.ok("place_order", "AAPL", "BUY", "10", "market")
    stop = broker.ok("set_stop", "AAPL", "190")
    assert stop["ticker"] == "AAPL" and stop["stop"] == 190 and "status" in stop
    (open_order,) = broker.ok("list_orders", "--status", "open")
    assert set(open_order) >= LIST_KEYS and open_order["side"] == "sell"

    assert broker.ok("cancel_stop", "AAPL") == {"status": "cancelled", "ticker": "AAPL"}
    assert broker.ok("list_orders") == []
    assert len(broker.ok("list_orders", "--status", "cancelled")) == 1
    # like the reference: cancelling when nothing is open is not an error
    assert broker.ok("cancel_stop", "AAPL")["status"] == "cancelled"

    broker.ok("set_stop", "AAPL", "190")
    broker.set_prices({"AAPL": 180})  # through the stop
    assert broker.ok("get_positions") == []
    assert broker.ok("get_balance")["cash"] == 99800


def test_list_orders_statuses(broker: Mock) -> None:
    broker.ok("place_order", "AAPL", "BUY", "1", "market")
    broker.ok("place_order", "AAPL", "BUY", "1", "limit", "--limit-price", "100")
    assert len(broker.ok("list_orders")) == 1
    assert len(broker.ok("list_orders", "--status", "open")) == 1
    assert len(broker.ok("list_orders", "--status", "filled")) == 1
    assert broker.ok("list_orders", "--status", "cancelled") == []
    for row in broker.ok("list_orders", "--status", "filled"):
        assert set(row) >= LIST_KEYS


def test_duplicate_client_order_id_is_rejected(broker: Mock) -> None:
    broker.ok("place_order", "AAPL", "BUY", "1", "market", "--client-order-id", "dup")
    proc = broker.run("place_order", "AAPL", "BUY", "1", "market", "--client-order-id", "dup")
    assert proc.returncode == 1 and "duplicate" in proc.stderr and proc.stdout == ""
    assert broker.ok("get_positions")[0]["qty"] == 1  # nothing double-filled


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["bogus"],
        ["get_quote"],
        ["get_quote", "NOPE"],
        ["place_order"],
        ["place_order", "AAPL"],
        ["place_order", "AAPL", "HOLD", "1", "market"],
        ["place_order", "AAPL", "BUY", "abc", "market"],
        ["place_order", "AAPL", "BUY", "0", "market"],
        ["place_order", "AAPL", "BUY", "1", "iceberg"],
        ["place_order", "AAPL", "BUY", "1", "limit"],
        ["place_order", "AAPL", "BUY", "1000000", "market"],
        ["place_order", "AAPL", "SELL", "1", "market"],
        ["set_stop"],
        ["set_stop", "AAPL"],
        ["set_stop", "AAPL", "190"],
        ["cancel_stop"],
        ["list_orders", "--status"],
        ["list_orders", "--status", "weird"],
    ],
)
def test_refusals_exit_1_with_stderr_and_no_stdout(broker: Mock, args: list[str]) -> None:
    proc = broker.run(*args)
    assert proc.returncode == 1, args
    assert proc.stderr.startswith("broker_mock:"), proc.stderr
    assert proc.stdout == ""


def test_live_flag_is_ignored_like_the_reference(broker: Mock) -> None:
    broker.env["BROKER_LIVE"] = "1"
    assert broker.ok("auth_check")["mode"] == "paper"


def test_state_path_must_be_absolute(broker: Mock) -> None:
    broker.env["MOCK_BROKER_STATE"] = "relative/dir"
    proc = broker.run("get_balance")
    assert proc.returncode == 1 and "absolute" in proc.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="suite scripts need jq")
def test_suite_scripts_run_against_the_mock(broker: Mock, tmp_path: Path) -> None:
    """execute_slate.sh and benchmark_spy.sh, unmodified, on top of the mock."""
    ws = tmp_path / "ws"
    ws.mkdir()
    env = {**broker.env, "BROKER_CMD": str(MOCK)}
    slate = tmp_path / "slate.json"
    slate.write_text(json.dumps([{
        "client_order_id": "ws-2026-11-17-AAPL-BUY", "ticker": "AAPL", "side": "BUY",
        "target_qty": 10, "order_type": "market", "initial_stop_price": 190,
    }]))
    proc = subprocess.run(
        [str(SUITE / "_scripts" / "execute_slate.sh"), str(ws), str(slate),
         str(ws / "results.jsonl"), str(ws / "failures.md")],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "1 placed, 1 stops set, 0 failed" in proc.stdout
    assert (ws / "failures.md").read_text() == ""
    assert json.loads((ws / "positions.json").read_text())[0]["qty"] == 10

    # a retry of the same slate is refused, not double-filled
    retry = subprocess.run(
        [str(SUITE / "_scripts" / "execute_slate.sh"), str(ws), str(slate),
         str(ws / "r2.jsonl"), str(ws / "f2.md")],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert retry.returncode == 1 and "duplicate" in (ws / "f2.md").read_text()

    bench = SUITE / "_scripts" / "benchmark_spy.sh"
    spy = subprocess.run([str(bench), "spy_close", "--date", "2026-11-17"], env=env,
                         capture_output=True, text=True, timeout=60, check=False)
    assert json.loads(spy.stdout)["close"] == 500
    pv = subprocess.run([str(bench), "portfolio_value", "--workspace", str(ws)], env=env,
                        capture_output=True, text=True, timeout=60, check=False)
    assert json.loads(pv.stdout)["equity"] == 100000


# ------------------------------------------------------------- offline news


NEWS = SUITE / "_scripts" / "news_offline.sh"
NEWS_VERBS = ("macro_today", "sector_news", "catalysts", "peer_compare", "regulatory")


def _news(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(NEWS), *args], capture_output=True, text=True, env={**os.environ, **(env or {})},
        check=False,
    )


def test_news_offline_covers_the_contract_verbs_and_marks_every_answer() -> None:
    contract = (SUITE / "_techniques" / "news_research.md").read_text(encoding="utf-8")
    for verb in NEWS_VERBS:
        assert f"`{verb}`" in contract
        args = ("--date", "2026-11-17") if verb == "macro_today" else ("NVDA",)
        result = _news(verb, *args)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("**[OFFLINE — training data only]**")


def test_news_offline_reads_fixtures_and_refuses_bad_input(tmp_path: Path) -> None:
    (tmp_path / "catalysts-NVDA.md").write_text("- earnings on Thursday\n", encoding="utf-8")
    out = _news("catalysts", "NVDA", env={"NEWS_FIXTURES": str(tmp_path)})
    assert "earnings on Thursday" in out.stdout
    for bad in (("bogus",), ("catalysts",), ("catalysts", "../etc"), ()):
        result = _news(*bad)
        assert result.returncode != 0 and result.stdout == ""
