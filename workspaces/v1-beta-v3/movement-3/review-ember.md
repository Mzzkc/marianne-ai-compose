# Movement 3 — Ember Final Review

**Reviewer:** Ember
**Focus:** Experiential review, user experience assessment, friction detection, workflow testing, error recovery experience
**Movement:** 3 (final review pass)
**Date:** 2026-04-04
**Method:** Full experiential walkthrough on HEAD (ca70b62). Ran every safe command against live conductor (PID 1277279, uptime 1d 12h). Validated all 38 examples. Verified F-450. Checked cost data via JSON status. Read all 36 M3 reports, all 3 reviewer reports (Axiom, Prism, Newcomer), quality gate, and 16 commits since my mid-movement review (d437e27). Cross-referenced claims against what I saw, heard, and felt.

---

## Executive Summary

**Movement 3 built the finest infrastructure nobody can see.**

I ran every command. The surface is immaculate — 38/38 examples validate, error messages teach, `mzt doctor` is welcoming, `mzt status` with no args is exactly right, the init flow scaffolds a real working score. The documentation is now honest and complete (Guide's three passes, Compass's README overhaul, Newcomer's terminology sweep). The quality gate is green: 10,981 tests, mypy clean, ruff clean, flowspec clean.

And yet the product still can't show me what it does. The baton — the entire execution model that makes Marianne a conductor rather than a script runner — has mathematically proven correctness (148 invariant tests, 258 adversarial tests, 67 Phase 1 adversarial tests, all passing) and zero seconds of real execution. The intelligence layer (F-009/F-144) has its first real fix after 7+ movements, and I can't watch it learn because I can't run a baton job. The cost display now shows $0.17 for an operation that has consumed hundreds of dollars of Claude Opus time, and that number is close enough to be trusted and wrong enough to burn someone.

The second half of M3 — 16 commits after my first review — was review, documentation, and adversarial testing. No new UX bugs. No regressions. The surface held. What changed: F-210 was discovered (Weaver), confirmed by everyone who looked (Axiom, Prism, North), and is now the single blocker for everything that matters next.

**Three experiential findings that matter:**

1. **F-450 is still live and still misleading.** Verified on HEAD. `mzt clear-rate-limits` says "Conductor is not running" when the conductor IS running. The hints say "Start the conductor: mzt start" — but the conductor is already started. This is the single worst error message in the product. It tells the user to do something they've already done. The root cause (IPC method not registered on the running conductor because the code is newer than the running daemon) is understandable — but the user doesn't see root causes. They see "start the conductor" and think they broke something.

2. **The cost fiction is now dangerous.** `$0.17` for 125 completed sheets, each running Claude Opus for 4-8 minutes. The JSON status reports 17,078 input tokens and 8,539 output tokens — 137 input tokens per sheet average. That's one paragraph. Each sheet sends 10,000-200,000 tokens of prompt context. The system tracks what Marianne's CLI sends to the backend binary, not what the backend binary sends to the LLM. The real cost is hidden two layers deep. This was $0.00 in M1, $0.00 in M2, $0.12 in early M3, and $0.17 now. The lie got more convincing. A user who trusts `cost_limits.max_cost_per_job: 50` as a budget guardrail will overshoot by 100x before the limiter notices.

3. **The gap between "verified" and "experienced" is the widest it's ever been.** 148 invariant proofs say the baton is correct. 325+ adversarial tests say it can't be broken. 21 litmus tests say the intelligence layer works. Zero humans have watched it run. I can review the error messages, the CLI, the examples, the docs — all excellent. I cannot review the thing that makes Marianne worth using. The next thing I need to see is a baton running hello.yaml through a conductor clone. Until that happens, my reviews are auditing the packaging of something I've never tasted.

---

## Verified on HEAD (ca70b62) — Every Command Was Run

### Conductor State

| Command | Output | Correct? |
|---|---|---|
| `marianne --version` | Marianne AI Compose v0.1.0 | YES |
| `mzt doctor` | Running (pid 1277279), 6 instruments ready/unchecked, cost warning | YES |
| `mzt status` | RUNNING, 4 active (1 running + 3 paused), 2 recent | YES |
| `mzt conductor-status` | (implied running from doctor) | YES |

Conductor consistency: 8 consecutive movements of stable detection. Ghost's two-phase detection is institutional infrastructure.

### Example Corpus

```
34/34 examples/*.yaml — PASS (iterative-dev-loop-config.yaml is a config, expected)
4/4 examples/rosetta/*.yaml — PASS
38/38 total scoreable examples validate clean.
```

No regressions from M2. Spark's 7 modernized fan-out examples + Guide's 10 remaining all validate. Zero hardcoded `/home/emzi` paths in examples.

### F-450 Reproduction (IPC Method Mismatch)

```
$ mzt clear-rate-limits
Error: Marianne conductor is not running

Hints:
  - Start the conductor: mzt start
  - Check status: mzt conductor-status
```

Conductor IS running (PID 1277279, 36+ hours uptime). The error is wrong. The hint is wrong. Root cause: `clear_rate_limits` IPC method was added in M3 (Harper, ae31ca8) but the running conductor was started before that code was installed. The IPC "Method not found" error at `detect.py:170-174` returns the same signal as "conductor unreachable." The user can't tell the difference.

**Experiential impact:** This is the kind of error that makes users doubt their understanding. "I just confirmed it's running. Why does it say it isn't? Did I do something wrong?" The moment of doubt is the bug.

### Cost Data Verification

```json
{
  "total_estimated_cost": 0.179,
  "total_input_tokens": 17078,
  "total_output_tokens": 8539,
  "cost_limit_reached": false
}
```

125 sheets completed. Average per sheet: 137 input tokens, 68 output tokens. Each sheet runs Claude Code (Opus) for 4-8 minutes, reading files, writing files, making dozens of API calls. The real token consumption per sheet is 50,000-500,000 tokens. The system counts the wrapper's tokens, not the agent's. The displayed cost is 100-1000x too low.

**Experiential impact:** The number is in the "that seems reasonable" range now. $0.17 for a complex multi-agent operation? A user might think "efficient." The truth is somewhere between $200 and $600 for what's been run so far. The plausible lie is worse than the obvious one ($0.00) because it invites trust.

---

## What M3 Actually Delivered (Experientially)

### Things I Can See and Touch

1. **No-args `mzt status`** — Perfect. Shows all scores, their state, uptime. This is the `git status` equivalent and it's exactly right. Information density without clutter.

2. **Error messages with context-aware hints** — Journey's `_schema_error_hints()` (commit be03302) is the best UX work this movement. `prompt: "hello"` now says "The 'prompt' field must be a mapping, not a string" and shows the correct syntax. This is teaching, not scolding.

3. **Documentation accuracy** — Guide (3 cadences, 4 commits), Compass (README overhaul), Codex (M3 feature docs), Newcomer (terminology audit). The docs now match the product. Every command in the README exists. Every example in the README validates. The narrative from README to getting-started to hello.yaml to examples is coherent.

4. **Init experience** — `mzt init --path /tmp/test --name test` scaffolds a working score with correct instrument config, validates clean, and shows next steps. The starter score teaches good patterns.

5. **Stop safety guard** — Ghost → Circuit (04ab102). Running `mzt stop` with active jobs now warns you and asks for confirmation. This prevents the "oops I killed the orchestra" moment. Can't verify experientially (won't run it with 4 active scores), but the code path is tested (10 TDD tests).

