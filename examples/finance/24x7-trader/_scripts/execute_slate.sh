#!/usr/bin/env bash
# execute_slate.sh — deterministic order execution from a final slate.
#
# Reads a slate JSON file and places orders via the broker. NO LLM in
# this path — pure scripted iteration. This is the wall between the
# adversarial-review stage and the broker.
#
# Each order is placed with the deterministic client_order_id from the
# slate (Alpaca rejects duplicates, giving us idempotency on retry).
#
# Usage:
#   execute_slate.sh <workspace> <final-slate.json> <results.jsonl> <failures.md>
#
# Always touches the failures file (empty if no failures, populated if any).
# Appends one JSON line to results.jsonl per order attempt (success or failure).
# Appends successful broker responses to <workspace>/trade-log.jsonl.

set -euo pipefail

ws="${1:?workspace required}"
slate="${2:?slate required}"
results="${3:?results.jsonl path required}"
failures="${4:?failures.md path required}"

BROKER="${BROKER_CMD:?BROKER_CMD must be an absolute path}"
[[ -x "$BROKER" ]] || { echo "BROKER_CMD not executable: $BROKER" >&2; exit 1; }

[[ -f "$slate" ]] || { echo "slate file not found: $slate" >&2; exit 1; }

# Always ensure the result/failure files exist (even if empty)
: > "$results"
: > "$failures"

today_ts=$(date -Iseconds)

# Iterate the slate (POSIX-safe — no LLM)
proposal_count=$(jq 'length' "$slate")
ok_count=0
fail_count=0
stop_set_count=0

for i in $(seq 0 $((proposal_count - 1))); do
  prop=$(jq ".[$i]" "$slate")
  client_id=$(jq -r '.client_order_id' <<<"$prop")
  ticker=$(jq -r '.ticker' <<<"$prop")
  side=$(jq -r '.side' <<<"$prop")
  qty=$(jq -r '.target_qty // 0' <<<"$prop")
  order_type=$(jq -r '.order_type // "market"' <<<"$prop")
  limit=$(jq -r '.limit_price // ""' <<<"$prop")
  stop=$(jq -r '.initial_stop_price // ""' <<<"$prop")

  # Build the place_order command
  cmd=("$BROKER" place_order "$ticker" "$side" "$qty" "$order_type")
  [[ -n "$limit" && "$limit" != "null" ]] && cmd+=(--limit-price "$limit")
  [[ -n "$client_id" && "$client_id" != "null" ]] && cmd+=(--client-order-id "$client_id")

  if order_resp=$("${cmd[@]}" 2>"${results}.err"); then
    # Success — append to results and trade-log
    line=$(jq -nc \
      --arg ts "$today_ts" --argjson prop "$prop" --argjson resp "$order_resp" \
      --arg result "ok" \
      '{ts: $ts, result: $result, proposal: $prop, broker_response: $resp}')
    echo "$line" >> "$results"
    echo "$line" >> "$ws/trade-log.jsonl"
    ok_count=$((ok_count + 1))

    # If a stop is requested AND the order was a BUY, set it
    if [[ "$side" == "BUY" || "$side" == "buy" ]] && [[ -n "$stop" && "$stop" != "null" ]]; then
      if stop_resp=$("$BROKER" set_stop "$ticker" "$stop" 2>>"${results}.err"); then
        stop_line=$(jq -nc \
          --arg ts "$today_ts" --arg t "$ticker" --argjson stop_resp "$stop_resp" \
          '{ts: $ts, result: "stop_set", ticker: $t, broker_response: $stop_resp}')
        echo "$stop_line" >> "$results"
        echo "$stop_line" >> "$ws/trade-log.jsonl"
        stop_set_count=$((stop_set_count + 1))
      else
        # Stop failed, but order succeeded. Log to failures but keep going.
        printf '## %s — stop set failed (order %s placed)\n\n```\n%s\n```\n\n' \
          "$ticker" "$client_id" "$(cat "${results}.err")" >> "$failures"
      fi
    fi
  else
    # Order failed — diagnose, log, continue (Andon Cord)
    err=$(cat "${results}.err")
    line=$(jq -nc \
      --arg ts "$today_ts" --argjson prop "$prop" --arg err "$err" \
      --arg result "fail" \
      '{ts: $ts, result: $result, proposal: $prop, error: $err}')
    echo "$line" >> "$results"
    printf '## %s %s — order failed\n\n**Client order id:** %s\n\n```\n%s\n```\n\n' \
      "$side" "$ticker" "$client_id" "$err" >> "$failures"
    fail_count=$((fail_count + 1))
  fi
done

rm -f "${results}.err"

# Refresh positions snapshot post-execution
"$BROKER" get_positions > "$ws/positions.json" || true

echo "execute_slate: ${ok_count} placed, ${stop_set_count} stops set, ${fail_count} failed"

# Exit non-zero only on TOTAL failure (no orders placed AND non-empty slate)
if [[ "$proposal_count" -gt 0 && "$ok_count" -eq 0 ]]; then
  exit 1
fi
