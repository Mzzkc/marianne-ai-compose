# Memory System Design

**Date:** 2026-04-19 (merged from two independent design runs)
**Scope:** Memory substrate, retrieval, consolidation, and integration for Marianne Mozart
**Dependency:** S6 (Marianne Mozart) from the Baton Primitives spec
**Synthesis basis:** Run 1 promoted spec, Run 2 draft spec, both runs' adversarial reviews, ground-truth codebase verification

---

## Status

status: designed (merged)

This spec defines Marianne Mozart's memory system — the persistent, structured knowledge substrate that enables her to learn from experience, recognize patterns, maintain per-venue knowledge, build user relationships, and inform both her conscious (remote LLM) and unconscious (local model) reasoning. The memory system is distinct from the existing learning store, which continues to operate as execution telemetry infrastructure. The memory system sits above the learning store, distilling raw telemetry into experiential knowledge.

**Provenance:** This merged specification draws its backbone from the promoted Run 1 spec (revised after reject-and-redesign review), incorporating Run 2's dual-output consolidation pipeline, training data extraction, user alias system, vocabulary normalization, and retrieval attribution. All Critical and Important review findings from both runs have been addressed. Ground-truth verification confirmed schema version 15, 13 CREATE TABLE statements, and 7,279 total lines across the learning store's Python files.

---

## Goal

Give Marianne Mozart a memory that:

1. **Enables sub-second retrieval** for smart conductor decisions during live execution
2. **Accumulates with automatic lifecycle management** — consolidation compresses, archival ages out raw facts, forgetting prunes what consolidation has absorbed
3. **Maintains per-venue knowledge** — each project's conventions, patterns, and failure history are distinct
4. **Tracks per-user relationships** — how each user works, their preferences, trust level, expertise areas
5. **Aligns structurally with the RLF person model** — identity maps to L1-L4, knowledge to belief store, relationships to relationship memory, experience to developmental history
6. **Supports Collective Associated Memory (CAM) readiness** — multiple agents can eventually share the memory substrate through controlled, identity-scoped access
7. **Operates fully offline** — no cloud embedding APIs, no remote databases, no external services. Provisioning via `mzt memory init` for air-gapped environments

These seven goals directly satisfy S6's six memory requirements (fast retrieval, accumulation, per-venue, per-user, RLF compatibility, CAM readiness) plus the system-wide offline constraint.

---

## Requirements

### S6 Requirements (Verbatim) with Spec-Level Commitments

**S6-R1: Fast retrieval for real-time conductor decisions.** When Marianne is acting as smart conductor, she needs sub-second access to relevant experience. She cannot re-read files or query a slow store during live execution.

> *Spec commitment:* Tiered retrieval interface with three latency tiers — fast (<50ms, entity-lookup + `sqlite-vec` KNN), standard (<200ms, multi-signal fusion), comprehensive (<2s, full search + re-ranking). The fast path uses pre-computed entity neighbor caches for sub-millisecond structured queries plus disk-backed `sqlite-vec` KNN for <10ms vector search. In-process SQLite access eliminates all network overhead. Vector storage is disk-backed via `sqlite-vec`, ensuring bounded daemon RAM (NFR-08).

**S6-R2: Accumulation over time.** Interaction memory, execution observations, decisions and their outcomes — these grow without bound. The system needs consolidation, compression, and forgetting.

> *Spec commitment:* ADD-only fact storage with automatic tiered consolidation (hot/warm/cold/core). The archival lifecycle (FR-01) satisfies the forgetting requirement — consolidated facts older than the retention period (default 30 days) have their raw content and embeddings pruned, preserving only metadata and entity links for audit trail. Core-tier source facts are exempt from archival. Dreaming (consolidation) compresses facts into patterns and narratives, extending Legion's proven tiering model. Consolidation is also the training data pipeline (FR-09, FR-11) — dual-output by design.

**S6-R3: Per-venue knowledge.** Marianne works across venues. Each venue's context, conventions, and history are distinct. Cross-venue knowledge (patterns, general skills) is shared.

> *Spec commitment:* Namespaced database files — one SQLite database per venue, one global database for cross-venue patterns and shared entities. Retrieval scopes venue-local first, global fallback, with explicit multi-database retrieval flow and a 1.2x relevance boost for venue-local results (FR-05). Venue hash is derived from the workspace's canonical path.

**S6-R4: Per-user relationships.** Marianne learns how each user works — their preferences, communication style, trust level, areas of expertise.

> *Spec commitment:* User relationship store in global database with identity resolution across harnesses (TUI, Discord, Telegram, CLI). The `user_aliases` table maps multiple platform identities to a single canonical user (FR-06). Manual aliasing via `mzt memory alias` merges identities. All interaction methods accept any alias — resolution is transparent.

**S6-R5: RLF compatibility.** The memory architecture must map cleanly onto the RLF person model when integration lands.

> *Spec commitment:* Direct structural mapping — core tier to L1 identity, warm tier to L2 beliefs, hot facts to L3 working memory, cold narratives to L4 developmental history. `CAMExportProtocol` serializes memory state for RLF consumption. The `training_examples` table maps to developmental feedback in the RLF model.

**S6-R6: Collective Associated Memory (CAM) readiness.** The memory substrate should be designed so that other Marianne agents could eventually share a common memory system.

> *Spec commitment:* Every `MemoryStore` method accepts `agent_id`. The `agent_id` column is structurally enforced in the schema as a NOT NULL column with composite indexes on every data table (FR-07). Initial implementation uses constant `"marianne_mozart"`. Shared entities (instruments, error codes) live in a shared namespace; facts are agent-scoped. The `decisions` table is the first CAM artifact — shared knowledge produced by the conductor, available to all future agents without agent_id filtering.

### Functional Requirements

