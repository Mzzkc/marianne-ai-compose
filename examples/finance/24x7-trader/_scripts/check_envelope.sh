#!/usr/bin/env bash
# check_envelope.sh — deterministic risk-envelope enforcement.
#
# Reads <workspace>/risk-envelope.yaml and a proposals JSON file.
# Reads cash + equity from a balance JSON file (NOT the envelope — the
# envelope is operator-edited and never holds runtime state).
#
# Exits non-zero if any proposal violates any cap. Prints violations
# to stderr. This is the wall between agent reasoning and the broker.
#
# Usage:
#   check_envelope.sh <workspace> <proposals.json> <balance.json>
#
# balance.json schema (produced by `broker get_balance`):
#   {cash: <number>, equity: <number>, buying_power: <number>}
# NOTE: Alpaca's `equity` already includes cash + long_market_value;
#       we use `equity` as the portfolio total, NOT `equity + cash`.

set -euo pipefail

ws="${1:?workspace required}"
proposals="${2:?proposals.json required}"
balance="${3:?balance.json required (pass output of: broker get_balance)}"

[[ -f "$ws/risk-envelope.yaml" ]] || { echo "envelope missing: $ws/risk-envelope.yaml" >&2; exit 2; }
[[ -f "$proposals" ]]              || { echo "proposals missing: $proposals" >&2; exit 2; }
[[ -f "$balance" ]]                || { echo "balance missing: $balance" >&2; exit 2; }
[[ -f "$ws/positions.json" ]]      || { echo "positions snapshot missing: $ws/positions.json" >&2; exit 2; }

# Parse envelope using yq if present, else python+pyyaml
if command -v yq >/dev/null 2>&1; then
  envelope_json=$(yq -o=json '.' "$ws/risk-envelope.yaml")
else
  envelope_json=$(python3 -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))' "$ws/risk-envelope.yaml")
fi

python3 - "$envelope_json" "$proposals" "$ws/positions.json" "$balance" <<'PY'
import json, sys
from collections import Counter

env = json.loads(sys.argv[1])
proposals = json.load(open(sys.argv[2]))
positions = json.load(open(sys.argv[3]))
balance = json.load(open(sys.argv[4]))

caps = env.get("caps", {})
forbidden = env.get("forbidden", {})
allowed = env.get("allowed", {})
families = set(env.get("thesis_families", []))
overrides = env.get("operator_overrides", {})
blocklist = set(overrides.get("blocklist", []) or [])

# Alpaca's `equity` includes cash + long_market_value (per Alpaca docs).
# Use equity as the portfolio total. Cash is broken out for the cash-floor check.
cash = float(balance.get("cash", 0.0))
total = float(balance.get("equity", 0.0))

if total <= 0:
    print(f"VIOLATION: portfolio total (equity) is {total} — cannot compute percentages", file=sys.stderr)
    sys.exit(2)

violations = []
sector_alloc = Counter()
family_count = Counter()

# Existing positions toward sector/family caps
for p in positions:
    sector_alloc[p.get("sector", "unknown")] += float(p.get("market_value", 0.0))
    fam = p.get("thesis_family", "unknown")
    family_count[fam] += 1

