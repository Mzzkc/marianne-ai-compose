"""``mzt validate --render-prompts``: every sheet's full prompt, deduplicated (GH #89, #138)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config import JobConfig
from marianne.validation.rendering import (
    _sheet_range_label,
    generate_preview,
    render_prompts_markdown,
)

runner = CliRunner()


def _json(output: str) -> dict:
    """The JSON document in CLI output; a logging line may precede it under pytest."""
    return json.loads(output[output.index("{"):])


def _score(tmp_path: Path) -> Path:
    (tmp_path / "ws").mkdir()
    path = tmp_path / "score.yaml"
    path.write_text(dedent(f"""\
        name: dedup-test
        workspace: {tmp_path / "ws"}
        sheet:
          size: 1
          total_items: 3
          fan_out:
            2: 2
        prompt:
          template: |
            {{% if stage == 2 %}}
            Build the thing (shared by every voice).
            {{% else %}}
            Stage {{{{ stage }}}}: sheet {{{{ sheet_num }}}}.
            {{% endif %}}
    """))
    return path


def test_range_label_collapses_runs() -> None:
    assert _sheet_range_label([1]) == "1"
    assert _sheet_range_label([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10"


def test_markdown_groups_identical_renders(tmp_path: Path) -> None:
    path = _score(tmp_path)
    preview = generate_preview(JobConfig.from_yaml(path), path)
    md = render_prompts_markdown(preview, "dedup-test")
    assert "4 sheet(s), 3 distinct prompt(s)." in md
    assert "## Sheets 2-3" in md  # the two voices share one section
    assert "## Sheet 1" in md and "## Sheet 4" in md
    assert "Build the thing (shared by every voice)." in md
    assert md.count("Build the thing") == 1


def test_markdown_reports_a_render_error_per_sheet(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    path = tmp_path / "bad.yaml"
    path.write_text(dedent(f"""\
        name: bad-render
        workspace: {tmp_path / "ws"}
        sheet:
          size: 1
          total_items: 1
        prompt:
          template: "{{{{ missing | nonexistent_filter }}}}"
    """))
    preview = generate_preview(JobConfig.from_yaml(path), path)
    md = render_prompts_markdown(preview, "bad-render")
    assert "1 render error(s)" in md
    assert "## Sheet 1 — render error" in md


def test_cli_writes_the_dump_and_names_it(tmp_path: Path) -> None:
    path = _score(tmp_path)
    out = tmp_path / "out" / "prompts.md"
    result = runner.invoke(app, ["validate", str(path), "--render-prompts", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "Rendered prompts:" in result.output and "3 distinct" in result.output
    assert "## Sheets 2-3" in out.read_text()


def test_cli_dash_prints_the_dump(tmp_path: Path) -> None:
    path = _score(tmp_path)
    result = runner.invoke(app, ["validate", str(path), "--render-prompts", "-"])
    assert result.exit_code == 0, result.output
    assert "# Rendered prompts — dedup-test" in result.output
    assert "Build the thing (shared by every voice)." in result.output


def test_json_carries_full_prompts_only_when_asked(tmp_path: Path) -> None:
    path = _score(tmp_path)
    plain = runner.invoke(app, ["validate", str(path), "--json"])
    assert plain.exit_code == 0, plain.output
    sheets = _json(plain.output)["rendering"]["sheets"]
    assert "rendered_prompt" not in sheets[0]

    out = tmp_path / "prompts.md"
    full = runner.invoke(
        app, ["validate", str(path), "--json", "--render-prompts", str(out)],
    )
    assert full.exit_code == 0, full.output
    sheets = _json(full.output)["rendering"]["sheets"]
    assert sheets[1]["rendered_prompt"] == sheets[2]["rendered_prompt"]
    assert "Build the thing" in sheets[1]["rendered_prompt"]
    assert out.exists()
