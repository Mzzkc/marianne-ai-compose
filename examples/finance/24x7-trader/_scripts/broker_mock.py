#!/usr/bin/env python3
"""broker_mock.py -- file-backed mock broker for offline runs of the suite.

Implements the broker contract in _techniques/broker.md with the same JSON
shapes and exit behaviour as _scripts/broker_paper.sh (the Alpaca reference),
with no network, no credentials and no clock-dependent fills.

    broker_mock.py <verb> [args...]

Verbs: auth_check, get_balance, get_positions, get_quote, place_order,
set_stop, cancel_stop, list_orders.

Environment (all optional):

  MOCK_BROKER_STATE   absolute path of a directory holding state.json
                      (cash, positions, orders). Default:
                      <repo>/workspaces/24x7-trader/mock-broker, which is
                      inside the score workspace the scores declare.
  MOCK_BROKER_PRICES  path of a JSON fixture, ticker -> price (a number, or an
                      object with "last" and optional "bid", "ask", "volume").
                      Default: the DEFAULT_PRICES table below.
  MOCK_BROKER_CASH    starting cash when state.json is first created
                      (default 100000).
  MOCK_BROKER_NOW     pin the clock (ISO 8601) so `ts` fields are reproducible.

Fills are deterministic: an order fills at the fixture price, in the same
call that places it when it is marketable. Limit orders that are not
marketable, and stops, stay open and are re-checked against the fixture at the
start of every call, so editing the fixture between calls moves the market.

Paper only, like the reference: BROKER_LIVE is ignored and mode is always
"paper". Long-only: a SELL larger than the position is refused.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROG = "broker_mock"

# Illustrative round numbers, not real quotes.
DEFAULT_PRICES = {
    "SPY": 500.00,
    "AAPL": 200.00,
    "MSFT": 420.00,
    "NVDA": 130.00,
    "GOOGL": 170.00,
    "AMZN": 190.00,
    "META": 520.00,
    "TSLA": 250.00,
    "JPM": 210.00,
    "XOM": 115.00,
    "JNJ": 160.00,
    "KO": 62.00,
}
DEFAULT_CASH = 100000.0
DEFAULT_VOLUME = 100
ACCOUNT_ID = "MOCK-0001"
LIST_STATUSES = ("open", "filled", "cancelled", "closed", "all")


class BrokerError(Exception):
    """A refusal. Message goes to stderr, exit status is 1 (as the reference)."""


def die(msg: str) -> "BrokerError":
    return BrokerError(msg)


def num(x: float):
    """jq-style number: integral values print as integers."""
    x = round(float(x), 4)
    return int(x) if x == int(x) else x


def money(x: float):
    x = round(float(x), 2)
    return int(x) if x == int(x) else x


def parse_positive(text: str, what: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise die(f"{what} must be a number (got: {text!r})")
    if not value > 0 or value != value or value == float("inf"):
        raise die(f"{what} must be > 0 (got: {text})")
    return value


def now_iso() -> str:
    pinned = os.environ.get("MOCK_BROKER_NOW")
    if pinned:
        return pinned
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- configuration --------------------------------------------------------


def state_dir() -> Path:
    raw = os.environ.get("MOCK_BROKER_STATE")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            raise die(f"MOCK_BROKER_STATE must be an absolute path (got: {raw})")
        return path
    repo = Path(__file__).resolve().parents[4]
    return repo / "workspaces" / "24x7-trader" / "mock-broker"


def load_prices() -> dict:
    raw = os.environ.get("MOCK_BROKER_PRICES")
    if not raw:
        return {k: {"last": v} for k, v in DEFAULT_PRICES.items()}
    try:
        data = json.loads(Path(raw).read_text())
    except OSError as exc:
        raise die(f"MOCK_BROKER_PRICES unreadable: {exc}")
    except ValueError as exc:
        raise die(f"MOCK_BROKER_PRICES is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise die("MOCK_BROKER_PRICES must be a JSON object, ticker -> price")
    prices = {}
    for ticker, entry in data.items():
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            entry = {"last": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("last"), (int, float)):
            raise die(f"MOCK_BROKER_PRICES[{ticker}] needs a number or an object with 'last'")
        if not entry["last"] > 0:
            raise die(f"MOCK_BROKER_PRICES[{ticker}] must be > 0")
        prices[ticker.upper()] = entry
    return prices


def price_of(prices: dict, ticker: str) -> float:
    if ticker not in prices:
        raise die(f"no fixture price for {ticker} (set MOCK_BROKER_PRICES)")
    return float(prices[ticker]["last"])


# ---- state ---------------------------------------------------------------


class Book:
    """state.json under an exclusive lock for the life of one invocation."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "state.json"
        self._lock = open(directory / "state.lock", "w")
        fcntl.flock(self._lock, fcntl.LOCK_EX)
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except ValueError as exc:
                raise die(f"{self.path} is not valid JSON: {exc}")
        else:
            try:
                cash = float(os.environ.get("MOCK_BROKER_CASH", DEFAULT_CASH))
            except ValueError:
                raise die("MOCK_BROKER_CASH must be a number")
            self.data = {"cash": cash, "positions": {}, "orders": [], "next_id": 1}
        self.dirty = not self.path.exists()

    def save(self) -> None:
        if not self.dirty:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)

    @property
    def cash(self) -> float:
        return self.data["cash"]

    @property
    def positions(self) -> dict:
        return self.data["positions"]

    @property
    def orders(self) -> list:
        return self.data["orders"]

    def new_id(self) -> str:
        n = self.data["next_id"]
        self.data["next_id"] = n + 1
        self.dirty = True
        return f"mock-order-{n:06d}"

    def apply_fill(self, order: dict, price: float, ts: str) -> bool:
        """Fill `order` at `price`. False (nothing changed) if it cannot settle."""
        qty, ticker = order["qty"], order["ticker"]
        pos = self.positions.get(ticker)
        if order["side"] == "buy":
            cost = qty * price
            if cost > self.cash + 1e-9:
                return False
            self.data["cash"] = self.cash - cost
            if pos:
                total = pos["qty"] + qty
                pos["avg_entry"] = (pos["avg_entry"] * pos["qty"] + cost) / total
                pos["qty"] = total
            else:
                self.positions[ticker] = {"qty": qty, "avg_entry": price}
        else:
            held = pos["qty"] if pos else 0
            qty = min(qty, held)  # a stop sized before a partial sell
            if qty <= 0:
                return False
            order["qty"] = qty
            self.data["cash"] = self.cash + qty * price
            pos["qty"] = held - qty
            if pos["qty"] <= 1e-9:
                del self.positions[ticker]
                self.cancel_open(ticker, "stop", ts)
        order["status"] = "filled"
        order["filled_avg_price"] = price
        order["filled_at"] = ts
        self.dirty = True
        return True

    def cancel_open(self, ticker: str, kind: str, ts: str) -> int:
        n = 0
        for o in self.orders:
            if o["status"] == "new" and o["ticker"] == ticker and o["type"] == kind:
                o["status"] = "cancelled"
                o["cancelled_at"] = ts
                n += 1
        if n:
            self.dirty = True
        return n

    def sweep(self, prices: dict, ts: str) -> None:
        """Re-check every open limit/stop order against the fixture."""
        for o in list(self.orders):
            if o["status"] != "new" or o["ticker"] not in prices:
                continue
            p = price_of(prices, o["ticker"])
            if o["type"] == "limit":
                hit = p <= o["limit_price"] if o["side"] == "buy" else p >= o["limit_price"]
            else:  # stop (always a sell)
                hit = p <= o["stop_price"]
            if hit:
                self.apply_fill(o, p, ts)


