"""User journey tests for Movement 2, Cycle 2 features.

These tests tell stories. Real users, real scenarios, real edges.

User stories covered:
1. Dana's score-level instruments — she names instruments for readability and
   expects per-sheet assignment, instrument_map, and movements to resolve them.
2. Marcus's credential-heavy workflow — his agents print API keys in tracebacks
   and he trusts Mozart to redact them before they hit the state DB.
3. Priya's recovery scenario — she resumes a job after a restart and expects
   attempt counts to carry forward so sheets don't get infinite retries.
4. Leo's cost-conscious score — he sets cost limits and expects both the baton
   run and resume paths to enforce them correctly.

Movement 2 — Journey.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config.job import InstrumentDef, JobConfig, MovementDef
from marianne.core.sheet import Sheet, build_sheets
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import DispatchRetry, SheetAttemptResult
from marianne.daemon.baton.state import BatonSheetStatus
from marianne.utils.credential_scanner import redact_credentials


# =========================================================================
# Helpers
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    prompt: str = "Test prompt",
    workspace: str = "/tmp/test-ws",
) -> Sheet:
    """Create a minimal Sheet for testing."""
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        instrument_name=instrument,
        workspace=Path(workspace),
        prompt_template=prompt,
        template_file=None,
        validations=[],
        timeout_seconds=60.0,
        prelude=[],
        cadenza=[],
    )


# =========================================================================
# 1. Dana's Score-Level Instruments
# =========================================================================


class TestDanaScoreLevelInstruments:
    """Dana is setting up a multi-instrument score. She defines named instruments
    at the score level for readability:

        instruments:
          fast: {profile: gemini-cli}
          careful: {profile: claude-code, config: {model: opus}}

    She assigns them to sheets using per_sheet_instruments, instrument_map, and
    movement-level instruments. She expects each sheet to resolve to the correct
    profile — not the alias name.
    """

    @pytest.fixture
    def dana_score(self, tmp_path: Path) -> JobConfig:
        workspace = tmp_path / "dana-workspace"
        workspace.mkdir()
        return JobConfig(
            name="dana-multi-instrument",
            workspace=str(workspace),
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(profile="gemini-cli"),
                "careful": InstrumentDef(profile="claude-code", config={"model": "opus"}),
            },
            sheet={
                "size": 1,
                "total_items": 4,
                "per_sheet_instruments": {
                    1: "fast",
                    2: "careful",
                },
            },
            prompt={"template": "Dana's sheet {{ sheet_num }}"},
        )

    def test_per_sheet_fast_resolves_to_gemini(self, dana_score: JobConfig) -> None:
        """Sheet 1 assigned 'fast' should use gemini-cli, not the alias."""
        sheets = build_sheets(dana_score)
        assert sheets[0].instrument_name == "gemini-cli"

    def test_per_sheet_careful_resolves_to_claude(self, dana_score: JobConfig) -> None:
        """Sheet 2 assigned 'careful' should use claude-code."""
        sheets = build_sheets(dana_score)
        assert sheets[1].instrument_name == "claude-code"

    def test_careful_config_merged(self, dana_score: JobConfig) -> None:
        """Sheet 2's instrument config should include the model override."""
        sheets = build_sheets(dana_score)
        assert sheets[1].instrument_config.get("model") == "opus"

    def test_unassigned_sheets_use_score_level(self, dana_score: JobConfig) -> None:
        """Sheets 3-4 have no per-sheet assignment and should use the score-level default."""
        sheets = build_sheets(dana_score)
        assert sheets[2].instrument_name == "claude-code"
        assert sheets[3].instrument_name == "claude-code"

    def test_instrument_map_resolves_aliases(self, tmp_path: Path) -> None:
        """Dana uses instrument_map to batch-assign 'fast' to sheets 1-3."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        config = JobConfig(
            name="map-resolve",
            workspace=str(workspace),
            instrument="claude-code",
            instruments={"fast": InstrumentDef(profile="gemini-cli")},
            sheet={
                "size": 1,
                "total_items": 4,
                "instrument_map": {"fast": [1, 2, 3]},
            },
            prompt={"template": "Work on {{ sheet_num }}"},
        )
        sheets = build_sheets(config)
        # First 3 sheets should resolve to gemini-cli
        for i in range(3):
            assert sheets[i].instrument_name == "gemini-cli", f"Sheet {i+1}"
        # Sheet 4 falls through to score-level default
        assert sheets[3].instrument_name == "claude-code"

    def test_movement_instrument_resolves_alias(self, tmp_path: Path) -> None:
        """Dana assigns 'fast' to an entire movement."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        config = JobConfig(
            name="movement-resolve",
            workspace=str(workspace),
            instrument="claude-code",
            instruments={"fast": InstrumentDef(profile="gemini-cli")},
            movements={
                1: MovementDef(name="explore", instrument="fast"),
                2: MovementDef(name="synthesize"),
            },
            sheet={"size": 1, "total_items": 2},
            prompt={"template": "Work on {{ sheet_num }}"},
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "gemini-cli"
        assert sheets[1].instrument_name == "claude-code"


