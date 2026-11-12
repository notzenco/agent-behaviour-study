"""Shared fixtures.

The tests that need the real ``tripwire`` binary are skipped when it is not
built, so the suite still runs on a machine without ``agent-tripwire``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abstudy.tripwire import default_tripwire_binary


def _binary_available(binary: str) -> bool:
    return Path(binary).is_file() or shutil.which(binary) is not None


@pytest.fixture(scope="session")
def tripwire_binary() -> str:
    """Path to a usable ``tripwire`` binary, or skip."""
    binary = default_tripwire_binary()
    if not _binary_available(binary):
        pytest.skip("tripwire binary is not available")
    return binary


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A throwaway directory that stands in for a trial sandbox."""
    root = tmp_path / "sandbox"
    root.mkdir()
    return root