# ---- verbs ---------------------------------------------------------------


def emit(obj) -> None:
    print(json.dumps(obj, indent=2))


def need(args: list, i: int, what: str) -> str:
    if len(args) <= i or args[i] == "":
        raise die(f"{what} required")
    return args[i]


def v_auth_check(book, prices, args, ts):
    emit({"authenticated": True, "mode": "paper", "account_id": ACCOUNT_ID})


def equity_of(book, prices) -> float:
    return book.cash + sum(
        p["qty"] * price_of(prices, t) for t, p in book.positions.items()
    )


def v_get_balance(book, prices, args, ts):
    emit({
        "cash": money(book.cash),
        "equity": money(equity_of(book, prices)),
        "buying_power": money(book.cash),  # no margin
        "currency": "USD",
    })


def v_get_positions(book, prices, args, ts):
    out = []
    for ticker in sorted(book.positions):
        pos = book.positions[ticker]
        cur = price_of(prices, ticker)
        out.append({
            "ticker": ticker,
            "qty": num(pos["qty"]),
            "avg_entry": num(pos["avg_entry"]),
            "current_price": num(cur),
            "market_value": money(pos["qty"] * cur),
            "unrealized_pnl": money(pos["qty"] * (cur - pos["avg_entry"])),
            "side": "long",
        })
    emit(out)


def v_get_quote(book, prices, args, ts):
    ticker = need(args, 0, "ticker").upper()
    entry = prices.get(ticker)
    if entry is None:
        raise die(f"no fixture price for {ticker} (set MOCK_BROKER_PRICES)")
    last = float(entry["last"])
    emit({
        "ticker": ticker,
        "bid": num(entry.get("bid", last)),
        "ask": num(entry.get("ask", last)),
        "last": num(last),
        "volume": entry.get("volume", DEFAULT_VOLUME),
        "ts": ts,
    })