# =========================================================================
# 2. Marcus's Credential-Heavy Workflow
# =========================================================================


class TestMarcusCredentialRedaction:
    """Marcus runs scores that interact with multiple APIs. His agents sometimes
    print API keys in error tracebacks. He trusts Mozart to catch these before
    they end up in the state DB, diagnostic output, or logs.

    This tests the full credential scanner pipeline end-to-end.
    """

    def test_anthropic_key_in_traceback(self) -> None:
        """Agent prints an Anthropic key in a Python traceback."""
        error = (
            "Traceback (most recent call last):\n"
            "  File '/app/agent.py', line 42\n"
            '    client = Anthropic(api_key="sk-ant-api03-secret1234567890abcdef")\n'
            "AuthenticationError: Invalid API key"
        )
        result = redact_credentials(error)
        assert "sk-ant-api03-secret1234567890abcdef" not in result
        assert "[REDACTED_ANTHROPIC_KEY]" in result
        assert "AuthenticationError" in result  # Error context preserved

    def test_openai_key_in_json_output(self) -> None:
        """Agent accidentally outputs an OpenAI key in JSON."""
        output = '{"error": "auth failed", "key": "sk-proj-abcdefghij1234567890"}'
        result = redact_credentials(output)
        assert "sk-proj-abcdefghij1234567890" not in result

    def test_multiple_keys_in_single_output(self) -> None:
        """Agent prints both an AWS key and a Google key."""
        output = (
            "Connecting to AWS with AKIA1234567890ABCDEF\n"
            "Also trying Google with AIzaSyDeadBeef1234567890abcdefghij\n"
        )
        result = redact_credentials(output)
        assert "AKIA1234567890ABCDEF" not in result
        assert "AIzaSyDeadBeef1234567890abcdefghij" not in result
        assert "[REDACTED_AWS_KEY]" in result
        assert "[REDACTED_GOOGLE_KEY]" in result

    def test_clean_output_unchanged(self) -> None:
        """Normal output passes through untouched."""
        output = "Sheet 5 completed successfully. Generated 42 files."
        assert redact_credentials(output) == output

    def test_none_input_returns_none(self) -> None:
        """None (no output captured) passes through."""
        assert redact_credentials(None) is None

    def test_short_sk_prefix_not_redacted(self) -> None:
        """Short strings starting with 'sk-' should NOT be redacted.
        The scanner requires minimum lengths to avoid false positives."""
        output = "Variable sk-mode is set to production"
        result = redact_credentials(output)
        assert result == output  # No redaction — too short

    def test_github_token_redacted(self) -> None:
        """GitHub PATs in agent output must be caught."""
        output = "export GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz1234567890ab"
        result = redact_credentials(output)
        assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890ab" not in result

    def test_huggingface_token_redacted(self) -> None:
        """HuggingFace tokens in config output."""
        output = "Loading model with token hf_deadbeefcafe1234567890"
        result = redact_credentials(output)
        assert "hf_deadbeefcafe1234567890" not in result


