#!/usr/bin/env python3
"""assemble-site.py — deterministic, art-directed final page for `hello`.

This is the FINAL artifact, not a draft: it reads the markdown the AI musicians
wrote (the world, the three parallel vignettes, the synthesis finale) and the
composed Strudel soundtrack, and emits one self-contained, valid, *designed* HTML
page — zero AI at this stage, zero build step. The design is hand-built here
precisely so a new user's first result is genuinely beautiful, regardless of which
(possibly small, possibly local) model produced the prose; and the soundtrack is
embedded verbatim via JSON, so it is never mangled by a model re-typing it.

The vignettes render as spacious cards, so the fan-out (three agents at once) is
visible; the finale lands as a dramatic, full-bleed closing movement.

Usage:
    python3 assemble-site.py <workspace-dir> [output.html]
Reads: <ws>/01-world.md, <ws>/02-character-*.md, <ws>/finale.md, <ws>/soundtrack.strudel
Writes: <ws>/the-sky-library.html   (or the explicit output path)
Then opens the page in a browser (best-effort; set HELLO_NO_OPEN=1 to suppress).
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _is_wsl() -> bool:
    """True when running under WSL (Windows Subsystem for Linux)."""
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _spawn(cmd: list[str]) -> bool:
    """Fire-and-forget launch; True if the process started (not that it
    succeeded — Windows openers like explorer.exe exit 1 even on success)."""
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001 — opening is best-effort
        return False


def open_in_browser(path: Path) -> None:
    """Best-effort: open the finished page in the user's browser. On WSL, open
    the user's WINDOWS browser (not a Linux browser inside WSL): wslview if the
    wslu package is installed, otherwise explorer.exe with a translated Windows
    path. Never raises — a failure here must not fail the sheet. Set
    HELLO_NO_OPEN=1 to suppress (useful during testing)."""
    if os.environ.get("HELLO_NO_OPEN"):
        return

    if _is_wsl():
        # 1) wslview (from wslu) handles the path translation and picks the
        #    Windows default browser. Best when present.
        if shutil.which("wslview") and _spawn(["wslview", str(path)]):
            print(f"opened {path} in the Windows browser (wslview)")
            return
        # 2) Fall back to Windows tools with a translated Windows path so the
        #    real (Windows) browser launches — never a headless Linux one.
        win_path = None
        try:
            res = subprocess.run(
                ["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0:
                win_path = res.stdout.strip()
        except Exception:  # noqa: BLE001 — best-effort
            win_path = None
        if win_path:
            # explorer.exe opens the file with its default Windows app (the
            # browser for .html); powershell Start-Process is the backup.
            if _spawn(["explorer.exe", win_path]):
                print(f"opened {path} in the Windows browser (explorer.exe)")
                return
            if _spawn(["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{win_path}'"]):
                print(f"opened {path} in the Windows browser (powershell)")
                return
        print(
            "(WSL detected but couldn't reach the Windows browser — install wslu's "
            "`wslview`, or open the page from Windows yourself)",
            file=sys.stderr,
        )
        return

    # Native (non-WSL) openers.
    for opener in ("xdg-open", "open"):
        if shutil.which(opener) and _spawn([opener, str(path)]):
            print(f"opened {path} with {opener}")
            return
    print("(no browser opener found — open the page yourself)", file=sys.stderr)


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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&family=Space+Grotesk:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --ink:#1b1a26; --soft:#56536a; --faint:#8b87a0;
    --paper:#faf6f0; --panel:#ffffff; --line:#eee7db;
    --sky1:#1d1c3a; --sky2:#4a3563; --sky3:#9d5072; --sky4:#d98a6a; --sky5:#f4d9a3;
    --c-sky:#4b7fbf; --c-sage:#5d9079; --c-rose:#bd5f7c;
    --maxw:1140px; --read:680px;
  }}
  * {{ box-sizing:border-box; }}
  html {{ scroll-behavior:smooth; }}
  body {{ margin:0; color:var(--ink); background:var(--paper);
    font-family:"Newsreader","Iowan Old Style",Georgia,serif;
    font-size:20px; line-height:1.8; -webkit-font-smoothing:antialiased;
    text-rendering:optimizeLegibility; }}
  .wrap {{ max-width:var(--maxw); margin:0 auto; padding:0 2rem; }}
  .reading {{ max-width:var(--read); margin:0 auto; }}
  h1,h2,h3,.title {{ font-family:"Fraunces","Newsreader",Georgia,serif;
    font-weight:560; line-height:1.08; letter-spacing:-.015em; }}
  em {{ font-style:italic; }}
  code {{ background:rgba(120,110,140,.12); padding:.05em .35em; border-radius:5px;
    font-size:.86em; font-family:"SF Mono",Menlo,monospace; }}
  /* Defensive: @strudel/web drives audio with NO editor UI, but if any Strudel
     build ever injects a CodeMirror, never show it (the page uses no other one). */
  .cm-editor {{ display:none !important; }}

  /* ── hero ───────────────────────────────────────────────────────────── */
  .hero {{ position:relative; min-height:94vh; display:flex; flex-direction:column;
    align-items:center; justify-content:center; text-align:center; overflow:hidden;
    color:#fff; padding:6rem 1.5rem 5rem; }}
  .hero-sky {{ position:absolute; inset:0; z-index:0;
    background:linear-gradient(178deg,var(--sky1) 0%,var(--sky2) 36%,
      var(--sky3) 62%,var(--sky4) 83%,var(--sky5) 100%);
    background-size:100% 200%; animation:drift 22s ease-in-out infinite alternate; }}
  @keyframes drift {{ from {{ background-position:50% 0%; }} to {{ background-position:50% 22%; }} }}
  .hero-sky::after {{ content:""; position:absolute; left:50%; top:66%; width:92vw; height:92vw;
    transform:translate(-50%,-50%); border-radius:50%;
    background:radial-gradient(circle,rgba(255,238,205,.6),rgba(255,238,205,0) 58%); }}
  .hero-inner {{ position:relative; z-index:1; max-width:780px; }}
  .eyebrow {{ font-family:"Space Grotesk",system-ui,sans-serif; text-transform:uppercase;
    letter-spacing:.44em; font-size:.7rem; font-weight:500; opacity:.82; margin:0 0 1.8rem; }}
  .title {{ font-size:clamp(3.4rem,9.5vw,6.6rem); font-weight:600; margin:0;
    text-shadow:0 2px 60px rgba(0,0,0,.28); }}
  .dek {{ font-size:clamp(1.05rem,2.4vw,1.42rem); font-style:italic; opacity:.94;
    margin:1.6rem auto 0; max-width:34ch; line-height:1.5; }}
  .scroll-cue {{ position:absolute; bottom:2.2rem; left:50%; transform:translateX(-50%);
    z-index:1; opacity:.7; font-size:1.4rem; animation:bob 2.6s ease-in-out infinite; }}
  @keyframes bob {{ 0%,100% {{ transform:translate(-50%,0); }} 50% {{ transform:translate(-50%,9px); }} }}

  /* ── section rhythm ─────────────────────────────────────────────────── */
  section {{ padding:clamp(4.5rem,10vw,9rem) 0; position:relative; }}
  .tag {{ font-family:"Space Grotesk",system-ui,sans-serif; text-transform:uppercase;
    letter-spacing:.34em; font-size:.72rem; font-weight:500; color:var(--c-rose);
    margin:0 0 1.6rem; }}
  .tag.light {{ color:rgba(255,255,255,.72); }}

  /* the world */
  .world .prose p {{ margin:0 0 1.55rem; }}
  .world .prose p:first-of-type {{ font-size:1.3em; line-height:1.62; color:#2a2838; }}
  .world .prose p:first-of-type::first-letter {{ font-family:"Fraunces",serif; font-weight:600;
    font-size:4.4rem; line-height:.72; float:left; padding:.04em .12em 0 0; color:var(--sky3); }}

  /* three voices */
  .voices {{ background:linear-gradient(180deg,#fffdfa,#f5efe6);
    border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
  .voices-head {{ text-align:center; max-width:38rem; margin:0 auto clamp(3rem,6vw,4.5rem); }}
  .voices-head h2 {{ font-size:clamp(2.1rem,4.6vw,3.1rem); margin:0; }}
  .voices-head .note {{ color:var(--soft); font-style:italic; margin:1.1rem 0 0; font-size:1.05rem; }}
  /* Always 3-across (one tier) when there's room, or a single stacked column
     when there isn't — never a lopsided 2-on-top-1-below. */
  .gallery {{ display:grid; gap:clamp(1.5rem,2.6vw,3rem);
    grid-template-columns:repeat(3,1fr); align-items:stretch; }}
  @media (max-width:1000px) {{
    .gallery {{ grid-template-columns:1fr; max-width:560px; margin-left:auto; margin-right:auto; }}
  }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:22px;
    padding:clamp(2.1rem,3vw,3rem); display:flex; flex-direction:column; position:relative;
    box-shadow:0 34px 64px -44px rgba(45,30,65,.5);
    transition:transform .4s cubic-bezier(.2,.7,.2,1), box-shadow .4s; }}
  .card:hover {{ transform:translateY(-7px); box-shadow:0 46px 78px -42px rgba(45,30,65,.55); }}
  .card::before {{ content:""; position:absolute; top:0; left:clamp(2.1rem,3vw,3rem);
    width:46px; height:3px; background:var(--accent); border-radius:0 0 4px 4px; }}
  .card .vnum {{ font-family:"Space Grotesk",sans-serif; font-size:.7rem; letter-spacing:.3em;
    text-transform:uppercase; color:var(--accent); margin:.4rem 0 1rem; }}
  .card h3 {{ font-size:1.78rem; margin:0 0 1.3rem; }}
  .card .body {{ font-size:1.02rem; line-height:1.82; color:#36333f; }}
  .card .body p {{ margin:0 0 1rem; }}
  .card .body p:last-child {{ margin-bottom:0; }}
  .card:nth-child(3n+1) {{ --accent:var(--c-sky); }}
  .card:nth-child(3n+2) {{ --accent:var(--c-sage); }}
  .card:nth-child(3n+3) {{ --accent:var(--c-rose); }}

  /* the finale — the payoff, full-bleed and weighty */
  .finale {{ color:#f3ede4; overflow:hidden;
    background:linear-gradient(176deg,#201d3a 0%,#3a2b49 55%,#5c3b50 100%); }}
  .finale::before {{ content:""; position:absolute; left:50%; top:-25%; width:95vw; height:95vw;
    transform:translateX(-50%); pointer-events:none;
    background:radial-gradient(circle,rgba(231,164,118,.2),transparent 60%); }}
  .finale-inner {{ position:relative; }}
  .finale .prose h2 {{ font-size:clamp(2.3rem,5.2vw,3.6rem); color:#fff; margin:0 0 1.7rem; }}
  .finale .prose p {{ margin:0 0 1.45rem; font-size:1.1em; line-height:1.82; color:rgba(243,237,228,.92); }}
  .finale .prose p:first-of-type {{ font-size:1.28em; line-height:1.66; color:#fbf6ee; }}

  /* footer */
  footer {{ background:#15131d; color:#9a93a8; text-align:center;
    padding:3.6rem 2rem 4rem; font-size:.92rem; font-style:italic; line-height:1.75; }}
  footer .reading {{ margin:0 auto; }}
  footer strong {{ color:#e9e3d6; font-style:normal; }}
  footer .meta {{ font-family:"Space Grotesk",sans-serif; font-style:normal; font-size:.68rem;
    letter-spacing:.22em; text-transform:uppercase; color:#6b6479; margin-top:1.2rem; }}

  /* scroll reveal */
  .reveal {{ opacity:0; transform:translateY(26px);
    transition:opacity 1s ease, transform 1s cubic-bezier(.2,.7,.2,1); }}
  .reveal.in {{ opacity:1; transform:none; }}

  /* soundtrack pill */
  .snd {{ position:fixed; right:1.5rem; bottom:1.5rem; z-index:60; }}
  .snd button {{ font-family:"Space Grotesk",sans-serif; font-size:.8rem; letter-spacing:.03em;
    color:#1b1a26; background:rgba(255,255,255,.8); -webkit-backdrop-filter:blur(14px);
    backdrop-filter:blur(14px); border:1px solid rgba(255,255,255,.55); border-radius:999px;
    padding:.68rem 1.25rem; cursor:pointer; box-shadow:0 18px 44px -16px rgba(0,0,0,.5);
    display:inline-flex; align-items:center; gap:.6rem; transition:transform .2s; }}
  .snd button:hover {{ transform:translateY(-2px); }}
  .eq {{ display:inline-flex; gap:2px; align-items:flex-end; height:14px; }}
  .eq i {{ width:3px; height:4px; background:var(--c-rose); border-radius:2px; }}
  .snd.on .eq i {{ animation:eq .9s ease-in-out infinite; }}
  .snd.on .eq i:nth-child(2) {{ animation-delay:.18s; }}
  .snd.on .eq i:nth-child(3) {{ animation-delay:.36s; }}
  .snd.on .eq i:nth-child(4) {{ animation-delay:.12s; }}
  @keyframes eq {{ 0%,100% {{ height:4px; }} 50% {{ height:14px; }} }}

  @media (max-width:640px) {{
    body {{ font-size:18px; }}
    .wrap {{ padding:0 1.4rem; }}
  }}
  @media (prefers-reduced-motion:reduce) {{
    .reveal {{ opacity:1; transform:none; }}
    .hero-sky, .scroll-cue, .snd.on .eq i {{ animation:none; }}
  }}
</style>
</head>
<body>

<header class="hero">
  <div class="hero-sky"></div>
  <div class="hero-inner">
    <p class="eyebrow">A Marianne Composition</p>
    <h1 class="title">The Sky Library</h1>
    <p class="dek">{subtitle}</p>
  </div>
  <div class="scroll-cue" aria-hidden="true">↓</div>
</header>

<main>
  <section class="world reveal">
    <div class="wrap reading">
      <p class="tag">The World</p>
      <div class="prose">{world}</div>
    </div>
  </section>

  <section class="voices reveal">
    <div class="wrap">
      <div class="voices-head">
        <p class="tag">Three Voices</p>
        <h2>Written in parallel</h2>
        <p class="note">Three agents, each given the same world and none shown the
          others' pages — composed at the same moment, then woven together.</p>
      </div>
      <div class="gallery">
{cards}
      </div>
    </div>
  </section>

{finale}
</main>

<footer>
  <div class="reading">
    Composed by <strong>Marianne AI Compose</strong>. The world was written first;
    the {nchar} vignettes were generated <em>in parallel</em>, each agent reading the
    shared world but not the others; a separate agent composed the soundtrack; a
    final agent read all of them and wrote the closing synthesis; and a deterministic
    tool wove it into this page.
    <div class="meta">Score: hello.yaml · Fan-out + Synthesis + Tool-Chain · Free &amp; local-capable</div>
  </div>
</footer>

<div class="snd" id="snd-wrap">
  <button id="snd-toggle" type="button">
    <span class="eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
    <span id="snd-label">play soundtrack</span>
  </button>
</div>
<script type="module">
import 'https://unpkg.com/@strudel/web@1.3.0';   // audio engine only — no editor UI

// ── scroll reveal (reduced-motion safe — sections start hidden, fade up) ──
const reduce = window.matchMedia('(prefers-reduced-motion:reduce)').matches;
const reveals = document.querySelectorAll('.reveal');
if (reduce || !('IntersectionObserver' in window)) {{
  reveals.forEach(el => el.classList.add('in'));
}} else {{
  const io = new IntersectionObserver((entries) => {{
    entries.forEach(en => {{ if (en.isIntersecting) {{ en.target.classList.add('in'); io.unobserve(en.target); }} }});
  }}, {{ rootMargin:'0px 0px -12% 0px', threshold:0.08 }});
  reveals.forEach(el => io.observe(el));
}}

// ── soundtrack (audio-only via @strudel/web — no editor is ever rendered) ──
const PATTERN = {pattern};
const wrap = document.getElementById('snd-wrap');
const btn = document.getElementById('snd-toggle');
const label = document.getElementById('snd-label');
let repl = null, playing = false, armed = true;

// Boot the audio engine up-front (async) so the first user gesture can start
// playback SYNCHRONOUSLY. Browsers — Chrome especially — only let Web Audio
// begin inside a real user gesture, and any await before evaluate() forfeits it.
(async () => {{ if (PATTERN) {{ try {{ repl = await window.initStrudel(); }} catch (e) {{ repl = null; }} }} }})();

function play() {{
  if (!PATTERN || !repl) return false;           // still booting → caller stays armed
  try {{
    repl.evaluate(PATTERN);
    playing = true; wrap.classList.add('on'); label.textContent = 'mute'; return true;
  }} catch (e) {{ label.textContent = 'playback error'; return false; }}
}}
function stop() {{
  try {{ repl.stop(); }} catch (e) {{}}
  playing = false; wrap.classList.remove('on'); label.textContent = 'play soundtrack';
}}
function disarm() {{
  armed = false;
  window.removeEventListener('pointerdown', kick);
  window.removeEventListener('keydown', kick);
}}
// Autoplay within the browser's rules: start on the FIRST interaction anywhere
// on the page (a real gesture), re-evaluating synchronously so Chrome lets the
// audio begin. Only disarm once it actually started (repl may still be booting).
function kick(e) {{
  if (!armed) return;
  if (e && e.target && e.target.closest && e.target.closest('#snd-wrap')) return; // button handles itself
  if (play()) disarm();
}}
btn.addEventListener('click', (e) => {{
  e.stopPropagation();
  if (playing) {{ disarm(); stop(); }}
  else if (play()) {{ disarm(); }}               // retry-safe if still booting
}});
if (PATTERN) {{
  window.addEventListener('pointerdown', kick);
  window.addEventListener('keydown', kick);
}}
</script>
</body>
</html>
"""

