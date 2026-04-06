"""Tests for version management."""

from __future__ import annotations

import re

from src import __version__


def test_version_format() -> None:
    """Version should follow semantic versioning format."""
    # Matches X.Y.Z or X.Y.Z-dev
    pattern = r"^\d+\.\d+\.\d+(-dev)?$"
    assert re.match(pattern, __version__), f"Invalid version format: {__version__}"


def test_version_not_dev_in_release() -> None:
    """Installed package should not have -dev suffix."""
    # In CI, after installation, version should be X.Y.Z not X.Y.Z-dev
    if __version__ != "0.0.0-dev":
        assert not __version__.endswith("-dev"), (
            f"Release version should not end with -dev: {__version__}"
        )


def test_version_accessible() -> None:
    """Version should be importable from src package."""
    from src import __version__ as v

    assert v is not None
    assert isinstance(v, str)
    assert len(v) > 0
