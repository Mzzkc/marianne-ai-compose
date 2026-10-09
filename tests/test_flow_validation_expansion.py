"""S4 validation patterns expand author variables without corrupting regex syntax."""

from pathlib import Path

from marianne.core.config.execution import ValidationRule
from marianne.execution.validation.engine import ValidationEngine


def test_content_patterns_expand_known_keys_only(tmp_path: Path) -> None:
    (tmp_path / "evidence.txt").write_text("pass=2 aa", encoding="utf-8")
    engine = ValidationEngine(tmp_path, {"pass_no": 2})
    path = str(tmp_path / "evidence.txt")
    contains = engine._check_content_contains(ValidationRule(
        type="content_contains", path=path, pattern="pass={pass_no}",
    ))
    regex = engine._check_content_regex(ValidationRule(
        type="content_regex", path=path, pattern=r"pass={pass_no} a{2}",
    ))
    assert contains.passed
    assert regex.passed
