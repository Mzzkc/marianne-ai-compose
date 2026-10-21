# Research Examples

These scores orchestrate complex research, analysis, and knowledge synthesis workflows that transform raw information into structured insights. They cover academic research protocols, strategic business analysis, ML dataset curation, long-form content creation, and meta-level investigation of information architecture. Use them when you need rigorous, multi-perspective analysis with quality gates and evidence-based validation.

## Scores

| Score | What It Does | Sheets | Patterns Used | Time | Cost |
|-------|-------------|--------|--------------|------|------|
| [parallel-research-fanout](parallel-research-fanout.yaml) | Multi-source research synthesis across academic, industry, and patent domains | 5 | Fan-out + Synthesis, Movements | ~15-25m | ~$0.50-$2 |
| [systematic-literature-review](systematic-literature-review.yaml) | PRISMA 2020-compliant systematic review with dual-reviewer screening and quality assessment | 8 | Succession Pipeline | 4-8h | ~$5-$15 |
| [strategic-plan](strategic-plan.yaml) | Comprehensive strategic planning using PESTEL, Porter's Five Forces, and SWOT with SMART goal validation | 8 | Succession Pipeline | 4-8h | ~$5-$15 |
| [training-data-curation](training-data-curation.yaml) | ML training dataset curation from schema design through annotation, adjudication, and documentation | 7 | Succession Pipeline | 3-6h | ~$4-$10 |
| [nonfiction-book](nonfiction-book.yaml) | Non-fiction book authoring using the Snowflake Method, from one-sentence premise to polished manuscript | 8 | Succession Pipeline | 6-12h | ~$8-$20 |
| [context-engineering-lab](context-engineering-lab.yaml) | Meta-investigation of information architecture using five analytical lenses with dual-LLM preprocessing | 22 | Fan-out + Synthesis | 4-8h | ~$6-$18 |
| [research-agent](research-agent.yaml) | Multi-source deep research pipeline investigating any topic through independent source investigation | 9 | Source Triangulation + Immune Cascade + Relay Zone | ~20-40m | ~$1-$4 |

### parallel-research-fanout

Conducts evidence-based research by executing parallel searches across three independent domains (academic literature, industry reports, patents), then synthesizing findings into a coherent analysis that identifies convergent evidence, divergent claims, and research gaps. Produces a setup document, three domain-specific finding reports, and a cross-source synthesis with convergence scoring.

### systematic-literature-review

