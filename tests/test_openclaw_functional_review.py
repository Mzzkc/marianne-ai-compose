"""Independent boundary coverage for the OpenClaw instrument review."""

import json
from pathlib import Path

import pytest

from marianne.daemon.learning_hub import LearningHub
from marianne.learning.store import GlobalLearningStore
from tests.test_openclaw_instrument import fake_openclaw, openclaw_backend


@pytest.mark.parametrize("usage, expected", [
    ({}, (None, None)),
    ({"usage": {"input": 0, "output": 0}}, (0, 0)),
    ({"usage": {"input": 7}}, (7, None)),
])
def test_optional_usage_and_empty_final(usage: dict, expected: tuple) -> None:
    envelope = {"ok": True, "status": "ok", "final": "", **usage}
    result = openclaw_backend()._parse_output(json.dumps(envelope), "", exit_code=0)
    assert result.success
    assert result.stdout == ""
    assert (result.input_tokens, result.output_tokens) == expected


async def test_large_utf8_prompt_uses_stdin_without_shell_expansion(tmp_path: Path) -> None:
    backend = fake_openclaw(tmp_path, '''
import sys
from pathlib import Path
Path("prompt.bin").write_bytes(sys.stdin.buffer.read())
Path("argv.txt").write_text(repr(sys.argv))
print(FIXTURE)
''')
    prompt = "日本語 🐺\n$(touch injected) `touch injected` 'quoted'\n" * 5000
    try:
        result = await backend.execute(prompt, timeout_seconds=10)
        assert result.success
        assert result.stdout == "Created greeting.py."
        assert (tmp_path / "prompt.bin").read_bytes() == prompt.encode("utf-8")
        assert "touch injected" not in (tmp_path / "argv.txt").read_text()
        assert not (tmp_path / "injected").exists()
    finally:
        await backend.close()


async def test_nonzero_exit_wins_over_success_shaped_envelope(tmp_path: Path) -> None:
    backend = fake_openclaw(tmp_path, "import sys\nprint(FIXTURE)\nsys.exit(1)")
    try:
        result = await backend.execute("hello", timeout_seconds=10)
        assert not result.success
    finally:
        await backend.close()


@pytest.mark.parametrize("explicit", [False, True])
async def test_learning_isolation_preserves_real_restart_storage(
    tmp_path: Path, explicit: bool,
) -> None:
    requested = tmp_path / "explicit.db" if explicit else None
    expected = requested or tmp_path / "global-learning.db"
    store = GlobalLearningStore(requested)
    try:
        assert store.db_path == expected
        with store._get_connection() as conn:
            conn.execute("CREATE TABLE review_probe (value TEXT)")
            conn.execute("INSERT INTO review_probe VALUES ('persisted')")
    finally:
        store.close()
    hub = LearningHub(requested)
    try:
        await hub.start()
        assert hub.store.db_path == expected
        with hub.store._get_connection() as conn:
            assert conn.execute("SELECT value FROM review_probe").fetchone()[0] == "persisted"
    finally:
        await hub.stop()
