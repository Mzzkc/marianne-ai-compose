# Known Issues — Marianne AI Compose

**Applies to:** `v0.1.0-alpha`
**Last updated:** 2026-07-08

This is an **alpha** release. The core orchestration engine is well-tested
(10,000+ passing tests, `mypy --strict` clean, automated CI), but several
subsystems carry documented limitations. Read this before relying on Marianne
for anything important, and prefer the sandbox-free quickstart path
(`docs/sandbox-free-quickstart.md`).

The authoritative, always-current list lives in the
[issue tracker](https://github.com/Mzzkc/marianne-ai-compose/issues). The items
below are the ones most likely to affect a new user or evaluator.

---

## 1. Dashboard remote exposure remains a cautious path

The web dashboard (`mzt dashboard`) ships authentication routes, but they have
historically been treated as a local-only alpha surface. Project status marks
the minimal dashboard security review issue as closed, but remote deployment
still needs current security review for the environment where it will run.

- **Do not** expose the dashboard to the public internet.
- **Do not** rely on its auth as a security boundary.
- Bind it to `localhost` and put it behind your own authenticating proxy if you
  need remote access.

Tracking: [#292](https://github.com/Mzzkc/marianne-ai-compose/issues/292)
(minimal dashboard security review; historical alpha blocker).

---

## 2. `code_mode` is opt-in — prefer CLI instruments for first runs

Marianne has an optional **code-execution sandbox** (`code_mode`, backed by
`bwrap`). The historical path-mismatch blocker is marked closed in project
status, but `code_mode` still executes agent-generated code and remains a
specialized, opt-in path.

**Impact is limited:** `code_mode` is *opt-in*. It only runs for sheets that
explicitly set a `code_mode:` config. **Normal scores that drive CLI
instruments** (`claude-code`, `opencode`, `goose`, `codex`, Ollama via a
provider, etc.) **do not touch the sandbox at all** and are unaffected.

- **Recommended:** follow the [sandbox-free quickstart](docs/sandbox-free-quickstart.md).
  It uses curated CLI-instrument configs that avoid `code_mode` entirely.
- **Avoid for first runs:** authoring scores that use `code_mode:` unless you
  specifically need generated-code execution and have reviewed the risk.

Tracking: [#210](https://github.com/Mzzkc/marianne-ai-compose/issues/210)
(path mismatch; historical alpha blocker), [#165](https://github.com/Mzzkc/marianne-ai-compose/issues/165)
(onboarding blocker).

---

## 3. Conductor restart / recovery limitations

The conductor (daemon) is designed to run for days, and `mzt recover` +
`mzt resume` reset failed/cascaded sheets and continue interrupted work. Two
constraints remain:

- **Do not stop, restart, or kill the conductor while jobs are running.**
  Pause or cancel active jobs first, then `mzt stop`. Recovery from a hard
  interruption of in-flight work is not fully reliable.
- After an **auto-recovery on conductor restart**, `pause`/`cancel` on the
  recovered job can fail
  ([#162](https://github.com/Mzzkc/marianne-ai-compose/issues/162)).

Fixed in this release: the specific bug where `mzt recover` followed by
`mzt resume` re-FAILed a job immediately (the baton held stale sheet state and
fired `is_job_complete` before recover took effect), and the bug where the
`Attempts` column read `0` for all baton-driven sheets.

Broader recovery-reliability hardening is tracked in
[#305](https://github.com/Mzzkc/marianne-ai-compose/issues/305).

---

## 4. Schema / state-migration requirements

The on-disk state model is still consolidating:

- There is **no automatic schema migration** between versions yet. If you
  upgrade across a state-schema change, you may need to start affected jobs
  fresh (`mzt run --fresh`) rather than resuming old checkpoints.
- `CheckpointState` carries 100+ Pydantic fields while the SQLite backend
  persists a subset; a unified schema registry exists but is not fully wired in,
  and two independent `SCHEMA_VERSION` constants still need reconciliation.

Tracking:
[#288](https://github.com/Mzzkc/marianne-ai-compose/issues/288) (document the
state-reset / migration strategy),
[#223](https://github.com/Mzzkc/marianne-ai-compose/issues/223),
[#224](https://github.com/Mzzkc/marianne-ai-compose/issues/224),
[#320](https://github.com/Mzzkc/marianne-ai-compose/issues/320).

---

## Reporting issues

File bugs at <https://github.com/Mzzkc/marianne-ai-compose/issues>. Include the
score (with secrets redacted), the `mzt diagnose` output, and the conductor
logs from `logs/`.
