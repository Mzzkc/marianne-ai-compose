# Learning System Guide

Marianne's learning system is a cross-workspace knowledge accumulation engine that gets smarter with every job you run. It automatically discovers patterns, tracks their effectiveness, detects when they stop working, and uses this knowledge to improve future executions. This guide explains how the system works, what it learns, and how to use it effectively.

---

## What is the Learning System?

The learning system is Marianne's persistent memory — a global knowledge store that spans all your jobs and workspaces. Every time Marianne runs a sheet, validates output, recovers from an error, or applies a pattern, it records what happened. Over time, this data reveals:

- **What works** — Patterns that consistently lead to success
- **What doesn't** — Patterns that fail or degrade over time
- **Why things work** — Success factors that explain causality
- **When to explore** — Entropy monitoring that prevents over-convergence
- **What's changing** — Drift detection that spots shifting effectiveness

Unlike traditional machine learning that trains on static datasets, Marianne learns from **your actual work** — the scores you run, the validations you define, the errors you encounter. The learning loop is continuous: execute → measure → learn → adapt.

---

## Architecture

### Storage

Learning data is stored in a SQLite database at `~/.marianne/global-learning.db` using Write-Ahead Logging (WAL mode) for safe concurrent access from multiple jobs. The database schema includes:

- **patterns** — Learned behaviors, configurations, and recovery strategies
- **pattern_applications** — When and how patterns were applied
- **executions** — Sheet execution outcomes with context
- **error_recoveries** — Error handling and recovery attempts
- **rate_limit_events** — Cross-workspace rate limit coordination
- **escalation_decisions** — How escalations were resolved
- **exploration_budgets** — Exploration vs exploitation balance
- **entropy_responses** — Automatic diversity injection events

### Components

The learning system is built from six modular mixins, each handling a specific domain:

| Mixin | Provides |
|-------|----------|
| **PatternMixin** | Pattern recording, effectiveness tracking, trust scoring |
| **ExecutionMixin** | Execution outcome recording, statistics, similarity matching |
| **RateLimitMixin** | Cross-workspace rate limit coordination |
| **DriftMixin** | Effectiveness and epistemic drift detection, auto-retirement |
| **EscalationMixin** | Escalation decision recording and learning |
| **BudgetMixin** | Exploration budget management, entropy response |
| **PatternLifecycleMixin** | Automated pattern promotion, quarantine, validation lifecycle |

---

## Pattern Lifecycle

Patterns are the core learning artifact. A **pattern** is any configuration, behavior, or recovery strategy that Marianne detects or records. Patterns move through a lifecycle based on their measured effectiveness.

### Lifecycle States

```
PENDING → { ACTIVE, QUARANTINED }
ACTIVE → QUARANTINED (if effectiveness degrades)
QUARANTINED → VALIDATED (manual review)
```

| State | Meaning | Requirements |
|-------|---------|--------------|
| **PENDING** | New pattern, gathering evidence | Initial state for all patterns |
| **ACTIVE** (VALIDATED) | Proven effective, ready for use | ≥3 applications AND effectiveness ≥0.60 |
| **QUARANTINED** | Ineffective or degraded | ≥3 applications AND effectiveness <0.35 |

### Automatic Promotion

Marianne automatically promotes or quarantines patterns when they cross effectiveness thresholds:

**Promotion criteria** (PENDING → ACTIVE):
- Minimum 3 applications
- Effectiveness score ≥ 0.60 (60% success rate with Laplace smoothing)

**Quarantine criteria** (PENDING → QUARANTINED):
- Minimum 3 applications
- Effectiveness score < 0.35 (35% success rate)

**Degradation criteria** (ACTIVE → QUARANTINED):
- Effectiveness drops below 0.30 after validation
- Indicates a once-effective pattern is no longer working

### Effectiveness Scoring

Effectiveness is calculated as:

```
effectiveness = (successes + 0.5) / (total_applications + 1)
```

The formula uses **Laplace smoothing** to avoid extreme scores from small sample sizes. A pattern with 2 successes out of 3 applications gets:

```
effectiveness = (2 + 0.5) / (3 + 1) = 0.625
```

This is more conservative than raw 2/3 = 0.667, preventing premature promotion of lucky patterns.

---

## Trust Scoring

