"""Prevent reusing a V-code for a different check, including secondary emissions.

The obligation follows emission wherever it lives (S3 Inspect P2): the registry
test enumerates not only the primary check ids from ``create_default_checks``
but also the codes the validate command renders from load errors
(``_FLOW_CODES``) and every code the suppression known-map names. A code that
is emitted anywhere must be known to ``apply_suppression`` so a suppress
request can never silently drop an unknown row class.
"""

from marianne.cli.commands.validate import _FLOW_CODES
from marianne.validation.base import ValidationSeverity
from marianne.validation.output_contract import _known_suppression_severities
from marianne.validation.runner import create_default_checks


def test_primary_v_codes_are_unique_and_secondary_codes_are_reserved() -> None:
    codes = [check.check_id for check in create_default_checks()]
    assert len(codes) == len(set(codes))
    assert "V104" not in codes  # TimeoutRangeCheck's secondary INFO emission
    assert "V212" in codes  # SkipWhenSheetRangeCheck owns it
    assert "V110" in codes  # unused-variable INFO, distinct from V104
    # V310 doubles as ClassChainAvailabilityCheck's all-entries-unavailable
    # escalation; the primary owner is ClassNameResolutionCheck.
    assert codes.count("V310") == 1


def test_flow_render_map_codes_are_known_to_suppression() -> None:
    known = _known_suppression_severities()
    for flow_check, code in _FLOW_CODES.items():
        # The render map must never invent a code the suppression contract
        # cannot judge; tier comes from the owning check (V220 is a WARN
        # structural check, the explicit-map flow codes are ERROR).
        assert code in known, f"{flow_check} renders unknown code {code}"


def test_capability_class_family_is_registered_once_each() -> None:
    codes = [check.check_id for check in create_default_checks()]
    for code in ("V310", "V311", "V321", "V322", "V323", "V324", "V325"):
        assert codes.count(code) == 1
    known = _known_suppression_severities()
    assert known["V310"] == ValidationSeverity.ERROR
    assert known["V311"] == ValidationSeverity.WARNING
    assert known["V321"] == ValidationSeverity.ERROR
    assert known["V322"] == ValidationSeverity.WARNING
    assert known["V323"] == ValidationSeverity.ERROR
    assert known["V324"] == ValidationSeverity.ERROR
    assert known["V325"] == ValidationSeverity.INFO


def test_known_suppression_severities_are_total_over_checks() -> None:
    """Every primary check id is in the known map (suppressability is decided, not default)."""
    known = _known_suppression_severities()
    primary = {check.check_id: check.severity for check in create_default_checks()}
    for code, severity in primary.items():
        assert known.get(code) == severity, f"{code} missing or mistiered in known map"
