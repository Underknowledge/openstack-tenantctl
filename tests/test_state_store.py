"""Tests for the YamlFileStateStore."""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from src.state_store import YamlFileStateStore


class TestLoadNonexistent:
    """load() returns empty dict when state file does not exist."""

    def test_load_returns_empty_dict(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")
        assert store.load("nonexistent") == {}


class TestSaveAndLoadRoundtrip:
    """save() then load() returns the persisted data."""

    def test_roundtrip(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")
        fips = [{"id": "fip-1", "address": "10.0.0.1"}]
        store.save("proj", ["preallocated_fips"], fips)

        data = store.load("proj")
        assert data["preallocated_fips"] == fips


class TestSaveCreatesDirectoryLazily:
    """save() creates the state directory on first write."""

    def test_creates_directory(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "deep" / "nested" / "state"
        assert not state_dir.exists()

        store = YamlFileStateStore(state_dir)
        store.save("proj", ["router_ips"], [])

        assert state_dir.exists()
        assert (state_dir / "proj.state.yaml").exists()


class TestSavePreservesExistingKeys:
    """Multiple saves to the same file preserve all keys."""

    def test_preserves_keys(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")

        store.save("proj", ["preallocated_fips"], [{"id": "a"}])
        store.save("proj", ["router_ips"], [{"id": "r1"}])

        data = store.load("proj")
        assert data["preallocated_fips"] == [{"id": "a"}]
        assert data["router_ips"] == [{"id": "r1"}]


class TestSaveNestedKeyPath:
    """save() with multi-level key_path creates nested dicts."""

    def test_nested_key_path(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")

        store.save("proj", ["metadata", "project_id"], "uuid-123")
        store.save("proj", ["metadata", "domain_id"], "default")

        data = store.load("proj")
        assert data["metadata"]["project_id"] == "uuid-123"
        assert data["metadata"]["domain_id"] == "default"


class TestSaveEmptyKeyPathRaises:
    """save() with empty key_path raises ValueError."""

    def test_empty_key_path_raises(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")
        with pytest.raises(ValueError, match="key_path must not be empty"):
            store.save("proj", [], "value")


class TestSaveOverwritesExistingValue:
    """save() to the same key_path overwrites the previous value."""

    def test_overwrites(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")

        store.save("proj", ["preallocated_fips"], [{"id": "old"}])
        store.save("proj", ["preallocated_fips"], [{"id": "new"}])

        data = store.load("proj")
        assert data["preallocated_fips"] == [{"id": "new"}]


class TestLoadCorruptFile:
    """load() returns empty dict when the file contains non-mapping YAML."""

    def test_non_dict_yaml(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        state_file = state_dir / "proj.state.yaml"
        state_file.write_text("- just a list\n", encoding="utf-8")

        store = YamlFileStateStore(state_dir)
        assert store.load("proj") == {}


class TestStatePathFormat:
    """State file uses <state_key>.state.yaml naming convention."""

    def test_path_format(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")
        store.save("dev-team", ["router_ips"], [])

        assert (tmp_path / "state" / "dev-team.state.yaml").exists()


class TestMultipleProjects:
    """Different state_keys produce separate files."""

    def test_separate_files(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")

        store.save("proj-a", ["preallocated_fips"], [{"id": "a"}])
        store.save("proj-b", ["preallocated_fips"], [{"id": "b"}])

        assert store.load("proj-a")["preallocated_fips"] == [{"id": "a"}]
        assert store.load("proj-b")["preallocated_fips"] == [{"id": "b"}]


# --- Python-tag / safe_dump regression tests ---

_CORRUPTED_YAML = """\
metadata:
  last_reconciled_state: !!python/object/apply:src.models.project.ProjectState
  - locked
"""


class _FakeState(StrEnum):
    LOCKED = "locked"


class TestSaveUsesSafeDump:
    """save() writes plain YAML without Python-specific tags."""

    def test_no_python_tags(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")
        store.save("proj", ["metadata", "last_reconciled_state"], _FakeState.LOCKED)

        content = (tmp_path / "state" / "proj.state.yaml").read_text(encoding="utf-8")
        assert "!!python" not in content
        assert "locked" in content

    def test_strenum_roundtrips_as_plain_string(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path / "state")
        store.save("proj", ["metadata", "last_reconciled_state"], _FakeState.LOCKED)

        data = store.load("proj")
        assert data["metadata"]["last_reconciled_state"] == "locked"
        assert isinstance(data["metadata"]["last_reconciled_state"], str)


class TestLoadCorruptedPythonTags:
    """load() recovers gracefully when a state file has Python-specific tags."""

    def test_returns_empty_dict(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "proj.state.yaml").write_text(_CORRUPTED_YAML, encoding="utf-8")

        store = YamlFileStateStore(state_dir)
        assert store.load("proj") == {}

    def test_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "proj.state.yaml").write_text(_CORRUPTED_YAML, encoding="utf-8")

        store = YamlFileStateStore(state_dir)
        with caplog.at_level(logging.WARNING):
            store.load("proj")

        assert any("Corrupted state file" in msg for msg in caplog.messages)


class TestSaveOverwritesCorruptedFile:
    """save() overwrites a corrupted state file with clean YAML."""

    def test_self_healing(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        state_file = state_dir / "proj.state.yaml"
        state_file.write_text(_CORRUPTED_YAML, encoding="utf-8")

        store = YamlFileStateStore(state_dir)
        store.save("proj", ["metadata", "last_reconciled_state"], "present")

        # File is now clean
        content = state_file.read_text(encoding="utf-8")
        assert "!!python" not in content

        # And round-trips correctly
        data = store.load("proj")
        assert data["metadata"]["last_reconciled_state"] == "present"