Trust scoring goes beyond simple effectiveness to measure **how confident Marianne should be** in a pattern's reliability. Trust considers:

- **Application count** — More applications = higher confidence
- **Success consistency** — Stable success rate over time
- **Grounding confidence** — How well outputs matched expectations
- **Recency** — Recent successes weigh more than old ones

Trust scores influence pattern selection during execution. High-trust patterns are preferred when reliability matters; low-trust patterns may still be used during exploration phases.

---

## Entropy and Exploration Budget

### The Over-Convergence Problem

Without intervention, learning systems converge to a small set of "winning" patterns and stop exploring. This creates brittleness — when conditions change, the system has no diversity to adapt.

Marianne solves this with **entropy monitoring** and a **dynamic exploration budget**.

### Pattern Entropy

Entropy measures the diversity of pattern selection:

```
H = -Σ(p_i × log(p_i))
```

Where `p_i` is the probability of selecting pattern `i`. High entropy = diverse selection, low entropy = convergence to a few patterns.

**Entropy thresholds:**
- `> 2.0` — Healthy diversity
- `1.0 - 2.0` — Moderate convergence, watchlist
- `< 1.0` — Dangerous convergence, trigger response

### Exploration Budget

The exploration budget controls the probability of choosing a non-optimal pattern to inject diversity. It operates with a **floor and ceiling**:

- **Floor (0.05)** — Ensures 5% minimum exploration even in stable conditions
- **Ceiling (0.50)** — Caps exploration at 50% to prevent chaos
- **Dynamic adjustment** — Increases when entropy drops, decreases when entropy is healthy

When entropy crosses the low threshold, Marianne automatically:
1. Boosts exploration budget by 0.10 (10%)
2. Revisits up to 3 quarantined patterns for re-evaluation
3. Records the entropy response event for future analysis

---

## Drift Detection

Patterns that work today may stop working tomorrow. Marianne detects two types of drift:

### Effectiveness Drift

**What it measures:** Changes in success rate over time.

**How it works:** Compares recent applications (last N) vs older applications (previous N) using a sliding window. The drift score is:

```
drift = effectiveness_recent - effectiveness_old
```

- **Positive drift** — Pattern is improving
- **Negative drift** — Pattern is degrading
- **Magnitude > threshold** — Flagged for investigation

**Default parameters:**
- Window size: 5 applications per window
- Drift threshold: 0.20 (20% change)

**Example:**
```
Recent 5 applications: 4 successes → effectiveness = 0.80
Older 5 applications: 2 successes → effectiveness = 0.40
Drift = 0.80 - 0.40 = +0.40 (positive, improving)
```

### Epistemic Drift

**What it measures:** Changes in **belief confidence** over time.

Effectiveness drift tracks outcomes; epistemic drift tracks how certain Marianne is about those outcomes. This is measured via grounding confidence scores from validation checks.

**Why it matters:** A pattern might maintain 60% effectiveness but shift from highly confident (grounding = 0.9) to uncertain (grounding = 0.4). This signals deeper issues — the pattern still works sometimes, but Marianne doesn't know when.

**How it works:** Tracks the standard deviation of grounding confidence over time. Rising variance = increasing epistemic uncertainty.

---

## Success Factors (WHY Analysis)

Traditional learning systems answer "what worked?" Marianne also answers **"why did it work?"**

Success factors are contextual conditions that correlate with pattern effectiveness:

- Specific error codes that the pattern handles well
- Workspace characteristics (language, framework, test setup)
- Timing factors (time of day, retry count, sheet position)
- Validation types that the pattern satisfies

When Marianne records a pattern application, it captures context as success factors. Over time, patterns accumulate a metacognitive model of their own applicability.

**Example success factors for a "rate_limit_recovery" pattern:**
```json
{
  "error_code": "E101",
  "retry_count": 1,
  "time_of_day": "afternoon",
  "validation_types": ["file_exists", "content_regex"],
  "observations": 7,
  "correlation_strength": 0.82
}
```

This tells you the pattern works especially well for E101 errors on the first retry during afternoon execution with file-based validations — actionable insight for debugging and optimization.

---

## CLI Commands

Marianne provides 12 CLI commands for interacting with the learning system, organized by function.

### Statistics and Insights

#### `mzt learning-stats`

View global learning statistics — execution counts, pattern counts, effectiveness metrics.

