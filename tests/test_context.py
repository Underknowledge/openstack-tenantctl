"""Tests for context-building helpers (src/context.py)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import Mock

if TYPE_CHECKING:
    from pathlib import Path

import openstack.exceptions
import pytest

from src.context import (
    build_external_network_map,
    load_static_mapping_files,
    resolve_default_external_network,
    resolve_federation_context,
)
from src.models import DefaultsConfig, ProjectConfig


class TestBuildExternalNetworkMap:
    """Tests for build_external_network_map helper."""

    def test_builds_map_from_discovered_networks(self) -> None:
        """Should build name→id and id→id map from discovered networks."""
        mock_conn = Mock()
        mock_net = Mock()
        mock_net.id = "net-123"
        mock_net.name = "public"
        mock_conn.network.networks.return_value = [mock_net]

        result = build_external_network_map(mock_conn)

        assert result == {"public": "net-123", "net-123": "net-123"}
        mock_conn.network.networks.assert_called_once_with(**{"router:external": True})

    def test_builds_map_for_multiple_networks(self) -> None:
        """Should include all external networks in the map."""
        mock_conn = Mock()
        net1 = Mock(id="net-1")
        net1.name = "public"
        net2 = Mock(id="net-2")
        net2.name = "dmz"
        mock_conn.network.networks.return_value = [net1, net2]

        result = build_external_network_map(mock_conn)

        assert result == {
            "public": "net-1",
            "net-1": "net-1",
            "dmz": "net-2",
            "net-2": "net-2",
        }

    def test_returns_empty_map_when_no_networks(self) -> None:
        """Should return empty dict when no external networks exist."""
        mock_conn = Mock()
        mock_conn.network.networks.return_value = []

        result = build_external_network_map(mock_conn)

        assert result == {}


class TestResolveDefaultExternalNetwork:
    """Tests for resolve_default_external_network helper."""

    def test_returns_configured_network_when_found(self) -> None:
        """Should return network ID when explicit name is in the map."""
        net_map = {"public": "net-123", "net-123": "net-123"}
        defaults = DefaultsConfig(external_network_name="public")

        result = resolve_default_external_network(net_map, defaults)

        assert result == "net-123"

    def test_returns_empty_when_configured_network_not_found(self) -> None:
        """Should return empty string when explicit name is not in the map."""
        net_map = {"public": "net-123", "net-123": "net-123"}
        defaults = DefaultsConfig(external_network_name="nonexistent")

        result = resolve_default_external_network(net_map, defaults)

        assert result == ""

    def test_auto_discovers_when_exactly_one_external_network(self) -> None:
        """Should auto-discover when exactly one external network exists."""
        net_map = {"external": "net-auto", "net-auto": "net-auto"}
        defaults = DefaultsConfig()

        result = resolve_default_external_network(net_map, defaults)

        assert result == "net-auto"

    def test_returns_empty_when_multiple_external_networks(self) -> None:
        """Should return empty when multiple external networks found."""
        net_map = {
            "external1": "net-1",
            "net-1": "net-1",
            "external2": "net-2",
            "net-2": "net-2",
        }
        defaults = DefaultsConfig()

        result = resolve_default_external_network(net_map, defaults)

        assert result == ""

    def test_returns_empty_when_no_external_networks(self) -> None:
        """Should return empty when map is empty."""
        net_map: dict[str, str] = {}
        defaults = DefaultsConfig()

        result = resolve_default_external_network(net_map, defaults)

        assert result == ""


class TestResolveFederationContext:
    """Tests for resolve_federation_context helper."""

    def test_loads_existing_mapping(self, mock_config_dir: Path) -> None:
        """Should load existing federation mapping when it exists."""
        mock_conn = Mock()
        mock_mapping = Mock()
        mock_mapping.rules = [{"local": [], "remote": []}]
        mock_conn.identity.get_mapping.return_value = mock_mapping

        defaults = DefaultsConfig(federation_mapping_id="mapping-123")
        all_projects: list[ProjectConfig] = []

        current_rules, mapping_exists, static_rules = resolve_federation_context(
            mock_conn, str(mock_config_dir), defaults, all_projects
        )

        assert current_rules == [{"local": [], "remote": []}]
        assert mapping_exists is True
        assert static_rules == []
        # Verify the code looked up the correct mapping by ID
        mock_conn.identity.get_mapping.assert_called_once_with("mapping-123")

    def test_handles_mapping_not_found(self, mock_config_dir: Path) -> None:
        """Should handle NotFoundException when mapping doesn't exist yet."""
        mock_conn = Mock()
        mock_conn.identity.get_mapping.side_effect = openstack.exceptions.NotFoundException

        defaults = DefaultsConfig(federation_mapping_id="new-mapping")
        all_projects: list[ProjectConfig] = []

        current_rules, mapping_exists, static_rules = resolve_federation_context(
            mock_conn, str(mock_config_dir), defaults, all_projects
        )

        # Verify the code detected the mapping doesn't exist yet (will be created later)
        assert current_rules == []
        assert mapping_exists is False
        assert static_rules == []
        # Verify it attempted to look up the mapping
        mock_conn.identity.get_mapping.assert_called_once_with("new-mapping")

    def test_falls_back_to_project_mapping_id(self, mock_config_dir: Path) -> None:
        """Should fall back to first project's mapping_id when not in defaults."""
        mock_conn = Mock()
        mock_mapping = Mock()
        mock_mapping.rules = []
        mock_conn.identity.get_mapping.return_value = mock_mapping

        defaults = DefaultsConfig()
        all_projects = [
            ProjectConfig.from_dict(
                {
                    "name": "proj1",
                    "resource_prefix": "proj1",
                    "federation": {"mapping_id": "proj-mapping"},
                }
            ),
        ]

        _, mapping_exists, _ = resolve_federation_context(mock_conn, str(mock_config_dir), defaults, all_projects)

        assert mapping_exists is True
        mock_conn.identity.get_mapping.assert_called_once_with("proj-mapping")

    def test_loads_static_rules(self, mock_config_dir: Path) -> None:
        """Should load static federation rules from JSON file."""
        mock_conn = Mock()
        mock_conn.identity.get_mapping.side_effect = openstack.exceptions.NotFoundException

        # Add static rules to the file
        static_path = mock_config_dir / "federation_static.json"
        static_data = [{"local": [{"user": {"name": "admin"}}], "remote": []}]
        static_path.write_text(json.dumps(static_data))

        defaults = DefaultsConfig(
            federation_static_mapping_files=("federation_static.json",),
        )
        all_projects: list[ProjectConfig] = []

        _, _, static_rules = resolve_federation_context(mock_conn, str(mock_config_dir), defaults, all_projects)

        assert static_rules == static_data

    def test_no_mapping_id_in_defaults_or_projects(self, mock_config_dir: Path) -> None:
        """Should handle case when no mapping_id is configured anywhere."""
        mock_conn = Mock()

        defaults = DefaultsConfig()
        all_projects = [
            ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "proj1"}),
        ]

        current_rules, mapping_exists, static_rules = resolve_federation_context(
            mock_conn, str(mock_config_dir), defaults, all_projects
        )

        assert current_rules == []
        assert mapping_exists is False
        assert static_rules == []
        # Should not call get_mapping when no mapping_id is found
        mock_conn.identity.get_mapping.assert_not_called()

    def test_skips_projects_without_federation_config(self, mock_config_dir: Path) -> None:
        """Should skip projects that have federation=None when searching for mapping_id."""
        mock_conn = Mock()
        mock_mapping = Mock()
        mock_mapping.rules = []
        mock_conn.identity.get_mapping.return_value = mock_mapping

        defaults = DefaultsConfig()
        all_projects = [
            ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "proj1"}),
            ProjectConfig.from_dict(
                {
                    "name": "proj2",
                    "resource_prefix": "proj2",
                    "federation": {"mapping_id": "found-mapping"},
                }
            ),
        ]

        _, mapping_exists, _ = resolve_federation_context(mock_conn, str(mock_config_dir), defaults, all_projects)

        assert mapping_exists is True
        mock_conn.identity.get_mapping.assert_called_once_with("found-mapping")

    def test_uses_first_project_mapping_id_when_multiple_exist(self, mock_config_dir: Path) -> None:
        """Should use first project's mapping_id when multiple projects have federation config."""
        mock_conn = Mock()
        mock_mapping = Mock()
        mock_mapping.rules = []
        mock_conn.identity.get_mapping.return_value = mock_mapping

        defaults = DefaultsConfig()
        all_projects = [
            ProjectConfig.from_dict(
                {
                    "name": "proj1",
                    "resource_prefix": "proj1",
                    "federation": {"mapping_id": "first-mapping"},
                }
            ),
            ProjectConfig.from_dict(
                {
                    "name": "proj2",
                    "resource_prefix": "proj2",
                    "federation": {"mapping_id": "second-mapping"},
                }
            ),
        ]

        resolve_federation_context(mock_conn, str(mock_config_dir), defaults, all_projects)

        # Should only call with the first mapping_id found
        mock_conn.identity.get_mapping.assert_called_once_with("first-mapping")


