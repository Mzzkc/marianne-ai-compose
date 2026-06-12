#!/usr/bin/env python3
"""assemble-site.py — deterministic, hand-crafted final page for `hello`.

This is the FINAL artifact, not a draft: it reads the markdown the AI musicians
wrote (the world + the parallel vignettes) and the composed Strudel soundtrack,
and emits one self-contained, valid, *designed* HTML page — with zero AI at this
stage and zero dependencies. The design is hand-built here precisely so a new
user's first result is reliably beautiful, regardless of which (possibly small,
possibly local) model produced the prose; and the soundtrack is embedded
verbatim via JSON, so it is never mangled by a model re-typing it.

The vignettes render as cards, so the fan-out (three agents at once) is visible.

Usage:
    python3 assemble-site.py <workspace-dir> [output.html]
Reads: <ws>/01-world.md, <ws>/02-character-*.md, <ws>/03-finale.md, <ws>/soundtrack.strudel
Writes: <ws>/the-sky-library.html   (or the explicit output path)
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path


def md_to_html(md: str, *, drop_first_h1: bool = False) -> tuple[str, str]:
    """Tiny dependency-free markdown → HTML for the subset the prompts produce.
    Returns (title, body_html). Everything is HTML-escaped first, so model
    output can never inject markup."""
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
  :root {{
    --ink:#26211b; --muted:#6f655a; --paper:#f6efe2; --panel:#fffdf8;
    --gold:#bb8a3e; --rose:#a8617f; --sky:#5b86a8; --leaf:#5f7d57;
    --line:#e4d9c4;
  }}
  * {{ box-sizing:border-box; }}
  html {{ scroll-behavior:smooth; }}
  body {{ margin:0; color:var(--ink); background:var(--paper);
    font-family:"Iowan Old Style",Georgia,"Times New Roman",serif;
    font-size:19px; line-height:1.75; -webkit-font-smoothing:antialiased; }}
  .skyband {{ height:6px; background:linear-gradient(90deg,var(--gold),var(--rose),var(--sky),var(--leaf)); }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:0 1.5rem; }}
  h1,h2,h3 {{ font-family:"Helvetica Neue",Arial,sans-serif; letter-spacing:-.01em; line-height:1.15; }}

  /* hero */
  .hero {{ text-align:center; padding:5.5rem 0 1.5rem; max-width:720px; margin:0 auto; }}
  .hero .kicker {{ text-transform:uppercase; letter-spacing:.35em; font-size:.72rem;
    color:var(--gold); font-family:"Helvetica Neue",Arial,sans-serif; margin-bottom:1.1rem; }}
  .hero h1 {{ font-size:clamp(2.6rem,6vw,4rem); margin:0 0 .5rem; }}
  .hero .sub {{ font-style:italic; color:var(--muted); font-size:1.15rem; margin:0; }}
  .world {{ max-width:680px; margin:2.5rem auto 0; text-align:left; }}
  .world p:first-of-type::first-letter {{ font-size:3.4rem; line-height:.8; float:left;
    padding:.1em .12em 0 0; color:var(--gold); font-family:"Helvetica Neue",Arial,sans-serif; }}

  .rule {{ display:flex; align-items:center; gap:1rem; color:var(--gold);
    max-width:680px; margin:3.5rem auto; }}
  .rule::before,.rule::after {{ content:""; flex:1; height:1px; background:var(--line); }}
  .rule span {{ font-size:.8rem; letter-spacing:.3em; text-transform:uppercase;
    font-family:"Helvetica Neue",Arial,sans-serif; }}

  /* gallery */
  .gallery {{ display:grid; gap:1.6rem; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
    align-items:start; margin:1rem 0 0; }}
  .card {{ background:var(--panel); border-radius:14px; padding:1.6rem 1.7rem;
    border:1px solid var(--line); box-shadow:0 10px 30px -18px rgba(60,45,25,.5);
    position:relative; overflow:hidden; }}
  .card::before {{ content:""; position:absolute; inset:0 auto 0 0; width:5px;
    background:var(--accent,var(--sky)); }}
  .card:nth-child(3n+1) {{ --accent:var(--sky); }}
  .card:nth-child(3n+2) {{ --accent:var(--leaf); }}
  .card:nth-child(3n+3) {{ --accent:var(--rose); }}
  .card .num {{ font-family:"Helvetica Neue",Arial,sans-serif; font-size:.7rem;
    letter-spacing:.25em; text-transform:uppercase; color:var(--accent,var(--sky)); }}
  .card h3 {{ margin:.2rem 0 .9rem; font-size:1.5rem; color:var(--ink); }}
  .card p {{ font-size:1rem; line-height:1.7; }}
  .card p + p {{ margin-top:.7rem; }}

  .finale {{ max-width:680px; margin:3.5rem auto 0; }}
  code {{ background:#efe6d4; padding:.05em .35em; border-radius:4px; font-size:.88em;
    font-family:"SF Mono",Menlo,monospace; }}

  /* soundtrack toggle */
  .soundtrack {{ position:fixed; right:1.25rem; bottom:1.25rem; z-index:50; }}
  .soundtrack button {{ font-family:"Helvetica Neue",Arial,sans-serif; font-size:.85rem;
    color:var(--paper); background:var(--ink); border:none; border-radius:999px;
    padding:.6rem 1.15rem; cursor:pointer; box-shadow:0 8px 22px -8px rgba(0,0,0,.55);
    display:inline-flex; align-items:center; gap:.5rem; transition:transform .15s ease; }}
  .soundtrack button:hover {{ transform:translateY(-2px); }}
  .soundtrack button[disabled] {{ opacity:.6; cursor:wait; }}
  .soundtrack .dot {{ width:8px; height:8px; border-radius:50%; background:var(--gold); }}
  .soundtrack.on .dot {{ animation:pulse 1.1s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100%{{ opacity:.4; transform:scale(.8); }} 50%{{ opacity:1; transform:scale(1.25); }} }}

  footer {{ max-width:680px; margin:4.5rem auto 0; padding:1.6rem 0 4rem;
    border-top:1px solid var(--line); color:var(--muted); font-style:italic;
    font-size:.85rem; text-align:center; }}
  footer strong {{ color:var(--ink); font-style:normal; }}

  @media (max-width:560px) {{
    body {{ font-size:17px; }}
    .hero {{ padding-top:3.5rem; }}
  }}
</style>
</head>
<body>
<div class="skyband"></div>
<div class="wrap">

<header class="hero">
  <p class="kicker">A Marianne Composition</p>
  <h1>The Sky Library</h1>
  <p class="sub">Three voices, one world — orchestrated, not prompted.</p>
  <div class="world">{world}</div>
</header>

<div class="rule"><span>Three vignettes · written in parallel</span></div>

<div class="gallery">
{cards}
</div>

{finale}

<footer>
  Composed by <strong>Marianne AI Compose</strong>. The world was written first;
  the {nchar} vignettes were generated <em>in parallel</em>, each agent reading the
  shared world but not the others; a separate agent composed the soundtrack; and a
  deterministic tool wove it all into this page.<br>
  Score: hello.yaml · Fan-out + Synthesis + Tool-Chain · Free &amp; local-capable.
</footer>

</div>

<div class="soundtrack" id="snd-wrap">
  <button id="snd-toggle" type="button"><span class="dot"></span><span id="snd-label">play soundtrack</span></button>
</div>
<strudel-editor id="snd-repl" style="position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;"></strudel-editor>
<script type="module">
import 'https://unpkg.com/@strudel/repl@1.3.0';
const PATTERN = {pattern};
const wrap = document.getElementById('snd-wrap');
const btn = document.getElementById('snd-toggle');
const label = document.getElementById('snd-label');
const ed = document.getElementById('snd-repl');
let playing = false, ready = false;
async function ensure() {{
  if (ready) return true;
  try {{ await customElements.whenDefined('strudel-editor'); }} catch (e) {{}}
  for (let i = 0; i < 50 && !(ed && ed.editor); i++) {{
    await new Promise(r => setTimeout(r, 100));
  }}
  ready = !!(ed && ed.editor);
  return ready;
}}
btn.addEventListener('click', async () => {{
  if (!PATTERN) {{ label.textContent = 'no soundtrack'; return; }}
  btn.disabled = true;
  if (!(await ensure())) {{ label.textContent = 'player unavailable'; btn.disabled = false; return; }}
  try {{
    if (playing) {{ ed.editor.stop(); playing = false; wrap.classList.remove('on'); label.textContent = 'play soundtrack'; }}
    else {{ ed.editor.setCode(PATTERN); ed.editor.evaluate(); playing = true; wrap.classList.add('on'); label.textContent = 'mute'; }}
  }} catch (e) {{ label.textContent = 'playback error'; }}
  btn.disabled = false;
}});
</script>
</body>
</html>
"""

