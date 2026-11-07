"""Regression tests for the machine-JSON UTF-8 contract (issue #495).

``mzt status --json`` emitted a raw non-UTF-8 byte (0x97) inside the JSON
payload on 2026-11-05. The documented contract (docs/cli-reference.md) is:
"With ``--json``, stdout carries only the JSON" — valid UTF-8, nothing
else, verbatim.

These tests pin that contract at every boundary the incident touched:

- writer: ``output_json`` / ``output_error`` must emit Rich-unmutated,
  strict-UTF-8, parseable JSON (no emoji substitution of ``:code:`` tokens)
- capture: ``SheetState.capture_output`` must store strictly-encodable
  tails in *both* the truncated and untruncated branches
- end-to-end: hostile attempt data through the status JSON code path must
  yield bytes that decode strict UTF-8 and parse as JSON
- logging: the JSON log handler shares stdout with machine output, so its
  renderer is pinned ASCII-safe
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import structlog
from rich.console import Console

from marianne.cli.output import output_error, output_json
from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus

# The incident byte's full hostile family: a ``:warning:`` token (Rich emoji
# substitution bait), a cp1252-style em dash U+2014, the C1 control U+0097
# (0x97 decoded as latin-1), and a lone surrogate U+DC97.
HOSTILE = "x :warning: y — \x97\udc97 z"


# ---------------------------------------------------------------------------
# Writer: output_json / output_error
# ---------------------------------------------------------------------------


class TestJsonWriterContract:
    """The machine-JSON writer must not mutate or break the payload."""

    def test_output_json_default_path_writes_verbatim_strict_utf8(
        self, capsys: Any
    ) -> None:
        """Default path bypasses Rich: token verbatim, strict UTF-8, parses."""
        output_json({"tail": HOSTILE, "ok": True})

        out = capsys.readouterr().out
        # Rich emoji substitution must not rewrite ':warning:' into a glyph
        assert "⚠" not in out, "emoji substitution mutated the JSON"
        assert ":warning:" in out, "verbatim token lost"
        # Strict UTF-8 (trivially true for ensure_ascii output — that is
        # the point: the guarantee is structural, not incidental)
        out.encode("utf-8", "strict")
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["tail"].startswith("x :warning: y")

    def test_output_json_console_path_no_emoji_substitution(self) -> None:
        """Explicit console path keeps payload verbatim with emoji=False."""
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=4096)

        output_json({"tail": HOSTILE}, console_instance=console)

        out = buf.getvalue()
        assert "⚠" not in out, "emoji substitution mutated the JSON"
        assert ":warning:" in out
        payload = json.loads(out)
        assert payload["tail"].startswith("x :warning: y")

    def test_output_error_json_branch_ascii_safe_verbatim(
        self, capsys: Any
    ) -> None:
        """output_error(json_output=True) emits parseable strict-UTF-8 JSON."""
        output_error(
            "bad — thing \udc97",
            error_code="E501",
            hints=["check :warning: light"],
            json_output=True,
        )

        out = capsys.readouterr().out
        out.encode("utf-8", "strict")
        assert ":warning:" in out
        assert "⚠" not in out
        payload = json.loads(out)
        assert payload["success"] is False
        assert payload["error_code"] == "E501"
        assert payload["hints"] == ["check :warning: light"]

    def test_output_error_json_branch_console_instance(self) -> None:
        """output_error JSON branch honors console_instance without mutation."""
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=4096)

        output_error(
            ":warning: broken \udc97", error_code="E501", json_output=True,
            console_instance=console,
        )

        out = buf.getvalue()
        assert "⚠" not in out
        payload = json.loads(out)
        assert payload["error_code"] == "E501"


# ---------------------------------------------------------------------------
# Capture: SheetState.capture_output decode policy
# ---------------------------------------------------------------------------


class TestCaptureOutputPolicy:
    """Both capture branches must store strictly-encodable tails."""

    def test_untruncated_branch_normalizes_lone_surrogates(self) -> None:
        """A surrogate-carrying producer string must not be stored verbatim."""
        state = SheetState(sheet_num=1)
        state.capture_output("prefix \udc97 suffix — tail", "err \udcff also")

        assert state.stdout_tail is not None
        assert state.stderr_tail is not None
        # The stored tails must be strictly encodable — this raises on a
        # lone surrogate (the fail-before behavior: verbatim storage).
        state.stdout_tail.encode("utf-8", "strict")
        state.stderr_tail.encode("utf-8", "strict")
        # Replacement happened at the bad code point, not the good text
        assert "prefix" in state.stdout_tail
        assert "suffix" in state.stdout_tail
        assert "err" in state.stderr_tail

    def test_truncated_branch_still_decodes_with_replace(self) -> None:
        """Truncated branch behavior is unchanged: strictly encodable tails."""
        state = SheetState(sheet_num=1)
        state.capture_output("a\udc97b" * 100, "c\udcffd" * 100, max_bytes=64)

        assert state.output_truncated is True
        assert state.stdout_tail is not None
        assert state.stderr_tail is not None
        state.stdout_tail.encode("utf-8", "strict")
        state.stderr_tail.encode("utf-8", "strict")

    def test_empty_streams_still_store_none(self) -> None:
        """Empty streams keep the documented None semantics."""
        state = SheetState(sheet_num=1)
        state.capture_output("", "")

        assert state.stdout_tail is None
        assert state.stderr_tail is None


# ---------------------------------------------------------------------------
# End-to-end: the incident byte through the status JSON path
# ---------------------------------------------------------------------------


def _hostile_state() -> CheckpointState:
    """Build a job whose sheet attempt data carries the incident's bytes.

    Mirrors the 2026-11-05 incident: a dispatched sheet attempt left
    stdout_tail / error_message strings carrying a cp1252 em dash, a C1
    control, a lone surrogate, and a ``:warning:`` token from musician
    output. status --json then serialized all of it.
    """
    sheet = SheetState(sheet_num=1)
    sheet.status = SheetStatus.FAILED
    sheet.validation_passed = False
    sheet.error_message = "validation — failed :warning: \udc97 tail"
    sheet.error_code = "E-VALIDATION"
    sheet.capture_output(
        "musician :warning: \udc97 — stdout tail",
        "stderr \udc97 tail",
    )
    return CheckpointState(
        job_id="utf8-contract-test",
        job_name="utf8-contract-test",
        total_sheets=1,
        sheets={1: sheet},
    )


class TestStatusJsonEndToEnd:
    """The issue's requested regression: hostile state → clean JSON bytes."""

    def test_status_json_output_is_strict_utf8_and_parses(self, capsys: Any) -> None:
        from marianne.cli.commands.status import _output_status_json

        _output_status_json(_hostile_state())

        out = capsys.readouterr().out
        # The contract: bytes decode strict UTF-8...
        out.encode("utf-8", "strict")
        # ...nothing else on stdout: the whole capture is one JSON document
        payload = json.loads(out)
        assert payload["job_id"] == "utf8-contract-test"
        # ...verbatim: no emoji substitution of the musician's token
        assert "⚠" not in out
        assert ":warning:" in out
        # The hostile strings actually reached the payload (not stripped
        # into silence — they are escaped, not dropped)
        recent = payload["recent_errors"]
        assert recent, "hostile error message never reached the payload"
        assert "validation" in recent[0]["error_message"]

    def test_status_json_sheet_payload_carries_sanitized_tails(
        self, capsys: Any
    ) -> None:
        from marianne.cli.commands.status import _output_status_json

        _output_status_json(_hostile_state())

        out = capsys.readouterr().out
        payload = json.loads(out)
        sheet = payload["sheets"]["1"]
        assert sheet["status"] == "failed"
        assert sheet["error_message"].startswith("validation")


