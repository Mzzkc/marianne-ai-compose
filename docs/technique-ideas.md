# Technique Ideas — Running List

Potential techniques to formalize into the technique corpus. Each came up in conversation and was flagged for later implementation.

## Ideas

1. **Spec-as-cadenza via symlinked dirs** — Agent has a target symlink (e.g., `workspace/active-spec/`) pointing to a spec directory. Reads the spec as cadenza context. Can retarget the symlink to different specs as work evolves. Potential kind: `spec-link`. *(2026-04-18, migration session)*

2. **Stakes as technique** — The `prompt.stakes` field (compressed meditation) as a skill-kind technique rather than a prompt config field. Cleaner separation: identity grounding is a technique, not prompt plumbing. *(2026-04-18, migration session)*

3. **Thinking method as technique** — The `prompt.thinking_method` field (TSVS) as a skill-kind technique with `phases: [all]`. Currently prompt config; would compose better as a technique component in the ECS model. *(2026-04-18, migration session)*

4. **Safe triage from git repos** — Pattern for safely triaging work from a git repository with prompt injection checks. *(2026-04-17, issue-solver brainstorm)*

5. **Technique corpus discovery** — A discovery score that scans existing scores/prompts and identifies recurring patterns to formalize as named techniques. *(2026-04-17, issue-solver brainstorm)*

6. **Spec writing as technique** — A technique that wraps the auto-spec-writing flow from `docs/plans/compose-system/`. Agents invoke it to generate specs using the spec-writing config. Composes with existing agent workflows instead of being a bespoke score. *(2026-04-18, roster-failure session)*

7. **External publishing techniques** — Reddit, Medium, email posting as skill-kind techniques. Fills the OpenClaw-parity capability gap. Need safe auth story + rate-limiting per platform. *(2026-04-18, roster-failure session)*

8. **Moltbook interaction** — Safe notebook read/write as a technique. Boundaries: what agents can see vs. modify, audit trail. *(2026-04-18, roster-failure session)*

9. **Autonomous marketing** — Probably a *suite* of techniques (content drafting, posting, analytics) plus a recurring concert pattern, not a single technique. Flagged for scoping. *(2026-04-18, roster-failure session)*
