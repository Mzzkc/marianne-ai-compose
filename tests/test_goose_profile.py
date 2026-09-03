"""Tests for the goose instrument profile correctness.

Goose (`goose run`) requires an explicit input-source flag — one of
``-i`` / ``--instructions``, ``-t`` / ``--text``, or ``--recipe``. Without
one of these, goose exits with code 1 and the error:

    Error: Must provide either --instructions (-i), --text (-t), or --recipe.
    Use -i - for stdin.

The plugin CLI backend's ``_build_command`` (see
``src/marianne/execution/instruments/cli_backend.py`` around line 316)
only emits ``prompt_flag`` when *both* ``prompt_via_stdin=True`` AND
``stdin_sentinel`` is set. The stdin sentinel defaults to ``None`` per
``CliCommand`` (``src/marianne/core/config/instruments.py`` line ~220),
so if the goose profile relies on defaults, the built argv omits the
flag entirely and goose crashes on every invocation.

These tests are TDD: they lock in the requirement that the shipped
goose profile produces a valid goose invocation. They will fail against
the current YAML (missing ``stdin_sentinel``, ``prompt_flag: -t``) and
pass after the fix that sets ``prompt_flag: -i`` and
``stdin_sentinel: "-"``.

The end-to-end test exercises the real goose binary against z.ai — it
is marked ``slow`` and skips when goose is not installed.

Design history: the goose-fallback-debug handoff (2026-04-18), preserved
in the project's private handoff archive.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from marianne.core.config.instruments import InstrumentProfile
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.instruments.loader import InstrumentProfileLoader

BUILTINS_DIR = (
    Path(__file__).resolve().parent.parent
    / "src" / "marianne" / "instruments" / "builtins"
)
GOOSE_YAML = BUILTINS_DIR / "goose.yaml"


@pytest.fixture
def goose_profile() -> InstrumentProfile:
    """Load the shipped goose profile straight from disk."""
    assert GOOSE_YAML.is_file(), f"Goose profile not found at {GOOSE_YAML}"
    profiles = InstrumentProfileLoader.load_directory(BUILTINS_DIR)
    assert "goose" in profiles, (
        f"goose profile failed to load from {BUILTINS_DIR}. "
        f"Loaded: {sorted(profiles.keys())}"
    )
    return profiles["goose"]


class TestGooseProfileConfig:
    """The shipped goose.yaml must yield a valid goose CLI invocation."""

    def test_prompt_via_stdin_is_true(self, goose_profile: InstrumentProfile) -> None:
        """Goose handles prompt via stdin (large prompts, consistency)."""
        assert goose_profile.cli is not None
        assert goose_profile.cli.command.prompt_via_stdin is True

    def test_stdin_sentinel_is_set(self, goose_profile: InstrumentProfile) -> None:
        """Goose requires an input-source flag pointing at stdin.

        Without ``stdin_sentinel``, ``_build_command`` omits ``prompt_flag``
        entirely, leading to the "Must provide either --instructions…"
        failure. The sentinel is the literal ``-`` that follows ``-i``.
        """
        assert goose_profile.cli is not None
        assert goose_profile.cli.command.stdin_sentinel == "-", (
            "goose.yaml must set stdin_sentinel: '-' so the backend "
            "emits the '-i -' argv pair required by `goose run`."
        )

    def test_prompt_flag_is_minus_i(self, goose_profile: InstrumentProfile) -> None:
        """Goose's stdin-compatible flag is ``-i``, not ``-t``.

        `goose run -t -` is rejected — only ``-i -`` routes stdin into the
        instructions channel. Keeping this explicit prevents the flag
        from silently drifting back to ``-t``.
        """
        assert goose_profile.cli is not None
        assert goose_profile.cli.command.prompt_flag == "-i"

    def test_result_path_matches_current_goose_json(
        self, goose_profile: InstrumentProfile,
    ) -> None:
        """Goose run JSON currently stores assistant text in messages[1]."""
        from marianne.utils.json_path import extract_json_path

        assert goose_profile.cli is not None
        sample = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Say exactly OK"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "OK"}],
                },
            ],
            "metadata": {"status": "completed"},
        }
        path = goose_profile.cli.output.result_path

        assert path == "messages[1].content[0].text"
        assert extract_json_path(sample, path) == "OK"


class TestPluginCliBackendBuildsGooseCommand:
    """PluginCliBackend must produce a usable argv for the goose profile."""

    def test_argv_contains_run_subcommand(
        self, goose_profile: InstrumentProfile
    ) -> None:
        backend = PluginCliBackend(goose_profile)
        cmd = backend._build_command("hello goose", timeout_seconds=None)
        assert cmd[0] == "goose"
        assert "run" in cmd

    def test_argv_contains_stdin_flag_and_sentinel(
        self, goose_profile: InstrumentProfile
    ) -> None:
        """The built argv must include ``-i -`` adjacent to each other."""
        backend = PluginCliBackend(goose_profile)
        cmd = backend._build_command("hello goose", timeout_seconds=None)

        assert "-i" in cmd, f"Missing -i flag. argv was: {cmd}"
        idx = cmd.index("-i")
        assert idx + 1 < len(cmd) and cmd[idx + 1] == "-", (
            f"Expected '-i' followed by '-' sentinel, got argv: {cmd}"
        )

    def test_argv_omits_literal_prompt_text(
        self, goose_profile: InstrumentProfile
    ) -> None:
        """Prompt text must NOT appear in argv — it flows through stdin."""
        backend = PluginCliBackend(goose_profile)
        secret_marker = "PROMPT_TEXT_SHOULD_NOT_BE_IN_ARGV_xyzzy"
        cmd = backend._build_command(secret_marker, timeout_seconds=None)

        assert secret_marker not in cmd, (
            f"Prompt leaked into argv (should flow via stdin): {cmd}"
        )


@pytest.mark.overnight
class TestGooseEndToEnd:
    """Real subprocess smoke test — exercises the shipped binary.

    Skips cleanly when goose is not installed. Relies on the existing
    goose configuration on the host (provider credentials, default
    model). Uses a trivial prompt so cost/time stay minimal.
    """

    def test_goose_accepts_stdin_via_configured_argv(
        self, goose_profile: InstrumentProfile
    ) -> None:
        if shutil.which("goose") is None:
            pytest.skip("goose binary not installed on PATH")

        import subprocess

        backend = PluginCliBackend(goose_profile)
        argv = backend._build_command(
            "Reply with exactly one word: hello",
            timeout_seconds=None,
        )

        # Drop the --model flag if the profile provided one and no default
        # is configured on this host — avoids surprising CI failures.
        # The configured default model is used normally in Marianne.
        result = subprocess.run(
            argv,
            input="Reply with exactly one word: hello\n",
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"goose rejected the configured argv. "
            f"argv={argv!r}\nstderr={result.stderr!r}"
        )