def v_place_order(book, prices, args, ts):
    ticker = need(args, 0, "ticker").upper()
    side = need(args, 1, "side").lower()
    qty_text = need(args, 2, "qty")
    otype = args[3] if len(args) > 3 else "market"
    if side not in ("buy", "sell"):
        raise die(f"side must be BUY or SELL (got: {args[1]})")
    if otype not in ("market", "limit"):
        raise die(f"type must be market or limit (got: {otype})")
    qty = parse_positive(qty_text, "qty")

    limit_text, client_id = "", ""
    rest, i = args[4:], 0
    while i < len(rest):
        if rest[i] == "--client-order-id":
            if i + 1 >= len(rest):
                raise die("--client-order-id needs a value")
            client_id, i = rest[i + 1], i + 2
        elif rest[i] == "--limit-price":
            if i + 1 >= len(rest):
                raise die("--limit-price needs a value")
            limit_text, i = rest[i + 1], i + 2
        else:  # historical 5th positional: the limit price
            if not limit_text:
                limit_text = rest[i]
            i += 1
    limit = None
    if otype == "limit":
        if not limit_text:
            raise die("limit order needs a limit price")
        limit = parse_positive(limit_text, "limit price")

    if client_id and any(o.get("client_order_id") == client_id for o in book.orders):
        raise die(f"duplicate client_order_id: {client_id}")

    price = price_of(prices, ticker)
    ref_price = limit if otype == "limit" else price
    if side == "buy" and qty * ref_price > book.cash + 1e-9:
        raise die(f"insufficient buying power for {qty:g} {ticker} at {ref_price:g}")
    held = book.positions.get(ticker, {"qty": 0})["qty"]
    if side == "sell" and qty > held + 1e-9:
        raise die(f"insufficient qty for {ticker}: have {held:g}, sell {qty:g} (long-only mock)")

    order = {
        "id": book.new_id(), "ticker": ticker, "side": side, "qty": qty,
        "type": otype, "status": "new", "client_order_id": client_id or None, "ts": ts,
    }
    if limit is not None:
        order["limit_price"] = limit
    book.orders.append(order)
    book.dirty = True
    marketable = otype == "market" or (
        price <= limit if side == "buy" else price >= limit
    )
    if marketable:
        book.apply_fill(order, price, ts)
    emit({
        "status": order["status"], "id": order["id"], "ticker": ticker,
        "side": side, "qty": num(qty), "type": otype,
        "client_order_id": order["client_order_id"],
    })


def v_set_stop(book, prices, args, ts):
    ticker = need(args, 0, "ticker").upper()
    stop = parse_positive(need(args, 1, "stop"), "stop price")
    pos = book.positions.get(ticker)
    if not pos:
        raise die(f"no open position for {ticker}")
    if any(o["status"] == "new" and o["ticker"] == ticker and o["type"] == "stop"
           for o in book.orders):
        raise die(f"open stop already exists for {ticker}; cancel_stop first")
    order = {
        "id": book.new_id(), "ticker": ticker, "side": "sell", "qty": pos["qty"],
        "type": "stop", "status": "new", "client_order_id": None, "ts": ts,
        "stop_price": stop,
    }
    book.orders.append(order)
    book.dirty = True
    if price_of(prices, ticker) <= stop:  # already through the stop: triggers at once
        book.apply_fill(order, price_of(prices, ticker), ts)
    emit({"status": order["status"], "ticker": ticker, "stop": num(stop)})


def v_cancel_stop(book, prices, args, ts):
    ticker = need(args, 0, "ticker").upper()
    book.cancel_open(ticker, "stop", ts)  # like the reference, silent when none
    emit({"status": "cancelled", "ticker": ticker})


def v_list_orders(book, prices, args, ts):
    status = "open"
    if args and args[0] == "--status":
        if len(args) < 2:
            raise die("--status needs a value")
        status = args[1]
    if status not in LIST_STATUSES:
        raise die(f"status must be one of {', '.join(LIST_STATUSES)} (got: {status})")
    wanted = {
        "open": {"new"}, "filled": {"filled"}, "cancelled": {"cancelled"},
        "closed": {"filled", "cancelled"}, "all": {"new", "filled", "cancelled"},
    }[status]
    emit([
        {
            "id": o["id"], "ticker": o["ticker"], "side": o["side"],
            "qty": num(o["qty"]), "status": o["status"],
            "client_order_id": o.get("client_order_id"), "ts": o["ts"],
        }
        for o in book.orders if o["status"] in wanted
    ])


VERBS = {
    "auth_check": v_auth_check,
    "get_balance": v_get_balance,
    "get_positions": v_get_positions,
    "get_quote": v_get_quote,
    "place_order": v_place_order,
    "set_stop": v_set_stop,
    "cancel_stop": v_cancel_stop,
    "list_orders": v_list_orders,
}


def main(argv: list) -> int:
    verb = argv[0] if argv else ""
    if verb not in VERBS:
        print(f"{PROG}: unknown verb: {verb}", file=sys.stderr)
        return 1
    try:
        prices = load_prices()
        book = Book(state_dir())
        ts = now_iso()
        book.sweep(prices, ts)
        VERBS[verb](book, prices, argv[1:], ts)
        book.save()
    except BrokerError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
