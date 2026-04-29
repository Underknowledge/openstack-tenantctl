"""Tests for the config scaffolding module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from src.scaffold import write_sample_config


class TestWriteSampleConfig:
    """Tests for write_sample_config()."""

    def test_creates_defaults_yaml(self, tmp_path: Path) -> None:
        """Should create defaults.yaml in target directory."""
        written = write_sample_config(tmp_path)

        defaults = tmp_path / "defaults.yaml"
        assert defaults.exists()
        assert defaults in written

    def test_creates_minimal_project(self, tmp_path: Path) -> None:
        """Should create projects/minimal.yaml."""
        written = write_sample_config(tmp_path)

        minimal = tmp_path / "projects" / "minimal.yaml"
        assert minimal.exists()
        assert minimal in written

    def test_creates_federation_static_files(self, tmp_path: Path) -> None:
        """Should create both federation_static JSON files."""
        written = write_sample_config(tmp_path)

        assert (tmp_path / "federation_static.json").exists()
        assert (tmp_path / "federation_static_group.json").exists()
        assert len([p for p in written if p.suffix == ".json"]) == 2

    def test_returns_all_written_paths(self, tmp_path: Path) -> None:
        """Should return exactly 4 files."""
        written = write_sample_config(tmp_path)

        assert len(written) == 4

    def test_defaults_yaml_has_content(self, tmp_path: Path) -> None:
        """Bundled defaults.yaml should contain real config content."""
        write_sample_config(tmp_path)

        text = (tmp_path / "defaults.yaml").read_text()
        assert "quotas:" in text
        assert "network:" in text

    def test_creates_target_directory_if_missing(self, tmp_path: Path) -> None:
        """Should create the target directory when it doesn't exist."""
        target = tmp_path / "nested" / "config"
        write_sample_config(target)

        assert (target / "defaults.yaml").exists()

    def test_refuses_overwrite_when_yaml_exists(self, tmp_path: Path) -> None:
        """Should raise FileExistsError when target already has YAML files."""
        (tmp_path / "existing.yaml").write_text("name: foo\n")

        with pytest.raises(FileExistsError, match="already contains YAML"):
            write_sample_config(tmp_path)

    def test_refuses_overwrite_when_nested_yaml_exists(self, tmp_path: Path) -> None:
        """Should detect YAML files in subdirectories."""
        projects = tmp_path / "projects"
        projects.mkdir()
        (projects / "my.yaml").write_text("name: bar\n")

        with pytest.raises(FileExistsError):
            write_sample_config(tmp_path)

    def test_allows_non_yaml_files_in_target(self, tmp_path: Path) -> None:
        """Non-YAML files should not trigger the overwrite guard."""
        (tmp_path / "readme.txt").write_text("hello")

        written = write_sample_config(tmp_path)

        assert len(written) == 4
