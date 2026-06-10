"""#227: socket/PID defaults move from /tmp to ~/.config/mzt.

/tmp is wiped on reboot and shared world-writable; the composer's call is
to keep all of mzt's runtime/config artifacts in ``~/.config/mzt`` for
consistency with standard tooling. New names use the ``mzt`` prefix (CLI
rename direction), and conductor clones follow the same convention.

Legacy probe (transitional, read-side only): a conductor started before
this change still serves on ``/tmp/marianne.sock``/``.pid``. To avoid
blinding ``mzt status``/``mzt stop`` to that LIVE conductor — or letting
``mzt start`` spawn a second conductor against the same state DB — the
client-side resolvers fall back to the legacy paths when the new default
is absent and the legacy artifact exists. The probe self-eliminates: once
the conductor restarts on the new paths, the legacy files are gone and
the fallback never fires.
"""

from __future__ import annotations

from pathlib import Path

from marianne.daemon.config import (
    LEGACY_PID_PATH,
    LEGACY_SOCKET_PATH,
    DaemonConfig,
    SocketConfig,
)


class TestNewDefaults:
    def test_socket_default_in_config_mzt(self) -> None:
        assert SocketConfig().path == Path.home() / ".config" / "mzt" / "mzt.sock"

    def test_pid_default_in_config_mzt(self) -> None:
        assert DaemonConfig().pid_file == Path.home() / ".config" / "mzt" / "mzt.pid"

    def test_legacy_constants_point_at_old_tmp_paths(self) -> None:
        assert Path("/tmp/marianne.sock") == LEGACY_SOCKET_PATH
        assert Path("/tmp/marianne.pid") == LEGACY_PID_PATH


class TestSocketResolutionFallback:
    def test_prefers_new_default_when_present(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from marianne.daemon import detect

        new = tmp_path / "new" / "mzt.sock"
        new.parent.mkdir(parents=True)
        new.touch()
        legacy = tmp_path / "legacy.sock"
        legacy.touch()
        monkeypatch.setattr(
            detect, "_default_socket_path", lambda: new
        )
        monkeypatch.setattr(detect, "LEGACY_SOCKET_PATH", legacy)

        assert detect._resolve_socket_path(None) == new

    def test_falls_back_to_live_legacy_socket(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from marianne.daemon import detect

        new = tmp_path / "new" / "mzt.sock"  # absent
        legacy = tmp_path / "legacy.sock"
        legacy.touch()
        monkeypatch.setattr(detect, "_default_socket_path", lambda: new)
        monkeypatch.setattr(detect, "LEGACY_SOCKET_PATH", legacy)

        assert detect._resolve_socket_path(None) == legacy

    def test_neither_present_uses_new_default(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from marianne.daemon import detect

        new = tmp_path / "new" / "mzt.sock"
        legacy = tmp_path / "legacy.sock"
        monkeypatch.setattr(detect, "_default_socket_path", lambda: new)
        monkeypatch.setattr(detect, "LEGACY_SOCKET_PATH", legacy)

        assert detect._resolve_socket_path(None) == new

    def test_explicit_path_always_wins(self, tmp_path: Path) -> None:
        from marianne.daemon import detect

        explicit = tmp_path / "explicit.sock"
        assert detect._resolve_socket_path(explicit) == explicit


class TestPidResolutionFallback:
    def test_prefers_default_with_live_pid(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import os

        from marianne.daemon import process

        preferred = tmp_path / "mzt.pid"
        preferred.write_text(str(os.getpid()))
        legacy = tmp_path / "legacy.pid"
        legacy.write_text(str(os.getpid()))
        monkeypatch.setattr(process, "LEGACY_PID_PATH", legacy)

        assert process._resolve_live_pid_file(preferred) == preferred

    def test_falls_back_to_live_legacy_pid(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import os

        from marianne.daemon import process

        preferred = tmp_path / "mzt.pid"  # absent
        legacy = tmp_path / "legacy.pid"
        legacy.write_text(str(os.getpid()))
        monkeypatch.setattr(process, "LEGACY_PID_PATH", legacy)

        assert process._resolve_live_pid_file(preferred) == legacy

    def test_dead_legacy_pid_not_used(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from marianne.daemon import process

        preferred = tmp_path / "mzt.pid"
        legacy = tmp_path / "legacy.pid"
        legacy.write_text("999999999")  # not a live PID
        monkeypatch.setattr(process, "LEGACY_PID_PATH", legacy)

        assert process._resolve_live_pid_file(preferred) == preferred


class TestClonePaths:
    def test_clone_runtime_artifacts_under_config_mzt(self) -> None:
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("alpha")
        runtime = Path.home() / ".config" / "mzt"
        assert paths.socket == runtime / "clone-alpha.sock"
        assert paths.pid_file == runtime / "clone-alpha.pid"
        assert paths.log_file == runtime / "clone-alpha.log"
        # State DB stays in ~/.marianne (data, not runtime).
        assert paths.state_db == Path.home() / ".marianne" / "clone-alpha-state.db"

    def test_default_clone_paths(self) -> None:
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths(None)
        runtime = Path.home() / ".config" / "mzt"
        assert paths.socket == runtime / "clone.sock"
        assert paths.pid_file == runtime / "clone.pid"
