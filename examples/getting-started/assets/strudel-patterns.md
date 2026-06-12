---
name: strudel-patterns
description: Strudel syntax reference for live DJ performance. Dense code — not prose. Mini notation, effects, modulation, transforms, samples.
---

# Strudel Quick Reference

```js
// ═══ TEMPO ═══
setcps(bpm / 240)  // 120=0.5  128=0.533  130=0.542  140=0.583

// ═══ MINI NOTATION ═══
"bd sd hh cp"       // space = equal time  |  "~" = rest  |  "-" = rest
"[hh hh]"           // group into one slot  |  "bd*4" = repeat 4x
"bd@3 sd"           // weight (3/4 + 1/4)  |  "bd:2" = sample var 2
"<sd cp>"           // alternate cycles  |  "bd(3,8)" = Euclidean
"[c3,e3,g3]"        // comma = chord  |  "bd!3 sd" = replicate (no speedup)
"hh?0.2"            // 20% random drop  |  "bd|sd|cp" = random choice
"c _ _ e"           // tie/elongate  |  "{f a c e}%16" = polymetric

// ═══ CORE ═══
s("bd sd hh cp")                      // trigger samples
note("c3 eb3 g3").sound("sawtooth")   // synth — .sound() REQUIRED or silent
stack(p1, p2, p3)                     // layer  |  cat(p1,p2) = sequential
fastcat(p1, p2, p3)                   // squeeze into 1 cycle
arrange(4, intro, 8, drop, 2, break)  // timed structure  |  silence = stop

// ═══ EFFECTS ═══
.gain(0-1)  .lpf(Hz)  .hpf(Hz)  .bpf(Hz)  .lpq(0-50)  .hpq(0-50)
.djf(0-1)                            // 0=LP, 0.5=bypass, 1=HP — DJ filter knob
.room(0-1)  .size(0-1)               // reverb send + size
.delay(0-1)  .delaytime(cycles)  .delayfeedback(0-0.8)
.pan(0-1)  .speed(N)  .crush(4-16)  .vowel("a e i o")
.shape(0-1)  .distort(0-10)  .coarse(1-32)  // waveshape, distort, downsample
.phaser(spd)  .chorus(amt)  .leslie(amt)     // modulation FX
.compressor("-20:20:10:.002:.02")             // thresh:ratio:knee:att:rel
.orbit(N)                                     // effects bus

// ═══ ENVELOPES & SYNTHESIS ═══
.adsr(".1:.1:.5:.2")  .ad(".05:.3")  .ar(".01:.5")  // envelope shorthands
note("c3").s("sine").fm(2).fmh(3).fmdecay(.3)       // FM synthesis
.lpf(300).lpenv(4).lpa(.01).lpd(.3)                  // filter envelope per note
.lpf(200).lpdepth(2000).lprate(4)                     // filter LFO
.penv(12).pdecay(.1)                                  // pitch drop (808 kick)
.unison(5).detune(0.1).spread(0.8)                    // thick supersaw
.duckorbit(2).duckattack(0.2).duckdepth(1)            // sidechain pump
// Synths: sine sawtooth square triangle pulse supersaw pink brown crackle

// ═══ MIX: Kick 0.9  Snare 0.7  Hats 0.4  Bass 0.6  Pads 0.3  Leads 0.5

// ═══ MODULATION — replaces any number ═══
// Shapes: sine cosine saw isaw tri square perlin rand (+ bipolar: sine2 etc)
.lpf(sine.range(400, 4000).slow(16))   // 16-cycle filter sweep
.gain(saw.range(0.2, 0.8).fast(2))     // 2 ramps per cycle
.speed(perlin.range(0.8, 1.2))         // subtle random drift
// .rangex(lo,hi) = exp curve (for freq)  |  .segment(N) = step-quantize

// ═══ TRANSFORMS ═══
.fast(N) .slow(N) .hurry(N)  .rev()  .palindrome()
.jux(x=>x.rev())  .juxBy(0.5, x=>x.fast(2))     // stereo width
.every(N, x=>x.fast(2))  .sometimes(fn)  .often(fn)  .rarely(fn)
.off(1/8, x=>x.note().add(7))                    // delayed transformed layer
.superimpose(x=>x.fast(2).gain(.5))               // instant layer
.iter(4)  .chunk(4, fast(2))                      // rotating patterns
.echo(3, 1/8, 0.5)  .brak()  .swingBy(1/3, 2)   // stutter, break, swing
.linger("<1 .5 .25 .125>")  .bite(8, "0 1 2 3")  // truncate, reslice
.struct("x ~ x x ~ x ~ x")  .mask("<0 0 1 1>")  // impose/gate rhythm
.degradeBy(0.5)  .within(0, 0.5, fast(2))        // random thin, partial FX
.arp("up")  .arp("updown")  .arp("random")       // arpeggiate chords

// ═══ TONAL ═══
n("0 2 4 6").scale("C:minor")  .transpose(7)  .scaleTranspose("<0 -1>")
chord("<Am7 Dm7 G7 Cmaj7>").voicing()  .rootNotes(2)  // chords + bass

// ═══ SAMPLES ═══
// Load: samples('github:tidalcycles/dirt-samples')
// Slice: .begin(0-1) .end(0-1)  .chop(16)  .loopAt(4)  .fit()
// Reslice: .slice(8, "0 1 <2 3> 4 5 6 7")  .splice(8, pat)
// Scrub: .scrub("{0.1!2 .25@3 0.7!2}%8")
// Banks: .bank("RolandTR808") — also TR909 TR707 AkaiLinn. Never invent names.

// ═══ HYDRA (if using visuals) ═══
// await initHydra()  |  Hydra lines end with ;  |  stack() MUST be LAST

// ═══ FATAL MISTAKES ═══
// note() without .sound()       → silence
// Hydra last without semicolon  → audio silent
// .delayfeedback >= 0.9         → runaway feedback
// .crush(1) = extreme           → use 4-8 for tasteful lo-fi
// .lpq > 30                     → self-oscillation / ear damage
// .fm() without .s("sine")      → no effect on samples
// {{ }} in Marianne YAML        → escape with {% raw %}{% endraw %}
```
