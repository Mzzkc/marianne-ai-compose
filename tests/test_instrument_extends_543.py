"""Profile ``extends`` (GH #543): an overlay file merged over a lower layer's profile."""

from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import pytest

from marianne.instruments.loader import (
    InstrumentProfileLoader,
    _Layer,
    clear_profile_memo,
    load_all_profiles,
    load_all_profiles_with_failures,
    profile_source_dirs,
)


@pytest.fixture(autouse=True)
def _cold_memo() -> None:
    clear_profile_memo()


def _w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip())
    return path


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "org", tmp_path / "venue"


def _failures(org: Path, venue: Path) -> dict[str, str]:
    _p, failures = load_all_profiles_with_failures(organization_dir=org, venue_dir=venue)
    return {f.path.name: f.reason_code for f in failures}


def test_real_builtin_overlay_inherits_blocks_and_overrides_model(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(venue / "claude-code.yaml", """\
        extends: claude-code
        default_model: "glm-5.3[1m]"
        """)
    base = load_all_profiles(organization_dir=org, venue_dir=tmp_path / "none")["claude-code"]
    got = load_all_profiles(organization_dir=org, venue_dir=venue)["claude-code"]
    assert base.effort is not None and base.tools is not None
    assert base.cli is not None and base.cli.interactive is not None
    assert base.cli.interactive.usage is not None
    assert got.default_model == "glm-5.3[1m]"
    assert got.effort == base.effort
    assert got.tools == base.tools
    assert got.cli is not None and got.cli.interactive is not None
    assert got.cli.interactive.usage == base.cli.interactive.usage
    assert got.models == base.models
    assert got.model_dump(exclude={"default_model"}) == base.model_dump(exclude={"default_model"})
    assert got._extends == ("claude-code", "builtin")


def test_name_defaults_to_base_and_other_name_is_kept(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(org / "alias.yaml", """\
        extends: claude-code
        name: my-claude
        display_name: Mine
        """)
    _w(org / "plain.yaml", """\
        extends: claude-code
        description: changed
        """)
    got = load_all_profiles(organization_dir=org, venue_dir=venue)
    assert got["my-claude"].display_name == "Mine"
    assert got["claude-code"].description == "changed"
    assert got["my-claude"].effort is not None


def test_merge_rules_recursive_lists_replace_null_clears(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    base = load_all_profiles(organization_dir=org, venue_dir=venue)["claude-code"]
    _w(org / "claude-code.yaml", """\
        extends: claude-code
        capabilities: [tool_use]
        effort: null
        cli:
          command:
            executable: other-claude
        """)
    got = load_all_profiles(organization_dir=org, venue_dir=venue)["claude-code"]
    assert got.capabilities == {"tool_use"}
    assert got.effort is None
    assert got.tools == base.tools
    assert got.cli is not None and base.cli is not None
    assert got.cli.command.executable == "other-claude"
    assert got.cli.command.prompt_flag == base.cli.command.prompt_flag
    assert got.cli.interactive == base.cli.interactive


def test_venue_extends_organization_layer_which_extends_builtin(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(
        org / "claude-code.yaml",
        "extends: claude-code\ndefault_model: org-model\ndescription: org\n",
    )
    _w(venue / "claude-code.yaml", "extends: claude-code\ndefault_model: venue-model\n")
    got = load_all_profiles(organization_dir=org, venue_dir=venue)["claude-code"]
    assert got.default_model == "venue-model"
    assert got.description == "org"
    assert got.effort is not None
    assert got._extends == ("claude-code", "organization")


def test_missing_base_is_a_typed_failure(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(org / "x.yaml", "extends: no-such-profile\nname: x\n")
    assert _failures(org, venue) == {"x.yaml": "instrument_extends_missing_base"}
    assert "x" not in load_all_profiles(organization_dir=org, venue_dir=venue)


def test_extends_in_builtin_layer_is_refused(tmp_path: Path) -> None:
    b = tmp_path / "builtins"
    _w(b / "a.yaml", "extends: a\nname: a\n")
    _p, failures = InstrumentProfileLoader._scan_directory(
        b, layers=[_Layer(b, "builtin", True)],
        layer_index=0,
    )
    assert [f.reason_code for f in failures] == ["instrument_extends_in_builtin"]


def test_extends_naming_only_same_or_higher_layer_is_refused(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(org / "up.yaml", "extends: only-venue\nname: up\n")
    _w(venue / "only-venue.yaml", """\
        name: only-venue
        display_name: V
        kind: cli
        cli:
          command: {executable: v, prompt_flag: "-p"}
          output: {format: text}
        """)
    assert _failures(org, venue) == {"up.yaml": "instrument_extends_not_below"}


def test_same_layer_cycle_is_refused(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(org / "a.yaml", "extends: b\nname: a\n")
    _w(org / "b.yaml", "extends: a\nname: b\n")
    got = _failures(org, venue)
    assert got == {
        "a.yaml": "instrument_extends_not_below",
        "b.yaml": "instrument_extends_not_below",
    }


def test_broken_base_reports_through_the_extender(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(org / "base.yaml", "extends: nothing-below\nname: base\n")
    _w(venue / "top.yaml", "extends: base\nname: top\n")
    got = _failures(org, venue)
    assert got["base.yaml"] == "instrument_extends_missing_base"
    assert got["top.yaml"] == "instrument_extends_missing_base"


def test_merged_result_that_fails_validation_is_a_validation_failure(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(org / "claude-code.yaml", "extends: claude-code\nno_such_field: 1\n")
    assert _failures(org, venue) == {"claude-code.yaml": "instrument_schema_stale"}
    kept = load_all_profiles(organization_dir=org, venue_dir=venue)["claude-code"]
    assert kept.effort is not None


def test_bad_extends_value_is_refused(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    _w(org / "x.yaml", "extends: [a]\nname: x\n")
    assert _failures(org, venue) == {"x.yaml": "instrument_extends_invalid"}


def test_editing_the_base_file_invalidates_the_extender(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    base = _w(org / "mid.yaml", """\
        name: mid
        display_name: Mid
        kind: cli
        description: one
        cli:
          command: {executable: m, prompt_flag: "-p"}
          output: {format: text}
        """)
    _w(venue / "mid.yaml", "extends: mid\ndefault_model: pinned\n")
    first = load_all_profiles(organization_dir=org, venue_dir=venue)["mid"]
    assert first.description == "one"
    assert load_all_profiles(organization_dir=org, venue_dir=venue)["mid"] is first
    base.write_text(base.read_text().replace("description: one", "description: two!"))
    st = base.stat()
    os.utime(base, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    second = load_all_profiles(organization_dir=org, venue_dir=venue)["mid"]
    assert second.description == "two!"
    assert second.default_model == "pinned"
    assert second._source_sha256 != first._source_sha256


def test_overlay_digest_changes_with_the_overlay_file(tmp_path: Path) -> None:
    org, venue = _dirs(tmp_path)
    over = _w(venue / "claude-code.yaml", "extends: claude-code\ndefault_model: a\n")
    one = load_all_profiles(organization_dir=org, venue_dir=venue)["claude-code"]
    over.write_text("extends: claude-code\ndefault_model: bb\n")
    two = load_all_profiles(organization_dir=org, venue_dir=venue)["claude-code"]
    assert two.default_model == "bb" and two._source_sha256 != one._source_sha256
    assert two._source_path == over.resolve()


def test_default_dirs_helper_unchanged() -> None:
    assert profile_source_dirs()[0].name == "builtins"
