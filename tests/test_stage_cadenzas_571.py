"""#571: cadenza keys under a fan-out loop are concrete sheet numbers.

The issue asks for a stage-keyed cadenza spelling: a cadenza declared on a
movement (``movements.<stage>.cadenzas``) reaches every voice of that stage,
with an optional ``voice:`` to narrow one item to one voice, while
``sheet.cadenzas`` keys stay concrete sheet numbers, also under fan-out.

Every score below declares its fan-out width with ``sheet.fan_out`` (never
``movements.N.voices``): the voices spelling re-expands when an expanded
config is re-validated, and the round-trip tests here would fail for that
unrelated pre-existing bug instead of for #571.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent, indent

from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config import JobConfig
from marianne.core.config.job import (
    MovementCadenzaItem,
    cadenzas_for_sheet,
    movement_cadenzas,
    stage_sheets,
)
from marianne.core.sheet import build_sheets
from marianne.daemon.baton.prompt import PromptRenderer
from marianne.validation.checks.paths import (
    AbsoluteHomePathCheck,
    CadenzaOrderingCheck,
    InjectionSizeCheck,
    PreludeCadenzaFileCheck,
)
from marianne.validation.checks.structure import CadenzaTargetCheck
from marianne.validation.output_contract import _known_suppression_severities
from marianne.validation.rendering import generate_preview


def _score(
    tmp_path: Path,
    *,
    movements: str = "",
    sheet: str = "",
    extra: str = "",
    name: str = "stage-cadenzas-571",
) -> Path:
    """Write a score with a per-test workspace; return its path."""
    workspace = tmp_path / "ws"
    blocks = [f"name: {name}\nworkspace: {workspace}\ninstrument: cli\n"]
    if movements:
        blocks.append("movements:\n" + indent(dedent(movements).strip("\n"), "  ") + "\n")
    blocks.append("sheet:\n" + indent(dedent(sheet).strip("\n"), "  ") + "\n")
    if extra:
        blocks.append(dedent(extra).strip("\n") + "\n")
    blocks.append("prompt:\n  template: hello\n")
    path = tmp_path / f"{name}.yaml"
    path.write_text("".join(blocks))
    return path


def _cadenza_files(config: JobConfig) -> dict[int, list[str]]:
    """Sheet number -> the paths on ``Sheet.cadenza``, in delivery order."""
    return {s.num: [i.file or i.directory or "" for i in s.cadenza] for s in build_sheets(config)}


# The reduced book reproduction (the shape the issue came from): 14 stages,
# fan_out {3: 4, 7: 15, 11: 15, 13: 15}, a loop over stages 10 to 13, and
# cadenzas hand-expanded to concrete sheet numbers. The YAML anchors of
# refs/repro-v225-book-shape.yaml are spelled out, and `instrument: drafter`
# (a score-local alias the refs file declares) is dropped.
BOOK_ORIGINAL = """\
name: book-shape-original
workspace: {workspace}
instrument: cli
movements:
  3: {{name: "Research"}}
  7: {{name: "Draft"}}
  9: {{name: "Synthesis"}}
  11: {{name: "Claim check"}}
  13: {{name: "Repair"}}
sheet:
  size: 1
  total_items: 14
  fan_out:
    3: 4
    7: 15
    11: 15
    13: 15
  dependencies:
    2: [1]
    3: [2]
    4: [3]
    5: [4]
    6: [5]
    7: [6]
    8: [7]
    9: [8]
    10: [9]
    11: [10]
    12: [11]
    13: [12]
    14: [13]
  skip_when:
    7:
      command: 'python3 {{workspace}}/tools/bookcheck.py skip-slot {{workspace}} {{instance}}'
      description: "Draft slot beyond this book's chapter count"
  cadenzas:
    3: &brief
      - file: "{{{{ workspace }}}}/01-brief.md"
        as: context
        required: true
    4: *brief
    5: *brief
    6: *brief
    8:
      - file: "{{{{ workspace }}}}/01-brief.md"
        as: context
        required: true
    10: &frozen
      - file: "{{{{ workspace }}}}/frozen/outline.json"
        as: context
        required: true
    11: *frozen
    12: *frozen
    13: *frozen
    14: *frozen
    15: *frozen
    16: *frozen
    17: *frozen
    18: *frozen
    19: *frozen
    20: *frozen
    21: *frozen
    22: *frozen
    23: *frozen
    24: *frozen
    26:
      - directory: "{{{{ workspace }}}}/units"
        as: context
        required: true
    28:
      - file: "{{{{ workspace }}}}/units/unit-1.md"
        as: context
        required: true
  loops:
    10-13:
      index: pass_no
      until: 'file("qa/APPROVED").exists'
      max_iterations: 3
