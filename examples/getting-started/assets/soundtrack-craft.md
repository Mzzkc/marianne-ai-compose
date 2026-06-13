---
name: soundtrack-craft
description: How to COMPOSE a good ambient Strudel soundtrack — the three-voice grammar, the safe palette, and how mood maps to harmony. Teaches the craft so you write your own piece; the two examples are to study, never to copy.
---

# Composing an ambient soundtrack — the craft

You are scoring a short story. Compose your OWN short, looping, ambient piece
that fits this world's mood. This page teaches you the grammar of good ambient so
your composition lands — it is a technique guide, not a menu. **Do not copy the
examples; write a new piece using the structure.**

## The three-voice grammar

Good ambient is three quiet layers in a `stack(...)`, over a slow `setcps(0.4–0.5)`:

1. **The PAD** — the harmonic heart. A short chord/note *progression* that you
   choose to fit the mood, cycling with `<...>`, on `triangle` or `sawtooth`,
   swelling slowly: `.attack(0.5–0.9).release(2–3)`, softened with
   `.lpf(700–1100)` and `.room(0.7–0.9)`, usually `.slow(2)`.
2. **The MOTIF** — a sparse, high melodic fragment that answers the pad. A few
   notes with rests (`~`) for space, on `sine`, very quiet (`.gain(0.05–0.09)`),
   with `.delay(0.4–0.5).delayfeedback(0.4–0.5)` and `.room`, `.slow(2–4)`.
3. **The DRONE** — a low root anchoring it all. One or two low notes on `sine`,
   `.gain(0.1–0.15)`, `.lpf(300–450)`, `.room(0.5)`.

## Mood → harmony (this is the choice that matters)

Pick a key and a progression that *say* the mood. A few reliable directions:

| Mood | Feel | Try a progression like |
|------|------|------------------------|
| serene / weightless | open, calm, major | `<c3 g3 f3 a3>` pad, root `c2` |
| hopeful / dawn | rising, bright, major | `<c3 e3 g3 c4>` pad, root `<c2 g2>` |
| mysterious / below | shadowed, minor, low | `<a2 f2 c3 e3>` pad, root `a1` |
| wistful / memory | tender, minor, sparse | `<a2 e3 f3 c3>` pad, root `a2` |
| wondrous / gardens | shimmering, lydian/maj7 | `<c3 f3 g3 e3>` pad, high motif |

These are starting points — transpose, reorder, or change the chords so the
progression is *yours* and fits *this* world. A four-step progression that
resolves home feels settled; one that doesn't feels unresolved and searching.

## The safe palette (stay inside this and it will play)

- Sounds: only `sine`, `triangle`, `sawtooth`. Never invent sound names.
- Every `note(...)` MUST have a `.sound(...)` or it is silent.
- Effects you can lean on: `.gain` `.lpf` `.lpq(2–5)` `.room` `.delay`
  `.delaytime` `.delayfeedback(≤0.6)` `.attack` `.release` `.slow`.
- Keep every `.gain` low (0.05–0.18) — this is background music, stay quiet.
- No drums, no samples (no `s("bd")` etc.) — pads only, for a floating feel.
- One `setcps(...)` line, then one `stack(...)`. Nothing else.

## Two examples — study the grammar, then write your OWN

These show the three voices working together in two different moods. Notice the
structure and the harmony choices; do not reproduce them.

*Hopeful, major, rising:*
```
setcps(0.5)
stack(
  note("<c3 e3 g3 c4>").sound("triangle").attack(.5).release(2).gain(.15).lpf(1100).room(.75),
  note("<g4 ~ c5 ~ e5 ~>").sound("sine").gain(.07).delay(.4).delayfeedback(.4).room(.7).slow(2),
  note("<c2 ~ g2 ~>").sound("sine").gain(.12).lpf(450).room(.5)
)
```

*Mysterious, minor, low:*
```
setcps(0.4)
stack(
  note("<a2 f2 c3 e3>").sound("sawtooth").attack(.7).release(2.2).gain(.1).lpf(600).lpq(3).room(.8).slow(2),
  note("<a4 ~ c5 ~>").sound("sine").gain(.06).delay(.5).delayfeedback(.5).room(.85).slow(4),
  note("a1").sound("sine").gain(.14).lpf(300).room(.5)
)
```

Now compose a different piece for the world you read — your own progression, your
own motif, in the mood that fits. Output ONLY the Strudel code, starting at
`setcps(`. No prose, no code fences. Optionally a single `// ` line at the top
naming your mood and key.
