"""Learning and pattern management commands for Marianne CLI.

This package implements commands for monitoring and managing the learning system.
Originally a single 1673-line file, now split into focused modules:

- _patterns: Pattern listing and WHY analysis (patterns-list, patterns-why)
- _stats: Learning statistics, insights, activity
  (learning-stats, learning-insights, learning-activity)
- _drift: Drift detection (learning-drift, learning-epistemic-drift)
- _entropy: Entropy monitoring (patterns-entropy, entropy-status)
- _budget: Exploration budget (patterns-budget)

All commands are re-exported from this __init__.py for backward compatibility.
The import path `from .commands.learning import ...` continues to work unchanged.
"""

from marianne.cli.commands.learning._budget import patterns_budget
from marianne.cli.commands.learning._drift import (
    learning_drift,
    learning_epistemic_drift,
)
from marianne.cli.commands.learning._entropy import entropy_status, patterns_entropy
from marianne.cli.commands.learning._export import (
    learning_export,
    learning_record_evolution,
)
from marianne.cli.commands.learning._patterns import patterns_list, patterns_why
from marianne.cli.commands.learning._stats import (
    learning_activity,
    learning_insights,
    learning_stats,
)

__all__ = [
    "patterns_why",
    "patterns_list",
    "learning_stats",
    "learning_insights",
    "learning_drift",
    "learning_epistemic_drift",
    "patterns_entropy",
    "patterns_budget",
    "entropy_status",
    "learning_activity",
    "learning_export",
    "learning_record_evolution",
]
