"""Shared fixtures for the memory tests."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def short_tmp() -> Iterator[Path]:
    """A short private directory (AF_UNIX paths are limited); removed afterwards."""
    root = Path(tempfile.mkdtemp(prefix="mg", dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
