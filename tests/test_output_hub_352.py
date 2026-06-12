"""#352 increment 3: live output streaming — hub, ring, redaction, fan-out.

Contract under test (lab design, ~/lab-archives/2026-06-11-streaming-352):
- redaction ONCE at entry on COMPLETE lines (chunk-boundary splits must
  not leak credential halves past the redactor)
- ~256KB per-sheet drop-oldest ring with an explicit eviction marker
- subscriber queues drop on backpressure with a ``[dropped N lines]``
  marker — never block the daemon loop
- writer/flush are fail-open: observability must never break execution
- the instrument seam: the CLI instrument executor emits drain chunks
  to the wired callback (real subprocess), and callback errors don't
  fail the run
"""

from __future__ import annotations

from marianne.daemon.output_hub import (
    SUBSCRIBER_QUEUE_SIZE,
    OutputStreamHub,
    SheetOutputBuffer,
)


class TestLineAssemblyAndRedaction:
    def test_chunk_boundary_line_assembly(self) -> None:
        buf = SheetOutputBuffer()
        assert buf.feed("stdout", b"hello wo") == []
        assert buf.feed("stdout", b"rld\nsecond ") == ["hello world"]
        assert buf.feed("stdout", b"line\n") == ["second line"]

    def test_credential_split_across_chunks_still_redacted(self) -> None:
        """The redaction hazard the lab called out: a key split across two
        chunks must be reassembled BEFORE redaction sees it."""
        key = "sk-ant-api03-" + "a" * 40
        buf = SheetOutputBuffer()
        buf.feed("stdout", f"token: {key[:20]}".encode())
        lines = buf.feed("stdout", f"{key[20:]} end\n".encode())
        assert len(lines) == 1
        assert key not in lines[0]
        assert "REDACTED" in lines[0]

    def test_stderr_lines_tagged(self) -> None:
        buf = SheetOutputBuffer()
        assert buf.feed("stderr", b"warning: x\n") == ["[stderr] warning: x"]

    def test_streams_buffer_independently(self) -> None:
        buf = SheetOutputBuffer()
        buf.feed("stdout", b"out-part")
        lines = buf.feed("stderr", b"err-line\n")
        assert lines == ["[stderr] err-line"]
        assert buf.feed("stdout", b"ial\n") == ["out-partial"]

    def test_oversized_partial_force_flushed(self) -> None:
        buf = SheetOutputBuffer()
        lines = buf.feed("stdout", b"x" * (9 * 1024))
        assert len(lines) == 1
        assert len(lines[0]) == 9 * 1024

    def test_flush_emits_unterminated_partial(self) -> None:
        buf = SheetOutputBuffer()
        buf.feed("stdout", b"no newline here")
        assert buf.flush() == ["no newline here"]
        assert buf.flush() == []

    def test_invalid_utf8_replaced(self) -> None:
        buf = SheetOutputBuffer()
        lines = buf.feed("stdout", b"ok \xff\xfe\n")
        assert lines and "�" in lines[0]


class TestRingEviction:
    def test_drop_oldest_with_marker(self) -> None:
        buf = SheetOutputBuffer(capacity_bytes=100)
        for i in range(20):
            buf.feed("stdout", f"line-{i:02d}-aaaaaaaaaa\n".encode())
        snap = buf.snapshot()
        assert snap[0].startswith("[dropped ")
        assert snap[-1] == "line-19-aaaaaaaaaa"
        assert "line-00-aaaaaaaaaa" not in snap

    def test_under_capacity_no_marker(self) -> None:
        buf = SheetOutputBuffer(capacity_bytes=10_000)
        buf.feed("stdout", b"one\ntwo\n")
        assert buf.snapshot() == ["one", "two"]


