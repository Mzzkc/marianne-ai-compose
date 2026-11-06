"""Keep pattern proof pointers usable and citation telemetry independent of proofs."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "scores/rosetta-corpus"
TOOL = CORPUS / "tools/unproven.py"
LEGACY = {
    "dead-letter-quarantine.yaml",
    "echelon-repair.yaml",
    "immune-cascade.yaml",
    "prefabrication.yaml",
    "shipyard-sequence.yaml",
    "source-triangulation.yaml",
}


def run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def patterns() -> dict[str, dict]:
    return {
        path.stem: yaml.safe_load(path.read_text().split("---", 2)[1])
        for path in (CORPUS / "patterns").glob("*.md")
    }


def test_proof_pointers_resolve() -> None:
    for slug, meta in patterns().items():
        if target := meta.get("proof_score"):
            assert Path(target).name == target, slug
            assert (CORPUS / "proof-scores" / target).is_file(), (slug, target)


def test_every_ledger_primary_has_a_pointer() -> None:
    result = run_tool(str(CORPUS), "--json")
    assert result.returncode == 0, result.stderr
    meta = patterns()
    for slug, proofs in json.loads(result.stdout)["primary"].items():
        assert meta[slug].get("proof_score") in [p + ".yaml" for p in proofs], slug


def test_pointers_name_their_pattern_as_primary() -> None:
    for slug, meta in patterns().items():
        if target := meta.get("proof_score"):
            header = (CORPUS / "proof-scores" / target).read_text()[:8000]
            declaration = r"PRIMARY PATTERN\s*:\s*([^\n]+)"
            if target in LEGACY:
                declaration = r"Pattern\s*:\s*([^\n]+)"
            match = re.search(declaration, header)
            assert match and meta["name"].casefold() in match[1].casefold(), slug


@pytest.fixture
def usage_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "scores/rosetta-corpus"
    (corpus / "patterns").mkdir(parents=True)
    (corpus / "proof-scores").mkdir()
    for slug, name in {"alpha": "Alpha (Review)", "beta": "Beta", "gamma": "Gamma"}.items():
        (corpus / "patterns" / f"{slug}.md").write_text(f'---\nname: "{name}"\n---\n')
    (corpus / "proof-scores/example.yaml").write_text("# PRIMARY PATTERN : Beta\n")
    (tmp_path / "scores/run.yaml").write_text("# ALPHA (REVIEW) and alpha (review)\n")
    (tmp_path / "scores/nested").mkdir()
    (tmp_path / "scores/nested/run.yaml").write_text("# Beta + Alpha (Review)\n")
    (tmp_path / "scores/ignored.md").write_text("Gamma")
    return corpus


def test_usage_counts_citing_compositions_once_and_excludes_proofs(usage_corpus: Path) -> None:
    result = run_tool(str(usage_corpus), "--usage", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"alpha": 2, "beta": 1, "gamma": 0}


def test_usage_explicit_directory_includes_proofs_when_requested(usage_corpus: Path) -> None:
    result = run_tool(str(usage_corpus), "--usage", str(usage_corpus), "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"alpha": 0, "beta": 1, "gamma": 0}


def test_usage_human_report_includes_uncited_patterns(usage_corpus: Path) -> None:
    result = run_tool(str(usage_corpus), "--usage")
    assert result.returncode == 0, result.stderr
    assert "Most-cited" in result.stdout
    assert "Never-cited" in result.stdout
    assert "Gamma" in result.stdout


def test_usage_rejects_missing_directory(usage_corpus: Path) -> None:
    result = run_tool(str(usage_corpus), "--usage", str(usage_corpus / "absent"))
    assert result.returncode != 0
    assert "directory" in result.stderr.lower()


def test_check_still_refuses_an_existing_primary(usage_corpus: Path) -> None:
    result = run_tool(str(usage_corpus), "--check", "beta")
    assert result.returncode == 1
    assert "ALREADY PROVEN" in result.stdout