```bash
mzt learning-stats              # Human-readable summary
mzt learning-stats --json       # JSON output for scripting
```

**Sample output:**
```
Global Learning Statistics

Executions
  Total recorded: 342
  First-attempt success: 73.4%

Patterns
  Total learned: 89
  Avg effectiveness: 0.68

Data Sources
  Output patterns extracted: 34
  Error code patterns: 12
  Semantic failure patterns: 8

Workspaces
  Unique workspaces: 7

Error Recovery Learning
  Recoveries recorded: 45
  Recovery success rate: 82.2%
```

#### `mzt learning-insights`

Shows actionable insights from learning data — patterns to investigate, trends to watch, opportunities for optimization.

```bash
mzt learning-insights           # Top insights
mzt learning-insights -n 20     # Show 20 insights
mzt learning-insights --json    # JSON output
```

**Insight types:**
- High-performing patterns worth promoting
- Degrading patterns needing investigation
- Quarantined patterns ready for re-evaluation
- Success factors with strong correlations

#### `mzt learning-activity`

View recent learning activity — pattern applications, discoveries, promotions.

```bash
mzt learning-activity           # Last 50 events
mzt learning-activity -n 100    # Last 100 events
mzt learning-activity --json    # JSON output
```

### Pattern Analysis

#### `mzt patterns-list`

List all learned patterns with filtering.

```bash
mzt patterns-list                    # All patterns
mzt patterns-list --status active    # Only active patterns
mzt patterns-list --status quarantined  # Only quarantined
mzt patterns-list --min-priority 0.7    # Effectiveness ≥ 0.7
mzt patterns-list -n 20              # Limit to 20 results
mzt patterns-list --json             # JSON output
```

**Sample output:**
```
Pattern ID     Name                      Type              Status    Effectiveness  Apps
abc1234567890  rate_limit_exponential    error_recovery    ACTIVE    0.82          12
def9876543210  validation_retry_logic    validation        ACTIVE    0.74          8
ghi5551112222  api_timeout_handling      error_recovery    QUARANTINE 0.28         5
```

#### `mzt patterns-why`

Analyze WHY patterns succeed — metacognitive insight into success factors.

```bash
mzt patterns-why                 # All patterns with WHY analysis
mzt patterns-why abc123          # Specific pattern (first 10 chars of ID)
mzt patterns-why --min-obs 3     # Only patterns with 3+ factor observations
mzt patterns-why -n 10           # Limit to 10 patterns
mzt patterns-why --json          # JSON output
```

**Sample output:**
```
╭─ WHY Analysis ─────────────────────────────────╮
│ Rate Limit Exponential Backoff                │
│ Type: error_recovery                          │
╰───────────────────────────────────────────────╯

Success Factors (7 total, 15 observations):

  error_code: E101
    Observations: 5
    Correlation: 0.91
    Interpretation: Strong correlation

  retry_count: 1
    Observations: 4
    Correlation: 0.78
    Interpretation: Moderate correlation

  validation_type: content_regex
    Observations: 6
    Correlation: 0.82
    Interpretation: Strong correlation
```

### Drift Detection

#### `mzt learning-drift`

Calculate effectiveness drift for patterns.

```bash
mzt learning-drift                   # All patterns with drift
mzt learning-drift abc123            # Specific pattern
mzt learning-drift --threshold 0.15  # Custom drift threshold (default: 0.20)
mzt learning-drift --window 10       # Window size = 10 applications (default: 5)
mzt learning-drift --json            # JSON output
```

**Interpretation:**
- **Drift > +0.20** — Pattern improving significantly
- **Drift -0.10 to +0.10** — Stable
- **Drift < -0.20** — Pattern degrading, investigate

#### `mzt learning-epistemic-drift`

Calculate epistemic (belief-level) drift for patterns.

```bash
mzt learning-epistemic-drift                   # All patterns
mzt learning-epistemic-drift abc123            # Specific pattern
mzt learning-epistemic-drift --threshold 0.15  # Custom threshold
mzt learning-epistemic-drift --json            # JSON output
```

**What to look for:**
- Rising variance in grounding confidence = increasing uncertainty
- Stable mean with low variance = reliable pattern
- Declining mean confidence = pattern losing applicability

### Entropy and Budget

#### `mzt patterns-entropy`

View pattern selection entropy metrics.

