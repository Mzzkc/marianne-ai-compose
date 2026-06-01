"""Global constants for Marianne.

Centralizes magic numbers used throughout the codebase,
making them discoverable, consistent, and easy to modify.
"""

from pathlib import Path

# =============================================================================
# JSON/Dict Key Constants
# =============================================================================

SHEET_NUM_KEY = "sheet_num"
"""Standard key for sheet number in dicts and serialised state."""

STATE_DB_FILENAME = ".marianne-state.db"
"""Filename for the per-workspace SQLite state database (#245).

Centralizes a literal that was previously hand-constructed in 8+ path
expressions across cli/, daemon/, execution/, and core/config/. Construct the
path as ``workspace / STATE_DB_FILENAME`` so the filename has a single source of
truth."""

DAEMON_STATE_DB_PATH = Path("~/.marianne/daemon-state.db")
"""Path to the conductor's registry/state SQLite database (#312).

Centralizes a literal previously hand-constructed in 4 sites: the reserved
``DaemonConfig.state_db_path`` default, the reserved-field warning baseline in
``daemon/process.py``, and the two functional conductor-down readers in
``cli/helpers.py`` and ``cli/commands/recover.py``. Tilde is expanded at the
point of use (``DAEMON_STATE_DB_PATH.expanduser()``); the constant itself keeps
the ``~`` form so it matches the config default verbatim.

Note: this only deduplicates the literal. Config-driven *override* of the path
remains a deliberately deferred feature — ``state_db_path`` is documented as
reserved and logs a warning when set."""

VALIDATION_PASS_RATE_KEY = "validation_pass_rate"
"""Standard key for validation pass rate in checkpoint/job data."""

# =============================================================================
# Text Truncation Limits (characters)
# =============================================================================

TRUNCATE_STDOUT_TAIL_CHARS = 500
"""Default truncation limit for stdout/stderr tails in error display and state."""


# =============================================================================
# Healing / Diagnostic Context Limits
# =============================================================================

HEALING_CONTEXT_TAIL_CHARS = 10000
"""Maximum stdout/stderr characters captured for self-healing diagnostic context."""

# =============================================================================
# Dashboard / Rate Limiting
# =============================================================================

RATE_LIMIT_REQUESTS_PER_MINUTE = 60
"""Maximum requests per minute for API rate limiting."""

RATE_LIMIT_REQUESTS_PER_HOUR = 1000
"""Maximum requests per hour for API rate limiting."""

RATE_LIMIT_BURST_LIMIT = 10
"""Maximum burst requests in a short window."""

SSE_QUEUE_TIMEOUT_SECONDS = 30.0
"""Timeout for SSE event queue reads."""


# =============================================================================
# Stream / I/O Chunk Sizes (bytes)
# =============================================================================

STREAM_CHUNK_SIZE = 4096
"""Default chunk size for stream reads (4 KB)."""

FILE_HASH_CHUNK_SIZE = 8192
"""Chunk size for file hashing operations (8 KB)."""

# =============================================================================
# Validation Command Defaults
# =============================================================================

VALIDATION_COMMAND_TIMEOUT_SECONDS = 3600
"""Timeout for user-defined validation commands (1 hour)."""

VALIDATION_OUTPUT_TRUNCATE_CHARS = 500
"""Maximum characters for validation command output summaries."""

# =============================================================================
# Error Classifier Defaults
# =============================================================================

RESET_TIME_MINIMUM_WAIT_SECONDS = 300.0
"""Minimum wait time for reset-based rate limit delays (5 minutes)."""

RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0
"""Maximum wait time for parsed rate limit delays (24 hours).

Safety cap: without this, adversarial or malformed API responses like
'resets in 999999 hours' would schedule timers for years, effectively
blocking the instrument forever with no auto-recovery. 24 hours is the
longest any real API provider rate limit should last. If it's longer,
the operator can re-trigger via `mzt clear-rate-limits`.
"""

DEFAULT_QUOTA_WAIT_SECONDS = 3600.0
"""Default wait time when quota exhaustion is detected but no reset time parsed."""

DEFAULT_RATE_LIMIT_WAIT_SECONDS = 3600.0
"""Default wait time for generic rate limit detections."""