**FR-01: Fact storage with lifecycle.** The memory system stores individual facts as ADD-only records. Each fact has: content (text), timestamp, source (telemetry/conversation/decision/manual), entity links, agent ID, venue scope, and an embedding vector. Facts progress through a lifecycle: hot (unconsolidated, fully searchable) → consolidated (absorbed into warm/cold patterns, still queryable but lower-ranked) → archived (raw content pruned after retention period, entity links and metadata preserved). Archival satisfies S6's requirement for "forgetting" — consolidated facts that have been absorbed into warm or cold tier patterns are pruned after a configurable retention period (default 30 days post-consolidation). Core-tier source facts are exempt from archival.

**FR-02: Entity graph.** Named entities (instruments, venues, patterns, error codes, sheets, users) form nodes in a lightweight graph. Edges are typed relationships (caused_by, resolved_by, co_occurs_with, user_prefers, venue_convention). The graph enables associative retrieval — traversing connections rather than just matching content.

**FR-03: Tiered consolidation.** Facts progress through tiers via the dreaming process:
- **Hot (unconsolidated):** Individual facts at full fidelity. Recent, searchable.
- **Warm (pattern):** Multiple related facts compressed into a named pattern. Retains entity links.
- **Cold (narrative):** Aged patterns compressed into narrative summaries. Searchable but lower-ranked.
- **Core:** Permanently preserved knowledge. Never consolidated down. Manually or auto-promoted.

After consolidation, source facts enter the archival pipeline (FR-01). Archival prunes the raw `content` and `embedding` from consolidated facts older than the retention window, preserving only metadata and entity links for audit trail. This bounds database growth over months of operation.

**FR-04: Multi-signal retrieval.** Retrieval combines three signals: semantic similarity (embedding cosine distance via `sqlite-vec`), keyword matching (FTS5 with vocabulary normalization), and entity graph traversal (pre-computed neighbors seeded from query entities). Signals are fused with configurable weights. Every result carries retrieval attribution indicating which signal(s) matched and why, enabling diagnostic inspection via `mzt diagnose`.

**FR-05: Per-venue isolation.** Each venue (project) has its own memory database. Cross-venue knowledge (rosetta patterns, general instrument capabilities, system-wide patterns) lives in a global database. Retrieval searches venue-local first, then falls through to global via an explicit multi-database retrieval flow.

**FR-06: Per-user relationships.** User interactions are tracked: communication style, preferences, trust level, expertise areas, escalation history. Stored in the global database. Users are identified through an aliasing layer that resolves multiple harness identities (TUI session, CLI user, Discord handle, Telegram handle) to a single canonical user.

**FR-07: Identity-scoped access (CAM readiness).** Every read and write operation accepts an `agent_id` parameter. The `agent_id` column is structurally enforced in the schema — it appears on every data table (`facts`, `consolidated`, `relationships`, `decisions`) as a NOT NULL column with a composite index alongside the primary query columns. The initial implementation uses a single hard-coded identity (`"marianne_mozart"`). The Protocol supports future multi-tenant access without schema changes because isolation is enforced at the storage layer, not the application layer.

**FR-08: Non-blocking I/O.** All memory reads and writes are asynchronous and non-blocking. Embedding generation uses a dedicated, size-bounded `ThreadPoolExecutor` (default max 2 workers) separate from the default asyncio thread pool, preventing CPU-heavy embedding work from starving other async I/O. SQLite queries execute via `asyncio.to_thread()`. A failed write logs a warning but never fails the calling operation or gates the dispatch cycle.

**FR-09: Consolidation as a dual-output process.** Dreaming runs as a scheduled job (triggered by fact count threshold, idle time, periodic timer, or explicit command). It reads unconsolidated facts, extracts entities and relationships, identifies patterns, writes compressed summaries, marks source facts as consolidated, **and simultaneously extracts `(context, decision, outcome)` training tuples** for the unconscious model's fine-tuning pipeline. Consolidated facts enter the archival pipeline after the retention period.

**FR-10: Real-time telemetry ingestion via EventBus.** The `TelemetryIngester` operates in two modes:
- **Streaming mode (primary):** Subscribes to the baton's `EventBus` for relevant event types (`SheetAttemptResult`, `EscalationNeeded`, `LoopCompleted`, `SheetTriggerFired`). The EventBus subscriber pattern ensures decoupled integration — the ingester never touches `BatonCore` internals or risks blocking event dispatch. Each event is distilled into a hot fact and written to the venue-scoped memory database immediately. This ensures intra-job memory — if sheet 2 fails in a novel way, the memory system knows about it before sheet 5 starts.
- **Batch mode (supplementary):** Periodically reads from the learning store to capture aggregated patterns, trust score updates, and drift metrics that accumulate across jobs. Runs during consolidation or on idle.

The learning store schema is NOT modified. Streaming mode subscribes to EventBus events; batch mode reads the learning store read-only.

**FR-11: Training data extraction.** During consolidation, the dreaming process extracts `(context, decision, outcome)` tuples from `ConductorDecision` records where the outcome is known. These tuples are written to a `training_examples` table for the unconscious model's fine-tuning pipeline. The `TelemetryIngester` and training-data extractor are the same component — consolidation quality directly affects model quality.

**FR-12: Offline provisioning.** `mzt memory init` downloads and caches the embedding model (`all-MiniLM-L6-v2`, 80MB) to `~/.marianne/models/`. For air-gapped environments, `mzt memory init --from-path /path/to/model/` imports a pre-downloaded model directory. This command is idempotent — running it when the model is already cached is a no-op. The memory system initializes without the embedding model (degrading to keyword + entity search) but `mzt memory init` is the documented path for first-run setup.

### Non-Functional Requirements

**NFR-01: Retrieval latency.** Fast path (entity-lookup + sqlite-vec KNN): <50ms. Standard path (multi-signal): <200ms. Comprehensive path (full search + re-ranking): <2s. All measured on SQLite against stores with <100K facts.