```bash
mzt patterns-entropy             # Current entropy across all jobs
mzt patterns-entropy --job abc   # Entropy for specific job hash
mzt patterns-entropy --json      # JSON output
```

**Entropy interpretation:**
- `> 2.0` — Healthy diversity, system is exploring
- `1.0 - 2.0` — Moderate convergence, watch for trends
- `< 1.0` — Dangerous convergence, diversity injection needed

#### `mzt entropy-status`

Check entropy status and recent automatic responses.

```bash
mzt entropy-status               # Current status
mzt entropy-status --json        # JSON output
```

**Shows:**
- Current entropy level
- Recent entropy responses triggered
- Exploration budget adjustments
- Quarantined patterns revisited

#### `mzt patterns-budget`

View and manage exploration budget.

```bash
mzt patterns-budget              # Current budget across all jobs
mzt patterns-budget --job abc    # Budget for specific job hash
mzt patterns-budget --json       # JSON output
```

**Budget interpretation:**
- `< 0.10` — Heavy exploitation, minimal exploration
- `0.10 - 0.30` — Balanced explore/exploit
- `> 0.30` — High exploration, seeking new patterns

### Export and Evolution

#### `mzt learning-export`

Export learning data for analysis or backup.

```bash
mzt learning-export -o learning-data.json          # Export all data
mzt learning-export -o data.json --format json     # JSON format (default)
mzt learning-export -o data.md --format markdown   # Human-readable markdown
mzt learning-export --include-all                  # Include all data (patterns, executions, etc.)
```

**Export includes:**
- Pattern records with effectiveness scores
- Execution statistics
- Drift metrics
- Success factors
- Entropy metrics
- Error recovery records

#### `mzt learning-record-evolution`

Record evolution trajectory for a pattern (used by self-improvement scores).

```bash
mzt learning-record-evolution abc123 \
  --metric effectiveness \
  --value 0.82 \
  --context '{"iteration": 24, "phase": "stabilization"}'
```

---

## Quick Start

### View Learning Statistics

```bash
mzt learning-stats
```

This gives you a snapshot of what Marianne has learned so far — total executions, pattern count, success rates.

### Find High-Performing Patterns

```bash
mzt patterns-list --min-priority 0.7 -n 10
```

Shows your top 10 patterns with effectiveness ≥ 0.70. These are candidates for manual promotion or further investigation.

### Investigate Why a Pattern Works

```bash
mzt patterns-why abc123
```

Replace `abc123` with the first 10 characters of a pattern ID from `patterns-list`. This shows success factors — the conditions under which the pattern performs well.

### Check for Drift

```bash
mzt learning-drift --threshold 0.15
```

Flags patterns with effectiveness changes > 15%. Positive drift = improving, negative drift = degrading.

### Monitor Entropy

```bash
mzt patterns-entropy
```

If entropy is low (< 1.0), Marianne may have over-converged. Check the exploration budget and consider triggering an entropy response.

---

## Troubleshooting

### "No patterns found"

**Cause:** The learning store is empty — no jobs have run yet, or learning was disabled.

**Fix:**
1. Run a few scores with different configurations
2. Verify learning is enabled in `~/.marianne/conductor.yaml`:
   ```yaml
   learning:
     enabled: true
   ```
3. Check that the database exists: `ls -lh ~/.marianne/global-learning.db`

### Low entropy despite diverse workloads

**Cause:** Marianne has converged to a small set of high-performing patterns.

**Fix:**
1. Check current budget: `mzt patterns-budget`
2. If budget < 0.10, manually boost it by running scores with `--fresh` or varying configurations
3. Trigger an entropy response by running `mzt patterns-entropy` to check threshold
4. Revisit quarantined patterns: some may be context-specific and worth re-evaluating

### Pattern stuck in PENDING despite many applications

**Cause:** Effectiveness is between 0.35 and 0.60 — the "uncertain" range.

**Fix:**
1. Check effectiveness: `mzt patterns-list | grep <pattern-id>`
2. If effectiveness is stable at ~0.50, the pattern is marginal — it works sometimes
3. Investigate success factors: `mzt patterns-why <pattern-id>`
4. Consider manual validation or adjusting the promotion threshold in your workflow

### Drift detected but pattern still works

