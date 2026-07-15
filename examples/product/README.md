# Product Examples

These scores orchestrate business workflows that combine structured analysis, parallel expert perspectives, and quality-controlled synthesis. Each demonstrates how multiple AI agents can collaborate on tasks where single-agent approaches produce shallow or inconsistent results: hiring decisions benefit from systematic rubric application across candidates, legal contracts need cross-referenced sections that don't contradict, invoice analysis catches what single-pass reviews miss, and marketing content stays on-brand when generated in parallel from a shared strategy.

## Scores

| Score | What It Does | Sheets | Patterns Used | Time | Cost |
|-------|-------------|--------|--------------|------|------|
| [candidate-screening](candidate-screening.yaml) | Screen job candidates with parallel evaluation against weighted criteria | 5 | Fan-out + Synthesis | ~3m | ~$0.15 |
| [contract-generator](contract-generator.yaml) | Generate multi-section legal contracts with cross-referenced assembly | 5 | Fan-out + Synthesis, Prefabrication, Barn Raising | ~3m | ~$0.50 |
| [invoice-analysis](invoice-analysis.yaml) | Analyze invoices from financial, compliance, and fraud perspectives | 5 | Fan-out + Synthesis, Mission Command | ~3-5m | ~$0.15-0.25 |
| [marketing-content](marketing-content.yaml) | Generate coordinated content across blog, social, email, and landing page | 6 | Fan-out + Synthesis, Barn Raising | ~4m | ~$0.30 |

### candidate-screening.yaml

Evaluates job candidates against structured hiring criteria. Movement 1 parses job requirements into a weighted rubric with per-criterion scoring guides. Movement 2 fans out to evaluate each candidate in parallel — three independent voices analyze three candidates against all criteria, citing resume evidence and calculating weighted scores. Movement 3 synthesizes evaluations into comparative ranking with interview recommendations. This shows how Marianne prevents evaluation inconsistency: all reviewers score against the same rubric, and the synthesis catches scoring calibration issues that would slip past sequential reviews.

### contract-generator.yaml

Drafts complete service agreements from creative briefs. Movement 1 analyzes the brief and extracts structured terms (parties, obligations, milestones, risk areas). Movement 2 fans out to three parallel drafting teams: definitions/parties/recitals, obligations/deliverables/milestones, and terms/liability/disputes. Each team works from the shared analysis (Prefabrication pattern), following consistent drafting standards (Barn Raising pattern). Movement 3 assembles sections, validates cross-references, and checks that every identified term has a definition and every obligation appears in the final contract. Demonstrates how contract generation scales beyond monolithic prompts.

### invoice-analysis.yaml

Performs multi-perspective invoice analysis. Movement 1 parses invoice data and recalculates every line item to detect discrepancies. Movement 2 fans out to three expert analysts operating with Mission Command intent: financial auditor verifies calculations and discount application, compliance analyst checks contract terms and SLA credits, forensic accountant hunts for duplicate charges and anomalies. Movement 3 consolidates findings into severity-ranked issues with specific action items. The example invoice contains intentional anomalies (duplicate data transfer charges, missing discount) that single-pass analysis typically misses but multi-perspective review catches.

### marketing-content.yaml

Generates coordinated multi-channel marketing from a single brief. Movement 1 produces content strategy with pain points, key messages, tone guide, and channel-specific direction. Movement 2 fans out to four content creators: blog post (1200-1800 words with before/after examples), social media (LinkedIn posts + Twitter thread), email nurture sequence (3 emails with A/B subject lines), and landing page (hero through FAQ). Movement 3 audits all content for brand voice consistency, message presence, CTA alignment, and terminology consistency across channels. Shows how parallel generation with shared strategy prevents the drift that happens when each channel is prompted separately.

## Quick Start

```bash
mzt start
mzt run examples/product/candidate-screening.yaml
mzt status candidate-screen --watch
```

After the score completes (~3 minutes), read the output:

```bash
cat ../workspaces/candidate-screen/03-ranking.md
```

You'll see three candidates evaluated against consistent criteria with comparative ranking and interview recommendations.

## Adapting to Your Project

### candidate-screening.yaml
- Replace `prompt.variables.role` with your job details (title, team, level, location)
- Update `requirements.must_have` and `requirements.nice_to_have` lists
- Replace the three candidate resumes in `prompt.variables.candidates` with your actual candidate data
- Adjust evaluation criteria weighting in Movement 1 prompt (must-haves get 7-10, nice-to-haves get 3-6)
- Scale candidate count by changing `sheet.total_items` and `sheet.fan_out.2` to match your applicant pool

### contract-generator.yaml
- Edit `prompt.variables` to change contract type, parties, scope, timeline, budget, and special terms
- Modify Movement 2 section assignments if your contract needs different structural divisions
- Add regulatory requirements to `special_terms` (GDPR, SOC2, industry-specific compliance)
- Adjust Movement 3 assembly logic for contracts requiring additional sections (warranties, service levels, data processing addenda)

### invoice-analysis.yaml
- Replace `prompt.variables.invoice` with your vendor invoice details
- Update `contract_terms` to match your vendor agreements and payment terms
- Modify the three Movement 2 voice prompts to emphasize your organization's priority concerns (e.g., add PCI compliance checks, subscription billing validation, multi-currency handling)
- Add custom validation rules in the compliance voice for industry-specific requirements
- Expand the anomaly detection checklist for your expense patterns

### marketing-content.yaml
- Update `prompt.variables.company` with your brand name, industry, tagline, voice, and colors
- Replace `prompt.variables.product` with your product details, features, and pricing
- Modify `prompt.variables.campaign` for your goals, audience, CTA, landing URL, and differentiator
- Adjust Movement 2 channel assignments (add video scripts, remove email, etc.)
- Customize Movement 3 brand audit criteria to check your specific style guide rules

**Prerequisites**: Scores use `claude-code` with `codex-cli` fallbacks. Configure at least one of those CLIs before running them.

## Patterns Demonstrated

These scores demonstrate several Rosetta orchestration patterns:

- **[Fan-out + Synthesis](../../docs/rosetta/fan-out-synthesis.md)**: All four scores use this foundational pattern — parallel independent work followed by integrative synthesis. Candidate screening fans out evaluation across candidates, contract generation fans out section drafting, invoice analysis fans out expert perspectives, marketing content fans out channels.

- **[Prefabrication](../../docs/rosetta/prefabrication.md)**: contract-generator uses this to establish shared contracts before parallel work. Movement 1's term analysis becomes the interface contract that guides all three drafting teams in Movement 2.

- **[Barn Raising](../../docs/rosetta/barn-raising.md)**: contract-generator and marketing-content use this to maintain consistency across parallel work. Drafting standards ensure contract sections integrate cleanly; content strategy and brand voice prevent channel drift.

- **[Mission Command](../../docs/rosetta/mission-command.md)**: invoice-analysis demonstrates intent-based autonomy. Each expert analyst in Movement 2 receives outcome goals (verify accuracy, check compliance, find anomalies) rather than prescriptive procedures, allowing each to bring domain expertise without rigid coordination.

Full pattern catalog: [Rosetta Corpus Pattern Index](../../docs/rosetta/INDEX.md)