**NFR-02: Storage footprint.** The memory database for a moderately active venue (~1000 facts, ~500 entities) should be <50MB. The global database (cross-venue patterns, user relationships) should be <100MB. Archival (FR-01) prevents unbounded growth — databases stabilize once the archival cycle reaches steady state.

**NFR-03: Offline operation.** The entire memory system functions without internet after initial provisioning. The embedding model (`all-MiniLM-L6-v2`, 80MB) is provisioned via `mzt memory init` and cached in `~/.marianne/models/`. Alternatively, it downloads on first use if network is available — but this path is not guaranteed for air-gapped deployments. SQLite requires no external service. If the embedding model is not provisioned, retrieval degrades gracefully to keyword and entity-graph search only, with an INFO-level log explaining the degradation.

**NFR-04: Schema migration.** The memory database uses versioned schemas with forward-only migrations, following the pattern established by the learning store. Migrations are applied on first access.

**NFR-05: Crash safety.** Write operations use the learning store's atomic write pattern (write to temp, rename). SQLite WAL mode provides crash-safe reads during writes. A crash during consolidation leaves unconsolidated facts intact — they are consolidated on the next run.

**NFR-06: Event loop protection.** Embedding generation (model inference for query vectors) runs on a **dedicated `ThreadPoolExecutor`** (configurable, default `max_workers=2`) separate from the default asyncio thread pool. This prevents CPU-heavy embedding work (~5-10ms per embedding, but ~50ms under load with batching) from starving async file I/O, timer callbacks, and other threaded tasks in the conductor. SQLite queries execute via `asyncio.to_thread()` on the default pool. The baton's event loop must never block on memory operations.

**NFR-07: Atomic counter operations.** All numeric updates (entity `mention_count`, relationship `weight`, relationship `observation_count`, user `interaction_count`) use SQL-level atomic increments (`UPDATE ... SET weight = weight + 1`) rather than read-modify-write cycles. This prevents race conditions when overlapping sheet completions trigger concurrent memory writes in a fan-out scenario.

**NFR-08: Bounded daemon memory.** The memory system's RAM footprint is bounded. Vector storage uses `sqlite-vec` (disk-backed, zero RAM overhead for stored vectors). The only in-memory caches are entity `cached_neighbors` (JSON, negligible) and the `sqlite-vec` internal page cache (bounded by SQLite's `cache_size` pragma). The daemon's memory footprint does not grow with fact count.

---

## Design

### Database Schema

