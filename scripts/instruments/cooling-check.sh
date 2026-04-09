#!/usr/bin/env bash
# cooling-check.sh — Composting Cascade play output verification
#
# Exit 0: play was productive
# Exit 1: play was insubstantial
#
# Environment variables:
#   PLAYSPACE       — playspace directory for this agent
#   AGENT_DIR       — agent identity directory
#   PLAY_START_TIME — epoch timestamp when play started

set -euo pipefail

START="${PLAY_START_TIME:-0}"

# Check: Files created or modified in playspace since play started
NEW_FILES=$(python3 -c "
import os
count = 0
start = ${START}
for f in os.listdir('${PLAYSPACE}'):
    fp = os.path.join('${PLAYSPACE}', f)
    if os.path.isfile(fp) and os.path.getmtime(fp) > start:
        count += 1
print(count)
")

if [ "$NEW_FILES" -eq 0 ]; then
    echo "No files created or modified in playspace"
    exit 1
fi

# Check: growth.md was updated
GROWTH_MTIME=$(python3 -c "
import os
print(int(os.path.getmtime('${AGENT_DIR}/growth.md')))
")

if [ "$GROWTH_MTIME" -le "$START" ]; then
    echo "growth.md not updated during play"
    exit 1
fi

echo "Play produced ${NEW_FILES} artifacts and updated growth trajectory"
exit 0