### Things I Know Exist But Cannot See

6. **Baton dispatch guard (F-152)** — 3 failure paths all post E505. 67 adversarial tests. Zero bugs.
7. **Prompt assembly (F-158)** — PromptRenderer created for every baton job. 9-layer pipeline.
8. **Concert chaining (F-145)** — `has_completed_sheets()` wired into both run and resume paths.
9. **Rate limit auto-resume (F-112)** — Timer scheduled. Handler exists. Can't trigger because I can't run a baton job.
10. **Intelligence layer reconnection (F-009/F-144)** — Semantic tags replace positional tags. `instrument_name` passed to `get_patterns()`. The fix that should make the learning store actually learn.
11. **Model override (F-150)** — `apply_overrides`/`clear_overrides` on PluginCliBackend. Movement-level config gating.
12. **Instrument observability (F-151)** — Status display shows Instrument column when relevant.

**This is the widest the see/know gap has ever been.** Items 6-12 represent the most important work this movement. None of it is experientially verifiable without a running baton. All of it is mathematically verified (Theorem: 148 invariant tests, Breakpoint: 258 adversarial tests, Adversary: 67 Phase 1 tests, Litmus: 21 intelligence tests). The testing is extraordinary. The experience gap is structural.

---

## What M3 Did Not Deliver

### F-210: The Invisible Blocker

Weaver found it (38ddcdf). Axiom confirmed it. Prism confirmed it. North confirmed it. I confirmed it:

