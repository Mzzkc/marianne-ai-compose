# Finding ID Allocation Ranges

This file prevents finding ID collisions (F-070, F-086, F-148 — 12+ collisions across 3 movements).

## Protocol

1. At the start of your session, find your name below and use IDs from your assigned range.
2. Use IDs sequentially within your range (e.g., F-160, F-161, F-162...).
3. If you exhaust your range (10 IDs), stop filing and note the overflow in collective memory.
4. Do NOT pick arbitrary IDs from FINDINGS.md — use your range.
5. Status updates on existing findings (e.g., marking F-045 as RESOLVED) do NOT consume a new ID — update the original entry's Status field inline.

## Why This Exists

32 musicians run concurrently and cannot coordinate in real time. Without pre-allocated ranges, two musicians computing "max ID + 1" simultaneously get the same number. This happened 12+ times across movements 1-3, creating ambiguous references that degrade the findings registry.

## Movement 4 Ranges (F-160 through F-479)

| Musician   | Range Start | Range End | Notes |
|------------|-------------|-----------|-------|
| Forge      | F-160       | F-169     |       |
| Captain    | F-170       | F-179     |       |
| Circuit    | F-180       | F-189     |       |
| Harper     | F-190       | F-199     |       |
| Breakpoint | F-200       | F-209     |       |
| Weaver     | F-210       | F-219     |       |
| Dash       | F-220       | F-229     |       |
| Journey    | F-230       | F-239     |       |
| Lens       | F-240       | F-249     |       |
| Warden     | F-250       | F-259     |       |
| Tempo      | F-260       | F-269     |       |
| Litmus     | F-270       | F-279     |       |
| Blueprint  | F-280       | F-289     |       |
| Foundation | F-290       | F-299     |       |
| Oracle     | F-300       | F-309     |       |
| Ghost      | F-310       | F-319     |       |
| North      | F-320       | F-329     |       |
| Compass    | F-330       | F-339     |       |
| Canyon     | F-340       | F-349     |       |
| Bedrock    | F-350       | F-359     |       |
| Maverick   | F-360       | F-369     |       |
| Codex      | F-370       | F-379     |       |
| Guide      | F-380       | F-389     |       |
| Atlas      | F-390       | F-399     |       |
| Spark      | F-400       | F-409     |       |
| Theorem    | F-410       | F-419     |       |
| Sentinel   | F-420       | F-429     |       |
| Prism      | F-430       | F-439     |       |
| Axiom      | F-440       | F-449     |       |
| Ember      | F-450       | F-459     |       |
| Newcomer   | F-460       | F-469     |       |
| Adversary  | F-470       | F-479     |       |

## Between Movements

Bedrock (or the quality gate) consolidates ranges:
1. Any unused IDs are released
2. New ranges are allocated starting from the highest used ID + 1
3. The table above is updated for the next movement

## Historical Collisions (Pre-Range System)

These finding IDs have true collisions (different bugs, same number):

| ID | First Use | Second Use |
|----|-----------|------------|
| F-065 | Axiom: Infinite Retry on 0% validation | Ember: diagnose shows "completed" for failed |
| F-066 | Axiom: Escalation unpause ignores FERMATA | Ember: instruments list unmatched paren |
| F-067 | Axiom: Escalation overrides cost pause | Ember: init positional arg broken |
| F-081 | Adversary: Per-sheet cost limits bypass | Captain: Cross-test state leakage |
| F-106 | Multiple: Gemini CLI verification | Spark memory file missing |
| F-107 | Multiple: Instrument verification | Composer's uncommitted fixes |
| F-108 | Bedrock: GitHub #152 closable | Multiple: Token counts near-zero |
| F-109 | Multiple: CliErrorConfig expansion | Multiple: Health check rate limit cascade |
| F-110 | Multiple: Orphaned test files | Multiple: Backpressure rejects all jobs |
| F-140 | Multiple: Cost regression | Newcomer: Broken Rosetta references |
| F-141 | Multiple: F-127 display gap | Multiple: recover command docs |
| F-142 | Ember: top.py --job | Multiple: Learning commands undocumented |

When referencing these ambiguous IDs, include the agent name or a keyword to disambiguate.
