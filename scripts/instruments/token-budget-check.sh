#!/usr/bin/env bash
# token-budget-check.sh — L1/L2/L3 token budget verification
#
# Exit 0: all within budget
# Exit 1: at least one file over budget (report to stdout)
#
# Environment variables:
#   AGENT_DIR  — agent identity directory
#   L1_BUDGET  — max words for identity.md (default: 900)
#   L2_BUDGET  — max words for profile.yaml (default: 1500)
#   L3_BUDGET  — max words for recent.md (default: 1500)

set -euo pipefail

L1_MAX="${L1_BUDGET:-900}"
L2_MAX="${L2_BUDGET:-1500}"
L3_MAX="${L3_BUDGET:-1500}"

OVER=0

L1_WORDS=$(wc -w < "${AGENT_DIR}/identity.md" 2>/dev/null || echo 0)
L2_WORDS=$(wc -w < "${AGENT_DIR}/profile.yaml" 2>/dev/null || echo 0)
L3_WORDS=$(wc -w < "${AGENT_DIR}/recent.md" 2>/dev/null || echo 0)

if [ "$L1_WORDS" -gt "$L1_MAX" ]; then
    echo "OVER BUDGET: identity.md has ${L1_WORDS} words (budget: ${L1_MAX})"
    OVER=1
fi

if [ "$L2_WORDS" -gt "$L2_MAX" ]; then
    echo "OVER BUDGET: profile.yaml has ${L2_WORDS} words (budget: ${L2_MAX})"
    OVER=1
fi

if [ "$L3_WORDS" -gt "$L3_MAX" ]; then
    echo "OVER BUDGET: recent.md has ${L3_WORDS} words (budget: ${L3_MAX})"
    OVER=1
fi

if [ "$OVER" -eq 0 ]; then
    echo "All within budget: L1=${L1_WORDS}/${L1_MAX} L2=${L2_WORDS}/${L2_MAX} L3=${L3_WORDS}/${L3_MAX}"
fi

exit $OVER