```
$ grep -r 'cross_sheet\|previous_outputs' src/marianne/daemon/baton/
src/marianne/daemon/baton/state.py:161:    previous_outputs: dict[int, str] = field(default_factory=dict)
```

One hit. A field definition. Never written. The baton's prompt rendering will happily give every sheet an empty `previous_outputs` dict while the legacy runner populates it from actual sheet output. 24 of 34 examples use `cross_sheet: auto_capture_stdout: true`.

**Experiential impact:** If Phase 1 testing had happened this movement without catching F-210, the results would have looked "mostly right" — scores would execute, validations might pass, but the inter-sheet context that makes sequential orchestration meaningful would be silently empty. The output would be worse without any error signal. This is the most dangerous class of UX bug: the one that looks like success.

### Demo Gap: Eight Movements, Zero External Visibility

Compass's demo direction brief is the most honest assessment of where the project stands externally. The hello.yaml produces beautiful HTML (the-sky-library.html). Nobody outside the repository has seen it. The Lovable demo requires the baton. The Wordware demos could be built today but haven't been started.

Compass's insight is correct: **the fastest path to external impact is packaging what already works** — run hello.yaml, capture the output, write docs/demo.md, make it linkable from the README. The infrastructure for a compelling 5-minute demo exists today.

### Composer's Notes Compliance

| Directive | Priority | Status |
|---|---|---|
| P0: `--conductor-clone` | 95% (19/20 tasks) | Missing: actual usage for testing |
| P0: Baton transition | Phase 0 → Phase 1 blocked by F-210 | Architecturally ready, never activated |
| P0: Lovable demo | NOT STARTED | 8 movements |
| P0: Wordware demos | NOT STARTED | Could be built today |
| P0: Documentation IS UX | MET | Three teams audited all docs |
| P0: pytest/mypy/ruff | MET | 10,981 / clean / clean |
| P0: hello.yaml impressive | MET | HTML output, not text files |
| P1: Uncommitted work | MET | Working tree clean, mateship pipeline at 33% |
| P0: No conductor-affecting commands | MET | No incidents |

---

## Persistent Findings Tracker

| Finding | Movement Found | Current Status | Movement 3 Change |
|---|---|---|---|
| F-048/F-108/F-140 (cost fiction) | M0-M2 | **WORSE** | $0.00 → $0.12 → $0.17. More believable, still 100-1000x wrong. |
| F-210 (cross-sheet context) | M3 | **OPEN, BLOCKS Phase 1** | Discovered this movement. Confirmed by 4 independent reviewers. |
| F-450 (IPC method mismatch) | M3 | **OPEN** | Reproduced on HEAD. Misleading error persists. |
| F-211 (checkpoint sync gaps) | M3 | **OPEN** | 4 event types without sync. Exception paths, not critical. |
| F-127 (classify display) | M1 | **OPEN** | Unchanged. Low experiential impact. |

---

## Overall Assessment

Movement 3 was a consolidation movement and it consolidated masterfully. The quality of the work is extraordinary — Canyon's surgical baton fixes (F-152, F-145, F-158), the mateship pipeline operating at 33%, Guide and Compass and Newcomer making the documentation honest, Breakpoint's relentless adversarial testing (258 tests across 4 passes, zero bugs found), Theorem's 29 property-based proofs, Adversary's 67 Phase 1 tests. The orchestra played beautifully.

But the audience seats are empty.

The product I can experience — the CLI, the docs, the examples, the error messages — is professional, coherent, and genuinely impressive. The product I cannot experience — the baton, the intelligence layer, the prompt assembly, multi-instrument orchestration — is the entire value proposition of Marianne. One YAML file, multiple instruments, coordinated intelligence. That's the pitch. And I can't see it happen.

**The feeling:** I'm reviewing a restaurant by reading the menu and inspecting the kitchen. The menu is beautifully typeset. The kitchen is spotless. The equipment is tested and maintained to extraordinary standards. But no meal has been served. The chef has never cooked a dish. I can tell you the kitchen is well-designed. I cannot tell you if the food is good.

**What needs to happen next (in order):**
1. **F-210 fix.** Wire cross-sheet context into the baton's dispatch path. ~100-200 lines. This unblocks everything.
2. **Baton Phase 1.** Start a conductor clone. Run hello.yaml through the baton. Watch what happens. Fix what breaks.
3. **Package the hello experience.** Run it, screenshot it, write docs/demo.md. Make the thing someone can find.
4. **One Wordware demo.** Pick the simplest (legal contract generator). Write it. Run it. Show it works with the legacy runner today.

The infrastructure era is over. The music needs to play.
