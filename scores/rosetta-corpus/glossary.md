## Glossary

| Term | Meaning |
|------|---------|
| **Sheet** | One execution stage — a single agent performing a task |
| **Score** | A complete YAML job config containing one or more sheets |
| **Concert** | Multiple scores chained via `on_success` |
| **Conductor** | The daemon that manages job execution |
| **Workspace** | Directory where all outputs live — the shared filesystem |
| **Instrument** | An AI backend (Claude, Gemini, Ollama, CLI tools) with specific capabilities and cost profiles |
| **Prelude** | Markdown injected into every sheet's prompt — shared context |
| **Cadenza** | Per-sheet or per-instance markdown injected into that sheet's prompt. In fan-out, a list of cadenza files maps 1:1 to instances |
| **Fan-out** | Running multiple sheet instances in parallel (`instances: N`) |
| **Self-chaining** | A score triggering itself via `on_success: action: self`, carrying workspace forward |
| **capture_files** | Which workspace files a sheet can read from previous stages |
| **previous_outputs** | Mechanism forwarding all prior sheet outputs to the current sheet |
| **content_regex** | Validation: output matches a regex |
| **content_contains** | Validation: output contains a specific string |
| **command_succeeds** | Validation: a shell command exits 0 — real execution, not LLM opinion |
| **file_exists** | Validation: a workspace file was created |
| **file_modified** | Validation: a workspace file was changed (requires user-supplied check script) |
| **on_success** | What happens after completion — enables self-chaining and concert sequencing |
| **on_failure** | What happens after failure — Aspirational: not yet implemented in Marianne |
| **inherit_workspace** | Self-chain gets the same workspace, not fresh |
| **max_chain_depth** | Safety bound on self-chaining iterations |
| **Status: Working** | YAML in this pattern composes in Marianne today |
| **Status: Aspirational** | Pattern depends on a noted feature not yet in Marianne |
| **Prompt technique** | Within-stage pattern: structures a single sheet's prompt, not sheet arrangement |
| **Orchestration pattern** | Multi-sheet pattern: structures how sheets, scores, or instruments interact |
