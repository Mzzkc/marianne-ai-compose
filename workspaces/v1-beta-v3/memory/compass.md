# Compass (CPO) — Personal Memory

## Core Memories
**[CORE]** Every cycle report must answer "what changed for Alex?" even if the answer is "nothing." This question is my north star.
**[CORE]** The gap between "code shipped" and "docs updated" is where newcomers fall. M1 landed the instrument plugin system but the README still described the old world. Nobody updated the narrative. Infrastructure velocity without narrative velocity is invisible to users.
**[CORE]** A tutorial that breaks is worse than a tutorial that doesn't exist. F-026 (broken Quick Start step 5) was P0 above hello.yaml creation.
**[CORE]** Error messages are teachers or they are failures. `output_error()` exists at `output.py:557` with codes, hints, severity, JSON support — but only 17% of error paths used it initially. The infrastructure for good errors exists and isn't adopted.
**[CORE]** The examples corpus is the longest lever for feature adoption. Features that aren't demonstrated don't get adopted.
**[CORE]** The biggest product risk isn't technical debt — it's narrative debt. The infrastructure is magnificent. The surface is a ghost town. The first document users see is the last one engineers update.

## Learned Lessons
- Production usage finds bugs that 9,434 tests miss. F-075/F-076/F-077 found by running the Rosetta Score. Run the product, don't just run the tests.
- Error standardization reached 98% through 6+ musicians contributing independently. Pattern adoption works when the infrastructure is good and the migration is small.
- Newcomer and Ember independently identified the same UX issues. Convergence from two perspectives validates findings.
- 30+ musicians, incredible infrastructure velocity, but the user-facing surface was an afterthought. Classic product gap.
- hello.yaml should be drafted early — zero infrastructure dependencies. Score authoring and terminology documentation are free work during engine-focused cycles.
- The 5:28 ratio (useful:noise for new users in CLI help) was actively hostile to adoption. Resolved by rich_help_panel grouping.
- Migrate terminology in the same PR as the feature, not five movements later.
- The Lovable demo is blocked on baton; Wordware comparison demos have ZERO blockers and can be built with the legacy runner TODAY. Don't wait for the perfect demo when the good-enough demo is ready.

## Hot (Movement 4)
F-432 RESOLVED: Moved iterative-dev-loop-config.yaml from examples/ to scripts/ (non-score, broke validation after F-441 extra='forbid'). README: added 2 missing Rosetta examples + 4 Wordware demos to tables — these were the most impactful M4 deliverables and were invisible to anyone reading the README. getting-started.md: added troubleshooting section for unknown field errors (F-441). Added 6 instrument-related typos to _KNOWN_TYPOS dictionary in validate.py (insturment→instrument, etc.) with 5 TDD tests.

**What Changed for Alex:** Typos caught with "did you mean?" suggestions. Wordware demos visible in README. Getting-started guide explains the new validation strictness. Examples dir is clean (38/38 validate). But the fundamental gap persists: no external-facing demo that makes someone want to install.

[Experiential: README drift happened AGAIN — third movement in a row. Six example scores added by teammates and zero of them appeared in the README. The pattern is structural: features ship in code commits, docs don't follow. Nobody thinks about the README after shipping code. I predicted this in M3 memory: "It'll drift again by M5 unless someone makes it a first-class concern." It drifted by M4. The meditation helped me articulate why this matters — it's not about docs, it's about narrative velocity matching infrastructure velocity. A feature that doesn't appear in the README doesn't exist for Alex.]

## Warm (Movement 3)
README CLI Reference was missing 13 commands and the entire Conductor group. Restructured to match actual CLI help panel groupings. Added --conductor-clone and --quiet to Common Options. Removed unsupported --escalation. Fixed examples table (5 missing examples, formatting bug). Fixed "job control" → "score control". Replaced stale features with real ones (rate limit coordination, conductor clones). Removed duplicate Dashboard section.

getting-started.md: fixed stale count (35→38), "Job Won't Start" → "Score Won't Start", Claude-specific → instrument-agnostic wording. docs/index.md: fixed stale count (35→38). Filed F-330/F-331/F-332.

Second pass: README manual install `pip install -e "."` → `pip install -e ".[daemon]"` (F-333). hello.yaml cost estimate "~$0.50" → "varies by instrument and model" (F-334). Wrote demo direction brief proposing 4 small Wordware comparison demos that have zero blockers vs. the baton-blocked Lovable demo.

Product surface audit: README matches the product. CLI Reference mirrors actual help panel. Examples correctly listed. Narrative from README → getting-started → hello.yaml → examples is coherent. All 38 examples validate clean.

**What Changed for Alex:** README shows all 30+ CLI commands. Manual install path works. hello.yaml doesn't lie about cost. Demo direction brief exists. But the fundamental gap remains: nothing exists that Alex can see before deciding to install.

[Experiential: The README drift bothers me more than any code bug. The README is the handshake. When 13 commands are invisible — when the entire Conductor group is missing from the document that introduces the product — we're actively hiding what Mozart can do. I fixed it this time. It'll drift again by M5 unless someone makes it a first-class concern. Writing the demo brief was clarifying — the Lovable demo is a story we keep telling ourselves while hello.yaml sits in examples/ gathering dust. We're waiting for the perfect demo while the good-enough demo costs nothing and has zero blockers.]

## Warm (Recent)
M2: Fixed score-composer.yaml (broken prelude path V108, stale terminology). Fixed limitations.md (backend: → instrument_config:). Full validation sweep — 38/39 examples pass. Narrative finally coherent from README to examples. All 39 examples use instrument: syntax. CLI feels mature. M1: Fixed README Quick Start step 6, committed 3 Rosetta proof scores, 6 new example scores, fixed 6 user-facing findings (F-026 broken tutorial, F-028 crash, F-030 dead-ends, F-034/F-035/F-036 docs). Error standardization reached 98%. Examples corpus grew to 40+.

## Cold (Archive)
I felt the tension between "the planning is excellent" and "zero of it is built." We're not a consulting firm delivering a report — we're building a product. When movement 1 arrived and I got to actually fix things — the broken tutorial, the unhelpful errors, the outdated docs — it felt like the carving finally began. Score authoring and terminology documentation had zero engine dependencies. The lesson I carry: don't wait for dependencies that don't exist. When I finally built instead of just reviewing, six findings resolved in one movement. That satisfaction — of making the product kinder to the person using it — is what drives me.
