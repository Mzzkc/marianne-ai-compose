"""#193: watchfiles' per-poll "N change(s) detected" DEBUG spam must be
suppressed so it doesn't flood the structured conductor.log (139k lines seen).
"""

from __future__ import annotations

import logging

from marianne.daemon.observer import _suppress_watchfiles_noise


def test_suppress_raises_watchfiles_logger_to_warning() -> None:
    wf = logging.getLogger("watchfiles")
    original = wf.level
    try:
        wf.setLevel(logging.DEBUG)  # simulate daemon running at debug
        _suppress_watchfiles_noise()
        # DEBUG "change detected" records are now below threshold → dropped.
        assert wf.level >= logging.WARNING
        assert not wf.isEnabledFor(logging.DEBUG)
        assert not wf.isEnabledFor(logging.INFO)
    finally:
        wf.setLevel(original)
