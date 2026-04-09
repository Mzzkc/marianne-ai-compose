#!/usr/bin/env bash
# maturity-check.sh — Soil Maturity Index developmental stage measurement
#
# Always exits 0 — this is a measurement, not a gate.
# Writes maturity-report.yaml to REPORT_PATH.
#
# Environment variables:
#   AGENT_DIR   — agent identity directory
#   REPORT_PATH — where to write the maturity report

set -euo pipefail

python3 -c "
import yaml
from pathlib import Path
from datetime import datetime, timezone

agent_dir = Path('${AGENT_DIR}')
report_path = Path('${REPORT_PATH}')

# Read profile
profile = yaml.safe_load((agent_dir / 'profile.yaml').read_text())

# Read growth
growth_text = (agent_dir / 'growth.md').read_text() if (agent_dir / 'growth.md').exists() else ''
growth_entries = growth_text.count('##') - 1  # rough count of sections minus header

# Compute metrics
current_stage = profile.get('developmental_stage', 'recognition')
standing_patterns = profile.get('standing_pattern_count', 0)
relationships = profile.get('relationships', {})
rel_count = len(relationships)
rel_strengths = [r.get('strength', 0) for r in relationships.values() if isinstance(r, dict)]
avg_strength = sum(rel_strengths) / len(rel_strengths) if rel_strengths else 0.0
coherence = profile.get('coherence_trajectory', [])
coherence_slope = 0.0
if len(coherence) >= 3:
    recent = coherence[-3:]
    coherence_slope = (recent[-1] - recent[0]) / len(recent)
cycle_count = profile.get('cycle_count', 0)

# Stage suggestion (simple thresholds)
suggested = current_stage
if current_stage == 'recognition' and cycle_count > 10 and rel_count > 0:
    suggested = 'integration'
elif current_stage == 'integration' and standing_patterns > 2 and growth_entries > 5:
    suggested = 'generation'
elif current_stage == 'generation' and standing_patterns > 5 and coherence_slope > 0.1:
    suggested = 'recursion'
elif current_stage == 'recursion' and standing_patterns > 10 and avg_strength > 0.7:
    suggested = 'transcendence'

report = {
    'current_stage': current_stage,
    'suggested_stage': suggested,
    'standing_pattern_count': standing_patterns,
    'relationship_count': rel_count,
    'avg_relationship_strength': round(avg_strength, 3),
    'coherence_slope': round(coherence_slope, 3),
    'growth_entry_count': growth_entries,
    'cycle_count': cycle_count,
    'assessed_at': datetime.now(timezone.utc).isoformat(),
}

report_path.parent.mkdir(parents=True, exist_ok=True)
with open(report_path, 'w') as f:
    yaml.dump(report, f, default_flow_style=False, sort_keys=False)

print(f'Stage: {current_stage} (suggested: {suggested})')
print(f'Standing patterns: {standing_patterns}, Relationships: {rel_count}')
"
exit 0