Orchestrates a rigorous academic literature review following PRISMA 2020 guidelines. Develops a research protocol, executes database searches with deduplication tracking, conducts dual-reviewer title/abstract screening with inter-rater agreement calculation (Cohen's kappa), performs full-text eligibility assessment, extracts standardized data, assesses study quality using your chosen framework (Cochrane, GRADE, Newcastle-Ottawa), synthesizes findings narratively or quantitatively, and produces a complete review with PRISMA checklist verification. Every phase has substantive quality gates ensuring reproducibility and methodological rigor.

### strategic-plan

Develops a comprehensive organizational strategy by applying three complementary analytical frameworks. Conducts PESTEL macro-environmental analysis and Porter's Five Forces competitive analysis in parallel, synthesizes findings into a SWOT position assessment, generates and evaluates strategic options, formulates SMART goals with implementation roadmap, and adds risk assessment with contingency planning. Produces eight interconnected documents that build from environmental intelligence to actionable implementation plans.

### training-data-curation

Creates publication-ready ML training datasets with documented quality metrics. Designs an annotation schema, develops a statistically sound sampling plan, conducts pilot annotation to test inter-annotator agreement (IAA), executes full dual-annotator labeling, adjudicates disagreements to create a gold standard, computes quality metrics and error analysis, and produces a complete Datasheet for Datasets following Gebru et al. framework. IAA thresholds gate progression from pilot to production, ensuring annotation quality before scaling.

### nonfiction-book

Transforms a book concept into a complete manuscript using the Snowflake Method's progressive elaboration approach. Starts with a one-sentence premise, expands to a synopsis with narrative arc, develops a detailed chapter outline, creates an entity bible for consistency (characters, organizations, concepts, sources), drafts all chapters, conducts systematic consistency review across the manuscript, performs structural revision based on audit findings, and produces final polish with assembled manuscript and bibliography. Word count gates ensure each phase achieves target depth.

### context-engineering-lab

Meta-level investigation of how projects should architect their information environment for AI agents and developers. Applies five analytical lenses (RAG/vector search, Knowledge Graphs, Dual-LLM preprocessing, Meta-cognitive, Experiential) to examine your project's information needs. Each analysis phase is preceded by a preprocessing phase that curates context (inspired by dual-LLM architectures where one model prepares enriched input before the conscious responder engages). Produces a working prototype specification for organizational context architecture, reviewed against all five lenses for completeness.

### research-agent

Multi-source deep research pipeline that investigates any topic through independent source investigation. Applies three composed patterns: Source Triangulation (extract → investigate ×3 → triangulate), Immune Cascade (cheap broad sweep narrows scope before expensive targeted investigation), and Relay Zone (context compression between stages). A broad scan identifies sources, relay+triage compresses and prioritizes targets, three parallel deep dives investigate independently, then triangulation cross-references findings and produces verdicts. A final learning stage documents methodology for future runs. Produces a comprehensive research report with verified findings, confidence ratings, and methodological transparency.

## Quick Start

```bash
# Start the conductor
mzt start

# For a fast introduction (15-25 minutes), try parallel research:
mzt run examples/research/parallel-research-fanout.yaml
mzt status fanout-research --watch

# For production-grade research workflows, try the systematic literature review:
mzt run examples/research/systematic-literature-review.yaml
mzt status systematic-literature-review --watch

# For strategic business analysis:
mzt run examples/research/strategic-plan.yaml
mzt status strategic-plan --watch
```

## Adapting to Your Project

All scores use `[CHANGE THIS: ...]` markers to indicate required customization points. Most provide realistic working defaults that produce interesting output even without customization.

### parallel-research-fanout

- **research_topic**: Your research question (default: AI code generation tools impact on productivity)
- **search_domains**: Three source types to search (default: Academic, Industry, Patents)
- **workspace**: Where outputs should be saved (default: `../../workspaces/fanout-workspace`)

No prerequisites. Runs immediately with defaults.

### systematic-literature-review

- **PICO framework**: Population, Intervention, Comparison, Outcome (defines research scope)
- **date_range**: Publication date constraints (e.g., "2010-2024")
- **databases**: Which databases to search (e.g., "PubMed, PsycINFO, Cochrane Library")
- **study_types**: Eligible study designs (e.g., "Randomized controlled trials")
- **quality_framework**: Risk of bias tool to use (e.g., "Cochrane Risk of Bias 2.0")

No tool prerequisites, but you may want to customize IAA thresholds based on your field's conventions.

### strategic-plan

- **organization_name**: Your organization or business unit
- **industry**: Primary industry/sector
- **geographic_scope**: Markets you operate in or target
- **planning_horizon**: Time frame for the strategy (e.g., "3 years")
- **current_situation**: Brief context (2-3 sentences)

Framework knowledge is baked into the prompts — you don't need to be familiar with PESTEL or Porter's Five Forces.

### training-data-curation

- **task_type**: Annotation task (e.g., "sentiment classification", "named entity recognition")
- **domain**: Subject area (e.g., "medical", "legal", "social media")
- **labels**: Label set for annotation (e.g., "positive, negative, neutral")
- **target_sample_size**: How many examples to annotate (e.g., "500")
- **iaa_threshold**: Minimum inter-annotator agreement to proceed (e.g., "0.80" for Cohen's kappa)

No annotation tools required — the score simulates dual-annotator workflow and produces structured outputs you can use with any annotation platform.

### nonfiction-book

- **book_title**: Working title for your book
- **book_topic**: Subject matter (e.g., "effective remote team management")
- **target_audience**: Who should read this (e.g., "engineering managers at Series A startups")
- **unique_angle**: What makes your perspective different
- **chapter_count**: How many chapters (default: 8-12)

The score provides realistic defaults (a book about context engineering for AI systems) that work as-is if you want to see the full pipeline before customizing.

### context-engineering-lab

- **project**: Description of your project/organization (what it does, what information it manages)
- **workspace**: Where investigation outputs should be saved

This score investigates YOUR project's information architecture needs. Point it at any codebase, organization, or knowledge domain.

### research-agent

- **topic**: The research question or topic to investigate (default: a sample topic on AI code generation)
- **verification_criteria**: What counts as evidence for or against claims (default: methodological rigor standards)
- **workspace**: Where outputs should be saved (default: `../workspaces/research-agent`)

No prerequisites. Runs immediately with defaults.

## Patterns Demonstrated

These scores demonstrate key orchestration patterns from the [Rosetta corpus](../../scores/rosetta-corpus/INDEX.md):

- **Fan-out + Synthesis**: Parallel independent work followed by cross-perspective integration (parallel-research-fanout, context-engineering-lab)
- **Succession Pipeline**: Each phase produces a categorically different artifact that becomes the next phase's input substrate (systematic-literature-review, strategic-plan, training-data-curation, nonfiction-book)
- **Movements**: Named stages for clear progress tracking in status output (all scores)
- **Quality Gates**: Evidence-based validation that work meets standards before proceeding (systematic-literature-review's IAA thresholds, training-data-curation's pilot gates)
- **Dual-perspective validation**: Multi-reviewer simulation for consensus building (systematic-literature-review's dual-reviewer screening, training-data-curation's dual-annotator workflow)
- **Progressive elaboration**: Starting simple and adding complexity through structured phases (nonfiction-book's Snowflake Method)

The Succession Pipeline pattern is especially prominent in this category: each score transforms the workspace substrate from one kind of artifact to another (protocol → search results → screening decisions → extraction forms → quality ratings → synthesis → final report). This is distinct from iteration (refining the same artifact) or fan-out (same method, different data).
