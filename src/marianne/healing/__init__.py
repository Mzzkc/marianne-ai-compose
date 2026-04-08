"""Self-healing module for Marianne.

Provides automatic diagnosis and remediation of common configuration
and execution errors. The healing system activates when:
1. All retries have been exhausted
2. The error is in a healable category
3. The --self-healing flag is enabled

Public exports:
- ErrorContext: Rich diagnostic context for failed executions
- DiagnosisEngine: Analyzes errors and suggests remedies
- Remedy: Protocol for remediation actions
- RemedyCategory: AUTOMATIC, SUGGESTED, or DIAGNOSTIC
- RemedyRegistry: Registry of available remedies
- SelfHealingCoordinator: Orchestrates the healing process
- HealingReport: Results of a healing attempt
- create_default_registry: Factory for built-in remedies
"""

from marianne.healing.context import ErrorContext
from marianne.healing.coordinator import HealingReport, SelfHealingCoordinator
from marianne.healing.diagnosis import Diagnosis, DiagnosisEngine
from marianne.healing.registry import RemedyRegistry, create_default_registry
from marianne.healing.remedies.base import Remedy, RemedyCategory, RemedyResult, RiskLevel

__all__ = [
    # Context
    "ErrorContext",
    # Diagnosis
    "Diagnosis",
    "DiagnosisEngine",
    # Remedies
    "Remedy",
    "RemedyCategory",
    "RemedyResult",
    "RiskLevel",
    # Registry
    "RemedyRegistry",
    "create_default_registry",
    # Coordination
    "SelfHealingCoordinator",
    "HealingReport",
]