# =========================================================================
# 3. Priya's Recovery Scenario
# =========================================================================


class TestPriyaRecoveryScenario:
    """Priya's job was running when the conductor restarted. She resumes it
    and expects:
    - Terminal sheets stay terminal (not re-executed)
    - In-progress sheets reset to PENDING (the musician died)
    - Attempt counts carry forward (no infinite retries)
    - The event loop gets kicked (DispatchRetry sent)
    """

    def test_recovery_preserves_completed(self) -> None:
        """Sheet 1 was COMPLETED before restart — must stay COMPLETED."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]

        cp = CheckpointState(
            job_id="priya-job",
            job_name="Priya's Resume",
            config_hash=None,
            total_sheets=2,
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.IN_PROGRESS,
                    attempt_count=3,
                ),
            },
        )

        adapter.recover_job("priya-job", sheets, {}, cp, max_retries=5)

        s1 = adapter._baton.get_sheet_state("priya-job", 1)
        s2 = adapter._baton.get_sheet_state("priya-job", 2)

        assert s1.status == BatonSheetStatus.COMPLETED
        assert s2.status == BatonSheetStatus.PENDING  # Reset from IN_PROGRESS
        assert s2.normal_attempts == 3  # Attempts preserved

    def test_recovery_kicks_event_loop(self) -> None:
        """After recovery, a DispatchRetry must be sent to wake the event loop."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        cp = CheckpointState(
            job_id="priya-kick",
            job_name="Kick Test",
            config_hash=None,
            total_sheets=1,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.PENDING),
            },
        )

        adapter.recover_job("priya-kick", sheets, {}, cp, max_retries=3)

        event = adapter._baton.inbox.get_nowait()
        assert isinstance(event, DispatchRetry)

    def test_recovery_does_not_resurrect_failed(self) -> None:
        """FAILED sheets must stay FAILED — recovery must not give them
        another chance."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        cp = CheckpointState(
            job_id="priya-fail",
            job_name="No Resurrection",
            config_hash=None,
            total_sheets=1,
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.FAILED,
                    attempt_count=5,
                ),
            },
        )

        adapter.recover_job("priya-fail", sheets, {}, cp, max_retries=5)

        state = adapter._baton.get_sheet_state("priya-fail", 1)
        assert state.status.is_terminal


# =========================================================================
# 4. Leo's Cost-Conscious Score
# =========================================================================


class TestLeoCostLimits:
    """Leo sets a $10 cost limit on his job. He expects the baton to enforce it
    in both the initial run path and the resume path (F-134)."""

    def test_cost_limit_wired_on_register(self) -> None:
        """When Leo registers a job with a cost limit, it must be set in the baton."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]

        adapter.register_job("leo-job", sheets, {}, max_cost_usd=10.0)

        assert "leo-job" in adapter._baton._job_cost_limits
        assert adapter._baton._job_cost_limits["leo-job"] == 10.0

    def test_cost_limit_wired_on_recover(self) -> None:
        """When Leo resumes after a restart, the cost limit must be re-established."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        cp = CheckpointState(
            job_id="leo-resume",
            job_name="Leo's Resume",
            config_hash=None,
            total_sheets=1,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.PENDING),
            },
        )

        adapter.recover_job("leo-resume", sheets, {}, cp, max_cost_usd=10.0, max_retries=3)

        assert "leo-resume" in adapter._baton._job_cost_limits
        assert adapter._baton._job_cost_limits["leo-resume"] == 10.0

    def test_no_cost_limit_when_none(self) -> None:
        """When cost limits are disabled, no entry should exist in the baton."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        cp = CheckpointState(
            job_id="leo-free",
            job_name="No Limit",
            config_hash=None,
            total_sheets=1,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.PENDING),
            },
        )

        adapter.recover_job("leo-free", sheets, {}, cp, max_cost_usd=None, max_retries=3)

        assert "leo-free" not in adapter._baton._job_cost_limits
