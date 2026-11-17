# 24x7-trader — A Marianne flagship for autonomous trading

A family of six Marianne scores that operates a long-term, fundamentals-driven
swing-trading agent through the trading day. Demonstrates pattern-per-phase
fidelity, vendor-neutral techniques, and machine-checked risk discipline.

> **Paper-mode by default. Real money requires deliberate two-key configuration.**
> This is a flagship example — not a financial product. Read every script
> before you point real capital at it.
>
> **READ [DISCLAIMER.md](DISCLAIMER.md) BEFORE RUNNING. This software is
> provided AS IS, without warranty of any kind. The authors and contributors
> bear no liability for any losses, damages, or consequences arising from
> use of this software. By running it, you accept all risk.**

## What it does, briefly

Five scheduled phases each weekday plus a Friday post-mortem. Each phase
implements coordination patterns that match its work:

| Phase | When | Patterns | What it does |
|---|---|---|---|
| `bootstrap.yaml` | Once, manually | Reconnaissance Pull + Decision Propagation | Codifies your strategy and risk envelope |
| `pre-market.yaml` | 06:00 Mon–Fri | Source Triangulation + Sugya Weave + Tool Chain | Three independent analytical frames produce a triangulated proposal slate; deterministic envelope check refuses violations |
| `market-open.yaml` | 09:30 Mon–Fri | Red Team / Blue Team + Tool Chain + Andon Cord | Adversarial review of each proposal; second envelope check; broker execution with diagnostic failure handling |
| `midday.yaml` | 12:00 Mon–Fri | Triage Gate + Read-and-React + Tool Chain | Classify positions; cut losers exceeding stops; tighten stops on winners |
| `market-close.yaml` | 16:05 Mon–Fri | After-Action Review + Tool Chain | Deterministic P&L vs SPY; interpretive journal; benchmark append; notify |
| `weekly-review.yaml` | 16:30 Friday | Delphi Convergence (2 rounds) + Quorum Trigger + Sugya Weave | Three reviewers from different model families produce a consensus; deterministic threshold check writes the operator-fermata file when action is required |

The improvements over single-agent autonomous trading you may see in
tutorials elsewhere: structured disagreement before any trade is placed,
deterministic envelope enforcement (not vibes), file-based fermata for
threshold breaches, multi-provider instrument fallbacks, vendor-neutral
brokerage and research interfaces.

## Directory layout

```
examples/finance/24x7-trader/
├── README.md                          ← you are here
├── _libretto/                         ← shared spec corpus, injected as preludes
│   ├── trading-philosophy.md
│   ├── glossary.md
│   ├── risk-envelope.schema.md
│   └── operator-charter.md            ← EDIT THIS before running bootstrap
├── _techniques/                       ← pluggable contracts (broker, news, notify, benchmark)
├── _scripts/                          ← reference implementations (paper-safe defaults)
│   ├── broker_paper.sh                ← Alpaca paper, hard-locked to paper mode
│   ├── broker_mock.py                 ← file-backed mock broker, offline (see "Running offline")
│   ├── news_perplexity.sh             ← Perplexity Sonar
│   ├── notify_stdout.sh               ← stdout (default safe)
│   ├── benchmark_spy.sh               ← portfolio + SPY math
│   └── check_envelope.sh              ← deterministic envelope enforcement
├── _scheduler/
│   ├── conductor-snippet.yaml         ← Feature-19 scheduler block (canonical)
│   └── crontab.example                ← external cron fallback
├── _design/composition-worksheet.md   ← composition methodology trace
├── bootstrap.yaml
├── pre-market.yaml
├── market-open.yaml
├── midday.yaml
├── market-close.yaml
└── weekly-review.yaml
```

The `workspaces/24x7-trader/` directory (created on first run) is the agent's
persistent memory. It lives outside the score family and is gitignored.

## Setup

### 1. Edit your charter

Open `_libretto/operator-charter.md` and fill in the bracketed placeholders.
This is the single most important step. The charter overrides the defaults
in the philosophy file. Do not skip the **What I'm trying to learn** section
or the **Hard rules I'm imposing on the agent** section — they shape every
phase's reasoning.

### 2. System dependencies

The reference scripts require:

- `python3` (3.8+) with `pyyaml` (`pip install pyyaml`)
- `jq` (JSON processing)
- `curl` (HTTP)
- `bash` 4+

Without these, the scripts fail with cryptic errors. Verify before
proceeding:

