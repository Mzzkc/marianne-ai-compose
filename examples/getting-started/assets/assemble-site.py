#!/usr/bin/env python3
"""assemble-site.py — deterministic website assembly for the `hello` score.

The hybrid synthesis floor: reads the markdown the AI musicians wrote (the
shared world + the parallel character vignettes) and emits ONE self-contained,
valid HTML page — zero AI, zero dependencies. It is the reliability guarantee
of onboarding: even if every model in the fallback chain produced only plain
text, a new user still opens a real, styled website.

The layout is a GALLERY: the world is the hero, and each parallel vignette is
its own card in a responsive grid — so the fan-out (three agents working at
once) is something you can SEE, not just read. A later AI sheet then polishes
this base into the final the-sky-library.html, tailoring it to the story told.

Usage:
    python3 assemble-site.py <workspace-dir> [output.html]

Reads (whichever exist):
    <ws>/01-world.md, <ws>/02-character-1.md … 02-character-N.md, <ws>/03-finale.md
Writes:
    <ws>/site-base.html   (or the explicit output path)
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path


def md_to_html(md: str, *, drop_first_h1: bool = False) -> tuple[str, str]:
    """Tiny dependency-free markdown → HTML for the subset the prompts produce.
    Returns (title, body_html); when drop_first_h1 is set, the first top-level
    heading is pulled out as the title (used for card headers). Everything is
    HTML-escaped first, so model output can never inject markup."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    title = ""
    in_list = False
    para: list[str] = []

    def inline(text: str) -> str:
        text = html.escape(text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
        return text

    def flush_para() -> None:
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        header = re.match(r"^(#{1,6})\s+(.*)$", line)
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        if header:
            flush_para()
            close_list()
            level = len(header.group(1))
            text = header.group(2)
            if drop_first_h1 and level == 1 and not title:
                title = text
                continue
            out.append(f"<h{min(level + 1, 6)}>{inline(text)}</h{min(level + 1, 6)}>")
        elif bullet:
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(bullet.group(1))}</li>")
        elif not line.strip():
            flush_para()
            close_list()
        else:
            close_list()
            para.append(line.strip())
    flush_para()
    close_list()
    return title, "\n".join(out)


