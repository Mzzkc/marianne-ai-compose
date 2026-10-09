"""Prevent reusing a V-code for a different check, including secondary emissions."""

from marianne.validation.runner import create_default_checks


def test_primary_v_codes_are_unique_and_secondary_codes_are_reserved() -> None:
    codes = [check.check_id for check in create_default_checks()]
    assert len(codes) == len(set(codes))
    assert "V104" not in codes  # TimeoutRangeCheck's secondary INFO emission
    assert "V212" in codes  # SkipWhenSheetRangeCheck owns it
    assert "V110" in codes  # unused-variable INFO, distinct from V104
