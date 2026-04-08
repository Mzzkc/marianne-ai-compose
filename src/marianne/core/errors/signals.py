"""Signal handling constants and utilities for Marianne error classification.

This module provides signal-related constants and helper functions used by
the error classifier to determine whether a process killed by a signal
should be retried or treated as a fatal error.

Signal Categories:
- RETRIABLE_SIGNALS: Signals that indicate transient conditions (network, termination)
- FATAL_SIGNALS: Signals that indicate crashes or unrecoverable errors
"""

import signal

# Signals that indicate the process should be retried
RETRIABLE_SIGNALS: set[int] = {
    signal.SIGTERM,  # Graceful termination request
    signal.SIGHUP,   # Terminal hangup
    signal.SIGPIPE,  # Broken pipe (network issues)
}

# Signals that indicate a fatal error (crash, out of memory, etc.)
FATAL_SIGNALS: set[int] = {
    signal.SIGSEGV,  # Segmentation fault
    signal.SIGBUS,   # Bus error
    signal.SIGABRT,  # Abort signal
    signal.SIGFPE,   # Floating point exception
    signal.SIGILL,   # Illegal instruction
}


def get_signal_name(sig_num: int) -> str:
    """Get human-readable signal name.

    Args:
        sig_num: The signal number (e.g., signal.SIGTERM)

    Returns:
        Human-readable signal name (e.g., "SIGTERM") or "signal N" if unknown
    """
    signal_names: dict[int, str] = {
        signal.SIGTERM: "SIGTERM",
        signal.SIGKILL: "SIGKILL",
        signal.SIGINT: "SIGINT",
        signal.SIGSEGV: "SIGSEGV",
        signal.SIGABRT: "SIGABRT",
        signal.SIGBUS: "SIGBUS",
        signal.SIGFPE: "SIGFPE",
        signal.SIGHUP: "SIGHUP",
        signal.SIGPIPE: "SIGPIPE",
    }
    return signal_names.get(sig_num, f"signal {sig_num}")
