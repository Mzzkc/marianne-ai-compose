# Broker Technique Contract

A "broker" is anything the agent can call to interact with a brokerage.
The reference implementation in `_scripts/broker_paper.sh` uses Alpaca's
paper-trading API. You can swap it for IBKR, Tradier, Schwab, or a
mocked file-based broker for testing — the scores call the contract,
not the implementation.

## Contract

The broker is a CLI tool invoked as:

```
broker <verb> [args...]
```

It must support the following verbs and exit codes. **Stdout MUST be
JSON** for all queries; orders return JSON with at least `{status, id, ...}`.

### Required verbs

| Verb | Args | Stdout JSON | Exit |
|---|---|---|---|
| `auth_check` | (none) | `{authenticated: bool, mode: "paper"|"live", account_id: str}` | 0 if ok |
| `get_balance` | (none) | `{cash, equity, buying_power, currency}` | 0 |
| `get_positions` | (none) | `[{ticker, qty, avg_entry, current_price, market_value, unrealized_pnl, side}]` | 0 |
| `get_quote` | `<TICKER>` | `{ticker, bid, ask, last, volume, ts}` | 0 |
| `place_order` | `<TICKER> <BUY|SELL> <QTY> <market|limit> [LIMIT_PRICE]` | `{status, id, ticker, side, qty, type}` | 0 if accepted |
| `set_stop` | `<TICKER> <STOP_PRICE>` | `{status, ticker, stop}` | 0 |
| `cancel_stop` | `<TICKER>` | `{status, ticker}` | 0 |
| `list_orders` | `[--status open|filled|cancelled]` | `[{id, ticker, side, qty, status, ts}]` | 0 |

### Two-key live trading safety

`place_order` MUST refuse to place a real order unless BOTH conditions hold:

1. Environment variable `BROKER_LIVE=1` is set.
2. File `<workspace>/LIVE_TRADING_ACKNOWLEDGED` exists.

If either is missing, the broker either runs in paper mode (preferred)
or exits non-zero with a clear message. The reference paper broker is
hard-coded to paper and ignores `BROKER_LIVE` even if set.

### Idempotency

`place_order` must accept an optional `--client-order-id <ID>` flag.
The scores pass a deterministic ID derived from the workspace + date
+ ticker + side. The broker must reject duplicate IDs to prevent
double-fills if a sheet retries.

### Configuration

Credentials come from environment variables only. Never read from the
workspace, never from arguments. Reference variables for the paper
broker:

- `ALPACA_KEY`
- `ALPACA_SECRET`
- `ALPACA_BASE_URL` (defaults to paper endpoint)

The score never references credential variables directly — only the
broker script does.

## Replacing the implementation

To use a different brokerage, write a script that satisfies the
contract above, place it at `_scripts/broker_<vendor>.sh`, and set
`BROKER_CMD` in your environment to its absolute path. The scores
invoke `${BROKER_CMD:-_scripts/broker_paper.sh}`.
