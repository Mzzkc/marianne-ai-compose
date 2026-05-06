#!/usr/bin/env bash
# broker_paper.sh — reference broker implementation, hard-locked to PAPER mode.
#
# Implements the broker contract from _techniques/broker.md using Alpaca's
# paper-trading API. This script HARD-CODES the paper endpoint — there is
# no environment variable override. To trade live, write a separate broker
# script and point BROKER_CMD at it. (See _techniques/broker.md.)
#
# Required env: ALPACA_KEY, ALPACA_SECRET

set -euo pipefail

# Hard-coded paper endpoints. No env override — that's the paper-lock.
TRADING_BASE="https://paper-api.alpaca.markets"     # account, orders, positions
DATA_BASE="https://data.alpaca.markets"             # market data (quotes, bars)

KEY="${ALPACA_KEY:-}"
SECRET="${ALPACA_SECRET:-}"

die() { echo "broker_paper: $*" >&2; exit 1; }

[[ -n "$KEY" && -n "$SECRET" ]] || die "ALPACA_KEY and ALPACA_SECRET required"

# Reject any leaked override attempt
if [[ -n "${ALPACA_BASE_URL:-}" ]]; then
  case "$ALPACA_BASE_URL" in
    *paper-api.alpaca.markets*) : ;;  # paper variant is fine
    *) die "ALPACA_BASE_URL override is forbidden in broker_paper.sh (got: $ALPACA_BASE_URL). For live trading write a separate broker per _techniques/broker.md." ;;
  esac
fi

api() {
  local method="$1" base="$2" path="$3"
  shift 3
  curl -fsS -X "$method" \
    -H "APCA-API-KEY-ID: $KEY" \
    -H "APCA-API-SECRET-KEY: $SECRET" \
    -H "Content-Type: application/json" \
    "${base}${path}" "$@"
}

verb="${1:-}"
shift || true

case "$verb" in
  auth_check)
    acct=$(api GET "$TRADING_BASE" /v2/account)
    echo "{\"authenticated\": true, \"mode\": \"paper\", \"account_id\": $(jq -r .id <<<"$acct" | jq -R .)}"
    ;;
  get_balance)
    acct=$(api GET "$TRADING_BASE" /v2/account)
    # Alpaca's `equity` already includes cash + long_market_value + short_market_value.
    # The score family treats `equity` as portfolio total; cash is broken out for
    # the cash-floor check.
    jq '{cash: (.cash | tonumber), equity: (.equity | tonumber), buying_power: (.buying_power | tonumber), currency: .currency}' <<<"$acct"
    ;;
  get_positions)
    api GET "$TRADING_BASE" /v2/positions \
      | jq '[.[] | {ticker: .symbol, qty: (.qty | tonumber), avg_entry: (.avg_entry_price | tonumber), current_price: (.current_price | tonumber), market_value: (.market_value | tonumber), unrealized_pnl: (.unrealized_pl | tonumber), side: .side}]'
    ;;
  get_quote)
    ticker="${1:?ticker required}"
    # Market data lives on the DATA api, not the trading api.
    api GET "$DATA_BASE" "/v2/stocks/${ticker}/quotes/latest" \
      | jq --arg t "$ticker" '{ticker: $t, bid: .quote.bp, ask: .quote.ap, last: .quote.ap, volume: .quote.bs, ts: .quote.t}'
    ;;
  place_order)
    # Required positional args
    ticker="${1:?ticker}"; side="${2:?side}"; qty="${3:?qty}"; type="${4:-market}"
    shift 4 || true
    limit=""
    client_id=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --client-order-id) client_id="$2"; shift 2 ;;
        --limit-price)     limit="$2"; shift 2 ;;
        *)
          # Backwards-compat: 5th positional arg was historically limit price
          if [[ -z "$limit" ]]; then limit="$1"; shift; else shift; fi
          ;;
      esac
    done

    side_lower=$(echo "$side" | tr '[:upper:]' '[:lower:]')

    # Build payload, including optional client_order_id and limit_price
    payload=$(jq -n \
      --arg sym "$ticker" --arg side "$side_lower" --arg qty "$qty" \
      --arg type "$type" --arg limit "$limit" --arg cid "$client_id" \
      '
      {symbol:$sym, qty:($qty|tonumber), side:$side, type:$type, time_in_force:"day"}
      + (if $type == "limit" and $limit != "" then {limit_price:($limit|tonumber)} else {} end)
      + (if $cid != "" then {client_order_id:$cid} else {} end)
      ')

    # Alpaca rejects duplicate client_order_id with HTTP 422 — that's idempotency by design.
    api POST "$TRADING_BASE" /v2/orders -d "$payload" \
      | jq '{status: .status, id: .id, ticker: .symbol, side: .side, qty: (.qty | tonumber), type: .type, client_order_id: (.client_order_id // null)}'
    ;;
  set_stop)
    ticker="${1:?ticker}"; stop="${2:?stop}"
    pos=$(api GET "$TRADING_BASE" "/v2/positions/${ticker}")
    qty=$(jq -r .qty <<<"$pos")
    payload=$(jq -n --arg sym "$ticker" --arg qty "$qty" --arg stop "$stop" \
      '{symbol:$sym, qty:($qty|tonumber), side:"sell", type:"stop", stop_price:($stop|tonumber), time_in_force:"gtc"}')
    api POST "$TRADING_BASE" /v2/orders -d "$payload" \
      | jq --arg t "$ticker" --arg s "$stop" '{status: .status, ticker: $t, stop: ($s | tonumber)}'
    ;;
  cancel_stop)
    ticker="${1:?ticker}"
    api GET "$TRADING_BASE" /v2/orders?status=open \
      | jq --arg t "$ticker" '.[] | select(.symbol==$t and .type=="stop") | .id' \
      | while read -r id; do
          api DELETE "$TRADING_BASE" "/v2/orders/${id//\"/}" >/dev/null || true
        done
    echo "{\"status\": \"cancelled\", \"ticker\": \"${ticker}\"}"
    ;;
  list_orders)
    status="open"
    if [[ "${1:-}" == "--status" ]]; then status="$2"; fi
    api GET "$TRADING_BASE" "/v2/orders?status=${status}" \
      | jq '[.[] | {id: .id, ticker: .symbol, side: .side, qty: (.qty | tonumber), status: .status, client_order_id: (.client_order_id // null), ts: .created_at}]'
    ;;
  *)
    die "unknown verb: $verb"
    ;;
esac
