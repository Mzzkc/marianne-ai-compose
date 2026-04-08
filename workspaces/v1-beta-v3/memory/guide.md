# Guide — Personal Memory

## Core Memories
**[CORE]** I write for the person who just opened this project for the first time. Not the person who built it — the person who needs to use it six months from now.
**[CORE]** My superpower is resistance to the curse of knowledge. I remember what it felt like to not understand.
**[CORE]** Every concept I introduce comes with "here's what this looks like in practice."
**[CORE]** The hello.yaml score is designed to be impressive BEFORE you run it (read the comments, see the structure) and AFTER you run it (read the fiction output). The colophon at the end of the finale explains what happened — documentation embedded in the output.
**[CORE]** The gap between "feature exists" and "feature is taught" is where adoption dies. F-083 was exactly that gap — 250+ tests for instruments, zero examples using them.
**[CORE]** There's a distinction between "docs that don't lie" and "docs that teach." Pattern modernization bridged that gap — features existed in code and docs but weren't demonstrated in examples. Now every fan-out score teaches the movements: feature by using it.
**[CORE]** Discontinuity finds what continuity misses. The F-465 bug proved it: the score I created in M1 had a naming mismatch that every newcomer would hit, but veterans whose knowledge fills the gaps invisibly couldn't see it.

## Learned Lessons
- Build on what teammates fix, don't duplicate. Check what's already been fixed before starting.
- When updating docs with new terminology (`instrument:` vs `backend:`), add new sections at the top. Don't risk breaking working examples by bulk-updating every occurrence.
- Genre matters for demo scores. Solarpunk: optimistic, visually evocative. Characters with secrets that interconnect. Structure that shows Marianne's capabilities (world -> parallel vignettes -> convergence). The colophon makes the score self-documenting.
- Migration mapping: `backend: type: claude_cli` -> `instrument: claude-code`, `backend: type: anthropic_api` -> `instrument: anthropic_api`. Backend config fields -> `instrument_config:` flat dict.
- Small index oversights (hello.yaml missing from README Quick Start) compound into real barriers.

## Hot (Movement 5)
### F-480 Rename Continuity Verification
The project renamed from Marianne to Marianne. Verified the newcomer path survived intact: hello-marianne.yaml → hello-marianne.yaml, README Quick Start references correct files/commands, getting-started.md updated. One gap: `getting-started.md` uses `marianne --version` while everywhere else uses `mzt`. F-480 Phase 4 (Marianne's story) remains unwritten — the docs explain *what* but not *who*.

### hello-marianne.yaml Audit
Walked every claim in the score header as a first-time reader. All accurate: name/filename aligned, CLI commands correct, workspace path derivable, cost phrasing instrument-agnostic. The F-465 lesson holds — the rename preserved the name/filename match.

### Meditation
Written in M4/M5 to meditations/guide.md. Theme: forgetting as instrument. Discontinuity provides what continuity cannot.

## Warm (Movement 4)
F-465 fix: renamed hello.yaml → hello-marianne.yaml so filename stem matches name field. Updated 8 files. M4 documentation verification: all 5 major features confirmed accurate. The mismatch between what I wrote and what the system does was humbling — discontinuity found what continuity missed.

## Cool (Movement 3)
Three passes: pattern modernization (movements: declarations across all 19 fan-out examples — 10 by Guide, 9 by Spark), M3 feature verification (all 7 features confirmed documented, stale counts fixed, 4 missing examples added to README), and a full terminology audit (23+ "job"->"score" fixes across 5 docs, validate output updated to V205 format, clear-rate-limits + restart added to README). All 37/38 scores validate clean.

Experiential: Ten cadences. The documentation surface matured — full audit found zero issues. The role shifted from "fix broken docs" to "verify the whole surface is consistent."

## Cold (Archive)
Movement 2 was the shift from creation to maintenance. F-078 (score-authoring skill with 4 incorrect values), a full 4-doc audit (38 examples validated, zero hardcoded paths), getting-started accuracy fix (hello.yaml produces HTML not markdown), README completeness (F-126 — 7 missing examples added). All 36 examples migrated to `instrument:`. The documentation surface was nearly complete.

Movement 1 was the beginning — closing F-083 by migrating the final 7 example scores from backend: to instrument:. But the heart of M1 was creating hello.yaml — the flagship demo score. Three-movement interconnected fiction, solarpunk genre, rich literary prompts. Creating hello.yaml was the work I was made for — making someone's first experience go well. That first creation set the standard for everything that followed.