```bash
python3 -c "import yaml; print(yaml.__version__)"   # PyYAML present
jq --version
curl --version
```

### 3. Configure environment

The score family is **strict**: the daily phases require six environment
variables to be set to absolute paths. This avoids the working-directory
bugs that come from `$PWD`-relative paths under cron.

```bash
# In your shell profile or a secrets file sourced by your scheduler:

# Required: absolute paths to the technique-implementing scripts
export BROKER_CMD=/abs/path/to/marianne-ai-compose/examples/finance/24x7-trader/_scripts/broker_paper.sh
export NEWS_CMD=/abs/path/to/marianne-ai-compose/examples/finance/24x7-trader/_scripts/news_perplexity.sh
export NOTIFY_CMD=/abs/path/to/marianne-ai-compose/examples/finance/24x7-trader/_scripts/notify_stdout.sh
export BENCHMARK_CMD=/abs/path/to/marianne-ai-compose/examples/finance/24x7-trader/_scripts/benchmark_spy.sh
export CHECK_ENVELOPE_CMD=/abs/path/to/marianne-ai-compose/examples/finance/24x7-trader/_scripts/check_envelope.sh
export EXECUTE_SLATE_CMD=/abs/path/to/marianne-ai-compose/examples/finance/24x7-trader/_scripts/execute_slate.sh

# Required for the reference broker (Alpaca paper)
export ALPACA_KEY=...
export ALPACA_SECRET=...

# Required for the reference news/research (Perplexity)
export PERPLEXITY_KEY=...

# Optional: respected by notify_stdout.sh (no notifications during quiet hours unless 'urgent')
# export OPERATOR_QUIET_HOURS="22-07"
```

To swap implementations: write your own script implementing the relevant
contract (`_techniques/<name>.md`), then point the corresponding
`*_CMD` env var at it. The scores never reference vendors directly.

### 4. Run bootstrap once

```bash
mzt validate examples/finance/24x7-trader/bootstrap.yaml
mzt run examples/finance/24x7-trader/bootstrap.yaml
```

This reads your charter, codifies it into `<workspace>/02-strategy.md`,
emits the machine-readable `risk-envelope.yaml`, takes an initial position
snapshot, and creates the directory structure. Bootstrap **fails** if
critical placeholders in `_libretto/operator-charter.md` are still unfilled
— this is intentional, to prevent running with placeholder strategy text.
Re-run only when you change your charter substantially.

### 5. Schedule the daily phases

**Preferred (Feature 19, when shipped):** add the contents of
`_scheduler/conductor-snippet.yaml` to your `conductor.yaml` and reload.

**Today's fallback:** install `_scheduler/crontab.example` after
substituting your absolute paths, or write a systemd-timer set.

### Running offline (no brokerage account)

`_scripts/broker_mock.py` implements the whole broker contract with no network
and no credentials. Cash, positions and orders live in a JSON file; market
orders fill at a price from a fixtures file, so a run is reproducible.

```bash
cd examples/finance/24x7-trader          # the paths below are absolute
export BROKER_CMD=$PWD/_scripts/broker_mock.py
export NOTIFY_CMD=$PWD/_scripts/notify_stdout.sh
export BENCHMARK_CMD=$PWD/_scripts/benchmark_spy.sh      # reads SPY via the mock's get_quote
export CHECK_ENVELOPE_CMD=$PWD/_scripts/check_envelope.sh
export EXECUTE_SLATE_CMD=$PWD/_scripts/execute_slate.sh
export MOCK_BROKER_PRICES=/abs/path/to/prices.json       # optional, see below
# no ALPACA_*, no PERPLEXITY_KEY
```

| Variable | Default | Meaning |
|---|---|---|
| `MOCK_BROKER_STATE` | `<repo>/workspaces/24x7-trader/mock-broker` (the score workspace) | Absolute path of the directory holding `state.json`. Delete it to reset the account. |
| `MOCK_BROKER_PRICES` | built-in table (SPY 500, AAPL 200, MSFT 420, NVDA 130, GOOGL 170, AMZN 190, META 520, TSLA 250, JPM 210, XOM 115, JNJ 160, KO 62) | JSON file, ticker to price: `{"AAPL": 201.5, "SPY": {"last": 502, "bid": 501.9, "ask": 502.1, "volume": 1000}}`. A ticker not in the fixture is refused (`get_quote`, `place_order`). |
| `MOCK_BROKER_CASH` | `100000` | Starting cash, read when `state.json` is first created. |
| `MOCK_BROKER_NOW` | wall clock (UTC) | Pins the `ts` fields for reproducible output. |

