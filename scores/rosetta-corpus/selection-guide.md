## Pattern Selection Guide

| Problem Type | Start With | Compose With | Difficulty |
|-------------|-----------|-------------|------------|
| Analyze something from multiple angles | Fan-out + Synthesis | Barn Raising, Triage Gate | Beginner |
| Broad search then targeted deep-dive | Immune Cascade | After-Action Review | Intermediate |
| Build something with a validation gate | Shipyard Sequence | Succession Pipeline | Beginner |
| Parallel work across different AI backends | Prefabrication | Barn Raising | Intermediate |
| Iteratively refine until stable | Fixed-Point Iteration | CDCL Search | Intermediate |
| Stress-test or red-team an artifact | Red Team / Blue Team | After-Action Review | Intermediate |
| Process a large batch of independent items | Stigmergic Workspace | Barn Raising | Beginner |
| Verify claims from independent sources | Source Triangulation | Triage Gate | Intermediate |
| Long-running work across many iterations | Cathedral Construction | After-Action Review | Advanced |
| Wait for external conditions | Dormancy Gate | Read-and-React | Beginner |
| Route work based on intermediate results | Read-and-React | Triage Gate | Beginner |
| Multi-score campaign with shared constraints | Barn Raising (concert) | Prefabrication | Intermediate |
| Compress context between pipeline stages | Relay Zone | Fan-out + Synthesis | Beginner |
| Tolerate partial fan-out failure | Quorum Consensus | Triage Gate | Intermediate |
| Choose the right instrument for work | Echelon Repair | Commissioning Cascade | Intermediate |
| Validate at multiple scopes with different tools | Commissioning Cascade | Echelon Repair, Shipyard Sequence | Intermediate |
| Reduce context window pressure | Forward Observer | Relay Zone, Screening Cascade | Beginner |
| Ensure handoff fidelity between stages | Closed-Loop Call | Relay Zone, Prefabrication | Intermediate |
| Produce a position from diverse inputs | Sugya Weave | Fan-out + Synthesis, Source Triangulation | Advanced |
| Propagate early decisions as constraints | Decision Propagation | CDCL Search | Advanced |
| Discover the approach before committing | Reconnaissance Pull | Mission Command | Beginner |
| Correct course mid-execution | FRAGO | Read-and-React, Lines of Effort | Advanced |
| Iterate on weak spots only | Rehearsal Spotlight | Echelon Repair, Soil Maturity Index | Advanced |
| Know when iterating is done (judgment tasks) | Delphi Convergence | Source Triangulation | Advanced |
| Know when iterating is done (character shift) | Soil Maturity Index | Fixed-Point Iteration | Advanced |
| Carry learning across iterations | Back-Slopping | Cathedral Construction, CDCL Search | Intermediate |
| Coordinate sustained parallel campaigns | Lines of Effort | Season Bible, After-Action Review | Advanced |
| Maintain coherence across a multi-score campaign | Season Bible | Lines of Effort, Relay Zone | Advanced |
| Prepare substrate for downstream consumers | Nurse Log | Fermentation Relay, Fan-out + Synthesis | Beginner |
| Process batches with escalating instruments | Screening Cascade | Echelon Repair, Immune Cascade | Intermediate |
| Select instruments via competitive probing | Vickrey Auction | Echelon Repair | Advanced |
| Use cheap instruments first, expensive later | Fermentation Relay | Echelon Repair, Succession Pipeline | Intermediate |
| Structure a prompt for agent autonomy | Commander's Intent Envelope | Mission Command | Beginner |
| Switch behavior mid-task on accumulated evidence | Quorum Trigger | Andon Cord, Circuit Breaker | Intermediate |
| Resolve contradictions before generating | Constraint Propagation Sweep | Decision Propagation | Beginner |
| Minimize AI cost with deterministic tools | The Tool Chain | Composting Cascade | Beginner |
| Test pipeline on a subset before full run | Canary Probe | Progressive Rollout, Dead Letter Quarantine | Beginner |
| Try multiple approaches, pick the winner | Speculative Hedge | Canary Probe | Intermediate |
| Quarantine and analyze batch failures | Dead Letter Quarantine | Circuit Breaker, Screening Cascade | Intermediate |
| Detect conflicts before integration | Clash Detection | Prefabrication, Andon Cord | Intermediate |
| Analyze from structurally different frames | Rashomon Gate | Source Triangulation, Sugya Weave | Intermediate |
| Deliver partial value when full output fails | Graceful Retreat | Dead Letter Quarantine, Andon Cord | Intermediate |
| Roll out changes in graduated phases | Progressive Rollout | Canary Probe, Dead Letter Quarantine | Intermediate |
| Broadcast failure-derived defenses | Systemic Acquired Resistance | After-Action Review, Back-Slopping | Advanced |
| Drive phase transitions with workspace metrics | Composting Cascade | The Tool Chain, Succession Pipeline | Advanced |
| Diagnose failure before retrying | Andon Cord | Circuit Breaker, Quorum Trigger | Intermediate |
| Handle instrument infrastructure failures | Circuit Breaker | Dead Letter Quarantine, Echelon Repair | Intermediate |
| Refine verification from coarse to fine | CEGAR Loop | Memoization Cache, CDCL Search | Advanced |
| Skip re-analysis of unchanged inputs | Memoization Cache | CEGAR Loop, Cathedral Construction | Intermediate |
| Undo side effects on concert failure | Saga Compensation Chain | After-Action Review | Advanced |

**If you're new, start here:** Fan-out + Synthesis → Shipyard Sequence → The Tool Chain → Canary Probe → Andon Cord. These five patterns cover the most common problems and compose with everything else.

---

# Foundational Pattern
