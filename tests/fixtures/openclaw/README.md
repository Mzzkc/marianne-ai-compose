# OpenClaw fixture provenance

Contract pinned to OpenClaw **v2026.10.1**, commit `e178c39e9aae3fa440735217f9aef9574f286677`.
These fixtures are synthetic source-derived protocol examples, not captured
provider transcripts. No OpenClaw executable was installed on the implementation
host; live authenticated execution was not verified.

Immutable upstream references:

- [Package version and executable](https://github.com/openclaw/openclaw/blob/e178c39e9aae3fa440735217f9aef9574f286677/package.json): `openclaw` binary, Node.js `>=24.16.0 <25 || >=26.1.0`.
- [CLI registration](https://github.com/openclaw/openclaw/blob/e178c39e9aae3fa440735217f9aef9574f286677/src/cli/program/register.agent-turn.ts): `agent exec`, `--json`, `--model`, `--timeout` and `--message-file -`. Its exec action explicitly inherits these parent options, which is why the generic backend can emit `openclaw agent --json --model MODEL --message-file - exec` without a multiword subcommand or wrapper. No separate approval flag is needed.
- [Prompt and config](https://github.com/openclaw/openclaw/blob/e178c39e9aae3fa440735217f9aef9574f286677/src/commands/agent-exec-input.ts): stdin reads UTF-8, max 4 MiB; exec defaults to coding tools, workspace-only filesystem access and full shell execution policy, while explicit operator policy wins.
- [Execution and authentication](https://github.com/openclaw/openclaw/blob/e178c39e9aae3fa440735217f9aef9574f286677/src/commands/agent-exec.ts): inherits client configuration and saved auth, creates temporary state, binds tools to cwd, emits JSON and stderr diagnostics. Exit 0 means success, 1 means error, 2 timeout; failure is not an exit-zero semantic envelope, so no `error_flag_path` is configured.
- [Result projection](https://github.com/openclaw/openclaw/blob/e178c39e9aae3fa440735217f9aef9574f286677/src/commands/agent-exec-result.ts): final text at `final`, tokens at `usage.input` / `usage.output` when available, error at `error.message`. Success fixture includes deliberate distractors to check path selection. Error fixture models exhausted fallbacks (exit 1); timeout tests also exercise exit 2.
- [State/config paths](https://github.com/openclaw/openclaw/blob/e178c39e9aae3fa440735217f9aef9574f286677/src/config/paths.ts): HOME-backed defaults and `OPENCLAW_STATE_DIR` / `OPENCLAW_CONFIG_PATH` overrides. These two overrides are the explicit env allowlist; provider keys need a local profile override. This filters inherited environment, not credentials loaded by OpenClaw from configured stores.
- [Agent documentation](https://github.com/openclaw/openclaw/blob/e178c39e9aae3fa440735217f9aef9574f286677/docs/cli/agent.md): recommended headless command, saved credentials, temporary state, policy defaults and native 600-second timeout.

The diagnostic regexes classify conventional auth/rate/capacity messages surfaced
on stderr; they are heuristics, not a guarantee about every provider's wording.
The fake executable exercises actual process launch, argv/stdin, environment,
working directory, nonzero errors and timeout reaping without network or billing.