```sql
-- Memory database (one per venue, plus one global)
-- Memory schema version 1 (distinct from the learning store's schema v15)

-- Facts: ADD-only, timestamped, entity-linked, with lifecycle
CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT,                 -- NULL after archival (content pruned)
    search_text TEXT,             -- Normalized for FTS5: UUIDs, timestamps, PIDs stripped
    timestamp REAL NOT NULL,      -- Unix epoch
    source TEXT NOT NULL,         -- 'telemetry', 'conversation', 'decision', 'manual'
    agent_id TEXT NOT NULL,       -- Identity scope (CAM readiness)
    venue_hash TEXT,              -- NULL for global facts
    embedding BLOB,              -- Float32 vector, pre-computed; NULL after archival
    embedding_model TEXT,         -- Model identifier for migration support
    consolidated INTEGER DEFAULT 0,  -- 0=hot, 1=consolidated into a pattern
    consolidated_at REAL,         -- Timestamp when consolidated; NULL if hot
    archived INTEGER DEFAULT 0,   -- 1=content and embedding pruned after retention
    tier TEXT DEFAULT 'hot',      -- 'hot', 'warm', 'cold', 'core'
    metadata TEXT                 -- JSON, source-specific context (preserved after archival)
);
CREATE INDEX idx_facts_timestamp ON facts(timestamp DESC);
CREATE INDEX idx_facts_agent ON facts(agent_id);
CREATE INDEX idx_facts_agent_consolidated ON facts(agent_id, consolidated);
CREATE INDEX idx_facts_consolidated ON facts(consolidated);
CREATE INDEX idx_facts_tier ON facts(tier);
CREATE INDEX idx_facts_archived ON facts(archived);

-- FTS5 virtual table for keyword search (vocabulary-normalized)
CREATE VIRTUAL TABLE facts_fts USING fts5(
    search_text,
    content='facts',
    content_rowid='id'
);

-- Vector index via sqlite-vec extension (disk-backed, bounded RAM)
CREATE VIRTUAL TABLE facts_vec USING vec0(
    fact_id INTEGER PRIMARY KEY,
    embedding float[384]
);

-- Entities: unique named entities with embeddings
CREATE TABLE entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    entity_type TEXT NOT NULL,    -- 'instrument', 'venue', 'pattern', 'error_code', 'sheet', 'user'
    embedding BLOB,              -- For entity-level semantic search
    cached_neighbors TEXT,        -- JSON: pre-computed first-degree neighbor IDs and types
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    mention_count INTEGER DEFAULT 1
);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_name ON entities(name);

-- Fact-Entity links (many-to-many)
CREATE TABLE fact_entities (
    fact_id INTEGER NOT NULL REFERENCES facts(id),
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    PRIMARY KEY (fact_id, entity_id)
);
CREATE INDEX idx_fact_entities_entity ON fact_entities(entity_id);

-- Entity relationships (graph edges)
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id INTEGER NOT NULL REFERENCES entities(id),
    target_entity_id INTEGER NOT NULL REFERENCES entities(id),
    relation_type TEXT NOT NULL,   -- 'caused_by', 'resolved_by', 'co_occurs_with', etc.
    weight REAL DEFAULT 1.0,       -- Strength (atomic increment on repeated observation)
    agent_id TEXT NOT NULL,         -- Identity scope
    first_observed REAL NOT NULL,
    last_observed REAL NOT NULL,
    observation_count INTEGER DEFAULT 1
);
CREATE INDEX idx_relationships_source ON relationships(source_entity_id);
CREATE INDEX idx_relationships_target ON relationships(target_entity_id);
CREATE INDEX idx_relationships_type ON relationships(relation_type);
CREATE INDEX idx_relationships_agent ON relationships(agent_id);

-- Consolidated summaries (tiered)
CREATE TABLE consolidated (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tier TEXT NOT NULL,          -- 'warm' (pattern), 'cold' (narrative), 'core' (permanent)
    content TEXT NOT NULL,
    search_text TEXT,            -- Normalized for FTS5
    timestamp REAL NOT NULL,
    source_fact_ids TEXT,        -- JSON array of fact IDs that were consolidated
    entity_ids TEXT,             -- JSON array of linked entity IDs
    agent_id TEXT NOT NULL,
    venue_hash TEXT,
    embedding BLOB,
    embedding_model TEXT,
    word_count INTEGER
);
CREATE INDEX idx_consolidated_tier ON consolidated(tier);
CREATE INDEX idx_consolidated_agent ON consolidated(agent_id);
CREATE INDEX idx_consolidated_agent_tier ON consolidated(agent_id, tier);

-- FTS5 for consolidated summaries
CREATE VIRTUAL TABLE consolidated_fts USING fts5(
    search_text,
    content='consolidated',
    content_rowid='id'
);

-- Vector index for consolidated entries
CREATE VIRTUAL TABLE consolidated_vec USING vec0(
    consolidated_id INTEGER PRIMARY KEY,
    embedding float[384]
);

-- Conductor decisions (shared knowledge, feeds unconscious training)
-- Note: decisions table has NO agent_id filter — it is shared namespace (first CAM artifact).
-- All agents read all decisions. This is intentional: conductor decisions are collective knowledge.
CREATE TABLE decisions (
    id TEXT PRIMARY KEY,         -- UUID
    job_id TEXT NOT NULL,
    sheet_num INTEGER,           -- NULL for job-level decisions
    timestamp REAL NOT NULL,
    trigger_event_type TEXT NOT NULL,
    trigger_summary TEXT NOT NULL,
    decision_action TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    decision_source TEXT NOT NULL,   -- 'heuristic', 'local_model', 'remote_llm', 'human'
    confidence REAL NOT NULL,
    outcome TEXT,                -- Filled in after: 'success', 'failed', 'no_op'
    outcome_timestamp REAL,
    context_snapshot TEXT         -- JSON, the ConductorContext serialized
);
CREATE INDEX idx_decisions_job ON decisions(job_id);
CREATE INDEX idx_decisions_action ON decisions(decision_action);
CREATE INDEX idx_decisions_source ON decisions(decision_source);
CREATE INDEX idx_decisions_timestamp ON decisions(timestamp DESC);

-- Training examples extracted during consolidation (feeds unconscious fine-tuning)
CREATE TABLE training_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL REFERENCES decisions(id),
    context_text TEXT NOT NULL,      -- The ConductorContext summarized as input
    decision_text TEXT NOT NULL,     -- The action taken as output
    outcome TEXT NOT NULL,           -- 'success' or 'failed'
    extracted_at REAL NOT NULL,
    used_in_training INTEGER DEFAULT 0,   -- 1 after consumed by fine-tuning pipeline
    venue_hash TEXT
);
CREATE INDEX idx_training_outcome ON training_examples(outcome);
CREATE INDEX idx_training_used ON training_examples(used_in_training);

-- User relationships (global database only)
CREATE TABLE user_relationships (
    user_id TEXT PRIMARY KEY,        -- Canonical user identifier
    display_name TEXT,
    communication_style TEXT,        -- JSON: preferences, formality level, etc.
    expertise_areas TEXT,            -- JSON array
    trust_level REAL DEFAULT 0.5,
    interaction_count INTEGER DEFAULT 0,
    first_interaction REAL,
    last_interaction REAL,
    escalation_history TEXT,         -- JSON: recent escalation decisions
    preferences TEXT                 -- JSON: per-venue overrides, notification prefs, etc.
);

-- User identity aliases (global database only)
-- Maps harness-specific identities to canonical user IDs
CREATE TABLE user_aliases (
    alias TEXT PRIMARY KEY,          -- e.g., 'discord:nannerl#1234', 'telegram:12345', 'tui:emzi'
    harness TEXT NOT NULL,           -- 'tui', 'cli', 'discord', 'telegram'
    user_id TEXT NOT NULL REFERENCES user_relationships(user_id),
    created_at REAL NOT NULL
);
CREATE INDEX idx_aliases_user ON user_aliases(user_id);
CREATE INDEX idx_aliases_harness ON user_aliases(harness);

-- Schema version tracking
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL
);
```

### Vector Storage: sqlite-vec

The memory system uses `sqlite-vec` for vector similarity search. `sqlite-vec` is a loadable SQLite extension that stores vectors on disk alongside the database, provides KNN queries via virtual tables, and requires zero RAM beyond SQLite's normal page cache. This eliminates the OOM risk that an unbounded in-memory vector index would create for a long-running daemon process.

**Architecture:**

1. **Storage.** Vectors are stored in `facts_vec` and `consolidated_vec` virtual tables using `sqlite-vec`'s `vec0` module. Each virtual table stores 384-dimensional float32 vectors indexed by row ID.

2. **On write.** When a new fact or consolidated entry is created with an embedding, the vector is inserted into the corresponding `vec0` virtual table in the same transaction as the row insert.

3. **On query.** KNN search is executed via `sqlite-vec`'s built-in distance functions: `SELECT fact_id, distance FROM facts_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?`. This returns the top-K nearest neighbors. For 100K vectors with 384 dimensions, this completes in <10ms on disk (sqlite-vec uses a flat index with SIMD-accelerated distance computation).