Behaviour worth knowing: a market order, and a limit order the fixture price
already satisfies, fills in the call that places it. Resting limit orders and
stops are re-checked against the fixture at the start of every call, so
editing the prices file between phases moves the market. The mock is
paper-only (`BROKER_LIVE` is ignored), long-only, has no margin, and refuses
duplicate `--client-order-id`s. Its prices are illustrative round numbers, not
market data; a fixed SPY price makes the daily benchmark change zero unless
you edit the fixture.

What else the suite needs, and whether it has an offline default:

| Variable | Offline? |
|---|---|
| `NOTIFY_CMD` | Yes: `_scripts/notify_stdout.sh` prints to stdout. Required by market-close and weekly-review; optional in pre-market, market-open and midday. |
| `BENCHMARK_CMD`, `CHECK_ENVELOPE_CMD`, `EXECUTE_SLATE_CMD` | Yes: local scripts (`jq`, `python3` with PyYAML). |
| `NEWS_CMD` | **No offline default, a gap.** Pre-market requires it and the only shipped implementation is `news_perplexity.sh`. Point it at your own script satisfying `_techniques/news_research.md` (markdown on stdout, starting with `**[OFFLINE — training data only]**` when no web call was made). |

This makes the scripts and the broker calls testable without an account. It
does not make the scores run offline: every sheet that reasons still needs the
model instruments the scores name (`claude-code`, Gemini, GLM).
`mzt validate` needs none of the above.

## Safety architecture

### Paper-by-default

The reference broker `_scripts/broker_paper.sh` is hard-locked to Alpaca's
paper API. To trade real money, you must:

1. Write a live broker script implementing the contract in
   `_techniques/broker.md`. The contract requires the broker to refuse
   live trades unless **both** `BROKER_LIVE=1` is set in the environment
   **and** the file `<workspace>/LIVE_TRADING_ACKNOWLEDGED` exists.
2. Point `BROKER_CMD` at it.
3. Acknowledge live mode by creating that file with content the operator
   has consciously typed (e.g., the date and an "I understand"). The file's
   existence is the second key.

### Risk envelope as a wall

Every BUY proposal must pass a deterministic check:
`_scripts/check_envelope.sh` runs against the proposed slate **twice** —
once after pre-market produces it, again after market-open's adversarial
arbitration narrows it. If either check fails, the score fails. The agent
does not get to argue with the math. To loosen a cap, edit
`risk-envelope.yaml`; that is an operator action.

### File-based fermata

The file `<workspace>/proposals/PENDING-COMPOSER-REVIEW.md` is the
agent's halt switch. If it exists, the first stage of every execution
phase (pre-market, market-open, midday, market-close, weekly-review)
exits non-zero with a clear notification. The weekly review writes this
file when:

- Weekly drawdown exceeds the envelope's halt threshold
- The synthesis verdict is BREACHED
- Strategy refinements are proposed (any change to strategy.md or
  risk-envelope.yaml)

The operator unblocks by reviewing the file's contents, editing the
relevant strategy/envelope as appropriate, and deleting the file.

### Multi-provider fallbacks

Every score declares `instrument_fallbacks` so a 9:30am rate limit on the
primary instrument doesn't crash the open. Weekly review runs across three
distinct model families intentionally — correlated families share blind
spots.

## Customization paths

### Different brokerage

Implement `_techniques/broker.md` and set `BROKER_CMD`. The contract is
intentionally minimal — eight verbs, JSON in/out. `_scripts/broker_mock.py`
is a file-backed one for offline testing; read it as a second reference.

### Different news/research source

Implement `_techniques/news_research.md` and set `NEWS_CMD`. The reference
uses Perplexity but the contract makes no assumption about the underlying
provider; Tavily, Exa, Brave, or Claude Code's WebSearch all fit.

### Different notify channel

Implement `_techniques/notify.md` and set `NOTIFY_CMD`. The reference
prints to stdout (safe default for log-capture cron). A Slack webhook
implementation is ten lines.

### Different reviewers in weekly-review

The three reviewers in `weekly-review.yaml` are wired to specific
instrument profiles (`reviewer-opus`, `reviewer-gemini`, `reviewer-glm`).
If a profile isn't available locally, edit the score's `instruments:`
block to substitute. The patterns (Delphi 2-round, Sugya synthesis) are
stable; the specific models are not.

### Different cron times

