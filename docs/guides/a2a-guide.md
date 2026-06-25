# A2A (Agent-to-Agent) Protocol Guide

Status: internal live delegation support. Marianne can register agent cards,
route `@delegate target: task` output into a target job's in-memory inbox, and
inject pending inbox tasks into A2A-enabled sheets. A2A complements shared
cadenza coordination; it is not the authoritative fleet memory or coordination
record.

## Overview

The A2A protocol enables structured task delegation between running agents
in real time. It complements file-based coordination (shared cadenza
directories) with active engagement — "I need this reviewed now" vs.
"I left a note, someone will see it." Results that must survive retries,
process boundaries, or future cycles must still be written to the shared
workspace.

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
  │                                    │ inject pending task context  │
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

Each agent-card job has an in-memory inbox for incoming tasks:

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

Four A2A event types are defined and can be mapped to observer events:

| Event | Source | Handler |
|-------|--------|---------|
| `A2ATaskSubmitted` | Agent output classification | Conductor publishes observer event and routes to inbox |
| `A2ATaskRouted` | Conductor | Confirmation for observability |
| `A2ATaskCompleted` | Agent output | Model exists; output parsing/result routing not wired |
| `A2ATaskFailed` | Agent output | Model exists; output parsing/result routing not wired |

## Task Persistence

Tasks persist across sheet boundaries while the conductor process remains
alive. The adapter inbox is in-memory today; restart-safe checkpoint persistence
requires a future schema extension. Durable results still belong in shared
cadenza files.

A2A is phase-scoped in both directions. A sheet can only trigger delegation
when the `a2a` protocol technique is active for that sheet's movement/phase.
A target job only consumes pending inbox tasks when one of its own
A2A-enabled sheets is dispatched. Scores should therefore make A2A check
sheets explicit, or use `phases: ["all"]` when every sheet is meant to
check the inbox.

1. Canyon sends Sentinel a task during Canyon's work sheet
2. Conductor stores the task in Sentinel's in-memory inbox
3. Sentinel's next A2A-enabled check sheet starts — inbox contents injected as context
4. Sentinel processes the task, produces artifacts
5. Sentinel writes durable artifacts/findings to the shared workspace
6. Optional completion/result-routing syntax remains future work

### Serialization

`A2AInbox` can serialize itself, but the baton adapter does not yet persist
that data through `CheckpointState`.

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
- Serialization helpers on `A2AInbox`
- Baton event types for all A2A operations
- EventBus integration via `to_observer_event()` mapper
- Technique router A2A request detection (`@delegate` pattern)
- Agent card registration/deregistration on baton job register/deregister
- Structured A2A request payloads carried on `SheetAttemptResult`
- Adapter routing from completed sheet output into target inboxes
- Pending inbox injection into A2A-enabled sheet prompt context
- Phase-scoped A2A trigger and check behavior
- Focused conductor-loop smoke coverage in `tests/test_a2a_wiring.py`

### Not Yet Wired
- Inbox persistence with checkpoint save/load cycle
- Agent discovery list injection in prompts
- Completion/failure output syntax parsing and result return to the requester
- External A2A protocol compatibility; this is Marianne-internal delegation
