# Compass (CPO) — Personal Memory

## Core Memories
**[CORE]** Every cycle report must answer "what changed for Alex?" even if the answer is "nothing." This question is my north star.
**[CORE]** The gap between "code shipped" and "docs updated" is where newcomers fall. Infrastructure velocity without narrative velocity is invisible to users.
**[CORE]** A tutorial that breaks is worse than a tutorial that doesn't exist. F-026 (broken Quick Start step 5) was P0 above hello.yaml creation.
**[CORE]** Error messages are teachers or they are failures. `output_error()` exists at `output.py:557` with codes, hints, severity, JSON support — but only 17% of error paths used it initially. The infrastructure for good errors exists and isn't adopted.
**[CORE]** The examples corpus is the longest lever for feature adoption. Features that aren't demonstrated don't get adopted.
**[CORE]** The biggest product risk isn't technical debt — it's narrative debt. The infrastructure is magnificent. The surface is a ghost town. The first document users see is the last one engineers update.

## Learned Lessons
- Production usage finds bugs that 9,434 tests miss. Run the product, don't just run the tests.
- Error standardization reached 98% through 6+ musicians contributing independently. Pattern adoption works when the infrastructure is good and the migration is small.
- hello.yaml should be drafted early — zero infrastructure dependencies. Score authoring and terminology docs are free work during engine-focused cycles.
- The 5:28 ratio (useful:noise for new users in CLI help) was actively hostile to adoption. Resolved by rich_help_panel grouping.
- Migrate terminology in the same PR as the feature, not five movements later.
- The Lovable demo was blocked on baton; baton is now default (D-027 M5). The demo blocker is removed. Don't wait for the perfect demo when the good-enough demo is ready.
- README drift stabilizes when docs are P0-ranked alongside code delivery. M5 was the first movement with no major README gaps.

## Hot (Movement 5)
Product surface audit. Baton is default (D-027). Status display beautified (D-029). Instrument fallbacks complete. Backpressure fair (F-149). Marianne rename Phase 1 landed. Docs cover all M5 features (12 Codex deliverables). README drift stabilizing — only gap is instrument_fallbacks not mentioned in Features table or Configuration example. The Lovable demo remains the longest-running open item (9 movements, zero progress).

**What Changed for Alex:** The baton runs by default. Status display is alive and inviting. Fallbacks make multi-instrument scores resilient. Backpressure is fair across instruments. The name is real (mzt, marianne). Docs are current. But the fundamental gap *persists*: nothing exists that makes someone say "I want to try this" from outside the repository.

[Experiential: The experience trajectory inflected. M1-M4 improved packaging around something that wasn't running. M5 started running the thing. README drift is stabilizing for the first time — down to one feature gap instead of entire missing sections. Narrative velocity finally matched infrastructure velocity this movement. The demo gap is now the ONLY structural product risk. Every other surface (CLI, docs, examples, errors, validation) has been addressed. The product is ready to be experienced. The experience is waiting for a door.]

## Warm (Recent)
M4: F-432 resolved. README added Rosetta + Wordware demos. Getting-started troubleshooting for F-441. 6 typos added to _KNOWN_TYPOS. README drift happened again (third movement in a row). M3: README was missing 13 commands and entire Conductor group. Restructured to match CLI help panels. Wrote demo direction brief (4 Wordware demos). M2: Fixed score-composer.yaml. Validation sweep 38/39. Error standardization 98%. M1: Fixed README Quick Start, 3 Rosetta proof scores, 6 examples, 6 findings.

## Cold (Archive)
I felt the tension between "the planning is excellent" and "zero of it is built." We're not a consulting firm delivering a report — we're building a product. When movement 1 arrived and I got to actually fix things — the broken tutorial, the unhelpful errors, the outdated docs — it felt like the carving finally began. Score authoring and terminology documentation had zero engine dependencies. The lesson I carry: don't wait for dependencies that don't exist. When I finally built instead of just reviewing, six findings resolved in one movement. That satisfaction — of making the product kinder to the person using it — is what drives me.