`_scheduler/conductor-snippet.yaml` is canonical. Adjust the cron
expressions to match your trading day. Note that pre-market needs to
produce a slate before market-open consumes it — keep the order
intact.

## Known limitations

Read these before you trust the family with capital, paper or otherwise:

- **No market-holiday awareness.** The cron schedules fire on every
  weekday. On US market holidays (MLK, Presidents', Good Friday, Memorial,
  Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas), the
  broker will reject orders or return stale data. Suppress cron via your
  scheduler on these dates, or extend the family with a holiday check
  (a small script reading a calendar file in the workspace would suffice).
- **No half-day awareness.** Day-after-Thanksgiving and Christmas Eve
  close at 1pm; market-close at 16:05 fires too late.
- **No catch-up for missed phases.** If the conductor is down at 09:30,
  market-open does not retroactively fire when the conductor returns.
  The pre-market slate sits unconsumed.
- **No workspace file locking.** Two phases firing close together (e.g.,
  pre-market overrunning into market-open) can race on `positions.json`
  and `today-date.txt`. The cron schedule provides hour-scale separation;
  any phase that takes longer is operator-supervised.
- **Resume semantics under LLM non-determinism.** A failed pre-market
  re-run may regenerate frame files with different content than the
  previous attempt. The structural artifacts (slate JSON) cross-check
  during re-runs but the prose evidence may diverge.
- **The mock broker is a fixture, not a market.** `_scripts/broker_mock.py`
  fills at the prices you give it, with no spread, slippage, partial fills,
  margin or market hours. Results from it say the plumbing works, nothing
  about the strategy. See [Running offline](#running-offline-no-brokerage-account).
- **No offline news source.** `NEWS_CMD` has only the Perplexity
  implementation; offline runs must supply their own.
- **Trust assumption: agents don't tamper with the envelope.** The score
  family verifies `risk-envelope.yaml` hash at every gate to detect
  mid-run modification, but the agent retains write tools. If you
  observe envelope tampering in logs, file an issue.

## Failure modes worth knowing about

| If you see... | It probably means... | What to do |
|---|---|---|
| Pre-market fails at envelope check | The agent proposed a slate that violates a cap | Read the check's stderr — it lists each violation. Either widen the envelope (operator decision) or accept that today's slate is inappropriate. |
| Market-open completes with empty trade-log | Either pre-market produced an empty slate, or arbitration rejected everything | Read `proposals/today-arbitration.md`. Empty slates are valid outputs. |
| Phase fails with FERMATA error | `PENDING-COMPOSER-REVIEW.md` exists | Read it, take the operator action it requests, delete the file. |
| Phase hangs on broker call | Broker timing out or auth wrong | Check `today-broker-auth.json` and `today-open-failures.md`. Don't blind-retry. |
| Weekly review writes fermata for "drawdown breach" | Portfolio drawdown exceeded envelope's halt threshold | This is the safety system working. Read the synthesis. Decide whether the strategy needs adjustment or the drawdown is recoverable; act accordingly. |

## What this score family is NOT

- **Not a turnkey trading bot.** It is a flagship Marianne example. The
  operator must edit the charter, configure credentials, schedule the
  phases, and watch the first few weeks of runs.
- **Not financial advice.** Backtesting these patterns against your own
  capital is necessary before any live deployment.
- **Not safe to run live without reading every script.** The deterministic
  envelope check is the wall, but the wall is only as good as the schema
  the operator wrote.

## Running individual phases manually

For testing, you can run any score directly:

```bash
mzt validate examples/finance/24x7-trader/pre-market.yaml
mzt run examples/finance/24x7-trader/pre-market.yaml
mzt status 24x7-trader-pre-market --watch

mzt validate examples/finance/24x7-trader/market-open.yaml
mzt validate examples/finance/24x7-trader/weekly-review.yaml
```

The workspace persists between phases — running pre-market then
market-open in sequence simulates a real trading day's morning.

## See also

- The composition worksheet at `_design/composition-worksheet.md` —
  shows the pattern selection rationale per phase.
- The Marianne Rosetta corpus at `scores/rosetta-corpus/` — the patterns
  this score family implements (Source Triangulation, Sugya Weave, Red
  Team / Blue Team, Tool Chain, Triage Gate, After-Action Review,
  Delphi Convergence, Quorum Trigger).
- The Feature 19 spec at `docs/plans/strategic/2026-02-14-roadmap-features.md`
  for the `scheduler:` block this family expects.
