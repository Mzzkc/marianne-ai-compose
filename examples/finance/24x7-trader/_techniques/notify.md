# Notify Technique Contract

Outbound notifications. Reference implementation prints to stdout
(safe default — no external dependencies). Real deployments swap in
Slack, ClickUp, Telegram, email, or whatever the operator prefers.

## Contract

```
notify <channel> <title> <body_file>
```

| Arg | Meaning |
|---|---|
| `<channel>` | Logical channel name from operator charter (`trades`, `daily`, `weekly`, `urgent`). The implementation maps these to its concrete destinations. |
| `<title>` | Short subject line (≤ 80 chars). |
| `<body_file>` | Path to a markdown file with the body. Implementation reads and posts it. |

### Behavior

- Exit 0 on successful delivery (or successful queueing).
- Exit non-zero on failure with diagnostic stderr.
- MUST NOT silently swallow errors — the score's Andon Cord depends
  on accurate exit codes.
- MUST respect operator charter quiet hours by no-op'ing if the
  channel is not `urgent` and the current local time is in quiet hours.
  (The reference implementation reads `OPERATOR_QUIET_HOURS=22-07` env.)

## Configuration

```
NOTIFY_CMD            — absolute path to the notify script
NOTIFY_CHANNEL_TRADES — destination for trade-confirmation messages
NOTIFY_CHANNEL_DAILY  — destination for daily close summaries
NOTIFY_CHANNEL_WEEKLY — destination for weekly review reports
NOTIFY_CHANNEL_URGENT — destination for fermata triggers
OPERATOR_QUIET_HOURS  — e.g. "22-07" (no quiet hours by default)
```

## Reference implementations

- `_scripts/notify_stdout.sh` — prints to stdout. Safe default for
  testing. Useful in containers / cron with stdout captured to a log.
- An operator can wire up `_scripts/notify_slack.sh` (POSTs to a
  webhook), `_scripts/notify_clickup.sh`, etc.
