# Operator Charter — EDIT THIS FILE

This is the operator's customizable axioms file. The defaults below
are starting points — replace them with your own thinking. The
content here is injected into every score and overrides defaults
from `trading-philosophy.md` where they conflict.

## Operator identity

- **Name:** [your name]
- **Account size band:** [paper / small live / midsize / large] — affects sizing decisions
- **Years of trading experience:** [be honest]
- **Risk tolerance descriptor:** [conservative / moderate / aggressive]

## What I'm trying to learn

Three concrete things you want to test or learn through running this
agent. These shape how the weekly review interprets the data.

1. [example: whether sector-rotation signals genuinely improve entry timing]
2. [example: whether macro frame pessimism is worth more than peer-relative strength]
3. [example: whether a 7% stop is too tight given how I size positions]

## Hard rules I'm imposing on the agent

Things you would never do, that the agent should also never do.
These are checked by the agent before it produces a proposal.

1. [example: no positions in tobacco, weapons, or fossil fuel extractors]
2. [example: no leveraged ETFs even when allowed by envelope]
3. [example: never more than three new positions in a single week]

## Mistakes I'm prone to

Self-reflection. The agent uses this to flag when its analysis
might be reinforcing a known bias of yours.

1. [example: I anchor on first-day price action and don't update]
2. [example: I cut winners too early — give them more rope]
3. [example: I'm too willing to add to losers]

## Communication preferences

- **Notify channel:** [stdout / slack / clickup / email — see `_techniques/notify.md`]
- **Notify on:** [every-trade / daily-summary-only / weekly-only / all]
- **Quiet hours:** [e.g., 22:00–07:00 — only emergencies]

## What goes in PENDING-COMPOSER-REVIEW.md

Things the agent should NEVER decide on its own:

- Risk envelope changes (caps, family list)
- Position sizing exceeding envelope max
- New asset class adoption
- Strategy changes proposed at weekly review

When any of these appear, the agent writes the proposal to
`proposals/PENDING-COMPOSER-REVIEW.md` and ALL execution phases halt
at stage 1 with a notification until you delete or rename the file.

## Versioning

Every meaningful change to this file should bump the version below
and date the change. The weekly review reads the version and notes
in its post-mortem when the charter changed.

- v1 — [date] — initial charter
