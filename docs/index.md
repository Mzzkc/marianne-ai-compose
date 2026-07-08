# Marianne AI Compose Documentation

Orchestration infrastructure for collaborative intelligence: composers write
declarative YAML scores, submit them to the conductor, and inspect sheets as
musicians execute them through instruments with validation gates.

## About the Name

This project is named after **Maria Anna "Nannerl" Mozart** (1751-1829), Wolfgang Amadeus Mozart's older sister. She was a keyboard virtuoso and prodigy in her own right — her father Leopold wrote that she played "so beautifully that everyone is talking about it." She toured Europe as a child performer alongside her younger brother, dazzling audiences with her skill and precision.

But when she turned eighteen, the tours stopped. Social conventions of the time forbade women from performing publicly or pursuing professional careers in music. While Wolfgang went on to become one of history's most celebrated composers, Nannerl's career ended before it truly began. Her compositions — the few that survived — suggest what might have been. She was denied her stage.

This project carries her name because it gives AI musicians a stage. Like an
orchestra conductor, Marianne coordinates multiple musicians through scores,
sheets, instruments, validations, and recoverable state.

The music metaphor isn't just aesthetic. It's structural. Scores, sheets, movements, instruments, concerts — these terms describe how the system works because they describe what the system *is*: infrastructure for collective intelligence, where no single musician is the star and the whole is greater than any part.

A tool named after a silenced prodigy, built to coordinate collaborative
intelligence through inspectable scores. That's what this is.

## Suggested Reading Paths

**New Users:**
1. [Getting Started](getting-started.md) — Installation, environment check, first score
2. [Score Writing Guide](score-writing-guide.md) — How to author Marianne scores
3. [Instrument Guide](instrument-guide.md) — Available instruments and how to add your own
4. [Examples Directory](../examples/) — Working score configurations

**Score Authors:**
1. [Score Writing Guide](score-writing-guide.md) — Comprehensive authoring guide
2. [Instrument Guide](instrument-guide.md) — Choosing and configuring instruments
3. [Configuration Reference](configuration-reference.md) — Every config field documented
4. [Examples Directory](../examples/) — Real-world score patterns
5. [Marianne Score Playspace](https://github.com/Mzzkc/marianne-score-playspace) — Creative showcase with real output

**System Administrators:**
1. [Daemon Guide](daemon-guide.md) — Conductor setup, configuration, troubleshooting
2. [CLI Reference](cli-reference.md) — Complete command reference
3. [Limitations](limitations.md) — Known limitations and workarounds

## Documentation

### Getting Started

- [**Getting Started**](getting-started.md) — Installation, environment check, first score
- [**Score Writing Guide**](score-writing-guide.md) — Comprehensive guide to authoring Marianne scores
- [**Instrument Guide**](instrument-guide.md) — Available instruments, adding your own, profile reference

### Reference

- [**CLI Reference**](cli-reference.md) — All Marianne commands with examples and options
- [**Configuration Reference**](configuration-reference.md) — Complete YAML schema documentation
- [**Limitations**](limitations.md) — Known constraints and recommended workarounds

### System Guides

- [**Daemon Guide**](daemon-guide.md) — Conductor daemon setup, operation, and debugging
- [**MCP Integration**](MCP-INTEGRATION.md) — Marianne's MCP server for external tool integration
- [**Marianne Reference**](marianne-reference.md) — Architecture overview and internal concepts

### Learning & Internals

- [**Distributed Learning Architecture**](DISTRIBUTED-LEARNING-ARCHITECTURE.md) — Cross-job pattern learning system

### Research (Internal)

- [**Token Compression Strategies**](research/TOKEN-COMPRESSION-STRATEGIES.md) — Research into reducing token costs (not yet implemented)
- [**Opus Convergence Analysis**](research/OPUS-CONVERGENCE-ANALYSIS.md) — Meta-analysis comparing Marianne and RLF evolution patterns

### Examples

- [**Examples Directory**](../examples/) — runnable examples plus dated
  validation-status notes
- [**Marianne Score Playspace**](https://github.com/Mzzkc/marianne-score-playspace) — Creative showcase: philosophy, worldbuilding, education, and more

## Repository

- [**GitHub Repository**](https://github.com/Mzzkc/marianne-ai-compose) — Source code, issues, and contributions
