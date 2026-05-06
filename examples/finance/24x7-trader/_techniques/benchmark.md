# Benchmark Technique Contract

Read-only access to benchmark prices (default SPY) and to the agent's
own portfolio value computation.

## Contract

```
benchmark <verb> [args...]
```

| Verb | Args | Stdout | Exit |
|---|---|---|---|
| `spy_close` | `[--date YYYY-MM-DD]` | `{ticker: "SPY", close, date}` JSON | 0 |
| `spy_history` | `--from YYYY-MM-DD --to YYYY-MM-DD` | CSV `date,close` rows | 0 |
| `portfolio_value` | `--workspace <PATH>` | `{date, equity, cash, total, positions_count}` JSON | 0 |

`portfolio_value` reads the workspace's `positions.json` (snapshotted by
the latest phase) and the broker's current quote for each ticker.

## Configuration

```
BENCHMARK_CMD          — absolute path
BENCHMARK_TICKER       — defaults to SPY; operator may set to QQQ, VTI, etc.
```

## Replacing the implementation

The reference `_scripts/benchmark_spy.sh` calls the broker's `get_quote`
verb for SPY (since the broker already has data access). A
network-only implementation could fall back to a free API.
