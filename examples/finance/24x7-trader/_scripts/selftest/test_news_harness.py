"""The 24x7-trader news_harness.sh builds the right headless harness command.

``_scripts/news_harness.sh`` implements the news contract
(``_techniques/news_research.md``) by calling ``codex`` or ``claude`` on the
operator's subscription. These tests put stub ``codex`` and ``claude``
executables first on PATH that record their argv and stdin, so they assert the
command line the script builds without any network, login or API key.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parents[2]
SCRIPT = SUITE / "_scripts" / "news_harness.sh"
PRE_MARKET = SUITE / "pre-market.yaml"

STUB = """#!/usr/bin/env bash
# Records argv (one per line) and stdin, then answers like a harness would.
base="$STUB_DIR/$(basename "$0")"
printf '%s\\n' "$@" > "$base.argv"
cat > "$base.stdin"
if [[ -n "${STUB_FAIL:-}" ]]; then echo "stub: login required" >&2; exit 3; fi
printf '%s' "${STUB_ANSWER-- Fed held rates (https://example.com/fed)}"
"""


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("codex", "claude"):
        stub = bindir / name
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "STUB_DIR": str(tmp_path),
        "HOME": str(tmp_path),
    }


def run(env: dict[str, str], *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args], env={**env, **extra}, capture_output=True, text=True,
        timeout=30, check=False,
    )


def argv(env: dict[str, str], name: str) -> list[str]:
    return Path(env["STUB_DIR"], f"{name}.argv").read_text().splitlines()


def stdin(env: dict[str, str], name: str) -> str:
    return Path(env["STUB_DIR"], f"{name}.stdin").read_text()


VERBS = [
    (["macro_today", "--date", "2026-11-17"], "2026-11-17"),
    (["sector_news", "Semiconductors"], "Semiconductors"),
    (["catalysts", "NVDA"], "NVDA"),
    (["peer_compare", "NVDA"], "NVDA"),
    (["regulatory", "NVDA"], "NVDA"),
]


@pytest.mark.parametrize(("args", "needle"), VERBS)
def test_codex_is_the_default_and_searches_live(env, args, needle):
    proc = run(env, *args)
    assert proc.returncode == 0, proc.stderr
    got = argv(env, "codex")
    # --search is a top-level codex flag; `codex exec --search` is refused by codex.
    assert got[0] == "--search"
    assert got[1] == "exec"
    assert "--sandbox" in got and got[got.index("--sandbox") + 1] == "read-only"
    assert got[-1] == "-"  # prompt on stdin
    prompt = stdin(env, "codex")
    assert needle in prompt
    assert "source URL" in prompt
    assert "https://example.com/fed" in proc.stdout
    assert not Path(env["STUB_DIR"], "claude.argv").exists()


@pytest.mark.parametrize(("args", "needle"), VERBS)
def test_claude_gets_only_the_web_tools(env, args, needle):
    proc = run(env, *args, NEWS_HARNESS="claude")
    assert proc.returncode == 0, proc.stderr
    got = argv(env, "claude")
    assert "-p" in got
    assert got[got.index("--tools") + 1] == "WebSearch,WebFetch"
    assert got[got.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert needle in stdin(env, "claude")
    assert not Path(env["STUB_DIR"], "codex.argv").exists()


def test_no_api_key_is_read_by_the_script():
    text = SCRIPT.read_text()
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_KEY", "CODEX_API_KEY"):
        assert name not in text


def test_model_override(env):
    run(env, "catalysts", "AAPL", NEWS_HARNESS_MODEL="gpt-6.1-terra")
    got = argv(env, "codex")
    assert got[got.index("--model") + 1] == "gpt-6.1-terra"


@pytest.mark.parametrize("harness", ["codex", "claude"])
def test_harness_failure_is_one_line_nonzero(env, harness):
    proc = run(env, "catalysts", "AAPL", NEWS_HARNESS=harness, STUB_FAIL="1")
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert len(proc.stderr.strip().splitlines()) == 1
    assert "news_harness" in proc.stderr and harness in proc.stderr


@pytest.mark.parametrize("harness", ["codex", "claude"])
def test_empty_answer_fails(env, harness):
    proc = run(env, "catalysts", "AAPL", NEWS_HARNESS=harness, STUB_ANSWER="")
    assert proc.returncode == 1 and proc.stdout == ""
    assert len(proc.stderr.strip().splitlines()) == 1


def test_answer_without_a_source_url_fails(env):
    proc = run(env, "catalysts", "AAPL", STUB_ANSWER="- NVDA is great")
    assert proc.returncode == 1 and proc.stdout == ""
    assert "cites no source URL" in proc.stderr


def test_offline_marker_passes_through_as_the_contract_defines(env):
    marked = "**[OFFLINE — training data only]**\n\n- from memory"
    proc = run(env, "catalysts", "AAPL", STUB_ANSWER=marked)
    assert proc.returncode == 0
    assert proc.stdout.startswith("**[OFFLINE")


def test_missing_harness_binary_fails(env):
    (Path(env["PATH"].split(":")[0]) / "claude").unlink()
    if shutil.which("claude", path="/usr/bin:/bin"):
        pytest.skip("a real claude is on the system PATH")
    proc = run(env, "catalysts", "AAPL", NEWS_HARNESS="claude")
    assert proc.returncode == 1
    assert "not found on PATH" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["nonsense"],
        ["catalysts"],
        ["catalysts", "AAPL; rm -rf /"],
        ["catalysts", "two words"],
        ["macro_today", "--date", "yesterday"],
        ["macro_today", "extra"],
    ],
)
def test_bad_arguments_fail_before_any_harness_runs(env, args):
    proc = run(env, *args)
    assert proc.returncode == 1
    assert len(proc.stderr.strip().splitlines()) == 1
    assert not Path(env["STUB_DIR"], "codex.argv").exists()


def test_unknown_harness_fails(env):
    proc = run(env, "catalysts", "AAPL", NEWS_HARNESS="perplexity")
    assert proc.returncode == 1
    assert "codex or claude" in proc.stderr


def test_pre_market_frames_search_the_web_directly():
    """The stage-2 frames' instrument alias grants WebSearch and WebFetch."""
    import yaml

    score = yaml.safe_load(PRE_MARKET.read_text())
    alias = score["instruments"]["researcher"]
    assert alias["profile"] == "claude-code"
    assert {"WebSearch", "WebFetch"} <= set(alias["config"]["tools"])
    assert score["movements"][2]["instrument"] == "researcher"
    json.dumps(score["movements"])  # plain data
    assert os.access(SCRIPT, os.X_OK)
