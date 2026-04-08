## Open Questions

- **Adaptive retry cost** — Retry cheap failures but not expensive ones. Echelon Repair + Vickrey Auction partially address instrument-level cost awareness. Concert-level cost budgeting remains unaddressed.
- **Token/cost budgeting** — No pattern quantifies token usage. Current workaround: instrument selection (cheap for exploration, expensive for synthesis) and Relay Zone for compression.
- **Within-score context compression** — Forward Observer addresses inter-stage compression. Intra-stage compression remains open.
- **Dynamic instrument selection** — Vickrey Auction identifies the best instrument but can't assign it at runtime. Concert-level mechanism (probe score → execution score) or conductor support needed.
- **Contact Point co-evolution** — Two agents co-evolving a shared artifact through responsive alternation is structurally unique but hard to validate. Needs further iteration.
- **Custom script inventory** — 10+ patterns reference user-supplied scripts. A standard library of validation utilities (convergence checking, YAML structure validation, cache management, temperature metrics) would reduce the barrier to adoption.
- **Composition grammar** — Patterns list "Composes With" but no pattern shows the COMBINED YAML for two composed patterns. A composition example appendix would demonstrate practical multi-pattern scores.
- **Cost model** — Canary Probe costs extra. Speculative Hedge costs 2x. Rashomon Gate costs Nx. CEGAR Loop costs unknown iterations. Patterns should include cost estimates relative to baseline.
- **Prompt technique boundary** — Within-stage patterns (Commander's Intent, Quorum Trigger, Constraint Propagation) operate at a different level than orchestration patterns. The boundary between "prompt advice" and "pattern" needs sharper definition.

---

*The Rosetta Pattern Corpus v4 — 56 patterns (38 from iterations 1-3, 18 from iteration 4), 10 generative forces, 11 generators. Revised after three adversarial reviews per iteration. All patterns include YAML snippets, failure modes, status markers, and composition guidance. Four patterns cut in iteration 4, eighteen strengthened, three moved to Awaiting Primitives.*
