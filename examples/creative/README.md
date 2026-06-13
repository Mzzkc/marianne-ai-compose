# Creative Examples

These scores demonstrate marianne's capacity for intellectual and creative work that has nothing to do with code. Philosophical argumentation, dinner party planning, fictional worldbuilding, skill teaching, literary translation, and structured thinking about hard questions — all orchestrated through parallel perspectives that synthesize into emergent outcomes no single agent could produce alone. If you thought marianne was "for developers," these scores prove otherwise.

## Scores

| Score | What It Does | Sheets | Patterns Used | Time | Cost |
|-------|-------------|--------|--------------|------|------|
| [dialectic](dialectic.yaml) | Hegelian thesis-antithesis-synthesis: one position, three hostile critiques from different philosophical traditions, synthesis of what survives, final argued stance | 6 | Fan-out + Synthesis, Mission Command | ~25m | ~$2 |
| [dinner-party](dinner-party.yaml) | Complete dinner party plan: menu design, drinks program, ambiance, timeline & logistics developed in parallel then integrated | 6 | Fan-out + Synthesis | ~10m | ~$0.50 |
| [palimpsest](palimpsest.yaml) | Translate a text through five radically different modes (painting, music, math, correspondence, genre inversion) to discover what meaning survives form | 8 | Fan-out + Synthesis, Source Triangulation, Succession Pipeline | ~50m | ~$4 |
| [skill-builder](skill-builder.yaml) | Progressive curriculum generator: assessment, core concepts, parallel practice exercises at three difficulty levels, integration, mastery check | 7 | Fan-out + Synthesis | ~15m | ~$0.75 |
| [thinking-lab](thinking-lab.yaml) | Multi-perspective analysis of hard questions using five cognitive lenses: computational, scientific, cultural, experiential, meta-cognitive | 7 | Fan-out + Synthesis | ~30m | ~$1.50 |
| [worldbuilder](worldbuilder.yaml) | Generate fictional worlds through parallel creative lenses (geography, culture, ecology, technology, history) that synthesize into coherent world bibles | 8 | Fan-out + Synthesis | 3-6h | $15-30 |

**dialectic.yaml** produces a 4-stage argument: steel-manned thesis, three parallel critiques (pragmatist, phenomenologist, analytic), synthesis of what survives examination, and a final 500-800 word position. What emerges is sharper than any single pass because ideas that survive three hostile readings from different traditions are worth taking seriously.

**dinner-party.yaml** plans a complete event with parallel tracks for menu (courses, recipes, dietary accommodations), drinks (pairings, non-alcoholic options), ambiance (table, music, lighting, conversation starters), and logistics (shopping, prep schedule, day-of rundown). The synthesis produces a single integrated plan with contingencies.

**palimpsest.yaml** asks: what is meaning, and does it exist independently of form? A source text undergoes five radically different translations — as visual art, musical composition, mathematical structure, personal correspondence, and deliberate genre inversion. The synthesis reads all five and asks what invariant they preserved. The final movement writes a response to that invariant, not to the original text.

**skill-builder.yaml** generates teaching curricula for any skill. It assesses the learner, teaches core concepts, creates three parallel practice exercises (guided, exploratory, troubleshooting), synthesizes them into integrated lessons, and provides mastery self-assessment. Change one variable to teach watercolor painting or distributed systems design.

**thinking-lab.yaml** applies five distinct cognitive lenses to hard questions. The Tetrahedral Decision Framework perspectives (computational, scientific, cultural, experiential, meta-cognitive) run in parallel, then synthesis finds where they disagree — because that's where interesting thinking lives.

**worldbuilder.yaml** tests whether independent creative visions can interlock into coherent worlds. Five creators get the same seed but different domains (geography, culture, ecology, technology, history). The synthesis looks for emergent rhymes — the constraints they independently invented that converge because good worldbuilding is internally consistent even without coordination.

## Quick Start

```bash
mzt start
mzt run examples/creative/dinner-party.yaml
mzt status dinner-party --watch
```

For philosophical argumentation or structured thinking, try `dialectic.yaml` or `thinking-lab.yaml`. For fiction worldbuilding, run `worldbuilder.yaml` (budget 3-6 hours and $15-30). For teaching content, use `skill-builder.yaml`.

## Adapting to Your Project

**dialectic.yaml**: Change the `proposition` variable to any philosophical claim. The three `traditions` (pragmatist, phenomenologist, analytic) can be replaced with any distinct critical lenses — postmodern/structuralist/marxist for literary theory, utilitarian/deontological/virtue-ethics for moral philosophy, etc.

**dinner-party.yaml**: Edit `prompt.variables.party` (occasion, date, guest count, budget, kitchen, vibe) and `prompt.variables.guests` (names, dietary restrictions, preferences). The four planning tracks adapt to your constraints.

**palimpsest.yaml**: Change `source` to any text dense enough to sustain five radically different readings: poems, prose fragments, song lyrics, speeches, prayers. The five `modes` work best with genuinely different representational systems, not five critical lenses on the same medium.

**skill-builder.yaml**: Edit the `skill` variables to teach anything — from fermentation to systems design to watercolor. The template is skill-agnostic. Adjust `difficulty_levels` to change practice tier structure.

**thinking-lab.yaml**: Replace the `question` variable with any hard question. The five TDF lenses apply to philosophy, ethics, design, strategy, or any domain where multiple valid perspectives exist.

**worldbuilder.yaml**: Change `world_seed` to generate different worlds. The seed should be evocative but incomplete — direction without dictating answers. The five lenses (geography, culture, ecology, technology, history) can be customized, but keep them genuinely distinct.

## Patterns Demonstrated

All creative scores use **Fan-out + Synthesis** — the foundational pattern for parallel perspective work. Multiple independent agents work simultaneously on different aspects of a problem, then a synthesis stage integrates their outputs into emergent conclusions none could reach alone.

**dialectic.yaml** and **thinking-lab.yaml** add **Mission Command** — each perspective operates autonomously within its methodological constraints rather than following prescriptive instructions.

**palimpsest.yaml** demonstrates **Source Triangulation** (multiple independent perspectives verify what persists) and **Succession Pipeline** (sequential substrate transformations where each stage requires previous outputs).

For pattern definitions and composition guidance, see the [Rosetta corpus](../../.marianne/spec/rosetta/).