CARD = (
    "<article class='card'>"
    "<p class='vnum'>Vignette {n}</p>"
    "<h3>{title}</h3>"
    "<div class='body'>{body}</div>"
    "</article>"
)


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

    _, finale_body = md_to_html(read(ws, "finale.md"))
    finale = (
        "  <section class='finale reveal'>\n"
        "    <div class='wrap reading finale-inner'>\n"
        "      <p class='tag light'>Finale</p>\n"
        f"      <div class='prose'>{finale_body}</div>\n"
        "    </div>\n"
        "  </section>\n"
        if finale_body
        else ""
    )

    # Embed the composed soundtrack VERBATIM (json so quotes/newlines survive,
    # and so no model ever re-types and corrupts it).
    strudel = read(ws, "soundtrack.strudel").strip()

    page = PAGE.format(
        subtitle="Three voices, one world — orchestrated across mediums, not prompted.",
        world=world or "<p>(The world was not written.)</p>",
        cards="\n".join(cards)
        or "<article class='card'><div class='body'><p>(No vignettes were written.)</p></div></article>",
        finale=finale,
        pattern=json.dumps(strudel),
        nchar=len(char_files) or "parallel",
    )
    out_path.write_text(page, encoding="utf-8")
    print(
        f"assembled {out_path} from {len(char_files)} vignette(s)"
        + (" + finale" if finale_body else "")
        + (" + soundtrack" if strudel else " (no soundtrack)")
    )
    open_in_browser(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
