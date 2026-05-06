# 24x7-trader — Composition Worksheet

Per `marianne:composing` skill Phase 4. This is the structural derivation; the
YAML files are the implementation.

## Goal

A flagship family of Marianne scores that operates a long-term/swing-trading
agent through the trading day, demonstrating pattern-per-phase fidelity.
Vendor-neutral via technique contracts. Paper-safe by default. Improvement
over the source video's single-agent, vibes-validated approach.

## Force analysis

| Force | Active? | Evidence |
|---|---|---|
| Information Asymmetry | ✓ | Operator's strategy/risk envelope must reach every phase identically; pre-market output drives market-open's input. |
| Finite Resources | ✓ | Capital is bounded; per-position cap is a hard limit; midday only acts on positions that exceed thresholds. |
| Partial Failure | ✓ | Broker/news/notify APIs fail independently; one phase failing must not corrupt another phase's data. |
| Exponential Defect Cost | ✓ | A bad trade is real capital. Catching a bad proposal at pre-market costs nothing; catching it after the broker call costs money. |
| Producer-Consumer Mismatch | ✓ | Research narrative → structured proposal slate → broker order payloads. Each is a different artifact kind. |
| Instrument-Task Fit | ✓ | Research/synthesis is LLM work; risk-envelope arithmetic is shell-script work; broker calls are CLI work. |
| Convergence Imperative | partial | Weekly review needs a 2-round Delphi to escape early anchoring on one reviewer's frame. |
| Accumulated Signal | ✓ | Drawdown breach across daily closes accumulates; weekly review is where it flips behavior (file-based fermata). |
| Structured Disagreement | ✓ | Single-frame trade analysis is the video's biggest gap. Three frames pre-market; red/blue at execution; three reviewers weekly. |
| Progressive Commitment | ✓ | Paper mode default; live trading requires `BROKER_LIVE=1` env AND `LIVE_TRADING_ACKNOWLEDGED` workspace file. Two-key safety. |

## Per-score pattern selection

### bootstrap.yaml — Reconnaissance Pull + Decision Propagation

Min stages: Reconnaissance Pull = 2 (explore + plan), Decision Propagation = 2
(brief + downstream-reference). Composed: 3 stages (intent capture, charter
synthesis, schema emission). Justified merge: planning and brief-emission
share a stage because the artifact IS the brief.

| Stage | Purpose | Instrument |
|---|---|---|
| 1 | Operator intent capture (read inputs, identify gaps) | scanner (cheap) |
| 2 | Charter synthesis (philosophy, risk envelope, axioms) | synthesizer (deep reasoning) |
| 3 | Schema emission (machine-readable risk-envelope.yaml) | scanner (deterministic translation) |

### pre-market.yaml — Source Triangulation + Sugya Weave + Tool Chain

Min stages: ST = 3 (extract → investigate × N → triangulate), Sugya Weave = 1
(synthesis), Tool Chain = 1 (deterministic). Composed: 6 stages.

| Stage | Purpose | Instrument | Pattern role |
|---|---|---|---|
| 1 | State read + gap identification + day's plan | scanner | ST extract |
| 2 | Three-frame fan-out (macro, sector/peer, technical levels) | analyst (3×) | ST investigate |
| 3 | Triangulation: convergences/tensions/gaps across frames | synthesizer | ST triangulate |
| 4 | Editorial synthesis → trade proposal slate w/ rationale + conviction | synthesizer | Sugya Weave |
| 5 | Risk-envelope check (deterministic CLI on proposals.json) | cli | Tool Chain |
| 6 | Journal: research-log entry, proposals committed | scanner | bookend |

### market-open.yaml — Red Team / Blue Team + Tool Chain + Andon Cord

Min stages: RT/BT = 3 (defense → attack → arbitration). Composed: 5 stages.

| Stage | Purpose | Instrument | Pattern role |
|---|---|---|---|
| 1 | Read proposals + current positions + check fermata | scanner | setup |
| 2 | Blue Team (proposal advocate) + Red Team (skeptic) — fan-out 2 | analyst (2×) | RT/BT defense + attack |
| 3 | Arbitration: per-proposal go/no-go with tier and stop levels | synthesizer | RT/BT arbitration |
| 4 | Risk-envelope re-check after arbitration | cli | Tool Chain |
| 5 | Execute approved orders, journal results, set stops | analyst | execution + AC diagnosis |

### midday.yaml — Triage Gate + Read-and-React + Fan-out

Min stages: TG = 3 (classify → route → process). Composed: 4 stages.

| Stage | Purpose | Instrument | Pattern role |
|---|---|---|---|
| 1 | Snapshot positions, classify (cut / tighten / hold / exit) | scanner | TG classify |
| 2 | Per-action fan-out: decisions for each flagged position | analyst (N×, dynamic) | TG route + R&R |
| 3 | Risk-envelope check on midday actions | cli | Tool Chain |
| 4 | Execute, journal, push stop adjustments | analyst | execution |

Note: midday fan-out width is the count of positions classified as
non-hold. Score uses fixed-width fan-out (configurable via variable);
sheets that have nothing to do exit cleanly.

### market-close.yaml — After-Action Review + Tool Chain

Min stages: AAR = 3 (capture → reflect → record). Composed: 4 stages.

