#!/usr/bin/env bash
# benchmark_spy.sh — reference benchmark implementation.
# Uses the broker's get_quote for SPY by default. Operator can swap.

set -euo pipefail

BROKER="${BROKER_CMD:?BROKER_CMD must be an absolute path to a broker.sh implementing _techniques/broker.md}"
TICKER="${BENCHMARK_TICKER:-SPY}"

die() { echo "benchmark_spy: $*" >&2; exit 1; }
[[ -x "$BROKER" ]] || die "BROKER_CMD not executable: $BROKER"

verb="${1:-}"
shift || true

case "$verb" in
  spy_close)
    "$BROKER" get_quote "$TICKER" | jq --arg t "$TICKER" '{ticker: $t, close: .last, date: (now | strftime("%Y-%m-%d"))}'
    ;;
  spy_history)
    # Stub: requires a real history API. Reference impl reads optional CSV.
    hist="${BENCHMARK_HISTORY_CSV:-}"
    [[ -f "$hist" ]] || die "spy_history needs BENCHMARK_HISTORY_CSV pointing to a date,close CSV"
    cat "$hist"
    ;;
  portfolio_value)
    [[ "${1:-}" == "--workspace" ]] || die "usage: portfolio_value --workspace <path>"
    ws="$2"
    [[ -f "$ws/positions.json" ]] || die "no positions.json in $ws"
    bal=$("$BROKER" get_balance)
    cash=$(jq -r .cash <<<"$bal")
    equity=$(jq -r .equity <<<"$bal")
    pos_count=$(jq 'length' "$ws/positions.json")
    date=$(date +%F)
    # Alpaca's `equity` already includes cash + long_market_value. Use equity AS the
    # portfolio total. Don't add cash again — that double-counts.
    jq -n --arg d "$date" --argjson c "$cash" --argjson e "$equity" --argjson n "$pos_count" \
      '{date: $d, equity: $e, cash: $c, total: $e, positions_count: $n}'
    ;;
  *)
    die "unknown verb: $verb"
    ;;
esac
