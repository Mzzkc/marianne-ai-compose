#!/usr/bin/env python3
"""Compare two programs on a corpus; a waiver excuses only the lines it names.

Used by examples/engineering/codebase-rewrite.yaml. Each file in INPUTS is fed
on stdin to the source command and the target command (both run through
``bash -c`` from the current directory). Their stdout and exit code must be
equal, EXCEPT for differences the waiver file scopes exactly.

Waiver file format, one waiver per input, comment lines start with ``#``::

    <input-filename>  <scope>  # <specification rule>

``<scope>`` is a comma-separated list of:

    exit                 the exit code may differ
    stdout:-<regex>      lines of SOURCE stdout matching <regex> may be absent from target
    stdout:+<regex>      lines of TARGET stdout matching <regex> may be extra
    stdout:*             all of stdout may differ   (a blanket; counted and reported)

A bare filename with no scope is the old whole-output waiver. It is REFUSED:
it exempts everything an input prints, so one rule could hide a changed
verdict behind a permitted framing change (GH #530).

After removing exactly the waived lines, source and target must be equal. An
input that still differs is a failure even though it is listed.

Output: one line per input (``SAME``, ``INTENDED``, ``DIFF``, ``BADWAIVER``),
then a ``SUMMARY`` line. Exit 0 when nothing fails, 1 when an input fails,
2 when the waiver file itself is unusable.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def run(command: str, data: bytes, timeout: float) -> tuple[list[str], int]:
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            input=data,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ["<timeout>"], 124
    return proc.stdout.decode("utf-8", "replace").splitlines(), proc.returncode


def parse_waivers(path: Path) -> tuple[dict[str, list[str]], list[str]]:
    waivers: dict[str, list[str]] = {}
    problems: list[str] = []
    if not path.exists():
        return waivers, problems
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = line.split(None, 1)
        name = parts[0]
        if len(parts) == 1:
            problems.append(
                f"line {number}: '{name}' has no scope; a bare filename waives the whole "
                "output and is refused. Name what may differ: exit, stdout:-<regex>, "
                "stdout:+<regex>."
            )
            continue
        scopes = [s.strip() for s in parts[1].split(",") if s.strip()]
        for scope in scopes:
            if scope == "exit" or scope == "stdout:*":
                continue
            m = re.fullmatch(r"stdout:([+-])(.+)", scope)
            if not m:
                problems.append(f"line {number}: unknown scope '{scope}' for '{name}'")
                continue
            try:
                re.compile(m.group(2))
            except re.error as exc:
                problems.append(f"line {number}: bad regex in '{scope}': {exc}")
        waivers.setdefault(name, []).extend(scopes)
    return waivers, problems


def apply(scopes: list[str], src: list[str], tgt: list[str], s_exit: int, t_exit: int):
    """Return (source, target, source_exit, target_exit) with waived parts removed."""
    if "stdout:*" in scopes:
        src, tgt = [], []
    for scope in scopes:
        m = re.fullmatch(r"stdout:([+-])(.+)", scope)
        if not m:
            continue
        pattern = re.compile(m.group(2))
        if m.group(1) == "-":
            src = [line for line in src if not pattern.search(line)]
        else:
            tgt = [line for line in tgt if not pattern.search(line)]
    if "exit" in scopes:
        s_exit = t_exit = 0
    return src, tgt, s_exit, t_exit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--waivers", type=Path, required=True)
    ap.add_argument("--source", required=True, help="source run command (bash -c)")
    ap.add_argument("--target", required=True, help="target run command (bash -c)")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument(
        "--max-blanket-share",
        type=float,
        default=0.10,
        help="fail when more than this share of inputs use stdout:* (default 0.10)",
    )
    args = ap.parse_args()

    waivers, problems = parse_waivers(args.waivers)
    for problem in problems:
        print(f"BADWAIVER {problem}")
    files = sorted(p for p in args.inputs.iterdir() if p.is_file())
    if not files:
        print("SUMMARY inputs=0 (empty corpus)")
        return 2
    unknown = sorted(set(waivers) - {p.name for p in files})
    for name in unknown:
        print(f"BADWAIVER '{name}' is not a file in the corpus")

    same = intended = failed = blankets = 0
    for path in files:
        data = path.read_bytes()
        src, s_exit = run(args.source, data, args.timeout)
        tgt, t_exit = run(args.target, data, args.timeout)
        if src == tgt and s_exit == t_exit:
            same += 1
            print(f"SAME {path.name}")
            continue
        scopes = waivers.get(path.name, [])
        if not scopes:
            failed += 1
            print(f"DIFF {path.name}")
            continue
        if "stdout:*" in scopes:
            blankets += 1
        a, b, ae, be = apply(scopes, src, tgt, s_exit, t_exit)
        if a == b and ae == be:
            intended += 1
            print(f"INTENDED {path.name} [{', '.join(scopes)}]")
        else:
            failed += 1
            detail = "exit" if ae != be else "stdout"
            print(f"DIFF {path.name} (listed, but {detail} still differs outside its waiver)")

    share = blankets / len(files)
    over = share > args.max_blanket_share
    print(
        f"SUMMARY inputs={len(files)} same={same} intended={intended} failed={failed} "
        f"blanket_waivers={blankets} bad_waivers={len(problems) + len(unknown)}"
        + (f" BLANKET_SHARE_EXCEEDED {share:.0%}>{args.max_blanket_share:.0%}" if over else "")
    )
    if problems or unknown:
        return 2
    return 1 if failed or over else 0


if __name__ == "__main__":
    sys.exit(main())