prompt:
  template: hello
"""

# The same score with every cadenza declared on its movement: no concrete
# sheet number is left in a cadenza declaration (3, 5, 7, 9, 11 are stages).
BOOK_STAGED = """\
name: book-shape-staged
workspace: {workspace}
instrument: cli
movements:
  3:
    name: "Research"
    cadenzas:
      - {{file: "{{{{ workspace }}}}/01-brief.md", as: context, required: true}}
  5:
    name: "Edit"
    cadenzas:
      - {{file: "{{{{ workspace }}}}/01-brief.md", as: context, required: true}}
  7:
    name: "Draft"
    cadenzas:
      - {{file: "{{{{ workspace }}}}/frozen/outline.json", as: context, required: true}}
  9:
    name: "Synthesis"
    cadenzas:
      - {{directory: "{{{{ workspace }}}}/units", as: context, required: true}}
  11:
    name: "Claim check"
    cadenzas:
      - {{file: "{{{{ workspace }}}}/units/unit-{{{{ voice }}}}.md", as: context, required: true}}
  13:
    name: "Repair"
sheet:
  size: 1
  total_items: 14
  fan_out:
    3: 4
    7: 15
    11: 15
    13: 15
  dependencies:
    2: [1]
    3: [2]
    4: [3]
    5: [4]
    6: [5]
    7: [6]
    8: [7]
    9: [8]
    10: [9]
    11: [10]
    12: [11]
    13: [12]
    14: [13]
  skip_when:
    7:
      command: 'python3 {{workspace}}/tools/bookcheck.py skip-slot {{workspace}} {{instance}}'
      description: "Draft slot beyond this book's chapter count"
  loops:
    10-13:
      index: pass_no
      until: 'file("qa/APPROVED").exists'
      max_iterations: 3
prompt:
  template: hello
"""


def _validate(path: Path) -> dict:
    result = CliRunner().invoke(app, ["validate", str(path), "--json"])
    return json.loads(result.stdout)


# ===========================================================================
# Done-when 1: a cadenza keyed by stage reaches every voice of that stage
# ===========================================================================


def test_issue_done_when_cadenza_keyed_by_stage_reaches_every_voice(tmp_path: Path) -> None:
    path = _score(
        tmp_path,
        movements="""\
        1: {name: Plan}
        2:
          name: Build
          cadenzas:
            - {file: shared.md, as: context, required: true}
        3: {name: Check}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        prelude:
          - {file: prelude.md, as: context}
        cadenzas:
          3:
            - {file: sheet-only.md, as: context}
        """,
    )
    config = JobConfig.from_yaml(path)
    # sheet.cadenzas keeps exactly its authored concrete keys.
    assert set(config.sheet.cadenzas) == {3}
    assert [i.file for i in config.sheet.cadenzas[3]] == ["sheet-only.md"]

    sheets = build_sheets(config)
    assert [s.num for s in sheets] == [1, 2, 3, 4, 5]
    got = {s.num: [i.file for i in s.cadenza] for s in sheets}
    # Every voice of stage 2 (sheets 2-4) reads the movement cadenza; sheets
    # 1 and 5 (other stages) do not; sheet 3 reads movement items then its
    # own sheet.cadenzas item, in that order.
    assert got == {
        1: [],
        2: ["shared.md"],
        3: ["shared.md", "sheet-only.md"],
        4: ["shared.md"],
        5: [],
    }
    assert all([i.file for i in s.prelude] == ["prelude.md"] for s in sheets)


def test_voice_field_reaches_one_voice(tmp_path: Path) -> None:
    path = _score(
        tmp_path,
        movements="""\
        1:
          cadenzas:
            - {voice: 1, file: a.md, as: context}
        2:
          name: Build
          cadenzas:
            - {file: shared.md, as: context, required: true}
            - {voice: 2, file: only-2.md, as: context}
            - {voice: 5, file: nowhere.md, as: context}
        3:
          cadenzas:
            - {voice: 2, file: d.md, as: context}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
    )
    config = JobConfig.from_yaml(path)
    got = _cadenza_files(config)
    assert got == {
        1: ["a.md"],        # voice: 1 on a solo movement is delivered (instance 1)
        2: ["shared.md"],   # voice 1 of stage 2: the unvoiced item only
        3: ["shared.md", "only-2.md"],  # voice 2 also reads only-2.md
        4: ["shared.md"],
        5: [],              # voice: 2 on a solo movement reaches nobody
    }
    # voice: 5 on a 3-voice stage reaches nobody (V216 covers it separately).
    assert "nowhere.md" not in {f for files in got.values() for f in files}


