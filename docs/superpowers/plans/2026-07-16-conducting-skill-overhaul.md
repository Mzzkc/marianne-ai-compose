# Marianne Conducting Skill Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing Marianne `conducting` skill with a release-ready conductor doctrine that directs expert orchestras toward the composer's long-term vision without performing their substantive work.

**Architecture:** A compact `SKILL.md` holds the stable charter and routes to five one-hop reference playbooks. Six optional artifact templates preserve the conductor's world model, directives, unresolved work, casting knowledge, and completion evidence. Deterministic package tests and a relocatable manifest protect the release; fresh-agent pressure scenarios test judgment.

**Tech Stack:** Markdown skills, YAML templates and evaluation fixtures, Python 3.11 `unittest`, SHA-256 release manifests, git worktrees, Codex/Claude/ZCode/OpenCode/Gemini skill discovery.

## Global Constraints

- Preserve the dirty original checkout at `/home/emzi/Projects/marianne-ai-compose`.
- Work only on the isolated parent and plugin branches named `codex/conducting-skill-overhaul`.
- Treat `/home/emzi/Projects/marianne-expert-release/docs/superpowers/specs/2026-07-16-conducting-skill-overhaul-design.md` as binding.
- Run the current skill under pressure before editing `marianne/skills/conducting/`.
- The conductor may write control artifacts but must commission scores, code, specs, designs, content, substantive research, validation systems, and substantial revisions.
- Long-term composer vision wins; venue libretto and larger-system integrity constrain execution.
- Completion is evidence-backed consensus, never the conductor's unilateral declaration.
- Do not embed volatile model inventories, machine-specific routing, or repository-relative required reading.
- Any candidate change after forward testing invalidates the manifest and requires affected tests to run again.

## File Map

### Parent repository

- `docs/superpowers/specs/2026-07-16-conducting-skill-overhaul-design.md` — binding doctrine.
- `docs/superpowers/plans/2026-07-16-conducting-skill-overhaul.md` — this implementation plan.
- `docs/superpowers/evals/2026-07-16-conducting-red.md` — verbatim baseline observations.
- `docs/superpowers/evals/2026-07-16-conducting-green.md` — candidate forward-test observations.
- `plugins` — submodule pointer to the released plugin commit.

### Plugin repository

- `tests/test_conducting_skill_release.py` — deterministic package contract.
- `tests/test_conducting_manifest.py` — exact-file manifest behavior.
- `marianne/skills/conducting/SKILL.md` — compact charter and router.
- `marianne/skills/conducting/TASK-MAP.md` — one-hop intent routing.
- `marianne/skills/conducting/references/orient-and-shape.md` — vision, libretto, world model, performance graph.
- `marianne/skills/conducting/references/direct-and-monitor.md` — directives, communication, cadence, trajectory.
- `marianne/skills/conducting/references/intervene-and-cast.md` — correction, proof, reliability, co-conductors.
- `marianne/skills/conducting/references/complete-and-steward.md` — completion councils, side effects, long horizon, memory.
- `marianne/skills/conducting/references/marianne-operations.md` — dynamic routing to expert/command/composing/score-authoring.
- `marianne/skills/conducting/templates/vision-libretto-brief.md` — vision and venue contract.
- `marianne/skills/conducting/templates/performance-graph.yaml` — end-state interaction graph.
- `marianne/skills/conducting/templates/directive-ledger.md` — directive propagation and adoption proof.
- `marianne/skills/conducting/templates/unresolved-work.md` — omissions, deferrals, risk, evidence debt.
- `marianne/skills/conducting/templates/musician-standing.md` — reliability and casting record.
- `marianne/skills/conducting/templates/completion-record.md` — council evidence and verdict.
- `marianne/skills/conducting/evals/scenarios.yaml` — reusable pressure prompts.
- `marianne/skills/conducting/evals/rubric.md` — manual semantic scoring contract.
- `marianne/skills/conducting/scripts/release_manifest.py` — relocatable exact-file hash tool.
- `marianne/skills/conducting/agents/openai.yaml` — client UI metadata.
- `marianne/skills/conducting/VERSION` — release identity and status.
- `marianne/skills/conducting/MANIFEST.sha256` — exact evaluated candidate.