**Cause:** Drift measures **change**, not absolute failure. A pattern can improve (positive drift) or shift contexts without breaking.

**Fix:**
1. Check drift direction: `mzt learning-drift <pattern-id>`
2. Positive drift = improving over time, no action needed
3. Negative drift = degrading, investigate success factors for context changes
4. Compare effectiveness drift with epistemic drift — if both are declining, the pattern is losing ground

### Large database size

**Cause:** Years of execution history accumulate. Default retention is unlimited.

**Fix:**
1. Check size: `du -h ~/.marianne/global-learning.db`
2. Export data for backup: `mzt learning-export -o backup.json`
3. Archive old executions (manual SQL):
   ```sql
   DELETE FROM executions WHERE created_at < date('now', '-6 months');
   ```
4. Vacuum the database: `sqlite3 ~/.marianne/global-learning.db "VACUUM;"`

---

## Advanced Usage

### Custom Exploration Budget

Override the default exploration budget for a specific job by setting it in the score config:

```yaml
# In your score YAML (advanced, not yet exposed in schema)
learning:
  exploration_budget_override: 0.25  # 25% exploration
```

### Pattern Quarantine Management

Quarantined patterns can be manually promoted or deleted:

```python
from marianne.learning.store import get_global_store
from marianne.learning.store.models import QuarantineStatus

store = get_global_store()

# Promote a quarantined pattern to VALIDATED
store.update_quarantine_status(
    pattern_id="abc1234567890",
    new_status=QuarantineStatus.VALIDATED,
)

# Delete a truly useless pattern
store.delete_pattern(pattern_id="xyz0987654321")
```

### Entropy Response Tuning

Customize entropy response behavior via `~/.marianne/conductor.yaml`:

```yaml
learning:
  enabled: true
  entropy_response:
    boost_budget: true
    revisit_quarantine: true
    max_quarantine_revisits: 5       # default: 3
    budget_floor: 0.08               # default: 0.05
    budget_ceiling: 0.40             # default: 0.50
    budget_boost_amount: 0.15        # default: 0.10
```

### Success Factor Extraction

When recording custom patterns programmatically, include rich context for success factor analysis:

```python
store.record_pattern(
    pattern_type="custom_recovery",
    pattern_content={"action": "restart_service"},
    context={
        "error_code": "E503",
        "retry_count": 2,
        "time_of_day": "morning",
        "workspace_language": "python",
        "validation_types": ["command_succeeds"],
    },
    source_job="my-job-123",
)
```

The more structured context you provide, the better Marianne's WHY analysis becomes.

---

## Learning System Metrics Reference

| Metric | Range | Interpretation |
|--------|-------|----------------|
| **Effectiveness** | 0.0 - 1.0 | Success rate with Laplace smoothing. >0.60 = good, <0.35 = poor |
| **Trust Score** | 0.0 - 1.0 | Confidence in pattern reliability. Considers recency, consistency, grounding |
| **Entropy** | 0.0 - ∞ | Pattern selection diversity. >2.0 = healthy, <1.0 = converged |
| **Exploration Budget** | 0.05 - 0.50 | Probability of non-optimal selection. Floor/ceiling enforced |
| **Drift** | -1.0 - +1.0 | Change in effectiveness. >+0.20 = improving, <-0.20 = degrading |
| **Grounding Confidence** | 0.0 - 1.0 | How well outputs matched validation expectations |

---

## Next Steps

**Related Documentation:**
- [Score Writing Guide](score-writing-guide.md) — Define scores that feed the learning system
- [Daemon Guide](daemon-guide.md) — The conductor coordinates learning across concurrent jobs
- [CLI Reference](cli-reference.md) — All learning CLI commands in detail
- [Configuration Reference](configuration-reference.md) — Enable/disable learning, configure retention

**Experiment:**
1. Run 5-10 scores with varied configurations
2. Check `mzt learning-stats` to see what's been learned
3. Use `mzt patterns-why` to understand success factors
4. Monitor entropy with `mzt patterns-entropy`
5. Export data with `mzt learning-export` for external analysis

**Contribute:**
- The learning system is self-improving — patterns learned from your workloads make Marianne smarter
- Report unexpected drift or entropy behavior as GitHub issues
- Share interesting success factors or insights with the community

---

*Learning System Guide — how Marianne accumulates knowledge, detects drift, and adapts over time.*
