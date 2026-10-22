# Qwen Code headless fixture provenance

Contract pinned to Qwen Code **v0.12.6**, commit
`ac30c98a266a6335f8ebc7a9011f266cf6b63f13` (released npm client
`@qwen-code/qwen-code@0.12.6`). Reviewed 2026-10-22.

These are **source-derived synthetic fixtures**, not authenticated CLI captures.
Text, UUID, session and timing values are invented test data; no credentials or
user content were captured. The success usage values (100 input, 50 output,
10 cache read) match vendor adapter test examples. The earlier assistant has
intentional distractor result/usage keys to catch selecting the wrong message.

Immutable official sources (all paths below are relative to this commit):

- [Argument definitions](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/cli/src/config/config.ts):
  `qwen -p PROMPT --model MODEL --yolo --output-format json`.
- [Result types](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/cli/src/nonInteractive/types.ts):
  `result`, `usage.input_tokens`, `usage.output_tokens`, `is_error`, `error.message`.
- [Result builder](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/cli/src/nonInteractive/io/BaseJsonOutputAdapter.ts):
  `buildResultMessage` constructs both terminal success and error objects.
- [JSON adapter](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/cli/src/nonInteractive/io/JsonOutputAdapter.ts):
  `emitResult` prints the full message array on stdout.
- [Adapter tests](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/cli/src/nonInteractive/io/JsonOutputAdapter.test.ts):
  representative usage and success/error contracts.
- [Headless execution](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/cli/src/nonInteractiveCli.ts):
  unsupported slash commands emit an error result and return without throwing
  (exit zero); the ordinary execution catch emits an error and calls `handleError`.
- [Error exits](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/cli/src/utils/errors.ts):
  `handleError` prints JSON diagnostics on stderr and exits nonzero. The error
  fixture tests both exit-zero slash-command refusal and nonzero failure parsing.
- [Provider environment mappings](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/core/src/models/constants.ts)
  and [auth validation](https://github.com/QwenLM/qwen-code/blob/ac30c98a266a6335f8ebc7a9011f266cf6b63f13/packages/cli/src/config/auth.ts).

The built-in deliberately supports saved Qwen OAuth credentials through HOME and
OpenAI-compatible provider configuration (`OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`OPENAI_MODEL`, `QWEN_MODEL`). Other provider families or custom `envKey` settings
require a local profile override with an explicit credential allowlist. No model
capacity or price is assumed: model selection defaults to the client's settings
and can be overridden by a score. `--yolo` auto-approves tool execution; use only
in an appropriate workspace. JSON transport does not imply schema output, MCP
configuration injection, interactive mode or vision integration.

No `qwen` executable was available on this host during implementation; live
authentication and file-editing smoke remain unavailable. Fake executable tests
cannot establish a vendor authentication result.