def read(ws: Path, name: str) -> str:
    p = ws / name
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Sky Library — A Marianne Composition</title>
<style>
  :root {{ --ink:#2b2724; --paper:#fbf7ef; --gold:#b8893b; --c1:#6c8ab0; --c2:#5c7a5a; --c3:#a86b8a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
         font-family:Georgia,'Iowan Old Style',serif; font-size:18px; line-height:1.65; }}
  .wrap {{ max-width:1040px; margin:0 auto; padding:3.5rem 1.5rem 5rem; }}
  header.hero {{ text-align:center; max-width:680px; margin:0 auto 1rem; }}
  header.hero h1 {{ font-size:2.7rem; margin:0 0 .3rem; letter-spacing:.5px; }}
  header.hero .tagline {{ font-style:italic; color:var(--gold); margin:0 0 1.8rem; font-size:1.1rem; }}
  header.hero .world {{ text-align:left; }}
  h2,h3,h4 {{ font-family:'Helvetica Neue',Arial,sans-serif; line-height:1.3; }}
  .gallery-label {{ text-align:center; font-family:'Helvetica Neue',Arial,sans-serif;
                    text-transform:uppercase; letter-spacing:.25em; font-size:.8rem;
                    color:var(--gold); margin:3rem 0 1.2rem; }}
  .gallery {{ display:grid; gap:1.5rem;
             grid-template-columns:repeat(auto-fit,minmax(270px,1fr)); align-items:start; }}
  .card {{ background:#fff; border-top:5px solid var(--accent,var(--c1));
          border-radius:6px; padding:1.4rem 1.5rem; box-shadow:0 2px 14px rgba(60,50,35,.08); }}
  .card:nth-child(3n+1) {{ --accent:var(--c1); }}
  .card:nth-child(3n+2) {{ --accent:var(--c2); }}
  .card:nth-child(3n+3) {{ --accent:var(--c3); }}
  .card h3 {{ margin:.1rem 0 .8rem; color:var(--accent); font-size:1.35rem; }}
  .card p {{ font-size:.97rem; }}
  code {{ background:#efe8d8; padding:.05em .35em; border-radius:3px; font-size:.9em; }}
  .finale {{ max-width:680px; margin:3.5rem auto 0; }}
  .ornament {{ text-align:center; color:var(--gold); letter-spacing:.6em; margin:2.5rem 0; }}
  footer.colophon {{ max-width:680px; margin:3.5rem auto 0; padding-top:1.5rem;
                     border-top:1px solid #d9cfb9; font-size:.85rem; font-style:italic;
                     color:#7a7166; text-align:center; }}
  .soundtrack-toggle {{ position:fixed; right:1rem; bottom:1rem; z-index:50;
    font:inherit; font-size:.85rem; color:var(--paper); background:var(--ink);
    border:none; border-radius:999px; padding:.5rem 1rem; cursor:pointer;
    box-shadow:0 2px 10px rgba(0,0,0,.2); opacity:.85; }}
  .soundtrack-toggle:hover {{ opacity:1; }}
  @media (max-width:520px) {{ body {{ font-size:17px; }} header.hero h1 {{ font-size:2.1rem; }} }}
</style>
</head>
<body>
<div class="wrap">
<header class="hero">
  <h1>The Sky Library</h1>
  <p class="tagline">A composition in movements — orchestrated, not prompted.</p>
  <div class="world">{world}</div>
</header>

<p class="gallery-label">Three vignettes · written in parallel</p>
<div class="gallery">
{cards}
</div>

{finale}

<footer class="colophon">
  This page was composed by <strong>Marianne AI Compose</strong> — an orchestration
  system that coordinates AI agents through declarative YAML scores. The world was
  written first; the {nchar} vignettes above were generated <em>in parallel</em>, each
  agent reading the shared world but not the others; a tool assembled this page and an
  agent polished it.<br>
  Score: hello.yaml · Fan-out + Synthesis · Free &amp; local-capable.
</footer>
</div>
{soundtrack}
</body>
</html>
"""

CARD = "<article class='card'><h3>{title}</h3>\n{body}</article>"

# The soundtrack player: a hidden Strudel REPL web component + a corner toggle.
# Never autoplays. The pattern is JSON-encoded so any quotes/newlines are safe.
SOUNDTRACK = """<button class="soundtrack-toggle" id="snd-toggle" type="button">♪ play soundtrack</button>
<strudel-editor id="snd-repl" style="position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;"></strudel-editor>
<script type="module">
import 'https://unpkg.com/@strudel/repl@1.3.0';
const PATTERN = {pattern};
const ed = document.getElementById('snd-repl');
const btn = document.getElementById('snd-toggle');
let playing = false;
btn.addEventListener('click', () => {{
  const repl = ed && ed.editor;
  if (!repl) {{ btn.textContent = '♪ loading…'; return; }}
  if (playing) {{ repl.stop(); playing = false; btn.textContent = '♪ play soundtrack'; }}
  else {{ repl.setCode(PATTERN); repl.evaluate(); playing = true; btn.textContent = '♪ mute'; }}
}});
</script>"""


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: assemble-site.py <workspace-dir> [output.html]", file=sys.stderr)
        return 2
    ws = Path(sys.argv[1]).resolve()
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ws / "site-base.html"

    _, world = md_to_html(read(ws, "01-world.md"))
    cards = []
    char_files = sorted(ws.glob("02-character-*.md"))
    for i, f in enumerate(char_files):
        title, body = md_to_html(f.read_text(encoding="utf-8", errors="replace"),
                                 drop_first_h1=True)
        cards.append(CARD.format(title=html.escape(title or f"Vignette {i + 1}"), body=body))
    _, finale_body = md_to_html(read(ws, "03-finale.md"))
    finale = (f"<div class='ornament'>· · ·</div>\n<section class='finale'>"
              f"<h2>The Finale</h2>\n{finale_body}</section>") if finale_body else ""

    # The composed soundtrack (movement 3), embedded as a mute-toggled player.
    # JSON-encoding makes the pattern a safe JS string literal.
    strudel = read(ws, "soundtrack.strudel").strip()
    soundtrack = SOUNDTRACK.format(pattern=json.dumps(strudel)) if strudel else ""

    page = PAGE.format(
        world=world or "<p>(The world was not written.)</p>",
        cards="\n".join(cards) or "<article class='card'><p>(No vignettes were written.)</p></article>",
        finale=finale,
        soundtrack=soundtrack,
        nchar=len(char_files) or "parallel",
    )
    out_path.write_text(page, encoding="utf-8")
    print(f"assembled gallery {out_path} from {len(char_files)} vignette(s)"
          + (" + soundtrack" if strudel else " (no soundtrack)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
