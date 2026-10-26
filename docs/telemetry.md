# Community telemetry

Telemetry is opt-in and disabled by default. No receiving service, background
upload, or default endpoint is configured. Consent, score collection, and network
submission are separate choices. Leaving telemetry disabled preserves existing
execution behavior.

## What is staged

With explicit user consent and `telemetry.enabled: true` in a score, the daemon
stages one JSONL envelope when a baton job completes or fails, including resumed
jobs. The envelope contains `schema_version: 1`, collection time, an opaque random
consent ID, the sanitized parsed score configuration, job status and sheet count,
and the live sheet outcomes. Available sheet fields include retries, validation
results, errors, outcome data, and learned patterns. There is no cross-run edit
history or separate user-modification diff capture.

The sanitizer recursively scrubs paths, home-directory usernames, email addresses,
URLs, credential fields, tokens, private keys, and credential-shaped strings in
mapping keys and values, including diagnostic prose. A mapping key is treated as
a credential when any of its `_`, `-` or camelCase segments is a credential word
(`access_key`, `auth`, `AccessToken`, `session-cookie`); the usage counters
(`input_tokens`, `token_count`, and so on) are exempt by exact name. Transport configuration is
excluded from the score payload. Pattern matching cannot guarantee anonymity:
names or other personal facts in arbitrary prompts and output can remain.
Inspect an export before deciding to share it. Do not enable collection for
scores whose prose you are unwilling or unauthorized to share.

## Local storage and consent

User-global consent lives in `~/.marianne/telemetry-consent.json`, independently
of score YAML. Missing, malformed, unreadable, or revoked consent refuses both
collection and submission. The default spool is `~/.marianne/telemetry.jsonl`;
scores may select another `telemetry.spool_path`. Data files are replaced
atomically with mode 0600. Stable private lock files serialize writers,
revocation, and flushes. Reading an untouched spool creates no data.
Spool and export destinations cannot use reserved `.lock` filenames or overlap
the selected consent/control files, preserving their stable lock identities.

```bash
mzt telemetry status
mzt telemetry consent --grant
```

Then opt individual scores in:

```yaml
telemetry:
  enabled: true
```

Consent alone does not enable a score. Score settings cannot grant user consent.
Collection is always local: `submit_mode` and `endpoint` are reserved keys the
schema accepts and no runtime reads, so setting them changes nothing; v1 sending
occurs only through the explicit flush command. No daemon background task uploads data.

## Inspect, export, and submit

```bash
mzt telemetry export
mzt telemetry export --out telemetry-review.jsonl
mzt telemetry flush --dry-run
mzt telemetry flush --push --endpoint https://your-receiver.example/telemetry
```

`status`, `export`, `consent`, and `flush` accept `--spool-path PATH` to select
a custom spool. Exports are JSONL, preserve staged records, and use private file
permissions. Protect exported copies yourself; spool purge does not erase them.

Plain `flush` is local-only and sends nothing. `--dry-run` never sends or prunes.
Network submission requires `--push`, an explicit HTTPS endpoint (HTTP is allowed
only on loopback for development), and fresh granted consent. No redirects,
environment proxy configuration, automatic retries, or endpoint credentials are
used. Configure a receiver you trust: this repository supplies the client only,
not a public collection service, recipient retention policy, or training service.
The POST body is `{"envelopes": [...]}`; any HTTP 2xx acknowledges the whole batch.

Only records belonging to the current consent ID can be sent. Re-granting after
revocation rotates that ID; purge old records before staging under a new grant.
Timeouts, connection errors, HTTP failures, invalid destinations, and storage
errors retain unacknowledged records and emit warnings without payload or URL
details. A successful flush removes only the submitted unchanged prefix, keeping
concurrently appended records. Concurrent flushes of the same spool are refused.
Delivery is at least once: a lost response or failed local acknowledgment can
cause duplicates on retry, so receivers should deduplicate envelopes.

## Revoke and purge (opt-out)

```bash
mzt telemetry consent --revoke
mzt telemetry consent --revoke --purge
mzt telemetry consent --purge --spool-path /path/to/custom-spool.jsonl
```

Revocation persists before optional purge, stops future collection and submission,
and waits for already-authorized local appends before returning. Collection and
submission read consent fresh; they do not cache grants. A network request already
in flight may still complete after revocation, and data already sent cannot be
retracted by this client. Revocation without purge retains staged data for local
inspection. Purge deletes only the selected spool; repeat it for every custom
spool. It does not delete exports, existing local learning databases, or receiver
copies. Turning `telemetry.enabled` off stops collection for that score without
changing global consent. Storage errors produce a nonzero CLI exit rather than
claiming an unsuccessful consent change succeeded.
