# Glossary — 24x7-trader

Shared vocabulary across all phases. Injected as prelude.

## Position concepts

- **Position** — an open holding of a single ticker, with entry price,
  size, current stop, and a thesis reference.
- **Thesis** — the written argument that justifies a position. Lives
  with the position record. Three falsifiable claims minimum.
- **Conviction tier** — HIGH / MEDIUM / LOW / NO-GO. Governs sizing.
- **Stop level** — price at which the position is cut. Initial stop is
  set at market-open; trailing stops are tightened mid-day on winners.
- **Drawdown** — peak-to-current decline of total portfolio value.

## Risk envelope terms

- **Per-position cap** — max % of portfolio in a single ticker.
- **Sector cap** — max % of portfolio in a single sector.
- **Thesis-family cap** — max number of simultaneous positions in
  the same thesis family.
- **Daily loss cap** — max single-day P&L loss before the agent halts
  new entries (existing positions still managed).
- **Max open positions** — total position count cap.
- **Cash floor** — minimum cash %; below this, no new entries.

## Phase terms

- **Pre-market phase** — research and proposal generation. Runs ~3
  hours before open. Does not place orders.
- **Market-open phase** — adversarial review and execution of
  proposals. Runs at or shortly after market open.
- **Midday phase** — position management. Runs near midday. Cuts
  losers exceeding stops; tightens stops on winners.
- **Market-close phase** — daily P&L computation, journal, notify.
- **Weekly review phase** — Friday post-mortem with strategy
  refinement proposals.

## Artifact terms

- **Proposal slate** — the list of proposed actions produced by
  pre-market and consumed by market-open. Each proposal has ticker,
  side (BUY/SELL/HOLD), tier, target size, suggested stop, and
  rationale.
- **Trade log** — append-only JSONL record of every order, fill,
  and stop adjustment. Source of truth for back-analysis.
- **Research log** — markdown notes per day, indexed by date.
- **Journal** — the agent's reflective record per day. Includes
  surprises, reads that proved wrong, and lessons.
- **Learnings** — operator-curated, append-only insights that
  survive across weeks. Pattern: WHEN-THEN-BECAUSE.
- **Benchmarks** — daily portfolio value and SPY close, CSV.
- **PENDING-COMPOSER-REVIEW.md** — the file-based fermata. If this
  file exists, ALL execution phases halt at stage 1 and notify the
  operator. Created by weekly-review when thresholds breach.

## Composition vocabulary (Marianne)

- **Score** — a YAML configuration. Each phase is one score.
- **Sheet** — one execution unit within a score.
- **Stage** / **movement** — a logical phase within a score's DAG.
- **Fan-out** — multiple parallel sheets from a single stage.
- **Prelude** — context injected into every sheet (this file is one).
- **Cadenza** — context injected into a specific sheet.
- **Instrument** — the AI backend or CLI tool a sheet runs on.
- **Technique** — a tool/skill abstraction shared across instruments.