class TestHubFanOut:
    def test_subscriber_receives_lines(self) -> None:
        hub = OutputStreamHub()
        sub = hub.subscribe("job", 1)
        hub.make_writer("job", 1)("stdout", b"hello\n")
        assert sub.queue.get_nowait() == "hello"

    def test_snapshot_independent_of_subscription(self) -> None:
        hub = OutputStreamHub()
        hub.make_writer("job", 2)("stdout", b"a\nb\n")
        assert hub.snapshot("job", 2) == ["a", "b"]
        assert hub.sheets_for_job("job") == [2]
        assert hub.snapshot("job", 99) == []

    def test_backpressure_drops_with_marker(self) -> None:
        hub = OutputStreamHub()
        sub = hub.subscribe("job", 1)
        write = hub.make_writer("job", 1)
        for i in range(SUBSCRIBER_QUEUE_SIZE + 50):
            write("stdout", f"l{i}\n".encode())
        drained: list[str] = []
        while not sub.queue.empty():
            drained.append(sub.queue.get_nowait())
        assert len(drained) == SUBSCRIBER_QUEUE_SIZE
        # The drop is INVISIBLE until queue space frees; the next offered
        # line is preceded by the marker.
        write("stdout", b"after-drain\n")
        assert sub.queue.get_nowait().startswith("[dropped ")
        assert sub.queue.get_nowait() == "after-drain"

    def test_unsubscribe_stops_delivery(self) -> None:
        hub = OutputStreamHub()
        sub = hub.subscribe("job", 1)
        hub.unsubscribe("job", 1, sub)
        hub.make_writer("job", 1)("stdout", b"x\n")
        assert sub.queue.empty()

    def test_clear_job_drops_rings(self) -> None:
        hub = OutputStreamHub()
        hub.make_writer("job", 1)("stdout", b"x\n")
        hub.clear_job("job")
        assert hub.snapshot("job", 1) == []

    def test_job_wide_subscription_tags_sheets(self) -> None:
        """`mzt watch JOB` (no sheet): every sheet's lines, [s<n>]-tagged."""
        hub = OutputStreamHub()
        sub = hub.subscribe("job")
        hub.make_writer("job", 1)("stdout", b"from-one\n")
        hub.make_writer("job", 2)("stdout", b"from-two\n")
        assert sub.queue.get_nowait() == "[s1] from-one"
        assert sub.queue.get_nowait() == "[s2] from-two"

    def test_job_wide_snapshot_ordered_and_tagged(self) -> None:
        hub = OutputStreamHub()
        hub.make_writer("job", 2)("stdout", b"two\n")
        hub.make_writer("job", 1)("stdout", b"one\n")
        assert hub.snapshot("job") == ["[s1] one", "[s2] two"]

    def test_job_wide_unsubscribe(self) -> None:
        hub = OutputStreamHub()
        sub = hub.subscribe("job")
        hub.unsubscribe("job", None, sub)
        hub.make_writer("job", 1)("stdout", b"x\n")
        assert sub.queue.empty()

    def test_writer_fail_open(self) -> None:
        """A broken subscriber/buffer must never raise into the drain."""
        hub = OutputStreamHub()
        sub = hub.subscribe("job", 1)
        sub.offer = None  # type: ignore[assignment]  # sabotage
        hub.make_writer("job", 1)("stdout", b"x\n")  # must not raise
        hub.flush("job", 1)  # must not raise


class TestInstrumentSeam:
    """The instrument drain → callback seam, against a REAL subprocess."""

    async def test_chunks_emitted_to_callback(self) -> None:
        from marianne.execution.instruments.cli_backend import PluginCliBackend
        from tests.test_drain_swap_352 import _python_profile

        instrument = PluginCliBackend(_python_profile())
        received: list[tuple[str, bytes]] = []
        instrument.set_output_callback(lambda s, c: received.append((s, c)))

        result = await instrument.execute(
            "import sys; print('to-stream'); sys.stderr.write('err-side\\n')",
            timeout_seconds=30,
        )

        assert result.success is True
        stdout_bytes = b"".join(c for s, c in received if s == "stdout")
        stderr_bytes = b"".join(c for s, c in received if s == "stderr")
        assert b"to-stream" in stdout_bytes
        assert b"err-side" in stderr_bytes

    async def test_callback_error_does_not_fail_execution(self) -> None:
        from marianne.execution.instruments.cli_backend import PluginCliBackend
        from tests.test_drain_swap_352 import _python_profile

        instrument = PluginCliBackend(_python_profile())

        def explode(stream: str, chunk: bytes) -> None:
            raise RuntimeError("observer broke")

        instrument.set_output_callback(explode)
        result = await instrument.execute("print('still works')", timeout_seconds=30)

        assert result.success is True
        assert "still works" in result.stdout

    async def test_callback_cleared_with_none(self) -> None:
        from marianne.execution.instruments.cli_backend import PluginCliBackend
        from tests.test_drain_swap_352 import _python_profile

        instrument = PluginCliBackend(_python_profile())
        received: list[bytes] = []
        instrument.set_output_callback(lambda s, c: received.append(c))
        instrument.set_output_callback(None)

        result = await instrument.execute("print('quiet')", timeout_seconds=30)
        assert result.success is True
        assert received == []
