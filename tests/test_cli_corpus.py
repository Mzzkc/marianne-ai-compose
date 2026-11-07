"""The corpus CLI assembles private local data without modifying its sources."""

import json
import stat

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.corpus.schema import TrainingExample

runner = CliRunner()


@pytest.mark.parametrize("command", [[], ["build"], ["stats"]])
def test_help(command):
    result = runner.invoke(app, ["corpus", *command, "--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_build_and_stats_over_mixed_sources(tmp_path):
    source = tmp_path / "examples"
    source.mkdir()
    (source / "one.yaml").write_text("name: one\n")
    (source / "two.yaml").write_text("name: one\n")
    spool = tmp_path / "telemetry.jsonl"
    spool.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "consent_id": "grant",
                "payload": {"score": {"name": "other"}},
            }
        )
        + "\n"
    )
    sources = f"{source},{spool}"
    result = runner.invoke(app, ["corpus", "stats", "--sources", sources, "--json"])
    assert result.exit_code == 0, result.output
    stats = json.loads(result.stdout)
    assert stats["total_examples"] == 2
    assert stats["by_source_kind"] == {"in_repo": 1, "telemetry": 1}
    destination = tmp_path / "corpus.jsonl"
    destination.write_text("old contents")
    destination.chmod(0o644)
    result = runner.invoke(
        app,
        [
            "corpus",
            "build",
            "--sources",
            sources,
            "--output",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    records = [
        TrainingExample.model_validate_json(line) for line in destination.read_text().splitlines()
    ]
    assert len(records) == 2
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert spool.exists()
    assert (
        "Total examples: 2"
        in runner.invoke(
            app,
            ["corpus", "stats", "--sources", sources],
        ).output
    )


@pytest.mark.parametrize("sources", ["", "missing", "score.txt"])
def test_invalid_sources_fail_without_creating_output(tmp_path, sources, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "score.txt").write_text("name: wrong format")
    destination = tmp_path / "out.jsonl"
    result = runner.invoke(
        app, ["corpus", "build", "--sources", sources, "--output", str(destination)]
    )
    assert result.exit_code != 0
    assert not destination.exists()


def test_build_cannot_overwrite_source_or_lock(tmp_path):
    spool = tmp_path / "telemetry.jsonl"
    spool.write_text("{}\n")
    for destination in (spool, tmp_path / "state.lock"):
        result = runner.invoke(
            app,
            [
                "corpus",
                "build",
                "--sources",
                str(spool),
                "--output",
                str(destination),
            ],
        )
        assert result.exit_code != 0
    assert spool.read_text() == "{}\n"


def test_build_cannot_overwrite_score_discovered_in_source_directory(tmp_path):
    score = tmp_path / "score.yaml"
    original = b"name: keep this score\n"
    score.write_bytes(original)
    result = runner.invoke(
        app,
        [
            "corpus",
            "build",
            "--sources",
            str(tmp_path),
            "--output",
            str(score),
        ],
    )
    assert result.exit_code != 0
    assert score.read_bytes() == original


def test_failed_output_replace_preserves_old_export_and_removes_temporary(tmp_path, monkeypatch):
    from pathlib import Path

    source = tmp_path / "examples"
    source.mkdir()
    (source / "score.yaml").write_text("name: example\n")
    destination = tmp_path / "output.jsonl"
    destination.write_text("previous export\n")

    def refuse_replace(self, target):
        raise OSError("private path and credentials")

    monkeypatch.setattr(Path, "replace", refuse_replace)
    result = runner.invoke(
        app,
        [
            "corpus",
            "build",
            "--sources",
            str(source),
            "--output",
            str(destination),
        ],
    )
    assert result.exit_code == 1
    assert "credentials" not in result.output
    assert destination.read_text() == "previous export\n"
    assert list(tmp_path.glob(".output.jsonl.*")) == []


def test_build_cannot_overwrite_global_consent_record(tmp_path, monkeypatch):
    from marianne.telemetry import ConsentRecord

    monkeypatch.setenv("HOME", str(tmp_path))
    consent = ConsentRecord()
    consent.grant()
    original = consent.path.read_bytes()
    sources = tmp_path / "examples"
    sources.mkdir()
    (sources / "score.yaml").write_text("name: sample\n")
    result = runner.invoke(
        app,
        [
            "corpus",
            "build",
            "--sources",
            str(sources),
            "--output",
            str(consent.path),
        ],
    )
    assert result.exit_code != 0
    assert consent.path.read_bytes() == original
