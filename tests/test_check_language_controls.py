"""Positive and negative controls for check_language.py.

Each check runs against a synthetic four-layer repository whose defects are
planted on purpose: the test proves the check FIRES on the planted defect and
stays SILENT on the matching correct form. A checker that only passes on the
real tree proves nothing about the defect class it claims to catch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import check_language as cl  # noqa: E402


class Stale(BaseModel):
    """Idle detection for a running sheet."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False, description="Turn idle detection on")
    idle_timeout_seconds: float = Field(
        default=300.0, description="Idle seconds before the sheet fails"
    )


class Cost(BaseModel):
    """Money guard that turns itself on when a limit is set."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False, description="Enforce the limits")
    max_cost_per_job: float | None = Field(default=None, description="Job limit in USD")

    @model_validator(mode="before")
    @classmethod
    def _auto_enable_when_limits_set(cls, data: Any) -> Any:
        return data


class Sched(BaseModel):
    """Recurring execution."""

    model_config = ConfigDict(extra="forbid")
    cron: str | None = Field(default=None, description="Five-field cron expression")


class Root(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Score name")
    retry: dict[str, Any] = Field(default_factory=dict)
    workspace: str = Field(default=".", description="Output directory")
    max_wall_seconds: float | None = Field(
        default=None, description="Wall-clock bound for a scheduled run"
    )
    stale_detection: Stale = Field(default_factory=Stale)
    cost_limits: Cost = Field(default_factory=Cost)
    schedule: Sched | None = Field(default=None, description="Recurring schedule for this score")
    orphan_key: int = Field(default=0, description="Orphan key nobody teaches")


SKILL = """\
Write `name`, `retry`, `workspace`, `max_wall_seconds`, `schedule` and `cron`.
Use `stale_detection.idle_timeout_seconds` with `enabled`. Use `cost_limits.max_cost_per_job`.
"""

DOCS_TEACHING = """\
Good example:

```yaml
name: ok
stale_detection:
  enabled: true
  idle_timeout_seconds: 600
```

A typo the loader drops:

```yaml
name: bad
stale_detection:
  idle_timeout_secs: 600
```

A proposal that is not the language:

```yaml
# PROPOSED config - does not exist
name: future
stale_detection:
  telepathy: true
```

For conductor configuration (in `~/.marianne/conductor.yaml`):

```yaml
learning:
  retention_days: 30
```
"""

CONSUMER = """\
def use(config):
    return (config.name, config.retry, config.workspace, config.stale_detection.enabled,
            config.stale_detection.idle_timeout_seconds, config.cost_limits.enabled,
            config.cost_limits.max_cost_per_job, config.schedule.cron)
