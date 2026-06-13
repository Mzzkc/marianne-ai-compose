---
name: soundtrack-recipes
description: Five complete, ready-to-play ambient Strudel soundtracks, tagged by mood. Pick the one that fits the world and use it as-is (or lightly transpose). These already sound good — do not compose from scratch.
---

# Soundtrack Recipes — pick one, don't reinvent

You are scoring a short story. Good ambient music is hard to invent from
primitives, so **don't**. Below are five complete, tested soundtracks, each
tuned to a mood. Your job:

1. Read the world and feel its dominant mood.
2. Pick the ONE recipe whose mood fits best.
3. Copy it **exactly**. You MAY change only the note letters inside `note("…")`
   to shift the key/feeling — keep every effect, number, and the structure.
4. Keep it quiet: do not raise any `.gain()` value.

These are pure synth pads and drones (no drums) — slow, soft, and atmospheric on
purpose. Output ONLY the code, starting at `setcps(`. No prose, no code fences.

---

## Recipe A — Serene · weightless calm
*A still, floating, peaceful world. The safe default.*
```
setcps(0.45)
stack(
  note("<c3 g3 f3 a3>").sound("triangle").attack(.8).release(2.5).gain(.16).lpf(900).room(.85).slow(2),
  note("<c5 ~ e5 ~>").sound("sine").gain(.07).delay(.5).delaytime(.375).delayfeedback(.45).room(.7).slow(2),
  note("c2").sound("sine").gain(.13).lpf(420).room(.5)
)
```

## Recipe B — Mysterious · something below
*Shadowed, uncertain, a held breath. Minor and low.*
```
setcps(0.4)
stack(
  note("<a2 f2 c3 e3>").sound("sawtooth").attack(.7).release(2.2).gain(.1).lpf(600).lpq(3).room(.8).slow(2),
  note("<a4 c5 e5>").sound("sine").gain(.06).slow(4).delay(.5).delayfeedback(.5).room(.85),
  note("a1").sound("sine").gain(.14).lpf(300).room(.5)
)
```

## Recipe C — Hopeful · dawn rising
*Light breaking, lift, gentle optimism. Major and open.*
```
setcps(0.5)
stack(
  note("<c3 e3 g3 c4>").sound("triangle").attack(.5).release(2).gain(.15).lpf(1100).room(.75),
  note("<g4 c5 e5 g5>").sound("sine").gain(.08).slow(2).delay(.4).delayfeedback(.4).room(.7),
  note("<c2 ~ g2 ~>").sound("sine").gain(.12).lpf(450).room(.4)
)
```

## Recipe D — Wistful · memory and distance
*Tender, a little sad, looking back. Sparse and warm.*
```
setcps(0.42)
stack(
  note("<a2 e3 f3 c3>").sound("sine").attack(.7).release(3).gain(.17).lpf(700).room(.9).slow(2),
  note("<a4 ~ c5 ~ e5 ~>").sound("triangle").gain(.07).delay(.5).delayfeedback(.5).room(.8).slow(2)
)
```

## Recipe E — Wondrous · shimmering gardens
*Curious, alive, glints of light. A slow high sparkle.*
```
setcps(0.48)
stack(
  note("<c3 f3 g3 e3>").sound("triangle").attack(.6).release(2).gain(.15).lpf(1000).room(.8),
  note("c5 e5 g5 b5").sound("sine").gain(.06).delay(.45).delaytime(.25).delayfeedback(.5).room(.85).slow(4),
  note("<c2 ~ ~ g2 ~ ~>").sound("sine").gain(.11).lpf(420).room(.5).slow(2)
)
```

---

**Rules that keep it playing (don't break these):**
- Only these sounds: `sine`, `triangle`, `sawtooth`. Never invent sound names.
- Every `note(...)` has a `.sound(...)` — without it, silence.
- Keep `.gain` low (it's already set right). No drums, no samples.
- One `setcps(...)` line, then one `stack(...)`. Nothing else.
