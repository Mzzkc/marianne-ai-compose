#!/usr/bin/env bash
# notify_stdout.sh — safe default notify implementation.
# Prints the message to stdout. Useful in cron with stdout captured to a log,
# or for testing. Replace with notify_slack.sh, notify_clickup.sh, etc.

set -euo pipefail

channel="${1:?channel required}"
title="${2:?title required}"
body_file="${3:?body_file required}"

[[ -f "$body_file" ]] || { echo "notify_stdout: body file not found: $body_file" >&2; exit 1; }

# Quiet hours respect (best-effort). Format "HH-HH" e.g. "22-07".
QH="${OPERATOR_QUIET_HOURS:-}"
if [[ -n "$QH" && "$channel" != "urgent" ]]; then
  start="${QH%-*}"; end="${QH#*-}"
  hour=$(date +%H)
  # quiet if (start <= hour) OR (hour < end) when window crosses midnight; or start <= hour < end otherwise
  if [[ "$start" -gt "$end" ]]; then
    if [[ "$hour" -ge "$start" || "$hour" -lt "$end" ]]; then exit 0; fi
  else
    if [[ "$hour" -ge "$start" && "$hour" -lt "$end" ]]; then exit 0; fi
  fi
fi

cat <<EOF
══════════════════════════════════════════════════════════════════════════════
[notify:${channel}] ${title}
$(date -Iseconds)
──────────────────────────────────────────────────────────────────────────────
$(cat "$body_file")
══════════════════════════════════════════════════════════════════════════════
EOF
