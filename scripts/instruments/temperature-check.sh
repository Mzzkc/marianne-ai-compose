#!/usr/bin/env bash
# temperature-check.sh — Composting Cascade play routing gate
#
# Exit 0: agent should play (phase transition)
# Exit 1: agent should work (no transition)
#
# Environment variables (set by Mozart from score config):
#   WORKSPACE             — project workspace path
#   AGENT_DIR             — agent identity directory
#   AGENT_NAME            — agent name
#   MEMORY_BLOAT_THRESHOLD — word count threshold for L3 (default: 3000)
#   STAGNATION_CYCLES     — cycles without growth.md update (default: 3)
#   MIN_CYCLES_BETWEEN_PLAY — minimum work cycles between play (default: 5)

set -euo pipefail

THRESHOLD="${MEMORY_BLOAT_THRESHOLD:-3000}"
STAGNATION="${STAGNATION_CYCLES:-3}"
MIN_BETWEEN="${MIN_CYCLES_BETWEEN_PLAY:-5}"

# Read cycle count and last play cycle from profile
CYCLE_COUNT=$(python3 -c "
import yaml, sys
try:
    d = yaml.safe_load(open('${AGENT_DIR}/profile.yaml'))
    print(d.get('cycle_count', 0))
except: print(0)
")
LAST_PLAY=$(python3 -c "
import yaml, sys
try:
    d = yaml.safe_load(open('${AGENT_DIR}/profile.yaml'))
    print(d.get('last_play_cycle', 0))
except: print(0)
")

# Guard: must have enough cycles since last play
SINCE_PLAY=$((CYCLE_COUNT - LAST_PLAY))
if [ "$SINCE_PLAY" -lt "$MIN_BETWEEN" ]; then
    echo "Too soon since last play (${SINCE_PLAY} < ${MIN_BETWEEN})"
    exit 1
fi

# Check 1: Memory bloat (L3 word count exceeds threshold)
L3_WORDS=$(wc -w < "${AGENT_DIR}/recent.md" 2>/dev/null || echo 0)
if [ "$L3_WORDS" -gt "$THRESHOLD" ]; then
    echo "Memory bloat: recent.md has ${L3_WORDS} words (threshold: ${THRESHOLD})"
    exit 0
fi

# Check 2: Stagnation (growth.md not modified recently)
if [ -f "${AGENT_DIR}/growth.md" ]; then
    GROWTH_AGE_DAYS=$(python3 -c "
import os, time
mtime = os.path.getmtime('${AGENT_DIR}/growth.md')
age_days = (time.time() - mtime) / 86400
print(int(age_days))
")
    if [ "$GROWTH_AGE_DAYS" -gt "$STAGNATION" ]; then
        echo "Stagnation: growth.md not modified in ${GROWTH_AGE_DAYS} days"
        exit 0
    fi
fi

# Check 3: No urgent tasks
if [ -f "${WORKSPACE}/TASKS.md" ]; then
    URGENT=$(grep -c '\- \[ \].*\(P0\|P1\)' "${WORKSPACE}/TASKS.md" 2>/dev/null || echo 0)
    URGENT=$(echo "$URGENT" | tr -d '[:space:]')
    if [ "$URGENT" -eq 0 ]; then
        echo "No P0/P1 tasks remaining"
        exit 0
    fi
fi

# Check 4: Composer play directive
if [ -f "${WORKSPACE}/composer-notes.yaml" ]; then
    PLAY_DIRECTIVE=$(python3 -c "
import yaml
try:
    d = yaml.safe_load(open('${WORKSPACE}/composer-notes.yaml'))
    for note in d.get('notes', []):
        if 'play' in str(note.get('directive', '')).lower() and '${AGENT_NAME}' in str(note.get('directive', '')):
            print('yes')
            break
    else:
        print('no')
except: print('no')
")
    if [ "$PLAY_DIRECTIVE" = "yes" ]; then
        echo "Composer directed play for ${AGENT_NAME}"
        exit 0
    fi
fi

# No conditions met — work
echo "All checks passed — continuing work"
exit 1