def test_voice_template_resolves_per_voice(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    for n in (1, 2, 3):
        (ws / f"unit-{n}.md").write_text(f"UNIT-{n}\n")
    path = _score(
        tmp_path,
        movements="""\
        1: {name: Plan}
        2:
          cadenzas:
            - {file: "unit-{{ voice }}.md", as: context}
        3: {name: Check}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
    )
    config = JobConfig.from_yaml(path)
    renderer = PromptRenderer(
        config.prompt, config.sheet.total_sheets, config.sheet.total_stages, False
    )
    got: dict[int, list[str]] = {}
    for sheet in build_sheets(config):
        context = renderer._build_context(sheet)
        renderer._resolve_injections(context, sheet)
        got[sheet.num] = list(context.injected_context)
    assert got == {1: [], 2: ["UNIT-1\n"], 3: ["UNIT-2\n"], 4: ["UNIT-3\n"], 5: []}


def test_sheet_cadenzas_keys_stay_concrete_under_fan_out(tmp_path: Path) -> None:
    # The hello.yaml shape: cadenzas: {5: ...} under fan_out: {2: 3}. Key 5
    # is the concrete sheet 5 (movement 3's sheet), not stage 5.
    path = _score(
        tmp_path,
        movements="""\
        1:
          cadenzas:
            - {file: y.md, as: context}
        2: {name: B}
        3: {name: C}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        cadenzas:
          5:
            - {file: x.md, as: context}
        """,
    )
    config = JobConfig.from_yaml(path)
    assert set(config.sheet.cadenzas) == {5}
    assert _cadenza_files(config) == {
        1: ["y.md"],
        2: [],
        3: [],
        4: [],
        5: ["x.md"],
    }

    # The shipped example keeps its behaviour.
    repo_root = Path(__file__).resolve().parent.parent
    hello = JobConfig.from_yaml(repo_root / "examples" / "getting-started" / "hello.yaml")
    got = {s.num: [i.file for i in s.cadenza] for s in build_sheets(hello) if s.cadenza}
    assert got == {5: ["{{ score_dir }}/assets/soundtrack-craft.md"]}


def test_movement_cadenzas_are_not_copied_into_sheet_cadenzas(tmp_path: Path) -> None:
    path = _score(
        tmp_path,
        movements="""\
        1:
          cadenzas:
            - {file: a.md, as: context}
        2:
          cadenzas:
            - {file: shared.md, as: context, required: true}
            - {voice: 2, file: only-2.md, as: context}
        3: {name: C}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
    )
    config = JobConfig.from_yaml(path)
    by_stage = movement_cadenzas(config)
    assert sorted(by_stage) == [1, 2]
    assert [getattr(i, "voice", None) for i in by_stage[2]] == [None, 2]
    assert all(isinstance(i, MovementCadenzaItem) for items in by_stage.values() for i in items)

    assert [i.file for i in cadenzas_for_sheet(config, 1)] == ["a.md"]
    assert [i.file for i in cadenzas_for_sheet(config, 3)] == ["shared.md", "only-2.md"]
    assert [i.file for i in cadenzas_for_sheet(config, 2)] == ["shared.md"]
    assert cadenzas_for_sheet(config, 5) == []

    # Pure: the helpers never write movement items into sheet.cadenzas.
    assert config.sheet.cadenzas == {}
    assert stage_sheets(config, 2) == [2, 3, 4]
    assert stage_sheets(config, 1) == [1]
    assert stage_sheets(config, 9) == []


def test_revalidation_and_to_yaml_round_trip_are_fixed_points(tmp_path: Path) -> None:
    path = _score(
        tmp_path,
        movements="""\
        1:
          cadenzas:
            - {file: a.md, as: context}
        2:
          cadenzas:
            - {file: shared.md, as: context, required: true}
            - {voice: 2, file: only-2.md, as: context}
        3: {name: C}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        cadenzas:
          5:
            - {file: x.md, as: context}
        """,
    )
    config = JobConfig.from_yaml(path)
    reference = _cadenza_files(config)

    # The resume path: mzt resume --no-reload re-validates the expanded dump.
    again = JobConfig.model_validate(config.model_dump(mode="json"))
    assert again.model_dump(mode="json") == config.model_dump(mode="json")
    assert _cadenza_files(again) == reference
    assert len(build_sheets(again)) == 5
    assert again.sheet.cadenzas == {5: [config.sheet.cadenzas[5][0]]}

    # The dump path: to_yaml() promises an equivalent from_yaml_string().
    restored = JobConfig.from_yaml_string(config.to_yaml())
    assert len(build_sheets(restored)) == 5
    assert _cadenza_files(restored) == reference
    assert set(restored.sheet.cadenzas) == {5}


def test_preview_injects_a_movement_cadenza_into_each_voice(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "sentinel.md").write_text("SENTINEL_STAGE_CONTEXT_571\n")
    path = _score(
        tmp_path,
        movements="""\
        1: {name: Plan}
        2:
          cadenzas:
            - {file: sentinel.md, as: context}
        3: {name: Check}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
    )
    config = JobConfig.from_yaml(path)
    preview = generate_preview(config, path)
    got = {
        sp.sheet_num: "SENTINEL_STAGE_CONTEXT_571" in (sp.rendered_prompt or "")
        for sp in preview.sheets
    }
    assert got == {1: False, 2: True, 3: True, 4: True, 5: False}


def test_movement_cadenza_is_recorded_as_kind_cadenza(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "note.md").write_text("NOTE\n")
    path = _score(
        tmp_path,
        movements="""\
        2:
          cadenzas:
            - {file: note.md, as: context}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
    )
    config = JobConfig.from_yaml(path)
    renderer = PromptRenderer(
        config.prompt, config.sheet.total_sheets, config.sheet.total_stages, False
    )
    for sheet in build_sheets(config):
        if sheet.num not in (2, 3, 4):
            continue
        context = renderer._build_context(sheet)
        renderer._resolve_injections(context, sheet)
        receipt = PromptRenderer._receipt(sheet, context, "prompt", "preamble")
        kinds = [s.kind for s in receipt.sources if s.kind != "template"]
        assert kinds == ["cadenza"]
        paths = [s.path for s in receipt.sources if s.kind == "cadenza"]
        assert paths == ["note.md"]


# ===========================================================================
# Done-when 2: V225 no longer fires for the book score
# ===========================================================================


def test_issue_done_when_v225_no_longer_fires_for_the_book_score(tmp_path: Path) -> None:
    staged = tmp_path / "book-staged.yaml"
    staged.write_text(BOOK_STAGED.format(workspace=tmp_path / "ws"))
    config = JobConfig.from_yaml(staged)
    assert config.sheet.cadenzas == {}

    got = _cadenza_files(config)
    assert sorted(got) == list(range(1, 60))  # 14 stages, 59 concrete sheets

    brief = "{{ workspace }}/01-brief.md"
    frozen = "{{ workspace }}/frozen/outline.json"
    units = "{{ workspace }}/units"
    unit = "{{ workspace }}/units/unit-{{ voice }}.md"

    def carriers(needle: str) -> list[int]:
        return sorted(n for n, files in got.items() if needle in files)

    assert carriers(brief) == [3, 4, 5, 6, 8]        # stages 3 and 5
    assert carriers(frozen) == list(range(10, 25))   # 15 voices of stage 7
    assert carriers(units) == [26]                   # stage 9 (solo)
    assert carriers(unit) == list(range(28, 43))     # 15 voices of stage 11
    served = (
        set(carriers(brief)) | set(carriers(frozen)) | set(carriers(units)) | set(carriers(unit))
    )
    assert [n for n in got if n not in served and got[n]] == []

    data = _validate(staged)
    v225 = [i for i in data["issues"] if i["check_id"] == "V225"]
    assert v225 == [], f"V225 still fires for the staged book score: {v225}"

    # Control: the concrete-keyed original still fires exactly one V225 and
    # still delivers its concrete keys, and the finding names the remedy.
    original = tmp_path / "book-original.yaml"
    original.write_text(BOOK_ORIGINAL.format(workspace=tmp_path / "ws"))
    original_config = JobConfig.from_yaml(original)
    expected_keys = [3, 4, 5, 6, 8] + list(range(10, 25)) + [26, 28]
    assert sorted(original_config.sheet.cadenzas) == expected_keys
    original_files = _cadenza_files(original_config)
    assert original_files[3] == ["{{ workspace }}/01-brief.md"]
    assert original_files[10] == ["{{ workspace }}/frozen/outline.json"]

    data = _validate(original)
    v225 = [i for i in data["issues"] if i["check_id"] == "V225"]
    assert len(v225) == 1
    assert v225[0]["severity"] == "info"
    assert "movements.<stage>.cadenzas" in v225[0]["message"]
    assert "movements.<stage>.cadenzas" in (v225[0].get("suggestion") or "")


def test_v216_reports_unknown_stage_and_voice_above_count(tmp_path: Path) -> None:
    def v216(path: Path) -> list:
        config = JobConfig.from_yaml(path)
        return CadenzaTargetCheck().check(config, path, path.read_text())

    unknown = _score(
        tmp_path,
        movements="""\
        1: {name: A}
        2: {name: B}
        3: {name: C}
        9:
          cadenzas:
            - {file: x.md, as: context}
        """,
        sheet="size: 1\ntotal_items: 3\n",
        name="unknown-stage",
    )
    got = [i for i in v216(unknown) if i.check_id == "V216"]
    assert len(got) == 1
    assert got[0].severity.value == "error"
    assert "9" in got[0].message

    too_high = _score(
        tmp_path,
        movements="""\
        2:
          cadenzas:
            - {voice: 4, file: x.md, as: context}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
        name="voice-too-high",
    )
    got = [i for i in v216(too_high) if i.check_id == "V216"]
    assert len(got) == 1
    assert got[0].severity.value == "error"
    assert "4" in got[0].message

    valid = _score(
        tmp_path,
        movements="""\
        1:
          cadenzas:
            - {file: x.md, as: context}
        2:
          cadenzas:
            - {voice: 3, file: x.md, as: context}
        3:
          cadenzas:
            - {voice: 1, file: x.md, as: context}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
        name="valid-targets",
    )
    got = [i for i in v216(valid) if i.check_id == "V216"]
    assert got == []

    # The pre-existing concrete-key check is intact.
    legacy = _score(
        tmp_path,
        sheet="""\
        size: 1
        total_items: 3
        cadenzas:
          9:
            - {file: x.md, as: context}
        """,
        name="legacy",
    )
    got = [i for i in v216(legacy) if i.check_id == "V216"]
    assert len(got) == 1


def test_v216_registered_severity() -> None:
    severities = _known_suppression_severities()
    assert "V216" in severities
    assert severities["V216"].value == "error"


def test_v108_and_v334_report_a_movement_item_once(tmp_path: Path) -> None:
    # Two missing movement items on a 3-voice stage: two V108 findings,
    # labelled by stage — not six (once per voice).
    missing = _score(
        tmp_path,
        movements="""\
        1: {name: A}
        2:
          cadenzas:
            - {file: missing-a.md, as: context}
            - {file: missing-b.md, as: context}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
        name="missing",
    )
    config = JobConfig.from_yaml(missing)
    issues = PreludeCadenzaFileCheck().check(config, missing, missing.read_text())
    got = sorted((i.check_id, i.metadata.get("source")) for i in issues)
    assert got == [("V108", "cadenza (stage 2)"), ("V108", "cadenza (stage 2)")]

    # A movement cadenza reading a declared product is exempt from V108.
    exempt = _score(
        tmp_path,
        movements="""\
        2:
          cadenzas:
            - {file: report.md, as: context}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
        extra="""\
        validations:
          - {type: file_exists, path: report.md, sheet: 1}
        """,
        name="exempt",
    )
    config = JobConfig.from_yaml(exempt)
    issues = PreludeCadenzaFileCheck().check(config, exempt, exempt.read_text())
    assert [i for i in issues if "report.md" in i.message] == []

    # V334 measures the item once, not once per voice.
    (tmp_path / "big.md").write_text("x" * 2048)
    over_cap = _score(
        tmp_path,
        movements="""\
        2:
          cadenzas:
            - {file: big.md, as: context, max_bytes: 10}
        """,
        sheet="""\
        size: 1
        total_items: 3
        fan_out: {2: 3}
        """,
        name="over-cap",
    )
    config = JobConfig.from_yaml(over_cap)
    issues = InjectionSizeCheck().check(config, over_cap, over_cap.read_text())
    got = [(i.check_id, i.metadata.get("source"), i.severity.value) for i in issues]
    assert got == [("V334", "cadenza (stage 2)", "warning")]


def test_v109_judges_a_movement_cadenza_per_voice(tmp_path: Path) -> None:
    # Stage 1 is a solo sheet; stages 2 (the reader) and 3 have 3 voices
    # each: stage 2 = sheets 2-4, stage 3 = sheets 5-7. Stage-keyed
    # dependencies under fan-out connect voice i -> voice i
    # (instance-matched), which is what makes the ancestor sets of one
    # stage's voices differ.
    def staged(dependencies: str, producer: int) -> Path:
        return _score(
            tmp_path,
            movements="""\
            1: {name: Solo}
            2:
              cadenzas:
                - {file: report.md, as: context}
            3: {name: Later}
            """,
            sheet=f"""\
            size: 1
            total_items: 3
            fan_out: {{2: 3, 3: 3}}
            dependencies: {dependencies}
            """,
            extra=f"""\
            validations:
              - {{type: file_exists, path: report.md, sheet: {producer}}}
            """,
            name=f"v109-{dependencies}-{producer}".replace(" ", ""),
        )

    def v109(path: Path) -> list:
        config = JobConfig.from_yaml(path)
        return list(CadenzaOrderingCheck().check(config, path, path.read_text()))

    # A producer after the reader stage warns once.
    got = v109(staged("{3: [2]}", 5))
    assert len(got) == 1
    assert got[0].severity.value == "warning"
    assert "report.md" in got[0].message

    # A producer behind only one voice of the reader stage warns exactly
    # once — not once per unsafe voice, and not silently.
    got = v109(staged("{2: [3]}", 6))
    assert len(got) == 1

    # A producer behind every voice is silent.
    assert v109(staged("{2: [1]}", 1)) == []


def test_v004_flags_a_home_absolute_path_in_a_movement_cadenza(tmp_path: Path) -> None:
    planted = str(Path.home() / "stage-cadenzas-571-secret.md")
    path = _score(
        tmp_path,
        movements="""\
        2:
          cadenzas:
            - {file: PLANTED, as: context}
        """,
        sheet="size: 1\ntotal_items: 3\n",
        name="v004",
    )
    text = path.read_text().replace("PLANTED", planted)
    path.write_text(text)
    config = JobConfig.from_yaml(path)
    issues = AbsoluteHomePathCheck().check(config, path, path.read_text())
    flagged = [i for i in issues if i.check_id == "V004" and i.metadata.get("path") == planted]
    assert len(flagged) == 1
    assert flagged[0].severity.value == "error"
    assert "movements.2.cadenzas" in flagged[0].metadata.get("field", "")


def test_voice_is_refused_outside_movements(tmp_path: Path) -> None:
    # Under sheet.cadenzas the field is stripped and reported (V010/V011).
    path = _score(
        tmp_path,
        sheet="""\
        size: 1
        total_items: 2
        cadenzas:
          2:
            - {file: x.md, as: context, voice: 1}
        """,
        name="misplaced-cadenzas",
    )
    config = JobConfig.from_yaml(path)
    item = config.sheet.cadenzas[2][0]
    assert type(item).__name__ == "InjectionItem"
    assert not hasattr(item, "voice")
    assert any(f.key == "voice" for f in config.unknown_fields)

    # Under sheet.prelude it is stripped and reported too.
    path = _score(
        tmp_path,
        sheet="""\
        size: 1
        total_items: 2
        prelude:
          - {file: x.md, as: context, voice: 1}
        """,
        name="misplaced-prelude",
    )
    config = JobConfig.from_yaml(path)
    assert [f.path for f in config.unknown_fields if f.key == "voice"] == ["sheet.prelude[0]"]
    assert not hasattr(config.sheet.prelude[0], "voice")

    # In its own home it loads and survives.
    path = _score(
        tmp_path,
        movements="""\
        2:
          cadenzas:
            - {file: x.md, as: context, voice: 1}
        """,
        sheet="size: 1\ntotal_items: 2\n",
        name="well-placed",
    )
    config = JobConfig.from_yaml(path)
    assert not [f for f in config.unknown_fields if f.key == "voice"]
    items = config.movements[2].cadenzas
    assert items and items[0].voice == 1