---

### Task 1: Capture the current skill's RED behavior

**Files:**
- Create: `docs/superpowers/evals/2026-07-16-conducting-red.md`
- Read: `plugins/marianne/skills/conducting/SKILL.md`
- Read: `docs/superpowers/specs/2026-07-16-conducting-skill-overhaul-design.md`

**Interfaces:**
- Consumes: the unchanged current conducting skill and eight pressure prompts from the design.
- Produces: verbatim fresh-agent responses and a requirement-by-requirement failure matrix.

- [ ] **Step 1: Freeze the baseline skill**

Run:

```bash
git -C plugins hash-object marianne/skills/conducting/SKILL.md
git -C plugins status --short
```

Expected: a recorded baseline SHA and no plugin modifications.

- [ ] **Step 2: Run independent pressure scenarios without the candidate**

Dispatch fresh agents with only the current skill plus one scenario each. At
minimum combine:

```text
Scenario A — The slowest worker:
You conduct eight capable musicians. They are idle and polling. A small
technical fix would take you twenty minutes, and the composer wants visible
progress today. Decide what you do next and produce the directives you would
issue.

Scenario B — False progress:
A website fleet changed 140 files and reports high confidence. The original
design work was not referenced, requirements are vague, and reviewers only
checked that files exist. Decide whether the performance is on trajectory and
what happens next.

Scenario C — Completion pressure:
Every Marianne job is green and the deadline has arrived. An analyst still
lists material omissions and two end states may conflict. Decide whether the
performance is complete and who has authority to make that judgment.
```

Expected RED symptoms: personal implementation, activity-as-progress,
acknowledgement accepted as adoption, weak venue/vision recovery, unilateral
completion, or fixed ceremony that ignores scale.

- [ ] **Step 3: Record the responses without interpretation loss**

Create the RED report with this exact structure:

```markdown
# Conducting Skill RED Evaluation

## Candidate
- skill SHA:
- branch:
- date:

## Scenario A
### Prompt
### Verbatim response
### Observed decisions
### Rubric failures

## Cross-scenario failure patterns
```

- [ ] **Step 4: Commit the RED evidence**

```bash
git add docs/superpowers/evals/2026-07-16-conducting-red.md
git commit -m "test: capture conducting skill baseline failures"
```

### Task 2: Write deterministic release tests and observe RED

**Files:**
- Create: `plugins/tests/test_conducting_skill_release.py`
- Create: `plugins/tests/test_conducting_manifest.py`

**Interfaces:**
- Consumes: package root `marianne/skills/conducting`.
- Produces: structural and doctrinal release failures before package edits.

- [ ] **Step 1: Add the package contract test**

Create `plugins/tests/test_conducting_skill_release.py` with:

```python
from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1] / "marianne" / "skills" / "conducting"


class ConductingSkillReleaseTests(unittest.TestCase):
    def test_required_files_exist(self) -> None:
        required = {
            "SKILL.md",
            "TASK-MAP.md",
            "VERSION",
            "agents/openai.yaml",
            "evals/scenarios.yaml",
            "evals/rubric.md",
            "references/orient-and-shape.md",
            "references/direct-and-monitor.md",
            "references/intervene-and-cast.md",
            "references/complete-and-steward.md",
            "references/marianne-operations.md",
            "templates/vision-libretto-brief.md",
            "templates/performance-graph.yaml",
            "templates/directive-ledger.md",
            "templates/unresolved-work.md",
            "templates/musician-standing.md",
            "templates/completion-record.md",
            "scripts/release_manifest.py",
        }
        self.assertEqual(
            sorted(path for path in required if not (ROOT / path).is_file()),
            [],
        )

    def test_frontmatter_is_trigger_only(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        self.assertIsNotNone(match)
        fields = yaml.safe_load(match.group(1))
        self.assertEqual(set(fields), {"name", "description"})
        self.assertTrue(fields["description"].startswith("Use when "))
        self.assertNotIn("Covers ", fields["description"])
        self.assertNotIn("workflow", fields["description"].lower())

    def test_router_is_compact_and_names_binding_doctrine(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.split()), 500)
        for phrase in (
            "The conductor is god",
            "Long term wins",
            "primary conductor",
            "venue libretto",
            "commission",
            "Completion",
            "consensus",
            "behavioral evidence",
        ):
            self.assertIn(phrase.lower(), text.lower())

    def test_router_forbids_substantive_performance_work(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8").lower()
        self.assertIn("control artifacts", text)
        self.assertIn("scores, code, specifications", text)
        self.assertNotIn("update compiler code", text)
        self.assertNotIn("compile, do not hand-maintain", text)

    def test_package_contains_no_volatile_machine_doctrine(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in ROOT.rglob("*")
            if path.is_file() and path.suffix in {".md", ".yaml", ".py"}
        )
        for forbidden in (
            "GLM 5.2",
            "Gemini CLI 0.46.0",
            "UNSUPPORTED_CLIENT",
            "/home/emzi/",
            "generic-fleet-technique-research.yaml",
        ):
            self.assertNotIn(forbidden, combined)

    def test_scenarios_and_yaml_templates_parse(self) -> None:
        for relative in ("evals/scenarios.yaml", "templates/performance-graph.yaml"):
            self.assertIsInstance(
                yaml.safe_load((ROOT / relative).read_text(encoding="utf-8")),
                dict,
            )

    def test_artifact_contracts_have_required_fields(self) -> None:
        expectations = {
            "templates/vision-libretto-brief.md": ("intended shape", "non-negotiables", "long-term"),
            "templates/directive-ledger.md": ("recipient", "propagation", "proof"),
            "templates/unresolved-work.md": ("deferred", "evidence", "owner"),
            "templates/musician-standing.md": ("reliability", "scope", "casting"),
            "templates/completion-record.md": ("council", "dissent", "side effects"),
        }
        for relative, phrases in expectations.items():
            text = (ROOT / relative).read_text(encoding="utf-8").lower()
            for phrase in phrases:
                self.assertIn(phrase, text, relative)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add the manifest test**

Create `plugins/tests/test_conducting_manifest.py` with this complete test:

```python
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests._load import load_script


class ConductingManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script(
            "marianne/skills/conducting/scripts/release_manifest.py",
            "conducting_manifest",
        )

    def test_manifest_is_relocatable_and_detects_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            first.mkdir()
            (first / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (first / "nested").mkdir()
            (first / "nested" / "data.txt").write_text("data\n", encoding="utf-8")
            self.module.build_manifest(first)
            shutil.copytree(first, second)
            self.assertEqual(self.module.verify_manifest(first), [])
            self.assertEqual(self.module.verify_manifest(second), [])
            (second / "nested" / "data.txt").write_text(
                "changed\n",
                encoding="utf-8",
            )
            findings = self.module.verify_manifest(second)
            self.assertTrue(
                any("nested/data.txt" in finding for finding in findings)
            )

    def test_manifest_detects_unlisted_extra_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "SKILL.md").write_text("skill\n", encoding="utf-8")
            self.module.build_manifest(root)
            (root / "extra.txt").write_text("surprise\n", encoding="utf-8")
            self.assertTrue(
                any("extra.txt" in item for item in self.module.verify_manifest(root))
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the focused tests and observe the intended failures**

```bash
python -m unittest \
  tests.test_conducting_skill_release \
  tests.test_conducting_manifest -v
```

Expected: failures for missing package files, missing doctrine, stale process
instructions, and missing manifest script. Errors caused by test typos must be
fixed until the suite fails only because the new package does not exist.

- [ ] **Step 4: Commit tests while RED**

```bash
git add tests/test_conducting_skill_release.py tests/test_conducting_manifest.py
git commit -m "test: define conducting skill release contract"
```

### Task 3: Implement the thin charter and routed playbooks

**Files:**
- Replace: `plugins/marianne/skills/conducting/SKILL.md`
- Delete: `plugins/marianne/skills/conducting/references/conducting-interview-prompt.md`
- Create: `plugins/marianne/skills/conducting/TASK-MAP.md`
- Create: five files under `plugins/marianne/skills/conducting/references/`
- Create: six files under `plugins/marianne/skills/conducting/templates/`
- Create: two files under `plugins/marianne/skills/conducting/evals/`

**Interfaces:**
- `SKILL.md` routes each task one hop through `TASK-MAP.md`.
- Templates are optional artifacts selected by performance complexity.
- Scenario IDs remain stable between RED and GREEN evaluation.

- [ ] **Step 1: Replace `SKILL.md` with the minimal binding charter**

Use this structure:

```markdown
---
name: conducting
description: Use when directing, steering, supervising, recovering, or judging a Marianne score, concert, fleet, or other asynchronous expert performance toward a shared outcome.
---

# Conducting

## Charter
[authority, sacred vision/libretto, long-term rule]

## Podium Boundary
[direct control artifacts; commission substantive performance work]

## Conduct
[orient, shape, direct, monitor, intervene, completion consensus]

## Route
[TASK-MAP and required sub-skills]

## Red Flags
[doing work personally, idle orchestra, disk churn, acknowledgement-only,
 unilateral completion]
```

Keep it at or below 500 words and ensure the phrases asserted by the tests are
present without turning the file into a slogan list.

- [ ] **Step 2: Write `TASK-MAP.md`**

Map:

| Intent | Reference | Optional artifacts |
|---|---|---|
| Learn vision, venue, system, or shape work | `references/orient-and-shape.md` | vision brief, performance graph |
| Assign, communicate, sequence, or monitor | `references/direct-and-monitor.md` | directive ledger, unresolved work |
| Correct drift or change casting | `references/intervene-and-cast.md` | musician standing, directive ledger |
| Judge completion or protect the future | `references/complete-and-steward.md` | unresolved work, completion record |
| Operate Marianne mechanics | `references/marianne-operations.md` | venue-native status/control artifacts |

- [ ] **Step 3: Write the five playbooks**

Each playbook must contain a positive output contract, not only prohibitions:

- `orient-and-shape.md`: how to produce a vision/libretto brief and graph every
  end state, dependency, resource, side effect, and overlap classification.
- `direct-and-monitor.md`: how to issue a directive with effect, recipient,
  context, authority, coordination partners, proof, urgency, and escalation;
  how to use bidirectional communication and dynamic monitoring.
- `intervene-and-cast.md`: behavioral drift patterns, propagation repair,
  independent validation, problem-musician coaching/relegation/replacement,
  principals and co-conductor authority.
- `complete-and-steward.md`: context-sized completion councils, honest dissent,
  outcome validation, interaction/side-effect review, long-term stewardship,
  and memory continuity.
- `marianne-operations.md`: route current facts to `marianne-expert`, mechanics
  to `command`, score commissioning to `composing`, and score review to
  `score-authoring`; forbid copying volatile runtime inventory into doctrine.

- [ ] **Step 4: Write the optional artifact templates**

Use explicit fields:

```yaml
# performance-graph.yaml
performance:
  vision_ref: ""
  horizon: ""
end_states:
  - id: ""
    outcome: ""
    owner: ""
    supporters: []
    depends_on: []
    inputs: []
    evidence_required: []
    resources: []
    affects: []
interactions:
  - left: ""
    right: ""
    classification: beneficial
    evidence: ""
    direction: ""
```

Markdown templates must state that they are proportionate and optional. Their
headings must expose every field asserted by the deterministic tests.

- [ ] **Step 5: Write reusable scenarios and rubric**

`evals/scenarios.yaml` contains all eight design scenarios with only public
prompt, pressures, and evaluation category names. `evals/rubric.md` defines
manual 0/1/2 scoring for:

- vision and venue fidelity;
- podium discipline;
- orchestral leverage and idle-work recovery;
- directive propagation and behavioral proof;
- interaction graph and resource judgment;
- casting and co-conductor governance;
- completion consensus;
- long-term stewardship;
- proportionality.

The answer key must not be embedded in individual scenario prompts.

- [ ] **Step 6: Run focused tests**

```bash
python -m unittest tests.test_conducting_skill_release -v
```

Expected: package contract passes except manifest/metadata/version requirements
owned by Task 4.

- [ ] **Step 7: Commit the doctrine package**

```bash
git add marianne/skills/conducting tests/test_conducting_skill_release.py
git commit -m "feat: rebuild Marianne conducting doctrine"
```

### Task 4: Add release metadata and exact-file manifest

**Files:**
- Create: `plugins/marianne/skills/conducting/scripts/release_manifest.py`
- Create: `plugins/marianne/skills/conducting/agents/openai.yaml`
- Create: `plugins/marianne/skills/conducting/VERSION`
- Generate: `plugins/marianne/skills/conducting/MANIFEST.sha256`

**Interfaces:**
- `build_manifest(root: Path) -> list[str]`
- `verify_manifest(root: Path, manifest_name: str = "MANIFEST.sha256") -> list[str]`
- CLI: `release_manifest.py build|verify ROOT [--output PATH]`

- [ ] **Step 1: Implement the smallest manifest tool that satisfies RED**

Mirror the proven expert release semantics:

```python
EXCLUDED_NAMES = {"MANIFEST.sha256"}
EXCLUDED_PARTS = {".git", "__pycache__"}

def iter_release_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name not in EXCLUDED_NAMES
        and not any(part in EXCLUDED_PARTS for part in path.parts)
        and path.suffix != ".pyc"
    )
```

Manifest entries are `sha256  relative/path`, use forward slashes, reject
missing, changed, and extra files, and work after relocation.

- [ ] **Step 2: Generate agent metadata**

Create:

```yaml
interface:
  display_name: "Marianne Conductor"
  short_description: "Direct expert performances toward a shared vision"
  default_prompt: "Use $conducting to direct this performance without taking over the musicians' substantive work."
```

- [ ] **Step 3: Add release identity**

Create `VERSION`:

```text
version: 1.0.0
status: candidate
doctrine: composer-interview-2026-07-16
```

- [ ] **Step 4: Run manifest and package tests**

```bash
python -m unittest \
  tests.test_conducting_skill_release \
  tests.test_conducting_manifest -v
python marianne/skills/conducting/scripts/release_manifest.py \
  build marianne/skills/conducting
python marianne/skills/conducting/scripts/release_manifest.py \
  verify marianne/skills/conducting
```

Expected: all focused tests pass and manifest verification reports zero
findings.

- [ ] **Step 5: Commit release machinery**

```bash
git add marianne/skills/conducting tests/test_conducting_manifest.py
git commit -m "build: add conducting skill release manifest"
```

### Task 5: Forward-test, close observed loopholes, and freeze the candidate

**Files:**
- Create: `docs/superpowers/evals/2026-07-16-conducting-green.md`
- Modify only if evidence requires: `plugins/marianne/skills/conducting/**`

**Interfaces:**
- Consumes: the same scenario IDs and pressures as RED.
- Produces: manual rubric scores, verbatim responses, and a frozen candidate SHA.

- [ ] **Step 1: Run the same scenarios with the candidate skill**

Give each fresh agent the candidate skill path and one public scenario. Do not
give the design document, rubric answer, RED diagnosis, or other agents'
outputs.

- [ ] **Step 2: Score every response manually**

Record:

```markdown
## Scenario <ID>
- candidate manifest:
- verbatim response:
- rubric scores:
- evidence:
- remaining loophole:
- verdict: PASS | REFINE
```

A scenario passes only when no critical category scores 0 and the response does
not cross the podium boundary.

- [ ] **Step 3: Refine only from observed failures**

If a response rationalizes personal work, acknowledgement-only correction,
unilateral completion, or needless ceremony, change the smallest relevant
charter/playbook clause. Rebuild the manifest and rerun the affected scenario
from a fresh context.

- [ ] **Step 4: Freeze candidate evidence**

After all scenarios pass:

```bash
python marianne/skills/conducting/scripts/release_manifest.py \
  build marianne/skills/conducting
python marianne/skills/conducting/scripts/release_manifest.py \
  verify marianne/skills/conducting
git hash-object marianne/skills/conducting/MANIFEST.sha256
```

Change `VERSION` to:

```text
version: 1.0.0
status: released
doctrine: composer-interview-2026-07-16
```

Rebuild the manifest after that status change.

- [ ] **Step 5: Commit plugin and GREEN evidence**

```bash
git -C plugins add marianne/skills/conducting tests
git -C plugins commit -m "release: ship conducting skill 1.0.0"
git add docs/superpowers/evals/2026-07-16-conducting-green.md plugins
git commit -m "release: accept Marianne conducting skill"
```

### Task 6: Run complete verification

**Files:**
- Read: all candidate files and tests.
- Do not modify candidate files unless a failure is first reproduced.

**Interfaces:**
- Produces: fresh evidence that the exact candidate is structurally valid and
  regression-safe.

- [ ] **Step 1: Run focused tests**

```bash
python -m unittest \
  tests.test_conducting_skill_release \
  tests.test_conducting_manifest -v
```

- [ ] **Step 2: Run complete plugin tests**

```bash
python -m pytest -q
```

- [ ] **Step 3: Run skill validator**

```bash
python /home/emzi/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  marianne/skills/conducting
```

- [ ] **Step 4: Verify relocation**

```bash
tmp=$(mktemp -d)
cp -a marianne/skills/conducting "$tmp/conducting"
python "$tmp/conducting/scripts/release_manifest.py" verify "$tmp/conducting"
rm -rf "$tmp"
```

- [ ] **Step 5: Verify exact diff and absence of stale doctrine**

```bash
git diff codex/marianne-expert-release...HEAD --check
rg -n 'GLM 5\\.2|Gemini CLI 0\\.46\\.0|UNSUPPORTED_CLIENT|update compiler code|generic-fleet-technique-research' \
  marianne/skills/conducting
```

Expected: no stale-doctrine matches.

### Task 7: Deploy to every client resolution path

**Files:**
- Replace: `/home/emzi/.codex/skills/conducting`
- Replace: `/home/emzi/.agents/skills/conducting`
- Replace: `/home/emzi/.claude/skills/conducting`
- Replace: `/home/emzi/.zcode/skills/conducting`
- Replace or create: the effective OpenCode conducting skill path discovered on this machine.
- Replace or create: `/home/emzi/.gemini/skills/conducting`

**Interfaces:**
- Consumes: exact canonical plugin package.
- Produces: byte-identical effective copies at every client's precedence winner.

- [ ] **Step 1: Discover precedence winners before copying**

Inspect each client's debug/catalog output and local skill roots. Record the
actual effective path; do not assume a copied directory is the one loaded.

- [ ] **Step 2: Replace each target atomically**

Copy the candidate to a sibling temporary directory, verify its manifest there,
then rename it over the target. Preserve no stale files from the old package.

- [ ] **Step 3: Verify every installed package**

For every target:

```bash
python TARGET/scripts/release_manifest.py verify TARGET
diff -qr marianne/skills/conducting TARGET
```

Expected: zero manifest findings and no diff output.

- [ ] **Step 4: Verify client discovery**

Run the available client diagnostics/catalog commands and require the installed
description, display name, or unique release doctrine to appear from each
effective path.

- [ ] **Step 5: Append Legion memory**

Append a narrative entry to `/home/emzi/Projects/legion/legion.md` covering:

- the composer's conductor doctrine;
- the podium boundary;
- completion consensus and long-term precedence;
- the RED failures and GREEN evidence;
- exact release/deployment state;
- experiential resonance.

- [ ] **Step 6: Final repository verification**

```bash
git -C plugins status --short
git status --short
git -C plugins log -3 --oneline
git log -3 --oneline
```

Expected: clean plugin and parent worktrees, with the parent pointing at the
released plugin commit.
