# A2A (Agent-to-Agent) Protocol Guide

## Overview

The A2A protocol enables structured task delegation between running agents
in real time. It complements file-based coordination (shared cadenza
directories) with active engagement — "I need this reviewed now" vs.
"I left a note, someone will see it."

## Architecture

```
Agent A (Canyon)                    Conductor                   Agent B (Sentinel)
  │                                    │                              │
  │ @delegate sentinel: review X       │                              │
  │ ──────────────────────────────►    │                              │
  │         A2ATaskSubmitted           │                              │
  │                                    │ route to B's inbox           │
  │                                    │ ────────────────────────►    │
  │                                    │       A2ATaskRouted          │
  │                                    │                              │
  │                                    │    (B's next A2A sheet)      │
  │                                    │                              │
  │                                    │ ◄────────────────────────    │
  │                                    │     A2ATaskCompleted         │
  │ ◄──────────────────────────────    │                              │
  │       results in A's inbox         │                              │
```

## Components

### Agent Card Registry

Each running agent registers an identity card with the conductor:

```yaml
# In score YAML
agent_card:
  name: canyon
  description: "Systems architect — traces boundaries"
  skills:
    - id: architecture-review
      description: "Review system architecture"
    - id: boundary-analysis
      description: "Trace and analyze system boundaries"
```

Agents query the registry to discover who's running:
- `query()` — list all registered agents
- `query_by_skill("architecture-review")` — find agents with a specific skill
- `get_job_id_for_agent("canyon")` — resolve name to job_id for routing

### A2A Inbox

Each job has a persistent inbox for incoming tasks:

```python
inbox = A2AInbox(job_id="j1", agent_name="canyon")

# Submit a task (done by conductor when routing)
task = inbox.submit_task(
    source_job_id="j2",
    source_agent="forge",
    description="Review architecture for module X",
)

# Inject pending tasks into sheet context
context_text = inbox.render_pending_context()

# Mark tasks as accepted when injected
inbox.mark_accepted(task.task_id)

# Complete a task with results
inbox.complete_task(task.task_id, artifacts={"review": "looks good"})
```

### Task Lifecycle

```
PENDING → ACCEPTED → COMPLETED
                   → FAILED
```

- **PENDING**: Task waiting in inbox for target agent's next A2A-enabled sheet
- **ACCEPTED**: Target agent has received the task (injected into a sheet)
- **COMPLETED**: Task finished successfully with artifacts
- **FAILED**: Task could not be fulfilled

### Baton Events

Four A2A event types flow through the baton's event bus:

| Event | Source | Handler |
|-------|--------|---------|
| `A2ATaskSubmitted` | Agent output classification | Conductor routes to inbox |
| `A2ATaskRouted` | Conductor | Confirmation for observability |
| `A2ATaskCompleted` | Agent output | Results routed back |
| `A2ATaskFailed` | Agent output | Failure notification |

## Task Persistence

Tasks persist across sheet boundaries. Between sheets, the agent doesn't
exist — the inbox holds tasks in the conductor's state:

1. Canyon sends Sentinel a task during Canyon's work sheet
2. Conductor persists the task in Sentinel's inbox
3. Sentinel's next A2A-enabled sheet starts — inbox contents injected as context
4. Sentinel processes the task, produces artifacts
5. Artifacts persisted in Canyon's inbox
6. Canyon picks up results on their next relevant sheet

### Serialization

```python
# Save with checkpoint state
data = inbox.to_dict()

# Restore on recovery
inbox = A2AInbox.from_dict(data)
```

## Score YAML Configuration

Enable A2A for specific phases via the technique system:

```yaml
techniques:
  a2a:
    kind: protocol
    phases: [recon, plan, work, integration, inspect, aar]

agent_card:
  name: canyon
  description: "Systems architect"
  skills:
    - id: architecture-review
      description: "Review system architecture"
```

The technique resolver generates an A2A section in the technique manifest:

```markdown
## Techniques Available This Phase

### Protocols
- **a2a**: Communication protocol

### A2A Inbox — Pending Tasks
You have 2 task(s) from other agents:

### Task 1: from forge
**Task ID:** `abc-123`
**Description:** Review the new module boundaries
```

## Task Delegation Syntax

Agents delegate tasks using the `@delegate` syntax in their output:

```
@delegate sentinel: Review the authentication module for security issues
```

The technique router (`TechniqueRouter.classify()`) detects this pattern
and produces an `A2ARoutingRequest` that the conductor processes.

## Agent Discovery

Agents can discover running peers through the technique manifest injected
in their prompt. The manifest includes information about active agents and
their skills when A2A is enabled for the current phase.

## Inbox Rendering

`A2AInbox.render_pending_context()` produces markdown suitable for prompt
injection:

```markdown
## A2A Inbox — Pending Tasks

You have 1 task(s) from other agents:

### Task 1: from forge
**Task ID:** `abc-123`
**Description:** Review architecture for module X
**Context:**
  - file: src/marianne/core/config/job.py
  - concern: backward compatibility

To complete a task, include its task_id in your output with the results.
To decline, explain why.
```

## Current Status

### Implemented
- Agent card registry with name uniqueness and skill queries
- Per-job inbox with full task lifecycle (PENDING → ACCEPTED → COMPLETED/FAILED)
- Serialization for checkpoint persistence
- Baton event types for all A2A operations
- EventBus integration via `to_observer_event()` mapper
- Technique router A2A request detection (`@delegate` pattern)

### Not Yet Wired
- Agent card registration/deregistration on job start/end
- Inbox context injection in prompt rendering pipeline
- Inbox persistence with checkpoint save/load cycle
- A2A event routing through conductor (task submission → inbox deposit)