4. **Bounded RAM.** `sqlite-vec` stores vectors on disk. The only in-memory overhead is SQLite's page cache, bounded by `PRAGMA cache_size`. No application-level matrix. The daemon's memory footprint does not grow with fact count.

5. **Distribution.** `sqlite-vec` ships as a single compiled extension (~500KB). It is loaded via `sqlite3.Connection.load_extension()` at startup. The `[memory]` extras group includes the `sqlite-vec` Python package which bundles pre-compiled extensions for all platforms.

**Graceful degradation:** If `sqlite-vec` fails to load (missing extension, platform incompatibility), vector search is unavailable. Retrieval falls back to keyword (FTS5) and entity-graph search only. The fast path degrades to entity-lookup only. This is logged at WARNING level with instructions to run `pip install marianne[memory]`.

### MemoryStore Protocol

```python
from typing import Protocol
from dataclasses import dataclass, field
from pathlib import Path

@dataclass(frozen=True)
class RetrievalAttribution:
    """Why a particular result was included in the snapshot."""
    source: str             # 'semantic', 'keyword', 'entity_graph', 'recency'
    score: float            # Signal-specific score before fusion
    detail: str             # Human-readable: "Matched entity E042 via co_occurs_with edge"

@dataclass(frozen=True)
class AttributedResult:
    """A memory result with its retrieval attribution."""
    content: str
    attributions: list[RetrievalAttribution]
    fused_score: float      # Final score after signal fusion

@dataclass(frozen=True)
class MemorySnapshot:
    """Bounded-size context for decision-making.

    Every result carries retrieval attribution so that conductor
    decisions are inspectable via mzt diagnose. An opaque fused
    score is not a diagnostic — the attribution explains WHY each
    result was retrieved.
    """
    recent_facts: list[AttributedResult]
    relevant_patterns: list[AttributedResult]
    entity_neighbors: list[str]
    past_decisions: list[str]
    venue_conventions: list[str]
    total_token_estimate: int

class MemoryStore(Protocol):
    """Unified memory interface for all Marianne runtime modes."""

    async def retrieve_relevant(
        self, query: str, agent_id: str,
        venue_hash: str | None = None,
        latency_tier: str = "standard",
        max_tokens: int = 2000,
    ) -> MemorySnapshot: ...

    async def record_fact(
        self, content: str, source: str, agent_id: str,
        venue_hash: str | None = None,
        entity_names: list[str] | None = None,
        metadata: dict | None = None,
    ) -> int: ...

    async def record_decision(self, decision: "ConductorDecision", agent_id: str) -> None: ...
    async def update_decision_outcome(self, decision_id: str, outcome: str) -> None: ...
    async def get_venue_knowledge(self, venue_hash: str, agent_id: str) -> "VenueKnowledge": ...
    async def get_user_preferences(self, user_id: str, agent_id: str) -> "UserPreferences": ...
    async def resolve_user_identity(self, alias: str, harness: str, agent_id: str) -> str: ...
    async def update_user_interaction(self, user_id: str, interaction_type: str, agent_id: str, metadata: dict | None = None) -> None: ...
    async def ingest_telemetry_event(self, event: dict, agent_id: str, venue_hash: str | None = None) -> int | None: ...
    async def ingest_telemetry_batch(self, events: list[dict], agent_id: str, venue_hash: str | None = None) -> int: ...
    async def consolidate(self, agent_id: str, venue_hash: str | None = None, max_facts_to_process: int = 500) -> "ConsolidationReport": ...
```

### Retrieval Implementation

Multi-signal retrieval with latency tiers. All retrieval operations run via `asyncio.to_thread()` (NFR-06). Embedding generation for query vectors runs on the dedicated embedding `ThreadPoolExecutor`.

**Fast path (<50ms):** Entity-lookup from `cached_neighbors` JSON column (effectively a hash-table lookup, <1ms) plus `sqlite-vec` KNN query against `facts_vec` (<10ms for 100K vectors on disk). The entity graph is the primary access pattern for the unconscious model's structured queries (instrument name, error code, sheet number). Pre-computed neighbors are refreshed during consolidation. Returns top-5 results with retrieval attribution.

**Standard path (<200ms):** Three parallel queries via `asyncio.gather()` within the thread:
1. `sqlite-vec` KNN over `facts_vec` (<10ms)
2. Keyword search with SQLite FTS5 on the `search_text` column (vocabulary-normalized)
3. Entity graph traversal from query entities via `cached_neighbors` and `relationships`

Results are fused with configurable weights (default: 0.4 semantic, 0.3 keyword, 0.3 entity). Each result carries a `RetrievalAttribution` list documenting which signals matched and their individual scores. Returns top-10 results.

**Comprehensive path (<2s):** Full standard search plus re-ranking, searching the `consolidated` table at all tiers, and including user relationship context. Used for compose conversations, maestro sessions, and diagnostic queries.

**Multi-database retrieval flow (cross-venue):** When `venue_hash` is provided, the standard and comprehensive paths execute against two databases: venue-local first (with 1.2x relevance boost), then global. Results are merged in Python after independent retrieval. Cosine similarity scores from `sqlite-vec` are directly comparable across databases because all embeddings use the same model.

### Vocabulary Normalization

The `search_text` column in `facts` and `consolidated` stores a normalized version of `content` optimized for FTS5 keyword search. Normalization is a **Python-side pre-processing step** — a sequence of regex substitutions applied before the SQL INSERT statement, not an SQLite custom tokenizer.

The normalization regexes strip:
- UUIDs (`[0-9a-f]{8}-[0-9a-f]{4}-...`)
- Timestamps (ISO 8601, Unix epoch floats)
- PIDs and port numbers
- File path prefixes (keeping only the basename)
- Random hashes and hex strings (>8 hex chars)

This prevents execution log noise from polluting keyword search results. The raw `content` is preserved unchanged for display and semantic embedding.