"""

HINTS = """\
GOOD = "Set stale_detection.enabled to true"
BAD = "Set stale_detection.idle_timout to 600"
"""


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _write(tmp_path, "plugins/marianne/skills/score-authoring/SKILL.md", SKILL)
    _write(tmp_path, "plugins/marianne/skills/composing/SKILL.md", "")
    for ref in cl.SKILL_REFS:
        _write(tmp_path, ref, "")
    _write(tmp_path, "docs/configuration-reference.md", "")
    _write(tmp_path, "docs/teaching.md", DOCS_TEACHING)
    documented = sorted(cl.emittable_vcodes() - {"V203"})
    table = "\n".join(f"| WARNING | {code} | text |" for code in documented)
    _write(tmp_path, "docs/cli-reference.md", f"### `mzt validate`\n\n{table}\n\n### next\n")
    _write(tmp_path, "src/marianne/consumer.py", CONSUMER)
    _write(tmp_path, "src/marianne/validation/hints.py", HINTS)
    return tmp_path


def _ids(repo: Path) -> set[str]:
    _keys, findings, _meta = cl.run_checks(repo, Root)
    return {f.id for f in findings}


def test_l01_fires_on_undescribed_and_not_on_described(repo: Path) -> None:
    ids = _ids(repo)
    assert "L01:retry" in ids
    assert "L01:name" not in ids


def test_l02_fires_on_divergent_name_only(repo: Path) -> None:
    ids = _ids(repo)
    assert "L02:workspace" in ids
    assert "L02:schedule" not in ids


def test_l03_fires_on_scheduled_semantics_outside_schedule(repo: Path) -> None:
    ids = _ids(repo)
    assert "L03:max_wall_seconds" in ids
    assert "L03:schedule.cron" not in ids


def test_l04_fires_on_phantom_validator_path_only(repo: Path) -> None:
    ids = _ids(repo)
    assert "L04:stale_detection.idle_timout" in ids
    assert "L04:stale_detection.enabled" not in ids


def test_l05_fires_on_untaught_key_only(repo: Path) -> None:
    ids = _ids(repo)
    assert "L05:orphan_key" in ids
    assert "L05:stale_detection.idle_timeout_seconds" not in ids


def test_l06_judges_each_block_in_its_own_language(repo: Path) -> None:
    ids = _ids(repo)
    assert "L06:stale_detection.idle_timeout_secs" in ids
    assert "L06:stale_detection.telepathy" not in ids, "a PROPOSED block is not teaching"
    assert "L06:conductor:learning.retention_days" in ids, (
        "a conductor.yaml block is judged by DaemonConfig"
    )
    assert not any(i.startswith("L06:name") for i in ids)


def test_l07_fires_on_the_one_undocumented_code(repo: Path) -> None:
    ids = {i for i in _ids(repo) if i.startswith("L07:")}
    assert ids == {"L07:V203"}


def test_l08_fires_on_unread_field_only(repo: Path) -> None:
    ids = _ids(repo)
    assert "L08:orphan_key" in ids
    assert "L08:stale_detection.idle_timeout_seconds" not in ids


def test_l09_spares_auto_enabling_sections(repo: Path) -> None:
    ids = _ids(repo)
    assert "L09:stale_detection" in ids
    assert "L09:cost_limits" not in ids


def test_l10_fires_when_a_typo_hint_lands_on_a_switched_off_section(
    repo: Path, monkeypatch: Any
) -> None:
    # D7 fixed the real table ("timeout" now names the sheet timeout), so the
    # dead-end shape is planted here to prove the RULE still catches it.
    from marianne.validation.checks import schema as schema_check

    monkeypatch.setattr(
        schema_check,
        "_KNOWN_TYPOS",
        {**schema_check._KNOWN_TYPOS, "timeout": "stale_detection.idle_timeout_seconds"},
    )
    ids = _ids(repo)
    assert "L10:stale_detection.idle_timeout_seconds" in ids


def test_l10_spares_a_hint_that_names_the_switch(
    repo: Path, monkeypatch: Any
) -> None:
    from marianne.validation.checks import schema as schema_check

    monkeypatch.setattr(
        schema_check,
        "_KNOWN_TYPOS",
        {
            **schema_check._KNOWN_TYPOS,
            "stagger": (
                "stale_detection.idle_timeout_seconds"
                " (requires stale_detection.enabled: true)"
            ),
        },
    )
    ids = _ids(repo)
    assert "L10:stale_detection.idle_timeout_seconds" not in ids


def test_l11_fires_on_an_unregistered_check_class(repo: Path) -> None:
    _write(
        repo,
        "src/marianne/validation/orphan.py",
        "class OrphanCheck:\n"
        "    @property\n"
        "    def check_id(self) -> str:\n"
        '        return "V987"\n',
    )
    ids = _ids(repo)
    assert "L11:V987" in ids


def test_l11_silent_on_registered_classes(repo: Path) -> None:
    ids = {i for i in _ids(repo) if i.startswith("L11:")}
    assert ids == set()


def test_baseline_turns_known_gaps_green_and_a_new_gap_red(
    repo: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    import marianne.core.config.job as job_module

    monkeypatch.setattr(job_module, "JobConfig", Root)
    baseline = tmp_path / "baseline.json"
    foreign = ["--json", "--allow-foreign-import"]  # the synthetic repo has no src/marianne
    assert cl.main(["--repo", str(repo), "--write-baseline", str(baseline), *foreign]) == 1
    assert cl.main(["--repo", str(repo), "--baseline", str(baseline), *foreign]) == 0
    data = json.loads(baseline.read_text())
    data["findings"].remove("L03:max_wall_seconds")
    baseline.write_text(json.dumps(data))
    assert cl.main(["--repo", str(repo), "--baseline", str(baseline), *foreign]) == 1


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_path: str | None = Field(default=None, description="Reference file for a comparison")


class RootWithHomonym(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Score name")
    source_path: str | None = Field(default=None, description="Path of the score file (loader-set)")
    validations: list[Rule] = Field(default_factory=list, description="Validation rules")


def test_l05_does_not_credit_a_root_key_for_a_homonym_taught_elsewhere(repo: Path) -> None:
    # The skill teaches `source_path` only as a validation-rule field.
    _write(
        repo,
        "plugins/marianne/skills/score-authoring/SKILL.md",
        "Use `name` and `validations`. A rule's `source_path` names the reference file.\n"
        "```yaml\nvalidations:\n  - source_path: ref.json\n```\n",
    )
    _keys, findings, _meta = cl.run_checks(repo, RootWithHomonym)
    ids = {f.id for f in findings}
    assert "L05:source_path" in ids, "root source_path is not taught by the rule-field mention"
    assert "L05:validations[].source_path" not in ids, "the rule field IS taught"


def test_a_missing_skill_surface_fails_loud_instead_of_scoring_empty(repo: Path) -> None:
    """Gate seam (Lens Integration P2 #1): a bare checkout has no plugins
    submodule; scoring a missing skill file as "" would report every
    skill-taught key as an L05 orphan with a straight face."""
    (repo / "plugins/marianne/skills/score-authoring/SKILL.md").unlink()
    with pytest.raises(cl.SurfaceMissing, match="plugins submodule"):
        cl.run_checks(repo, Root)


def test_main_refuses_a_marianne_imported_from_outside_the_repo(
    repo: Path, capsys: Any
) -> None:
    """Gate seam (Lens Integration P2 #3): the schema and validator registry
    must come from the same tree as the docs and skills."""
    assert cl.main(["--repo", str(repo), "--json"]) == 2
    assert "not under" in capsys.readouterr().err
    assert cl.main(["--repo", str(repo), "--json", "--allow-foreign-import"]) != 2
