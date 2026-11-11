"""Tier files as sections: parse, render, rank. Pure — no I/O.

A tier is a Markdown file directly inside the scope's tree (``recent.md``,
``growth.md``...). It holds two kinds of unit:

- **fact blocks** the backend wrote. Each opens with a level-2 heading that
  carries an HTML-comment marker and closes with a marker line::

      ## 2026-11-11 09:30:00Z <!-- fact:0123456789abcdef -->
      the fact text, verbatim (it may contain its own headings)
      <!-- /fact:0123456789abcdef -->

  The text between the markers is the fact, minus the one newline the
  writer adds. Headings inside it do not split it.
- **hand-written sections**: everything else, cut at ``## `` and ``### ``
  heading lines. Text before the first heading is a section of its own.
  Whitespace-only stretches are not units.

Units are returned with their character offsets into the decoded text so a
writer can splice one out without touching any other byte.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

_OPEN_RE = re.compile(r"^#{2,3} [^\n]*<!-- fact:([0-9a-f]{16}) -->[ \t]*\r?\n?\Z")
_CLOSE_RE = re.compile(r"^<!-- /fact:([0-9a-f]{16}) -->[ \t]*\r?\n?\Z")
_HEADING_RE = re.compile(r"^#{2,3} ")
_ANY_MARKER_RE = re.compile(r"<!-- /?fact:[0-9a-f]{16} -->")
_TOKEN_RE = re.compile(r"\w+")

RECENCY_HALF_LIFE_SECONDS = 30 * 86400.0
MATCH_WEIGHT = 0.6
RECENCY_WEIGHT = 0.4


@dataclass(frozen=True)
class Unit:
    """One recallable unit of a tier file."""

    unit_id: str
    tier: str
    content: str
    start: int
    end: int
    fact_id: str | None

    @property
    def size(self) -> int:
        return len(self.content.encode("utf-8"))


def decode(data: bytes) -> str:
    """Decode tier bytes so that :func:`encode` gives back the exact bytes."""
    return data.decode("utf-8", "surrogateescape")


def encode(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def _clean(text: str) -> str:
    """Text safe to hand to a caller: undecodable bytes become U+FFFD."""
    return encode(text).decode("utf-8", "replace")


def _lines(text: str) -> list[str]:
    """Split on ``\\n`` only (``str.splitlines`` also splits on U+2028 etc.)."""
    parts = text.split("\n")
    out = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def has_marker(text: str) -> bool:
    """True when ``text`` contains something the parser would read as a marker."""
    return _ANY_MARKER_RE.search(text) is not None


def render_block(fact_id: str, text: str, when: float) -> str:
    """The exact text written for one fact."""
    stamp = datetime.fromtimestamp(when, UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    return f"## {stamp} <!-- fact:{fact_id} -->\n{text}\n<!-- /fact:{fact_id} -->\n"


def _section_id(tier: str, content: str, seen: dict[str, int]) -> str:
    base = "sec-" + hashlib.sha256(f"{tier}\0{content}".encode()).hexdigest()[:16]
    count = seen.get(base, 0) + 1
    seen[base] = count
    return base if count == 1 else f"{base}-{count}"


def parse_tier(tier: str, text: str) -> list[Unit]:
    """Cut ``text`` (decoded with :func:`decode`) into units, in file order."""
    lines = _lines(text)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    units: list[Unit] = []
    seen: dict[str, int] = {}
    hand_start = 0

    def flush(end: int) -> None:
        chunk = text[hand_start:end]
        if chunk.strip():
            content = _clean(chunk)
            sid = _section_id(tier, content, seen)
            units.append(Unit(sid, tier, content, hand_start, end, None))

    i = 0
    while i < len(lines):
        line = lines[i]
        opened = _OPEN_RE.match(line)
        if opened:
            fid = opened.group(1)
            close = next(
                (j for j in range(i + 1, len(lines))
                 if (m := _CLOSE_RE.match(lines[j])) and m.group(1) == fid),
                None,
            )
            if close is not None:
                flush(offsets[i])
                body = "".join(lines[i + 1:close])
                if body.endswith("\n"):
                    body = body[:-1]
                units.append(Unit(fid, tier, _clean(body), offsets[i], offsets[close + 1], fid))
                hand_start = offsets[close + 1]
                i = close + 1
                continue
        if _HEADING_RE.match(line):
            flush(offsets[i])
            hand_start = offsets[i]
        i += 1
    flush(len(text))
    return units


def tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.lower()))


def score(query_tokens: frozenset[str], content: str, asserted_at: float, now: float) -> float:
    """``0.6 * match + 0.4 * 2^(-age / 30 days)``, rounded to 6 places.

    ``match`` is the share of query tokens present in the unit's text (0 for
    an empty query). The caller drops units with no match for a non-empty
    query.
    """
    match = len(query_tokens & tokens(content)) / len(query_tokens) if query_tokens else 0.0
    age = max(0.0, now - asserted_at)
    recency = 2.0 ** (-age / RECENCY_HALF_LIFE_SECONDS)
    return float(round(MATCH_WEIGHT * match + RECENCY_WEIGHT * recency, 6))


def matches(query_tokens: frozenset[str], content: str) -> bool:
    return not query_tokens or bool(query_tokens & tokens(content))