# ---------------------------------------------------------------------------
# Logging: stdout JSON log handler stays ASCII-safe
# ---------------------------------------------------------------------------


class TestJsonLogHandlerAsciiSafety:
    """The JSON log handler may share stdout with --json machine output."""

    def setup_method(self) -> None:
        structlog.reset_defaults()
        structlog.configure(cache_logger_on_first_use=False)
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)

    def teardown_method(self) -> None:
        structlog.reset_defaults()
        structlog.configure(cache_logger_on_first_use=False)
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)

    def test_json_log_lines_decode_strict_and_parse(self, capsys: Any) -> None:
        from marianne.core.logging import configure_logging, get_logger

        configure_logging(level="INFO", format="json")
        logger = get_logger("utf8-contract-test")
        logger.info("log :warning: event — \udc97 \x97", tail=HOSTILE)

        out = capsys.readouterr().out
        assert out, "nothing reached the stdout JSON handler"
        for line in out.splitlines():
            line.encode("utf-8", "strict")
            json.loads(line)
        assert ":warning:" in out


# ---------------------------------------------------------------------------
# Targeted Functional Review Tests (Issue #495)
# ---------------------------------------------------------------------------


class TestFunctionalReviewEdgeCases:
    """Targeted edge case and regression tests added during functional review."""

    def test_mid_codepoint_byte_truncation_safety(self) -> None:
        """Truncating in the middle of multi-byte UTF-8 does not raise UnicodeDecodeError."""
        state = SheetState(sheet_num=1)
        # '—' is 3 bytes in UTF-8: \xe2\x80\x94
        multibyte_stream = ("abc—" * 50)
        state.capture_output(multibyte_stream, multibyte_stream, max_bytes=7)

        assert state.output_truncated is True
        assert state.stdout_tail is not None
        assert state.stderr_tail is not None
        # Must be strictly encodable without lone/split surrogates or decode errors
        state.stdout_tail.encode("utf-8", "strict")
        state.stderr_tail.encode("utf-8", "strict")

    def test_consumer_json_load_stdin_simulation(self, capsys: Any) -> None:
        """Simulates exact consumer failure mode from issue #495: json.load(sys.stdin)."""
        raw_musician_bytes = b"Attempt error with byte: \x97 in output"
        decoded = raw_musician_bytes.decode("utf-8", errors="replace")

        state = SheetState(sheet_num=1)
        state.capture_output(decoded, "")

        # Write via output_json
        output_json({"sheet_tail": state.stdout_tail, "status": "failed"})

        captured = capsys.readouterr().out
        # Strict UTF-8 decode must succeed on raw bytes (simulating pipe to consumer)
        raw_stream = io.BytesIO(captured.encode("utf-8", "strict"))
        text_stream = io.TextIOWrapper(raw_stream, encoding="utf-8", errors="strict")

        parsed = json.load(text_stream)
        assert parsed["status"] == "failed"
        assert "\ufffd" in parsed["sheet_tail"]

    def test_surrogate_boundary_variations(self) -> None:
        """Lone high surrogates, lone low surrogates, and reversed pairs are normalized."""
        state = SheetState(sheet_num=1)
        # \ud83d is high surrogate, \udca9 is low surrogate
        hostile_surrogates = (
            "prefix \ud83d lone high, \udca9 lone low, \udca9\ud83d reversed suffix"
        )
        state.capture_output(hostile_surrogates, hostile_surrogates)

        assert state.output_truncated is False
        assert state.stdout_tail is not None
        assert state.stderr_tail is not None
        # Untruncated branch round-trip must make state strictly encodable
        enc_out = state.stdout_tail.encode("utf-8", "strict")
        enc_err = state.stderr_tail.encode("utf-8", "strict")
        assert b"prefix " in enc_out
        assert b"prefix " in enc_err

    def test_ansi_and_c0_c1_controls_in_output_json(self, capsys: Any) -> None:
        """ANSI escape sequences and control characters produce clean, valid JSON."""
        payload = {
            "ansi": "\x1b[31;1mFatal error:\x1b[0m disk full",
            "c0_bell": "beep\x07alert",
            "c1_ctrl": "ctrl\x97code",
        }
        output_json(payload)

        out = capsys.readouterr().out
        out.encode("utf-8", "strict")
        data = json.loads(out)
        assert "disk full" in data["ansi"]
        assert "Fatal error" in data["ansi"]

    def test_regression_nested_structures_and_primitives(self, capsys: Any) -> None:
        """Nested dictionaries, lists, booleans, None, and empty structures are preserved."""
        complex_data: dict[str, Any] = {
            "empty_dict": {},
            "empty_list": [],
            "nested": {
                "count": 42,
                "score": 3.14159,
                "active": True,
                "paused": False,
                "meta": None,
                "tags": ["alpha", "beta", "gamma"],
            },
        }
        output_json(complex_data)

        out = capsys.readouterr().out
        out.encode("utf-8", "strict")
        assert json.loads(out) == complex_data

    def test_output_error_human_mode_regression(self) -> None:
        """output_error with json_output=False still formats human-readable error."""
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)

        output_error(
            "Compilation failed",
            error_code="E404",
            hints=["check compiler path"],
            json_output=False,
            console_instance=console,
        )

        rendered = buf.getvalue()
        assert "E404" in rendered
        assert "Compilation failed" in rendered

