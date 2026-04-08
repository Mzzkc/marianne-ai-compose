## Patterns Awaiting Primitives

Confirmed by cross-domain convergence but requiring Marianne capabilities not yet available:

| Pattern | Blocked By | Notes |
|---------|-----------|-------|
| Bulkhead Isolation | Per-sheet resource budgets | Prevents one sheet from consuming all tokens |
| Kanban Pull | Conductor WIP limits | Pull-based work assignment |
| Supervision Tree | Concert restart strategies | Hierarchical fault tolerance (Erlang OTP model). Can be approximated with workspace snapshots + conductor-mediated restart |
| OODA Pulse | Self-correcting orientation phase | Observe-Orient-Decide-Act loop |
| The Aboyeur | Start-time scheduling | Stagger start times by predicted duration. Requires predictable execution times AND conductor scheduling support |
| Backpressure Valve | Concurrent score execution | Sequential batch processing is not backpressure. Requires concurrent producer/consumer with the consumer signaling capacity while both run. Source: Reactive Streams, TCP flow control (Iteration 4, cut after review) |
| Stretto Entry | Staggered/overlapping sheet execution | Overlapping pipeline: next instance starts before previous finishes. Approximation via file_exists dependencies produces sequential-with-trigger, not true overlap. Source: Fugal composition (Iteration 4, cut after review) |
| Comping Substrate | Concurrent score execution + shared filesystem | Adaptive coordination layer running alongside work scores. Requires reading all workspace outputs each iteration. Source: Jazz rhythm section (Iteration 4, cut after review) |
| Physarum Path Reinforcement | Dynamic fan-out allocation | Runtime allocation changes to instance counts and instrument assignments based on workspace state. Source: Physarum polycephalum optimization |
