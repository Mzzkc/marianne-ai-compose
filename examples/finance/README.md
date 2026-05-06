# Finance Examples

Score families demonstrating Marianne's coordination patterns applied to
financial workflows.

## Families

### 24x7-trader

A flagship six-score family operating an autonomous, long-term/swing-trading
agent through the trading day. Demonstrates pattern-per-phase fidelity,
deterministic risk-envelope enforcement, adversarial pre-trade review,
and two-round Delphi weekly retros.

- See `24x7-trader/README.md` for setup, customization, and safety
  architecture.
- The family is **paper-safe by default**. Live trading requires a
  two-key configuration documented in the family's README.
- Vendor-neutral via four technique contracts: `broker`, `news_research`,
  `notify`, `benchmark`. Reference scripts use Alpaca paper / Perplexity /
  stdout / SPY but all are swappable.

Patterns applied across the family:
Source Triangulation, Sugya Weave, Red Team / Blue Team, Triage Gate,
Tool Chain, After-Action Review, Delphi Convergence (2 rounds),
Quorum Trigger, Andon Cord, Reconnaissance Pull, Decision Propagation.

## Disclaimer

These examples are educational — they show how to compose multi-agent
coordination patterns in a high-stakes domain. They are **not financial
advice** and **not turnkey trading bots**. The operator is responsible
for understanding every script, validating the strategy against their
own capital posture, and supervising the first live deployments.
