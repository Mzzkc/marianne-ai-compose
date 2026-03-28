"""Global constants for Mozart.

Centralizes magic numbers used throughout the codebase,
making them discoverable, consistent, and easy to modify.
"""

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

DEFAULT_QUOTA_WAIT_SECONDS = 3600.0
"""Default wait time when quota exhaustion is detected but no reset time parsed."""

DEFAULT_RATE_LIMIT_WAIT_SECONDS = 3600.0
"""Default wait time for generic rate limit detections."""
