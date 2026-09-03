"""V215: warn when a score's instrument chain has nothing installed here.

The unknown-system onboarding guard. The deep fallback chain skips uninstalled
instruments at dispatch, so a chain that resolves to zero installed CLI binaries
would advance straight to HTTP fallbacks (which need a server/key) or exhaust.
V215 surfaces that at validate time with an actionable, free-path-first message.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.validation.checks.config import NoUsableInstrumentCheck


def _config(*, instrument: str, fallbacks: list[str] | None = None) -> JobConfig:
    return JobConfig.model_validate(
        {
            "name": "v212",
            "workspace": "./ws",
            "instrument": instrument,
            "instrument_fallbacks": fallbacks or [],
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "x"},
        }
    )


def _run(config: JobConfig) -> list:
    return NoUsableInstrumentCheck().check(config, Path("s.yaml"), "instrument: x")


class TestNoUsableInstrument:
    def test_quiet_when_an_installed_cli_is_in_the_chain(self) -> None:
        # `cli` is the built-in bash instrument — its binary (bash) is on PATH
        # in any test environment, so the chain is runnable: no warning.
        assert _run(_config(instrument="cli")) == []

    def test_warns_when_nothing_in_the_chain_resolves(self) -> None:
        # An unregistered instrument with no fallbacks: nothing installed.
        issues = _run(_config(instrument="ghost-instrument-xyz"))
        assert len(issues) == 1
        assert issues[0].severity.value == "warning"
        assert issues[0].check_id == "V215"
        assert "ollama" in (issues[0].suggestion or "").lower()

    def test_http_only_chain_warns_about_endpoints(self) -> None:
        # No installed CLI, but the maintained Ollama HTTP profile is present:
        # the message must flag that HTTP needs a server/key, not claim success.
        issues = _run(
            _config(instrument="ghost-instrument-xyz", fallbacks=["ollama"])
        )
        assert len(issues) == 1
        assert "HTTP" in issues[0].message

    def test_installed_fallback_silences_the_warning(self) -> None:
        # Primary missing, but a fallback (cli/bash) is installed → runnable.
        assert _run(_config(instrument="ghost-instrument-xyz", fallbacks=["cli"])) == []
