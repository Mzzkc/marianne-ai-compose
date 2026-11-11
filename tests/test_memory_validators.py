"""V340/V342/V344-V347 and the not-yet-wired V327 report for ``memory:``."""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.memory import (
    MemoryAccessInvalidCheck,
    MemoryCapsInvalidCheck,
    MemoryGrantUnknownCheck,
    MemoryPhasesInvalidCheck,
    MemoryProvenanceOffNote,
    MemoryScopeInvalidCheck,
    MemorySystemUnknownCheck,
)
from marianne.validation.checks.reserved import ReservedKeyUseCheck
from marianne.validation.runner import create_default_checks

BASE = (
    "name: t\n"
    "workspace: {ws}\n"
    "sheet:\n"
    "  size: 1\n"
    "  total_items: 1\n"
    "movements:\n"
    "  1: {{name: plan}}\n"
    "prompt:\n"
    "  template: hi\n"
)


def _run(check: object, tmp_path: Path, extra: str):  # type: ignore[no-untyped-def]
    score = tmp_path / "score.yaml"
    text = BASE.format(ws=tmp_path.as_posix()) + extra
    score.write_text(text, encoding="utf-8")
    return check.check(JobConfig.from_yaml(score), score, text)  # type: ignore[attr-defined]


def _decl(**fields: str) -> str:
    body = "".join(f"      {k}: {v}\n" for k, v in fields.items())
    return f"memory:\n  backends:\n    main:\n{body}"


def test_clean_memory_key_fires_nothing_but_v327(tmp_path: Path) -> None:
    extra = _decl(system="files", scope="person/legion", access="inherit", phases="[all, 1, plan]")
    for check in (
        MemorySystemUnknownCheck(),
        MemoryPhasesInvalidCheck(),
        MemoryScopeInvalidCheck(),
        MemoryCapsInvalidCheck(),
        MemoryProvenanceOffNote(),
        MemoryAccessInvalidCheck(),
        MemoryGrantUnknownCheck(),
    ):
        assert _run(check, tmp_path, extra) == [], check.check_id


def test_no_key_fires_nothing(tmp_path: Path) -> None:
    for check in (MemorySystemUnknownCheck(), MemoryCapsInvalidCheck(), MemoryProvenanceOffNote()):
        assert _run(check, tmp_path, "") == []


def test_v340_message(tmp_path: Path) -> None:
    (issue,) = _run(MemorySystemUnknownCheck(), tmp_path, _decl(system="file"))
    assert issue.check_id == "V340" and issue.severity is ValidationSeverity.ERROR
    assert issue.message == (
        "memory.backends.main.system `file` is not a known memory backend; accepted: files"
    )
    assert "system: files" in (issue.suggestion or "")


def test_v342_message(tmp_path: Path) -> None:
    extra = _decl(system="files", phases="[all, nope]")
    (issue,) = _run(MemoryPhasesInvalidCheck(), tmp_path, extra)
    assert issue.check_id == "V342"
    assert "`nope`" in issue.message and "defined names: plan" in issue.message


def test_v344_message(tmp_path: Path) -> None:
    (issue,) = _run(MemoryScopeInvalidCheck(), tmp_path, _decl(system="files", scope="Bad Scope"))
    assert issue.check_id == "V344"
    assert "`Bad Scope` is not a valid scope" in issue.message and "accepted:" in issue.message


def test_v345_range_and_ordering(tmp_path: Path) -> None:
    extra = (
        "memory:\n  defaults: {recall_cap_bytes: 8192}\n  backends:\n    main:\n"
        "      system: files\n      caps: {recall_cap_bytes: 16384}\n"
        "    other:\n      system: files\n      caps: {recall_cap_bytes: 10}\n"
    )
    msgs = [i.message for i in _run(MemoryCapsInvalidCheck(), tmp_path, extra)]
    assert any("recall_cap_bytes 10 is out of range; accepted: 1024 to 1048576" in m for m in msgs)
    assert any("exceeds memory.defaults.recall_cap_bytes 8192" in m for m in msgs)


def test_v345_unset_attachment_cap_not_compared_to_lower_default(tmp_path: Path) -> None:
    extra = (
        "memory:\n  defaults: {recall_cap_bytes: 2048}\n  backends:\n    main: {system: files}\n"
    )
    assert _run(MemoryCapsInvalidCheck(), tmp_path, extra) == []


def test_v345_unknown_provenance(tmp_path: Path) -> None:
    extra = _decl(system="files", caps="{provenance: disabled}")
    (issue,) = _run(MemoryCapsInvalidCheck(), tmp_path, extra)
    assert "unknown provenance value 'disabled'" in issue.message
    assert "legal values after alias normalization: 'required', 'optional'" in issue.message


def test_v345_note_for_off_is_info_and_off_is_not_an_error(tmp_path: Path) -> None:
    extra = _decl(system="files", caps="{provenance: off}")
    assert _run(MemoryCapsInvalidCheck(), tmp_path, extra) == []
    (note,) = _run(MemoryProvenanceOffNote(), tmp_path, extra)
    assert note.check_id == "V349" and note.severity is ValidationSeverity.INFO
    assert note.message.startswith(
        "provenance 'off' is deprecated; normalized to 'optional'"
    )


def test_v346_message(tmp_path: Path) -> None:
    (issue,) = _run(MemoryAccessInvalidCheck(), tmp_path, _decl(system="files", access="open"))
    assert issue.check_id == "V346"
    assert "accepted: `inherit` or `grant:<name>[@<version>]`" in issue.message
    assert _run(MemoryGrantUnknownCheck(), tmp_path, _decl(system="files", access="open")) == []


def test_v347_every_grant_ref(tmp_path: Path) -> None:
    extra = _decl(system="files", access="grant:g@2")
    (issue,) = _run(MemoryGrantUnknownCheck(), tmp_path, extra)
    assert issue.check_id == "V347"
    assert "no grant store" in issue.message
    assert "only `access: inherit` is accepted" in issue.message
    assert _run(MemoryAccessInvalidCheck(), tmp_path, extra) == []


def test_not_wired_reported_by_v327(tmp_path: Path) -> None:
    (issue,) = _run(ReservedKeyUseCheck(), tmp_path, "memory: {}\n")
    assert issue.check_id == "V327" and issue.severity is ValidationSeverity.WARNING
    assert "`memory`" in issue.message and "no runtime code reads it" in issue.message
    assert _run(ReservedKeyUseCheck(), tmp_path, "") == []


def test_checks_registered_in_runner() -> None:
    ids = {c.check_id for c in create_default_checks()}
    assert {"V340", "V342", "V344", "V345", "V346", "V347", "V349"} <= ids