| Stage | Purpose | Instrument | Pattern role |
|---|---|---|---|
| 1 | Snapshot positions, compute P&L vs SPY (deterministic) | cli | AAR capture |
| 2 | Daily journal — interpretive reflection on the day | analyst | AAR reflect |
| 3 | Append benchmarks.csv, update learnings (if novel) | scanner | AAR record |
| 4 | Notify (technique contract: notify_close summary) | cli | bookend |

### weekly-review.yaml — Delphi Convergence + Quorum Trigger + Sugya Weave

Min stages: Delphi = 3 (independent round → exposed round → consensus).
Quorum Trigger = 1 (threshold check). Sugya Weave = 1. Composed: 6 stages.

| Stage | Purpose | Instrument | Pattern role |
|---|---|---|---|
| 1 | Aggregate week's data (trades, journals, P&L, drawdown) | cli | setup |
| 2 | Round 1 — three independent reviewers (different families) | reviewer-opus / reviewer-gemini / reviewer-glm | Delphi independent |
| 3 | Round 2 — each reviewer sees others' R1, refines | reviewer-opus / reviewer-gemini / reviewer-glm | Delphi exposed |
| 4 | Consensus synthesis with argued position | synthesizer | Sugya Weave |
| 5 | Drawdown / strategy-change threshold check (deterministic) | cli | Quorum Trigger |
| 6 | Decision: write to PENDING-COMPOSER-REVIEW.md if breach | scanner | fermata gate |

## Structural fidelity check

| Pattern | Min stages | Stages covered | Merges (justified) | Pass? |
|---|---|---|---|---|
| **bootstrap** | | | | |
| Reconnaissance Pull | 2 | 1, 2 | none | ✓ |
| Decision Propagation | 2 | 2, 3 | "brief" merged with charter synthesis (stage 2) — the artifact IS the brief | ✓ |
| **pre-market** | | | | |
| Source Triangulation | 3 | 1, 2 (fan-out 3), 3 | none | ✓ |
| Sugya Weave | 1 | 4 | none | ✓ |
| Tool Chain | 1 | 5 | none | ✓ |
| **market-open** | | | | |
| Red Team / Blue Team | 3 | 1, 2 (fan-out 2), 3 | none | ✓ |
| Tool Chain | 1 | 4 | none | ✓ |
| Andon Cord | (within stage 5 prompt — diagnostic on broker failure) | 5 | within-stage | ✓ |
| **midday** | | | | |
| Triage Gate | 3 | 1, 2 (fan-out N), 4 | stage 4 merges TG-process with execution | ✓ |
| Read-and-React | (within fan-out prompts) | 2 | within-stage | ✓ |
| Tool Chain | 1 | 3 | none | ✓ |
| **market-close** | | | | |
| After-Action Review | 3 | 1, 2, 3 | none | ✓ |
| Tool Chain | 1 | 1 | merged: P&L computation IS AAR-capture (deterministic) | ✓ |
| **weekly-review** | | | | |
| Delphi Convergence | 3 | 2 (fan-out 3), 3 (fan-out 3), 4 | none | ✓ |
| Quorum Trigger | 1 | 5 | none | ✓ |
| Sugya Weave | 1 | 4 | none | ✓ |

All rows pass.

## Examples consulted

- `examples/research/research-agent.yaml` — borrowed: stage-named `movements:`,
  cost-graduated instrument tiers (scanner/analyst/synthesizer), workspace
  conventions, validation layering.  Doesn't fit because it has no
  cross-run state and no deterministic-tool stages.
- `examples/patterns/source-triangulation.yaml` — borrowed: ST 3-stage shape,
  fan-out 3 with synthesizer role.  Doesn't fit because pre-market needs an
  additional editorial synthesis stage AFTER triangulation (Sugya Weave).
- `examples/patterns/shipyard-sequence.yaml` (consulted, not used as shape) —
  the pre-market flow validates BEFORE expensive downstream (broker call) but
  market-open is too thin to be a Shipyard. The deterministic risk check
  serves the same role within market-open.
- `examples/research/strategic-plan.yaml` — borrowed: Succession Pipeline
  framing for bootstrap (charter synthesis IS a substrate transform).

## Workspace safety

- Path: `../../../workspaces/24x7-trader` (3 dirs up from each phase script).
  Resolves to `<project>/workspaces/24x7-trader` — outside any project-root
  markers, gitignored by repo policy.
- `workspace_lifecycle.archive_on_fresh: false` everywhere. We never wipe
  trade history. `--fresh` on these scores is a footgun; the README warns.
- Two-key live trading safety: `BROKER_LIVE=1` env AND
  `<workspace>/LIVE_TRADING_ACKNOWLEDGED` file. Reference broker script
  refuses to place real orders without both.

## Cron strategy

Per Feature 19 / GH#67. `_scheduler/conductor-snippet.yaml` is canonical;
`_scheduler/crontab.example` is the today-fallback for users who haven't
adopted Feature 19 yet.

## Validation philosophy

Three layers per stage:
1. `file_exists` — output present
2. `content_regex` / `content_contains` — required structural markers
3. `command_succeeds` — substantive content (`wc -w`) AND policy enforcement
   (risk-envelope check exits 0)

Process-validation litmus test (essentials.md pitfall #25) applied to each
score in self-review.
