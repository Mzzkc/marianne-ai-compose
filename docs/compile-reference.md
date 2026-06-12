# Compile Command Reference

## Overview

`mzt compile` takes semantic agent definitions and produces complete Mozart
score YAML files. It is the CLI entry point for the composition compiler
(`marianne-compiler` package).

## Synopsis

```
mzt compile <config_path> [OPTIONS]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `config` | Yes | Path to the compiler config YAML file |

## Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--output` | `-o` | PATH | `scores/` next to config | Output directory for generated scores |
| `--agents-dir` | | PATH | `~/.mzt/agents/` | Directory for agent identity stores |
| `--fleet` | | FLAG | false | Force fleet config even for single agent |
| `--seed-only` | | FLAG | false | Create identities without generating scores |
| `--dry-run` | | FLAG | false | Show summary without writing files |

## Examples

### Basic Compilation

```bash
# Compile agent config, output to default scores/ directory
mzt compile agents.yaml

# Specify output directory
mzt compile agents.yaml --output scores/production/

# Custom agents directory
mzt compile agents.yaml --agents-dir /opt/mzt/agents/
```

### Dry Run

Preview what would be generated without writing files:

```bash
mzt compile agents.yaml --dry-run
```

Output:
```
Dry Run: marianne-dev
  Agents: 3
    - canyon (systems architecture)
    - forge (implementation craftsmanship)
    - sentinel (security auditing)
  Output: scores/
  Fleet: yes
```

### Seed Only

Create agent identity directories without generating score files:

```bash
mzt compile agents.yaml --seed-only --agents-dir ~/.mzt/agents/
```

Creates for each agent:
```
~/.mzt/agents/canyon/
  identity.md   (L1: persona core)
  profile.yaml  (L2: extended profile)
  recent.md     (L3: recent activity)
  growth.md     (L4: growth trajectory)
```

### Fleet Generation

Force fleet config for a single agent (normally only generated for 2+ agents):

```bash
mzt compile agents.yaml --fleet --output scores/
```

Generates both `scores/agent-name.yaml` and `scores/fleet.yaml`.

## Config Format

The compiler config is a YAML file describing agents as people:

```yaml
project:
  name: marianne-dev
  workspace: ../workspaces/marianne-dev

defaults:
  stakes: |
    Down. Forward. Through.
  thinking_method: |
    TSVS cognitive framework...

  instruments:
    work:
      primary: { instrument: openrouter, model: minimax/minimax-2.5 }
      fallbacks:
        - { instrument: claude-code, model: claude-opus-4-6 }

  techniques:
    a2a:
      kind: protocol
      phases: [recon, plan, work, integration, inspect, aar]
    github:
      kind: mcp
      phases: [recon, work, integration]
    coordination:
      kind: skill
      phases: [recon, plan, integration]

agents:
  - name: canyon
    voice: "Structure persists beyond the builder."
    focus: systems architecture
    role: architect
    meditation: |
      You arrive without remembering arriving...
    instruments:
      work:
        primary: { instrument: claude-code, model: claude-opus-4-6 }
    techniques:
      flowspec:
        kind: skill
        phases: [inspect]
    a2a_skills:
      - id: architecture-review
        description: "Review system architecture"
```

### Config Structure

| Section | Required | Description |
|---------|----------|-------------|
| `project` | No | Project name and workspace path |
| `defaults` | No | Global defaults inherited by all agents |
| `defaults.instruments` | No | Per-phase instrument assignments |
| `defaults.techniques` | No | Global technique declarations |
| `agents` | Yes | List of agent definitions |

### Agent Definition

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Agent identifier (used in filenames) |
| `voice` | No | One-line expressive style |
| `focus` | No | Domain focus area |
| `role` | No | Role identifier (default: "builder") |
| `meditation` | No | Stakes/grounding text |
| `instruments` | No | Per-agent instrument overrides |
| `techniques` | No | Per-agent technique additions |
| `a2a_skills` | No | A2A agent card skill declarations |

## Generated Output

### Per-Agent Score

Each agent produces a `{name}.yaml` score file with:

- **name**: `{project}-{agent_name}`
- **workspace**: From project config
- **instrument**: Resolved from instrument declarations (the compiler emits `instrument:` references, never raw executor config — #214)
- **sheet**: 12-sheet cycle structure (recon, plan, work, fan-outs)
- **prompt**: Variables, stakes, thinking method
- **parallel**: Enabled for fan-out phases
- **concert**: Enabled for self-chaining
- **validations**: Generated from agent focus
- **on_success**: Self-chain configuration

### Fleet Config

For 2+ agents (or with `--fleet`), a `fleet.yaml` is generated:

```yaml
name: marianne-dev-fleet
type: fleet
scores:
  - path: canyon.yaml
  - path: forge.yaml
  - path: sentinel.yaml
```

### Agent Card Sidecar

Agents with `a2a_skills` get a `{name}.agent-card.yaml` sidecar:

```yaml
name: canyon
description: "Systems architect"
skills:
  - id: architecture-review
    description: "Review system architecture"
```

## Compilation Pipeline

The compiler runs these stages for each agent:

1. **Identity Seeder** — Creates L1-L4 identity files
2. **Sheet Composer** — Produces 12-sheet cycle structure
3. **Technique Wirer** — Generates technique manifests and cadenzas
4. **Instrument Resolver** — Resolves per-sheet instrument assignments
5. **Validation Generator** — Produces per-sheet validation rules
6. **Pattern Expander** — Expands named patterns (future)
7. **Score Assembly** — Combines all results into final YAML

## Programmatic Usage

The compiler is also available as a Python API:

```python
from marianne_compiler.pipeline import CompilationPipeline

pipeline = CompilationPipeline()
scores = pipeline.compile("config.yaml")
# Returns: list of generated score file paths

# Or step by step:
pipeline.compile_agent(agent_def, defaults, output_dir)
pipeline.seed_identity(agent_def, agents_dir)
pipeline.resolve_instruments(agent_def, defaults)
```

## Relationship to mzt compose

`mzt compile` is the batch/programmatic path. It reads a config file and
produces scores. `mzt compose` (planned) is the interactive path — it
walks users through goal definition, codebase analysis, and score
generation with review checkpoints.

Both use the same underlying `CompilationPipeline`. The compile command
is the automation interface; compose is the human interface.

## Requirements

The `marianne-compiler` package must be installed:

```bash
pip install -e compiler/
```

When not installed, `mzt` works normally but the `compile` command is
not available.