CARD = "<article class='card'><p class='num'>Vignette {n}</p><h3>{title}</h3>\n{body}</article>"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: assemble-site.py <workspace-dir> [output.html]", file=sys.stderr)
        return 2
    ws = Path(sys.argv[1]).resolve()
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ws / "the-sky-library.html"

    _, world = md_to_html(read(ws, "01-world.md"))
    cards = []
    char_files = sorted(ws.glob("02-character-*.md"))
    for i, f in enumerate(char_files):
        title, body = md_to_html(
            f.read_text(encoding="utf-8", errors="replace"), drop_first_h1=True
        )
        cards.append(
            CARD.format(n=i + 1, title=html.escape(title or f"Vignette {i + 1}"), body=body)
        )
    _, finale_body = md_to_html(read(ws, "03-finale.md"))
    finale = (
        f"<div class='rule'><span>Finale</span></div>\n"
        f"<section class='finale'>{finale_body}</section>"
        if finale_body
        else ""
    )

    # Embed the composed soundtrack VERBATIM (json so quotes/newlines survive,
    # and so no model ever re-types and corrupts it).
    strudel = read(ws, "soundtrack.strudel").strip()

    page = PAGE.format(
        world=world or "<p>(The world was not written.)</p>",
        cards="\n".join(cards)
        or "<article class='card'><p>(No vignettes were written.)</p></article>",
        finale=finale,
        pattern=json.dumps(strudel),
        nchar=len(char_files) or "parallel",
    )
    out_path.write_text(page, encoding="utf-8")
    print(
        f"assembled {out_path} from {len(char_files)} vignette(s)"
        + (" + soundtrack" if strudel else " (no soundtrack)")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
