"""Remedy implementations for self-healing.

This package contains all built-in remedies:

Automatic remedies (safe, apply without confirmation):
- CreateMissingWorkspaceRemedy: Creates missing workspace directories
- CreateMissingParentDirsRemedy: Creates missing parent directories
- FixPathSeparatorsRemedy: Fixes Windows path separators on Unix

Suggested remedies (require user confirmation):
- SuggestJinjaFixRemedy: Suggests fixes for Jinja template errors

Diagnostic remedies (provide guidance only):
- DiagnoseAuthErrorRemedy: Diagnoses authentication failures
- DiagnoseMissingCLIRemedy: Diagnoses missing Claude CLI
"""

from marianne.healing.remedies.base import Remedy, RemedyCategory, RemedyResult, RiskLevel
from marianne.healing.remedies.diagnostics import DiagnoseAuthErrorRemedy, DiagnoseMissingCLIRemedy
from marianne.healing.remedies.jinja import SuggestJinjaFixRemedy
from marianne.healing.remedies.paths import (
    CreateMissingParentDirsRemedy,
    CreateMissingWorkspaceRemedy,
    FixPathSeparatorsRemedy,
)
from marianne.healing.remedies.spec_fix import SuggestRecomposeRemedy, SuggestSpecFixRemedy

__all__ = [
    "SuggestRecomposeRemedy",
    "SuggestSpecFixRemedy",
    # Base
    "Remedy",
    "RemedyCategory",
    "RemedyResult",
    "RiskLevel",
    # Automatic
    "CreateMissingWorkspaceRemedy",
    "CreateMissingParentDirsRemedy",
    "FixPathSeparatorsRemedy",
    # Suggested
    "SuggestJinjaFixRemedy",
    # Diagnostic
    "DiagnoseAuthErrorRemedy",
    "DiagnoseMissingCLIRemedy",
]
