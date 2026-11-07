# Composition corpus groundwork for issue #148

Issue #148 proposes a model trained on Marianne score composition craft: syntax,
patterns, validation, and structural choices. The current work implements local
collection tooling. Training, model selection, composition routing, and funding
remain future work. Community adoption and a sufficiently diverse, annotated
corpus are still external conditions, even though #146's telemetry collector has
landed.

## Local assembly and measurement

```console
mzt corpus stats --sources examples,scores --json
mzt corpus build --sources examples,scores --output /tmp/composition-corpus.jsonl
mzt corpus stats --sources examples,/path/to/telemetry.jsonl --json
```

Sources are explicitly selected score directories or telemetry JSONL files.
Nothing discovers private user spools automatically, changes consent, submits
records, or contacts a model. Build writes a private (0600), atomically replaced
JSONL file. Sources and lock files cannot be used as the destination.

The repository contains roughly 71 score YAMLs before deduplication. Use the
stats command for the current distinct-output count: exact duplicate copies do
not increase the corpus's volume. Nested repositories (including rosetta-corpus),
symlinks, and lock files are excluded. Repository YAML is trusted local input and
is preserved byte-for-byte, including comments and line endings. Inspect it for
private material before any sharing. This trust rule does not apply to telemetry.

## Schema v1 and provenance

Each JSONL line is a `TrainingExample`: `instruction`, optional `input`, score
YAML `output`, optional human-authored `rationale`, and required `provenance`.
The default instruction is a placeholder for later curation, not a reconstruction
of the original composition request. No WHY explanation is generated or inferred.
A curator can fill `rationale` in a retained JSONL record and revalidate it through
`TrainingExample.model_validate_json`. The CLI currently assembles source material;
it does not reload annotated training JSONL as a telemetry spool.

Provenance schema version 1 records `source_path`, `source_kind` (`in_repo` or
`telemetry`), optional directory `category`, and an output SHA-256 `content_hash`.
Telemetry additionally carries `source_schema_version` and an opaque
`consent_hash`, derived from the collection grant identifier. No raw grant ID or
unrelated envelope prose is copied. The local source path itself may be private;
review provenance when preparing any externally shared artifact.

Telemetry envelopes must have a supported version, a nonempty collection grant
ID, and a nonempty score mapping. Malformed/unsupported records are skipped and
counted in a warning without logging their contents. Every accepted score passes
through the existing `telemetry.sanitizer.sanitize_score` again, including prose,
sensitive mapping keys, and removal of telemetry transport settings. Pattern
redaction cannot identify every personal fact. Sanitization and collection lineage
are not permission for redistribution or model training; those require separate
review and appropriate rights. Existing user-global collection/submission consent
remains unchanged. Processing a selected local historical spool does not renew or
assert a live grant.

Deduplication hashes exact output text; it does not normalize equivalent YAML.
When exact duplicates occur across sources, the repository example takes priority,
including its human rationale. Distinct records retain source discovery order.

## What the counters establish

`total_examples`, `by_source_kind`, `by_category`, and `annotated` describe the
assembled, deduplicated corpus. Unknown categories are not invented for telemetry.
`target_volume` is 200 and `meets_target` tests only that volume boundary. Reaching
it does not certify domain diversity, complexity coverage, score quality, human
WHY annotations, licensing, consent, or training readiness. Complexity tiers and
failure-analysis labels still need a separate curation design. Telemetry outcomes
are not yet joined into training examples.

## Relationship to the unconscious-local-model design

The existing [unconscious-local-model spec](../specs/2026-04-19-marianne-unconscious-local-model-design.md)
targets runtime conductor judgments: context, decision, and outcome records. Its
500-labeled-decision threshold is specific to that use case. Composition craft
instead uses score YAML and human rationale, with issue #148's proposed 200+
diverse-example threshold.

The reusable pipeline shape is extraction, quality filtering, instruction-format
records, held-out evaluation, shadow-mode A/B comparison, and rollback. Its manual
review and measurable improvement gates should inform later composition-model
experiments, without treating this groundwork as a trained or evaluated model.
Training compute, architecture selection, model packaging, deployment, routing,
and any permission to reuse community contributions are outside this change.
