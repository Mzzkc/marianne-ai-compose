#!/usr/bin/env python3
"""resolve-hello.py — discover this machine, then template a runnable hello.

Run by the cli prescore (`hello-setup.yaml`). No AI, no network model calls, so
it can never hang. It:

  1. picks the best AVAILABLE + REACHABLE instrument, free first (Ollama →
     free OpenRouter via crush → a paid CLI sub → a paid API);
  2. detects the browser-open command for this OS/shell (wslview / xdg-open /
     open / explorer.exe) and reports it (the assembler does the actual opening);
  3. reads the hello orchestration template and writes a RESOLVED copy with the
     chosen instrument baked in — so the run adapts to the machine and, via the
     assembler, ends with the finished page on screen.

This is the "score editing / templating" the onboarding demonstrates, kept
deliberately simple: load a score, change one field, add one hook, write it out.

Usage: resolve-hello.py <workspace-dir> <hello-template.yaml>
Writes: <workspace>/hello-resolved.yaml   (returns non-zero if nothing is usable)
"""

from __future__ import annotations

import os
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2):
            return True
    except Exception:
        return False


def pick_instrument() -> tuple[str | None, str | None, str]:
    """(instrument, model, human-label) — free first, only if reachable."""
    if _ollama_up() and _have("opencode"):
        return "opencode-gemma", None, "free · local Ollama"
    if os.environ.get("OPENROUTER_API_KEY") and _have("crush"):
        return "crush", "qwen/qwen3-coder:free", "free · OpenRouter"
    if _have("claude"):
        return "claude-code", None, "paid · Anthropic Max"
    if _have("gemini"):
        return "gemini-cli", None, "paid · Google"
    if _have("opencode") and os.environ.get("ZAI_API_KEY"):
        return "opencode", "zai-coding-plan/glm-5.1", "paid · Z.AI Coding Plan"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic_api", None, "paid · Anthropic API"
    return None, None, ""


def pick_opener() -> str:
    for cmd in ("wslview", "xdg-open", "open", "explorer.exe"):
        if _have(cmd):
            return cmd
    return ""


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: resolve-hello.py <workspace-dir> <hello-template.yaml>", file=sys.stderr)
        return 2
    ws = Path(sys.argv[1]).resolve()
    template = Path(sys.argv[2]).resolve()

    instrument, model, label = pick_instrument()
    opener = pick_opener()

    print(f"[discover] instrument : {instrument or 'NONE'}  ({label or 'nothing reachable'})")
    print(f"[discover] model      : {model or '(profile default)'}")
    print(f"[discover] open with  : {opener or 'NONE — open the file yourself'}")

    if instrument is None:
        print(
            "[discover] No usable AI instrument found. Set up ONE free path:\n"
            "  • install Ollama and `ollama pull gemma2` (local, no account), or\n"
            "  • set OPENROUTER_API_KEY and install crush.\n"
            "See docs/sandbox-free-quickstart.md.",
            file=sys.stderr,
        )
        return 1

    import yaml  # local import: only needed on the success path

    cfg = yaml.safe_load(template.read_text(encoding="utf-8"))

    # ── the score edit: pin the one instrument we confirmed works ──
    cfg["instrument"] = instrument
    cfg["instrument_config"] = {"model": model} if model else {}
    cfg.pop("instruments", None)
    cfg.pop("instrument_fallbacks", None)
    cfg["workspace"] = str(ws)

    # NOTE: the finished page is opened by the assembler (assemble-site.py) on
    # BOTH the direct and resolved runs, so we don't add a redundant on_success
    # opener here — one opener, no double-open. We still surface the detected
    # opener above so the user sees how their machine was read.

    # ── make the asset paths absolute so the resolved score runs from any
    #    workspace (the template uses a repo-relative path that only works
    #    2 levels under the repo). This is the templating, in plain sight. ──
    assets_rel = "{{ workspace }}/../../examples/getting-started/assets"
    assets_abs = str((template.parent / "assets").resolve())

    def abs_paths(node: Any) -> Any:
        if isinstance(node, str):
            return node.replace(assets_rel, assets_abs)
        if isinstance(node, dict):
            return {k: abs_paths(v) for k, v in node.items()}
        if isinstance(node, list):
            return [abs_paths(v) for v in node]
        return node

    out = ws / "hello-resolved.yaml"
    out.write_text(
        yaml.safe_dump(abs_paths(cfg), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"[discover] wrote resolved score → {out}")
    print(f"[discover] running the orchestration on: {instrument} ({label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