new_buys = 0
buy_total = 0.0
for prop in proposals:
    tkr = prop.get("ticker", "?")
    side = (prop.get("side") or "?").upper()

    # Forbidden order types (any side)
    order_type = prop.get("order_type", "market")
    if (allowed.get("order_types") and order_type not in allowed["order_types"]):
        violations.append(f"{tkr}: order_type '{order_type}' not in allowed list {allowed['order_types']}")

    # Sells must reference held positions (no shorts)
    if side == "SELL":
        held = next((p for p in positions if p.get("ticker") == tkr), None)
        if held is None:
            violations.append(f"{tkr}: SELL on ticker not held (forbidden short_sell)")
        else:
            target_qty = prop.get("target_qty")
            if target_qty is not None and float(target_qty) > float(held.get("qty", 0)):
                violations.append(f"{tkr}: SELL qty {target_qty} > held qty {held['qty']}")

    if side != "BUY":
        continue

    # BUY-side checks
    new_buys += 1

    if tkr in blocklist:
        violations.append(f"{tkr}: blocklisted by operator")

    fam = prop.get("thesis_family")
    if fam is None:
        violations.append(f"{tkr}: missing thesis_family (required)")
    elif fam not in families:
        violations.append(f"{tkr}: thesis_family '{fam}' not in declared families")

    asset = prop.get("asset_class", "equity")
    if asset in (forbidden.get("asset_classes") or []):
        violations.append(f"{tkr}: asset_class '{asset}' is forbidden")
    if (allowed.get("asset_classes") and asset not in allowed["asset_classes"]):
        violations.append(f"{tkr}: asset_class '{asset}' not in allowed list")

    # Per-position cap
    target_value = prop.get("target_value_usd")
    if target_value is None:
        violations.append(f"{tkr}: missing target_value_usd")
    else:
        target_value = float(target_value)
        buy_total += target_value
        pct = 100.0 * target_value / total
        if pct > caps.get("per_position_pct", 100):
            violations.append(f"{tkr}: target {pct:.1f}% exceeds per_position cap {caps['per_position_pct']}%")

        # target_qty * limit_price must reconcile (within 5% tolerance) with target_value_usd
        qty = prop.get("target_qty")
        limit = prop.get("limit_price") or prop.get("expected_price")
        if qty is not None and limit is not None and target_value > 0:
            implied = float(qty) * float(limit)
            drift = abs(implied - target_value) / target_value
            if drift > 0.05:
                violations.append(
                    f"{tkr}: target_qty * limit_price = {implied:.2f} disagrees with "
                    f"target_value_usd = {target_value:.2f} by {drift*100:.1f}% "
                    f"(must reconcile within 5%)"
                )

    # Sector cap (additive)
    sector = prop.get("sector", "unknown")
    if target_value is not None:
        sector_alloc[sector] += target_value
        if 100.0 * sector_alloc[sector] / total > caps.get("per_sector_pct", 100):
            violations.append(f"{tkr}: sector '{sector}' would exceed per_sector cap {caps['per_sector_pct']}%")

    # Family count
    if fam:
        family_count[fam] += 1
        if family_count[fam] > caps.get("per_thesis_family_count", 99):
            violations.append(f"{tkr}: would push family '{fam}' to {family_count[fam]} (cap {caps['per_thesis_family_count']})")

    # Initial stop depth
    initial_stop_pct = prop.get("initial_stop_pct")
    cap_stop_pct = caps.get("per_position_initial_stop_pct")
    if initial_stop_pct is not None and cap_stop_pct is not None:
        if float(initial_stop_pct) > float(cap_stop_pct):
            violations.append(
                f"{tkr}: initial_stop_pct {initial_stop_pct} exceeds cap "
                f"per_position_initial_stop_pct {cap_stop_pct}"
            )

# Max open positions
existing_open = len([p for p in positions if float(p.get("qty", 0)) != 0])
if existing_open + new_buys > caps.get("max_open_positions", 999):
    violations.append(
        f"would result in {existing_open + new_buys} open positions, "
        f"cap {caps['max_open_positions']}"
    )

# Cash floor — uses real cash from balance, subtracts BUY value
cash_after = cash - buy_total
floor_pct = caps.get("cash_floor_pct", 0)
if total > 0 and 100.0 * cash_after / total < floor_pct:
    violations.append(
        f"cash after entries ${cash_after:,.0f} = {100.0*cash_after/total:.1f}% "
        f"below floor {floor_pct}%"
    )

if violations:
    print("RISK ENVELOPE VIOLATIONS:", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    sys.exit(1)

print(
    f"envelope check OK: {len(proposals)} proposals "
    f"({new_buys} buys, ${buy_total:,.0f} total), "
    f"portfolio ${total:,.0f} ({100.0*cash/total:.1f}% cash)"
)
PY