### Consolidation (Dreaming)

The consolidation process extends Legion's dreaming pattern to the structured memory store. It is a **dual-output pipeline**: consolidation produces compressed memory AND training data for the unconscious model. The `TelemetryIngester` and training-data extractor are the same component.

1. **Select unconsolidated facts** — query `facts WHERE consolidated = 0 AND archived = 0` up to `max_facts_to_process`, ordered by timestamp.
2. **Extract entities** — identify named entities using an `EntityExtractor` with deterministic pattern rules: instrument names from `InstrumentRegistry.list_all()`, error codes matching `E\d{3,4}`, sheet numbers matching `sheet[_\s]?\d+`, venue identifiers from workspace paths. Optionally enhance with the unconscious model's classification if available.
3. **Identify patterns** — group facts by entity co-occurrence within a time window.
4. **Write warm-tier patterns** — summarize fact groups, preserve entity links, write to `consolidated` with `tier='warm'`. Mark source facts as `consolidated=1`.
5. **Age existing patterns** — warm patterns older than the aging threshold (default 7 days) compress into cold-tier narratives.
6. **Promote to core** — patterns appearing repeatedly (>10 occurrences) or manually flagged are promoted to `tier='core'` and never consolidated further.
7. **Extract training data** — scan `decisions` for records with known outcomes that have not yet been extracted. For each, generate a `(context_text, decision_text, outcome)` tuple. Write to the `training_examples` table.
8. **Archive old consolidated facts** — query facts where `consolidated = 1 AND archived = 0 AND consolidated_at < (now - retention_period)`. Prune `content` and `embedding`, remove from virtual tables. Preserve metadata, timestamps, entity links.
9. **Refresh entity neighbor caches** — recompute `cached_neighbors` JSON for entities whose relationship set changed.
10. **Produce consolidation report** — output counts and quality metrics.

**Trigger conditions:** Consolidation runs when (a) unconsolidated fact count exceeds a threshold (default 200), (b) the conductor has been idle for more than 5 minutes, (c) a periodic timer fires (default every 6 hours), or (d) explicitly triggered via `mzt memory consolidate`.

**Two-tier consolidation approach:**
1. **Rule-based consolidation (default, zero LLM cost):** Group facts by entity co-occurrence within time windows. Merge co-occurring facts into template-based summaries.
2. **LLM-enhanced consolidation (when instrument available):** Uses the configured consolidation instrument for richer pattern extraction.

### Embedding Thread Pool

The memory system creates a **dedicated `ThreadPoolExecutor`** for embedding work:

- **Max 2 workers:** Embedding is CPU-bound; more workers than cores provides no throughput gain.
- **Isolated from default pool:** SQLite queries, file reads, and other `asyncio.to_thread()` calls continue on the default pool without starvation.
- **Fan-out safety:** During a 50-sheet fan-out, the 50 embedding requests queue behind the 2 workers. Each takes ~10ms, so the full queue drains in ~250ms. SQLite writes proceed in parallel. The baton's event loop is never blocked.

### User Identity Resolution

The `user_aliases` table maps harness-specific identifiers to a canonical `user_id`:
- `tui:emzi` → `user_abc123`
- `discord:nannerl#1234` → `user_abc123`
- `telegram:98765` → `user_abc123`

Resolution is transparent — all `get_user_preferences()` and `update_user_interaction()` calls accept any alias. Manual aliasing via `mzt memory alias "discord:nannerl#1234" user_abc123` merges identities.

### File Layout

```
~/.marianne/
  memory/
    global.db              # Cross-venue: user relationships, aliases, general patterns
    venues/
      {venue_hash}.db      # Per-venue: facts, entities, patterns, decisions
  models/
    all-MiniLM-L6-v2/      # Embedding model (80MB, provisioned via mzt memory init)
    qwen3-4b-q4km.gguf     # Unconscious model (downloaded on first use)
  global-learning.db       # Existing learning store (unchanged, schema v15)
```

### RLF Person Model Mapping

| Marianne Memory | RLF Equivalent | Storage |
|----------------|----------------|---------|
| Core tier consolidated entries | L1 self-model | `consolidated WHERE tier='core'` |
| Warm tier patterns + entity graph | L2 belief store | `consolidated WHERE tier='warm'` + `relationships` |
| Hot tier unconsolidated facts | L3 working memory | `facts WHERE consolidated=0` |
| Cold tier narratives | L4 developmental history | `consolidated WHERE tier='cold'` |
| User relationships table | Relationship memory | `user_relationships` + `user_aliases` |
| Decision records | Experience log | `decisions` |
| Training examples | Developmental feedback | `training_examples` |

### CAM Export Protocol

```python
class CAMExportProtocol(Protocol):
    """Serialize memory state for RLF person model export."""
    async def export_identity(self, agent_id: str) -> dict: ...
    async def export_knowledge(self, agent_id: str, venue_hash: str | None = None) -> dict: ...
    async def export_relationships(self, agent_id: str) -> dict: ...
    async def export_experience(self, agent_id: str, since: float | None = None) -> dict: ...
```

---

## Open Questions

1. **Embedding model upgrade path.** If `all-MiniLM-L6-v2` proves inadequate for orchestration-domain content, how do we migrate? The schema stores `embedding_model` alongside each vector. `sqlite-vec` virtual tables would need recreation with a different dimension if the replacement model uses different dimensionality. Re-embedding is a batch operation during consolidation.

2. **Entity extraction quality.** Consolidation currently relies on deterministic pattern matching. A future improvement uses the unconscious model for entity extraction, but this creates a circular dependency (memory feeds the unconscious, the unconscious feeds memory). Resolution: entity extraction at consolidation time uses the dreaming LLM (remote), not the unconscious (local).

