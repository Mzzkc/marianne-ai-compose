"""V-CLS frame: capability-class validation checks (design §8).

One true-positive test per §8 row at its tier, plus the negative controls:
a class in the vocabulary but unconfigured fires V310 (not V210/V211's typo
row), a broken layer file stays silent for scores that name no class, and an
alias beats a class of the same name (§3.1) with only a V322 WARN.

Class state is exercised through the REAL loader: `class_source_paths` is
pointed at temp user/venue layers so `load_class_map` runs its own merge,
refusal, and sha256 bookkeeping — nothing here re-implements layer resolution.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config import JobConfig
from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    HttpProfile,
    InstrumentProfile,
)
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.capability_classes import (
    AliasShadowsClassCheck,
    ClassAliasProfileCheck,
    ClassChainAvailabilityCheck,
    ClassChainSummaryCheck,
    ClassesFileValidCheck,
    ClassModelConfigCheck,
    ClassNameResolutionCheck,
)
from marianne.validation.output_contract import apply_suppression, class_summary
from marianne.validation.runner import create_default_checks

_MISSING_BIN = "definitely-not-on-path-bin-xyz"

# Every profile the shipped default.yaml names, so default classes resolve
# fully under the fake registry and tests vary only what they own.
_DEFAULT_PROFILES = ("claude-code", "codex-cli", "antigravity", "opencode", "crush", "ollama")


def _profile(
    name: str,
    *,
    executable: str = "bash",
    raw_prompt: bool = False,
) -> InstrumentProfile:
    return InstrumentProfile(
        name=name,
        display_name=f"Fake {name}",
        kind="cli",
        capabilities={"tool_use"},
        raw_prompt=raw_prompt,
        cli=CliProfile(
            command=CliCommand(executable=executable, prompt_flag=None),
            output=CliOutputConfig(format="text"),
        ),
    )


def _fake_profiles(
    extra: dict[str, InstrumentProfile] | None = None,
) -> dict[str, InstrumentProfile]:
    profiles = {name: _profile(name) for name in _DEFAULT_PROFILES}
    profiles["ollama"] = InstrumentProfile(
        name="ollama",
        display_name="Fake ollama",
        kind="http",
        capabilities={"tool_use"},
        http=HttpProfile(base_url="http://localhost:11434", schema_family="openai"),
    )
    profiles["cli"] = _profile("cli", raw_prompt=True)  # the bash instrument
    profiles["gemini-cli"] = _profile("gemini-cli", executable=_MISSING_BIN)
    profiles.update(extra or {})
    return profiles


class _Layers:
    """Temp user/venue layers wired into the real loader for one test."""

    def __init__(
        self,
        monkeypatch,
        tmp_path: Path,
        user: dict[str, object] | None = None,
        venue: dict[str, object] | None = None,
    ) -> None:
        import marianne.instruments.classes as classes_module

        self.user_path = tmp_path / "user-classes.yaml"
        self.venue_path = tmp_path / "venue-classes.yaml"
        if user is not None:
            self.user_path.write_text(yaml.safe_dump(user, sort_keys=False))
        if venue is not None:
            self.venue_path.write_text(yaml.safe_dump(venue, sort_keys=False))
        real_default = (
            Path(classes_module.__file__).parent / "classes" / "default.yaml"
        ).resolve()

        def _paths(**_: object) -> tuple[Path, Path, Path]:
            return real_default, self.user_path, self.venue_path

        monkeypatch.setattr(classes_module, "class_source_paths", _paths)
        import marianne.instruments.loader as loader_module

        monkeypatch.setattr(
            loader_module, "load_all_profiles", lambda: _fake_profiles()
        )


def _score_yaml(**fields: object) -> str:
    base = {
        "name": "cls-test",
        "sheet": {"size": 2, "total_items": 2},
        "prompt": {"template": "work"},
    }
    base.update(fields)
    return yaml.safe_dump(base, sort_keys=False)


def _config(**fields: object) -> JobConfig:
    return JobConfig.model_validate(yaml.safe_load(_score_yaml(**fields)))


def _run(check, config: JobConfig, raw: str | None = None):
    return check.check(config, Path("score.yaml"), raw or _score_yaml())


# ---------------------------------------------------------------------------
# V310 — V-CLS-02: class-shaped names that cannot resolve here
# ---------------------------------------------------------------------------


class TestV310ClassNameResolution:
    def test_vocabulary_only_class_is_error_with_fix_command(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)  # no user/venue layers: defaults only
        # 'image' is in the shipped vocabulary but not in default.yaml.
        issues = _run(ClassNameResolutionCheck(), _config(instrument="image"))
        assert [i.check_id for i in issues] == ["V310"]
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "class 'image' has no instruments configured" in issues[0].message
        assert "mzt instruments classes write" in issues[0].message

    def test_tombstoned_class_is_error(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path, user={"version": 1, "classes": {"strong": None}})
        issues = _run(ClassNameResolutionCheck(), _config(instrument="strong"))
        assert [i.check_id for i in issues] == ["V310"]
        assert "class 'strong' has no instruments configured" in issues[0].message

    def test_all_entries_unregistered_is_error(self, monkeypatch, tmp_path) -> None:
        _Layers(
            monkeypatch,
            tmp_path,
            user={"version": 1, "classes": {"orphan": ["ghost-a", "ghost-b"]}},
        )
        issues = _run(ClassNameResolutionCheck(), _config(instrument="orphan"))
        assert [i.check_id for i in issues] == ["V310"]
        assert "ghost-a" in (issues[0].context or "")

    def test_configured_class_is_silent(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        assert _run(ClassNameResolutionCheck(), _config(instrument="strong")) == []

    def test_v210_does_not_double_report_class_domain(self, monkeypatch, tmp_path) -> None:
        from marianne.validation.checks.config import InstrumentNameCheck

        _Layers(monkeypatch, tmp_path)
        config = _config(instrument="image")
        raw = _score_yaml(instrument="image")
        assert InstrumentNameCheck().check(config, Path("s.yaml"), raw) == []

    def test_typo_stays_v210_with_suggestion(self, monkeypatch, tmp_path) -> None:
        from marianne.validation.checks.config import InstrumentNameCheck

        _Layers(monkeypatch, tmp_path)
        config = _config(instrument="clause-code")
        raw = _score_yaml(instrument="clause-code")
        issues = InstrumentNameCheck().check(config, Path("s.yaml"), raw)
        assert [i.check_id for i in issues] == ["V210"]
        assert "claude-code" in (issues[0].suggestion or "")
        assert "capability class" in issues[0].message


# ---------------------------------------------------------------------------
# V311 — V-CLS-03 partial availability, V-CLS-08 shadowed default class
# ---------------------------------------------------------------------------


class TestV311ChainAvailability:
    def test_partial_chain_warns_with_first_available(self, monkeypatch, tmp_path) -> None:
        _Layers(
            monkeypatch,
            tmp_path,
            user={
                "version": 1,
                "classes": {"half": ["claude-code", "gemini-cli"]},
            },
        )
        issues = _run(ClassChainAvailabilityCheck(), _config(instrument="half"))
        assert [i.check_id for i in issues] == ["V311"]
        assert issues[0].severity == ValidationSeverity.WARNING
        assert "1 of 2 entries cannot run here" in issues[0].message
        assert "the job will start at 'claude-code'" in issues[0].message

    def test_fully_unavailable_chain_escalates_to_v310(self, monkeypatch, tmp_path) -> None:
        _Layers(
            monkeypatch,
            tmp_path,
            user={
                "version": 1,
                "classes": {
                    # two distinct steps (profile, model) — both unavailable
                    "dead": [
                        "gemini-cli",
                        {"profile": "gemini-cli", "config": {"model": "m"}},
                    ],
                },
            },
        )
        issues = _run(ClassChainAvailabilityCheck(), _config(instrument="dead"))
        assert [i.check_id for i in issues] == ["V310"]
        assert issues[0].severity == ValidationSeverity.ERROR

    def test_shadowed_default_class_warns(self, monkeypatch, tmp_path) -> None:
        # A loaded profile named like a packaged-default class: the profile
        # wins (§3.1) and the class is unreachable — V311 WARN names it.
        _Layers(monkeypatch, tmp_path)  # 'vision' class exists only in default layer
        monkeypatch.setattr(
            "marianne.instruments.loader.load_all_profiles",
            lambda: _fake_profiles({"vision": _profile("vision")}),
        )
        issues = _run(ClassChainAvailabilityCheck(), _config(instrument="vision"))
        matches = [i for i in issues if "profile wins" in i.message]
        assert len(matches) == 1
        assert matches[0].check_id == "V311"
        assert matches[0].severity == ValidationSeverity.WARNING

    def test_response_format_arm_counts_unenforceable_entries(self, monkeypatch, tmp_path) -> None:
        _Layers(
            monkeypatch,
            tmp_path,
            user={"version": 1, "classes": {"schema-chain": ["claude-code", "ollama"]}},
        )
        # claude-code's fake command has no json_schema_flag; ollama is http
        # and can enforce. A json_schema request on the class therefore makes
        # exactly the CLI entry unenforceable → partial V311.
        config = _config(
            instrument="schema-chain",
            instrument_config={"response_format": {"type": "json_schema", "name": "out"}},
        )
        issues = _run(ClassChainAvailabilityCheck(), config)
        assert [i.check_id for i in issues] == ["V311"]


# ---------------------------------------------------------------------------
# V321 / V322 — alias misuse of class names
# ---------------------------------------------------------------------------


class TestAliasChecks:
    def test_alias_naming_class_profile_is_error(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        config = _config(
            instrument="my-alias",
            instruments={"my-alias": {"profile": "strong"}},
        )
        issues = _run(ClassAliasProfileCheck(), config)
        assert [i.check_id for i in issues] == ["V321"]
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "an alias must name a profile" in issues[0].message

    def test_alias_shadowing_class_name_warns(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        config = _config(
            instrument="strong",
            instruments={"strong": {"profile": "claude-code"}},
        )
        issues = _run(AliasShadowsClassCheck(), config)
        assert [i.check_id for i in issues] == ["V322"]
        assert issues[0].severity == ValidationSeverity.WARNING
        assert "the alias wins in this score" in issues[0].message
        # §3.1: the alias wins — no class resolution happens for 'strong'.
        assert _run(ClassNameResolutionCheck(), config) == []
        assert _run(ClassChainSummaryCheck(), config) == []


# ---------------------------------------------------------------------------
# V323 — model against a class primary (§2.4)
# ---------------------------------------------------------------------------


class TestV323ModelWithClassPrimary:
    def test_score_scope_model_is_error(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        config = _config(
            instrument="strong",
            instrument_config={"model": "some-model"},
        )
        issues = _run(ClassModelConfigCheck(), config)
        assert [i.check_id for i in issues] == ["V323"]
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "cannot apply to class 'strong'" in issues[0].message
        assert issues[0].message.startswith("sheet 1:")

    def test_class_entry_model_is_fine(self, monkeypatch, tmp_path) -> None:
        _Layers(
            monkeypatch,
            tmp_path,
            user={
                "version": 1,
                "classes": {"pinned": [{"profile": "claude-code", "config": {"model": "opus"}}]},
            },
        )
        config = _config(instrument="pinned")
        assert _run(ClassModelConfigCheck(), config) == []
        # The entry model is honoured at build time.
        summary = class_summary(config)
        assert summary["used"][0]["chain"] == ["claude-code/opus"]

    def test_per_sheet_model_is_error(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        config = _config(
            instrument="strong",
            sheet={
                "size": 1,  # two sheets so the per-sheet scope is distinct
                "total_items": 2,
                "per_sheet_instrument_config": {2: {"model": "m2"}},
            },
        )
        issues = _run(ClassModelConfigCheck(), config)
        assert [i.check_id for i in issues] == ["V323"]
        assert "sheet 2:" in issues[0].message


# ---------------------------------------------------------------------------
# V324 — invalid layer files, gated on class usage (V-CLS-07 / V-CLS-08 ERROR)
# ---------------------------------------------------------------------------


class TestV324ClassesFile:
    def _broken(self, tmp_path: Path) -> Path:
        bad = tmp_path / "user-classes.yaml"
        bad.write_text("version: 2\nclasses: {}\n")
        return bad

    def test_invalid_user_layer_is_error_when_class_used(self, monkeypatch, tmp_path) -> None:
        layers = _Layers(monkeypatch, tmp_path)
        self._broken(tmp_path)
        issues = _run(ClassesFileValidCheck(), _config(instrument="strong"))
        assert [i.check_id for i in issues] == ["V324"]
        assert issues[0].severity == ValidationSeverity.ERROR
        assert str(layers.user_path) in issues[0].message
        assert "version" in issues[0].message

    def test_invalid_layer_silent_when_no_class_used(self, monkeypatch, tmp_path) -> None:
        # Sentinel's lesson: never fire on state the score does not depend on.
        _Layers(monkeypatch, tmp_path)
        self._broken(tmp_path)
        assert _run(ClassesFileValidCheck(), _config(instrument="claude-code")) == []

    def test_empty_chain_layer_is_error(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        (tmp_path / "user-classes.yaml").write_text(
            "version: 1\nclasses:\n  strong: []\n"
        )
        issues = _run(ClassesFileValidCheck(), _config(instrument="strong"))
        assert [i.check_id for i in issues] == ["V324"]
        assert "empty chain" in issues[0].message

    def test_user_class_colliding_with_profile_refuses_layer(self, monkeypatch, tmp_path) -> None:
        # The collision refuses the user layer (ERROR arm of V-CLS-08), so
        # classes it defined stop looking class-shaped. The score names one
        # ('custom-x'); the gate treats unresolvable names + failed layers as
        # class usage, so the root cause (V324) renders beside V210's
        # unknown-name row.
        _Layers(
            monkeypatch,
            tmp_path,
            user={
                "version": 1,
                "classes": {
                    # non-builtin registered profile → the loader's collision
                    # refusal (builtin names are rejected by the schema first)
                    "custom-prof": ["claude-code"],
                    "custom-x": ["claude-code"],
                },
            },
        )
        monkeypatch.setattr(
            "marianne.instruments.loader.load_all_profiles",
            lambda: _fake_profiles({"custom-prof": _profile("custom-prof")}),
        )
        from marianne.validation.runner import ValidationRunner

        runner = ValidationRunner(create_default_checks())
        raw = _score_yaml(instrument="custom-x")
        config = JobConfig.model_validate(yaml.safe_load(raw))
        runner_issues = runner.validate(config, Path("s.yaml"), raw)
        v324 = [i for i in runner_issues if i.check_id == "V324"]
        assert v324 and "collides with a registered profile" in v324[0].message
        assert any(i.check_id == "V210" for i in runner_issues)


# ---------------------------------------------------------------------------
# V325 — resolution summary (V-CLS-11)
# ---------------------------------------------------------------------------


class TestV325Summary:
    def test_one_info_row_per_used_class(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        issues = _run(
            ClassChainSummaryCheck(),
            _config(instrument="strong", instrument_fallbacks=["fast"]),
        )
        assert sorted(i.metadata["class_name"] for i in issues) == ["fast", "strong"]
        assert all(i.check_id == "V325" for i in issues)
        assert all(i.severity == ValidationSeverity.INFO for i in issues)
        strong = next(i for i in issues if i.metadata["class_name"] == "strong")
        assert strong.message.startswith("Instruments: strong → ")
        assert "default layer, sha256 " in strong.message

    def test_no_class_no_row(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        assert _run(ClassChainSummaryCheck(), _config(instrument="claude-code")) == []

    def test_layers_carry_real_sha256(self, monkeypatch, tmp_path) -> None:
        import marianne.instruments.classes as classes_module

        _Layers(monkeypatch, tmp_path)
        summary = class_summary(_config(instrument="strong"))
        default_row = next(r for r in summary["layers"] if r["layer"] == "default")
        real_default = (
            Path(classes_module.__file__).parent / "classes" / "default.yaml"
        ).resolve()
        assert default_row["sha256"] == hashlib.sha256(real_default.read_bytes()).hexdigest()
        assert summary["per_sheet"][0]["requested"] == "strong"
        assert summary["per_sheet"][0]["chain"][0] == "claude-code"


# ---------------------------------------------------------------------------
# V307 — raw shell step falling back through a class (V-CLS-10)
# ---------------------------------------------------------------------------


class TestV307RawShellClassFallback:
    def test_raw_shell_sheet_with_class_fallback_warns(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        from marianne.validation.checks.cli import CliRawPromptBashCheck

        config = _config(
            sheet={
                "size": 2,
                "total_items": 2,
                "per_sheet_instruments": {1: "cli"},
                "per_sheet_fallbacks": {1: ["strong"]},
            },
        )
        issues = CliRawPromptBashCheck().check(config, Path("s.yaml"), _score_yaml())
        class_rows = [i for i in issues if "model instruments" in i.message]
        assert len(class_rows) == 1
        assert class_rows[0].check_id == "V307"
        assert class_rows[0].severity == ValidationSeverity.WARNING
        assert "can fall back to class 'strong'" in class_rows[0].message


# ---------------------------------------------------------------------------
# Suppression truth invariant (SC-7)
# ---------------------------------------------------------------------------


class TestSuppressionContract:
    def _known(self) -> dict[str, ValidationSeverity]:
        return {c.check_id: c.severity for c in create_default_checks()}

    def test_advisory_codes_are_suppressible(self) -> None:
        known = self._known()
        assert known["V311"] == ValidationSeverity.WARNING
        assert known["V322"] == ValidationSeverity.WARNING
        assert known["V325"] == ValidationSeverity.INFO

    def test_error_codes_are_refused(self) -> None:
        known = self._known()
        for code in ("V310", "V321", "V323", "V324"):
            assert known[code] == ValidationSeverity.ERROR

    def test_suppressing_warn_removes_row_and_keeps_exit_truth(
        self, monkeypatch, tmp_path
    ) -> None:
        _Layers(
            monkeypatch,
            tmp_path,
            user={"version": 1, "classes": {"half": ["claude-code", "gemini-cli"]}},
        )
        raw = _score_yaml(
            instrument="half",
            validate={"suppress": ["V311"]},
        )
        config = JobConfig.model_validate(yaml.safe_load(raw))
        issues = _run(ClassChainAvailabilityCheck(), config, raw)
        visible, suppressed = apply_suppression(config, issues)
        assert visible == []
        assert suppressed == [{"check_id": "V311", "count": 1}]

    def test_suppressing_error_yields_v012(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        raw = _score_yaml(instrument="image", validate={"suppress": ["V310"]})
        config = JobConfig.model_validate(yaml.safe_load(raw))
        issues = _run(ClassNameResolutionCheck(), config, raw)
        visible, _ = apply_suppression(config, issues)
        v012 = [i for i in visible if i.check_id == "V012"]
        assert len(v012) == 1
        assert "ERROR-tier code" in v012[0].message
        assert any(i.check_id == "V310" for i in visible)  # the ERROR row stays


# ---------------------------------------------------------------------------
# CLI end-to-end: exit codes and the additive --json summary (SC-2 / SC-5)
# ---------------------------------------------------------------------------


class TestCliContract:
    def test_v310_exits_one(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        score = tmp_path / "score.yaml"
        score.write_text(_score_yaml(instrument="image"))
        result = CliRunner().invoke(app, ["validate", str(score), "--json"])
        assert result.exit_code == 1
        import json

        data = json.loads(result.stdout)
        assert any(i["check_id"] == "V310" for i in data["issues"])

    def test_v323_exits_one(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        score = tmp_path / "score.yaml"
        score.write_text(
            _score_yaml(instrument="strong", instrument_config={"model": "x"})
        )
        result = CliRunner().invoke(app, ["validate", str(score), "--json"])
        assert result.exit_code == 1
        import json

        data = json.loads(result.stdout)
        assert any(i["check_id"] == "V323" for i in data["issues"])

    def test_v321_exits_one(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        score = tmp_path / "score.yaml"
        score.write_text(
            _score_yaml(instruments={"my-alias": {"profile": "strong"}}, instrument="my-alias")
        )
        result = CliRunner().invoke(app, ["validate", str(score), "--json"])
        assert result.exit_code == 1
        import json

        data = json.loads(result.stdout)
        assert any(i["check_id"] == "V321" for i in data["issues"])

    def test_v324_exits_one_only_with_class_use(self, monkeypatch, tmp_path) -> None:
        _Layers(monkeypatch, tmp_path)
        (tmp_path / "user-classes.yaml").write_text("version: 2\nclasses: {}\n")
        uses = tmp_path / "uses.yaml"
        uses.write_text(_score_yaml(instrument="strong"))
        result = CliRunner().invoke(app, ["validate", str(uses), "--json"])
        assert result.exit_code == 1
        import json

        assert any(i["check_id"] == "V324" for i in json.loads(result.stdout)["issues"])

        no_class = tmp_path / "plain.yaml"
        no_class.write_text(_score_yaml(instrument="claude-code"))
        result = CliRunner().invoke(app, ["validate", str(no_class), "--json"])
        data = json.loads(result.stdout)
        assert not any(i["check_id"] == "V324" for i in data["issues"])
        assert data["valid"] is True

    def test_warn_only_score_exits_zero_with_summary(self, monkeypatch, tmp_path) -> None:
        _Layers(
            monkeypatch,
            tmp_path,
            user={"version": 1, "classes": {"half": ["claude-code", "gemini-cli"]}},
        )
        score = tmp_path / "half.yaml"
        score.write_text(_score_yaml(instrument="half"))
        result = CliRunner().invoke(app, ["validate", str(score), "--json"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.stdout)
        assert any(i["check_id"] == "V311" for i in data["issues"])
        classes = data["summary"]["classes"]
        assert classes["used"][0]["class"] == "half"
        assert classes["per_sheet"][0]["requested"] == "half"
        default_row = next(r for r in classes["layers"] if r["layer"] == "default")
        real_default = (
            Path(marianne_classes_file()).parent / "classes" / "default.yaml"
        ).resolve()
        assert default_row["sha256"] == hashlib.sha256(real_default.read_bytes()).hexdigest()


def marianne_classes_file() -> str:
    import marianne.instruments.classes as classes_module

    return classes_module.__file__


# ---------------------------------------------------------------------------
# Deduped docstring sanity for the §8 message texts
# ---------------------------------------------------------------------------


def test_v215_docstring_no_longer_claims_skip() -> None:
    """#427: the docstring must say one dispatch attempt, never 'skips'."""
    from marianne.validation.checks import config as config_checks

    doc = config_checks.NoUsableInstrumentCheck.__doc__ or ""
    assert "skips uninstalled" not in doc
    assert "one dispatch attempt" in doc