class TestLoadStaticMappingFiles:
    """Tests for load_static_mapping_files helper."""

    def test_literal_filename(self, tmp_path: Path) -> None:
        """Literal filename pattern matches a single file."""
        rules = [{"local": [{"user": {"name": "admin"}}], "remote": []}]
        (tmp_path / "federation_static.json").write_text(json.dumps(rules))

        result = load_static_mapping_files(str(tmp_path), ("federation_static.json",))

        assert result == rules

    def test_wildcard_matches_multiple_sorted(self, tmp_path: Path) -> None:
        """Wildcard *.json matches multiple files in sorted order."""
        subdir = tmp_path / "static.d"
        subdir.mkdir()
        (subdir / "b.json").write_text(json.dumps([{"id": "b"}]))
        (subdir / "a.json").write_text(json.dumps([{"id": "a"}]))

        result = load_static_mapping_files(str(tmp_path), ("static.d/*.json",))

        assert result == [{"id": "a"}, {"id": "b"}]

    def test_numbered_glob(self, tmp_path: Path) -> None:
        """Glob [0-9]-*.json matches numbered files, ignores others."""
        subdir = tmp_path / "d"
        subdir.mkdir()
        (subdir / "1-active.json").write_text(json.dumps([{"id": "1"}]))
        (subdir / "2-active.json").write_text(json.dumps([{"id": "2"}]))
        (subdir / "skip-me.json").write_text(json.dumps([{"id": "skip"}]))

        result = load_static_mapping_files(str(tmp_path), ("d/[0-9]-*.json",))

        assert result == [{"id": "1"}, {"id": "2"}]

    def test_no_matches_returns_empty_with_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """No matches → empty list + warning logged."""
        result = load_static_mapping_files(str(tmp_path), ("nonexistent/*.json",))

        assert result == []
        assert "No files matched" in caplog.text

    def test_multiple_patterns_concatenated(self, tmp_path: Path) -> None:
        """Multiple patterns: results concatenated in pattern order."""
        (tmp_path / "first.json").write_text(json.dumps([{"id": "first"}]))
        (tmp_path / "second.json").write_text(json.dumps([{"id": "second"}]))

        result = load_static_mapping_files(str(tmp_path), ("second.json", "first.json"))

        assert result == [{"id": "second"}, {"id": "first"}]

    def test_empty_patterns_returns_empty(self, tmp_path: Path) -> None:
        """Empty patterns tuple → empty list, no file I/O."""
        result = load_static_mapping_files(str(tmp_path), ())

        assert result == []

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        """Invalid JSON in a matched file → json.JSONDecodeError propagates."""
        (tmp_path / "bad.json").write_text("not valid json{{{")

        with pytest.raises(json.JSONDecodeError):
            load_static_mapping_files(str(tmp_path), ("bad.json",))