3. **Consolidation cost.** As the fact store grows, LLM-enhanced consolidation becomes more expensive. Mitigation: incremental consolidation (process only new facts), fact-level deduplication, hard limits on consolidation context size. Rule-based consolidation (zero LLM cost) handles the common case.

4. **Graph scalability.** The entity graph is a SQLite-backed adjacency list with pre-computed neighbor caches. For moderate stores (<10K entities, <100K relationships), this is fast. For larger stores, benchmark at scale before committing to the current approach.

5. **Multi-venue memory interaction.** Can a pattern learned in venue A be applied to venue B? The current design stores per-venue facts separately, with global patterns in `global.db`. Cross-venue pattern promotion criteria are undefined — should it be automatic (pattern appears in 3+ venues) or manual?

6. **Concierge memory topology.** If the concierge runs on a different host, does it get its own memory copy or query remotely? This needs resolution before concierge implementation.

7. **Archival retention tuning.** The default 30-day retention period needs empirical validation. The retention period should be tunable per venue via conductor config.

---

## Integration

### Integration with Smart Conductor (S6)

The smart conductor calls `MemoryStore.retrieve_relevant()` after every relevant baton event. The returned `MemorySnapshot` is packed into the `ConductorContext` that the `DecisionSource` receives. Memory retrieval latency must stay within the fast (<50ms) or standard (<200ms) tier to fit the overall 500ms decision budget.

After every smart conductor decision, `MemoryStore.record_decision()` writes the `ConductorDecision` record. When the baton processes the consequence, `update_decision_outcome()` closes the feedback loop. The `MemorySnapshot`'s retrieval attribution enables end-to-end decision inspectability.

### Smart Conductor Guardrails

Memory operations are always budget-free: retrieving memory, recording facts, recording decisions. Only flow-control actions consume the per-job action budget. Escalation is also budget-free.

### Integration with Baton Event Stream (Real-Time Ingestion)

The `TelemetryIngester` subscribes to the baton's `EventBus` as a decoupled subscriber. It does NOT register callbacks directly on `BatonCore`. Subscribed event types: `SheetAttemptResult`, `EscalationNeeded`, `LoopCompleted`, `SheetTriggerFired`.

Facts are available for retrieval immediately — within the same job, within the same concert. The subscriber calls `record_fact()`, which generates an embedding on the dedicated embedding thread pool and writes to SQLite via `asyncio.to_thread()`. The baton's event loop does not block.

### Integration with Compose (S6)

During `mzt compose`, Marianne reads `get_venue_knowledge()` and `get_user_preferences()` to inform the interview. The comprehensive retrieval path (<2s) is acceptable here — compose is interactive, not event-loop-bound. User identity resolution ensures Marianne remembers the composer regardless of which harness they are using. Venue knowledge informs constraint surfacing during the design gate; user preferences inform the interview style and trust-calibrated escalation thresholds.

### Integration with Maestro TUI (S6)

On `mzt maestro` session start, Marianne retrieves recent interactions and venue context for a memory-informed greeting. Uses the comprehensive path. The `MemorySnapshot` provides the TUI with enough context to display relevant venue state, recent decisions, and the user's escalation history without requiring separate queries. During the session, every interaction triggers `update_user_interaction()` to build relationship memory.

### Integration with Concierge (S6)

Each concierge message triggers `resolve_user_identity()` to map the platform-specific handle (Discord username, Telegram ID) to a canonical user, then `get_user_preferences()` for context, and `update_user_interaction()` after response. Latency is bounded by messaging platform latency (seconds), not the memory system. The concierge uses the same identity resolution as the TUI — a single human using both Discord and the TUI accumulates a single relationship profile.

### Integration with Learning Store

The `TelemetryIngester` bridges the learning store to the memory system in **batch mode**: reads `SheetOutcome` records, `PatternRecord` updates, `EscalationDecisionRecord` entries. Distills each into a fact with entity links. The learning store (schema version 15, 13 tables, ~7,300 lines across its Python files) is NOT modified. The ingester is read-only against it.

### Integration with Musicians

Musicians do not directly access the memory store. The conductor provides relevant memory context via the prompt assembly pipeline (preludes, cadenzas). This preserves the separation between the conductor's knowledge and the musician's task.

### Integration with Venue Lifecycle

When a new venue is first observed, the memory system creates a new venue-scoped database. On subsequent jobs, the existing database provides accumulated venue knowledge.

### Integration with Existing Memory Protocol

The memory protocol technique defines L1-L4 layers with word budgets. The structured memory store extends this:
- L1 (identity, 900 words) → core tier consolidated entries
- L2 (profile, 1500 words) → warm tier patterns
- L3 (recent, 1500 words) → hot tier facts
- L4 (archive, unbounded) → cold tier narratives

---

## Risks and Failure Modes

### Risk 1: Retrieval Latency Exceeds Budget
**Likelihood:** Low-Medium. **Impact:** Smart conductor decisions delayed.
**Mitigation:** `sqlite-vec` KNN completes 100K-vector search in <10ms with SIMD. Entity `cached_neighbors` keeps graph lookup under 1ms. Tiered degradation from standard to fast path under load.

### Risk 2: Consolidation Produces Low-Quality Patterns
**Likelihood:** Medium. **Impact:** Bad memory retrieval AND bad training data (dual-output feedback loop).
**Mitigation:** Consolidation output validated against entity graph consistency. Decision quality monitored independently. Heuristic baseline provides a quality floor.

### Risk 3: Entity Graph Becomes Noisy
**Likelihood:** Medium. **Impact:** Graph traversal returns irrelevant results.
**Mitigation:** Deterministic `EntityExtractor` with explicit pattern rules. Entity deduplication and pruning. Pre-computed `cached_neighbors` limits noise propagation.

### Risk 4: Memory State Corruption on Crash
**Likelihood:** Low. **Impact:** Lost facts or corrupted entity graph.
**Mitigation:** SQLite WAL mode. Atomic write pattern. Consolidation is idempotent.

### Risk 5: Embedding Model Quality Insufficient for Domain
**Likelihood:** Medium. **Impact:** Semantic retrieval misses relevant facts.
**Mitigation:** Multi-signal retrieval compensates. Retrieval attribution makes signal contributions visible. Embedding model is swappable via config. The `embedding_model` column enables gradual re-embedding.

### Risk 6: sqlite-vec Extension Availability
**Likelihood:** Low. **Impact:** No vector search.
**Mitigation:** `[memory]` extras group bundles `sqlite-vec`. Graceful degradation to keyword + entity-graph only.

### Risk 7: CAM Integration Requires Schema Changes
**Likelihood:** Low (by design). **Impact:** Migration effort.
**Mitigation:** `CAMExportProtocol` abstraction layer. Versioned migration system. `decisions` table proves multi-tenant model early.

### Risk 8: New Dependencies Violate MN-005
**Likelihood:** Certain. `sentence-transformers` pulls `torch` (~2GB). `sqlite-vec` is ~500KB.
**Impact:** Package size increase.
**Mitigation:** Optional `[memory]` extras group. System is fully functional without — degrades to keyword + entity search.

---

## Outstanding Concerns

All four Critical issues from both runs' reviews (unbounded in-memory NumPy OOM, direct BatonCore coupling, thread pool exhaustion during fan-out, refusal to forget) have been resolved in this merged specification. The three Important issues (offline operation contradiction, CAM query isolation mechanics, cross-venue fallback ambiguity) have also been addressed. The following concerns remain as known limitations, not unresolved criticals:

1. **Cost tracking reliability.** The S6 parent spec acknowledges that "current cost tracking is known to be unreliable." The memory system's `training_examples` table and the loop-level `cost_limit_usd` budgets both depend on accurate cost data. Until cost tracking is remediated (a separate effort per S6), cost-based training data quality may be noisy, and cost-based loop termination may be imprecise. This is a cross-cutting concern tracked at the S6 level, not a memory system design flaw.

2. **Consolidation quality feedback loop.** Consolidation produces both compressed memory AND training data (FR-09, FR-11). If consolidation produces low-quality patterns, the training data is also low-quality, which degrades the unconscious model, which may degrade consolidation quality if the unconscious is used for entity extraction. The mitigation (rule-based consolidation as quality floor, independent decision quality monitoring, entity graph consistency validation) is sound but untested at scale. This risk is documented in Risk 2 above.

3. **Temporal decay vs rare-but-critical patterns.** Recency weighting assumes older memories are less relevant. In orchestration, a rare failure from six months ago may be more critical than a thousand recent successes. The core tier mitigates this for manually or auto-promoted patterns, but automatic promotion criteria (>10 occurrences) may miss rare-but-critical events that never reach that threshold. Further heuristics for critical event detection during ingestion may be needed.

---

## References

- **S6 Specification (Memory Architecture, Memory Interplay sections):** `docs/specs/2026-04-17-baton-primitives-and-marianne-mozart-design.md` — the parent spec defining the six memory requirements, four open research questions, and memory interplay contract.
- **Research Dossier:** `docs/research/2026-04-18-marianne-memory-and-unconscious-research-dossier.md` — synthesized findings from four parallel research streams (CAM, memory systems, local models, integration surface). Convergences, tensions, and emergent insights that directly informed this design.
- **Synthesis Brief:** `workspaces/memory-unconscious-synthesis-workspace/01-synthesis-brief.md` — ground-truth verification, merge strategy, and resolution of conflicts between two independent design runs.
- **Run 1 Draft Spec:** `workspaces/archive/marianne-memory-unconscious-run1-2026-04-19/04-spec-memory-draft.md` — first independent design run. Accurate schema version (v15), clean prior-art brief. Reviewed as reject-and-redesign due to in-memory NumPy OOM, intra-job memory blindness, missing training data pipeline, and SQLite vector math impossibility.
- **Run 2 Draft Spec:** `workspaces/marianne-memory-and-unconscious-research-workspace/04-spec-memory-draft.md` — second independent design run. Introduced S6 Requirements section, dual-output consolidation pipeline, training data extraction, user alias system, vocabulary normalization, and retrieval attribution. Reviewed as reject-and-redesign due to same NumPy OOM and BatonCore coupling issues.
- **Run 1 Review:** `workspaces/archive/marianne-memory-unconscious-run1-2026-04-19/05-review-memory.md` — identified intra-job memory blindness, missing training data pipeline, SQLite vector math impossibility, event loop blocking.
- **Run 2 Review:** `workspaces/marianne-memory-and-unconscious-research-workspace/05-review-memory.md` — identified unbounded NumPy OOM, direct BatonCore coupling, thread pool exhaustion on fan-out, refusal to forget.
- **Promoted Spec (Run 1, revised):** `docs/specs/2026-04-18-marianne-memory-system-design.md` — the backbone of this merged specification. Incorporated all critical fixes from both reviews: sqlite-vec, EventBus, archival lifecycle, dedicated embedding thread pool.
- **Companion Spec (Unconscious):** `docs/specs/2026-04-19-marianne-unconscious-local-model-design.md` — the parallel spec defining the DecisionSource Protocol and local model layer that consumes memory via `MemorySnapshot`.
- **Existing Learning Store:** `src/marianne/learning/store/` — 16 Python files, 7,279 lines, 13 CREATE TABLE statements, schema version 15. Preserved and bridged, not replaced.
- **Legion Identity Model:** `~/Projects/legion/legion_identity.md` — working example of hot/warm/cold/core tiered memory with dreaming consolidation. Lives outside any project repository so Legion persists across all projects, not just this one.
- **Memory Protocol Technique:** `plugins/marianne/techniques/memory-protocol.md` — four-layer (L1-L4) memory protocol with word budgets, the structural analogue to RLF's person model.
