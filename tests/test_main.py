"""Tests for main CLI entry point and helper functions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

if TYPE_CHECKING:
    from pathlib import Path

import openstack.exceptions
import pytest

from src.main import (
    _build_external_network_map,
    _load_and_filter_projects,
    _print_summary,
    _resolve_default_external_network,
    _resolve_federation_context,
    _setup_context,
    main,
)
from src.models import ProjectConfig
from src.utils import ActionStatus, ProvisionerError, SharedContext


@pytest.fixture
def mock_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory with minimal valid configs."""
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    projects_dir = config_dir / "projects"
    projects_dir.mkdir()

    # Create defaults.yaml with network.subnet structure
    defaults = {
        "quotas": {"compute": {"cores": 10}},
        "network": {
            "subnet": {
                "cidr": "192.168.1.0/24",
                "gateway_ip": "192.168.1.254",
                "allocation_pools": [{"start": "192.168.1.1", "end": "192.168.1.253"}],
            },
        },
    }
    (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))

    # Create project1.yaml in projects/ subdirectory
    project1 = {
        "name": "project1",
        "resource_prefix": "proj1",
        "network": {
            "subnet": {
                "cidr": "10.0.1.0/24",
                "gateway_ip": "10.0.1.254",
                "allocation_pools": [{"start": "10.0.1.1", "end": "10.0.1.253"}],
            },
        },
    }
    (projects_dir / "project1.yaml").write_text(yaml.dump(project1))

    # Create project2.yaml in projects/ subdirectory
    project2 = {
        "name": "project2",
        "resource_prefix": "proj2",
        "network": {
            "subnet": {
                "cidr": "10.0.2.0/24",
                "gateway_ip": "10.0.2.254",
                "allocation_pools": [{"start": "10.0.2.1", "end": "10.0.2.253"}],
            },
        },
    }
    (projects_dir / "project2.yaml").write_text(yaml.dump(project2))

    # Create federation_static.json
    static_rules: list[Any] = []
    (config_dir / "federation_static.json").write_text(json.dumps(static_rules))

    return config_dir


class TestLoadAndFilterProjects:
    """Tests for _load_and_filter_projects helper."""

    def test_loads_all_projects_when_no_filter(self, mock_config_dir: Path) -> None:
        """Should load all projects when no filter is specified."""
        projects, all_projects, defaults = _load_and_filter_projects(
            str(mock_config_dir), None
        )

        assert len(projects) == 2
        assert len(all_projects) == 2
        # Verify both projects are present
        project_names = {p.name for p in projects}
        assert project_names == {"project1", "project2"}
        # Verify defaults were loaded correctly
        assert "quotas" in defaults
        assert defaults["quotas"]["compute"]["cores"] == 10

    def test_filters_to_single_project(self, mock_config_dir: Path) -> None:
        """Should filter to single project when --project is specified."""
        projects, all_projects, _defaults = _load_and_filter_projects(
            str(mock_config_dir), "project1"
        )

        assert len(projects) == 1
        assert projects[0].name == "project1"
        assert len(all_projects) == 2  # all_projects should still contain both

    def test_raises_when_filtered_project_not_found(
        self, mock_config_dir: Path
    ) -> None:
        """Should raise ProvisionerError when filtered project doesn't exist."""
        with pytest.raises(ProvisionerError, match="project 'nonexistent' not found"):
            _load_and_filter_projects(str(mock_config_dir), "nonexistent")


class TestBuildExternalNetworkMap:
    """Tests for _build_external_network_map helper."""

    def test_builds_map_from_discovered_networks(self) -> None:
        """Should build name→id and id→id map from discovered networks."""
        mock_conn = Mock()
        mock_net = Mock()
        mock_net.id = "net-123"
        mock_net.name = "public"
        mock_conn.network.networks.return_value = [mock_net]

        result = _build_external_network_map(mock_conn)

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

        result = _build_external_network_map(mock_conn)

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

        result = _build_external_network_map(mock_conn)

        assert result == {}


class TestResolveDefaultExternalNetwork:
    """Tests for _resolve_default_external_network helper."""

    def test_returns_configured_network_when_found(self) -> None:
        """Should return network ID when explicit name is in the map."""
        net_map = {"public": "net-123", "net-123": "net-123"}
        defaults = {"external_network_name": "public"}

        result = _resolve_default_external_network(net_map, defaults)

        assert result == "net-123"

    def test_returns_empty_when_configured_network_not_found(self) -> None:
        """Should return empty string when explicit name is not in the map."""
        net_map = {"public": "net-123", "net-123": "net-123"}
        defaults = {"external_network_name": "nonexistent"}

        result = _resolve_default_external_network(net_map, defaults)

        assert result == ""

    def test_auto_discovers_when_exactly_one_external_network(self) -> None:
        """Should auto-discover when exactly one external network exists."""
        net_map = {"external": "net-auto", "net-auto": "net-auto"}
        defaults: dict[str, str] = {}

        result = _resolve_default_external_network(net_map, defaults)

        assert result == "net-auto"

    def test_returns_empty_when_multiple_external_networks(self) -> None:
        """Should return empty when multiple external networks found."""
        net_map = {
            "external1": "net-1",
            "net-1": "net-1",
            "external2": "net-2",
            "net-2": "net-2",
        }
        defaults: dict[str, str] = {}

        result = _resolve_default_external_network(net_map, defaults)

        assert result == ""

    def test_returns_empty_when_no_external_networks(self) -> None:
        """Should return empty when map is empty."""
        net_map: dict[str, str] = {}
        defaults: dict[str, str] = {}

        result = _resolve_default_external_network(net_map, defaults)

        assert result == ""


class TestResolveFederationContext:
    """Tests for _resolve_federation_context helper."""

    def test_loads_existing_mapping(self, mock_config_dir: Path) -> None:
        """Should load existing federation mapping when it exists."""
        mock_conn = Mock()
        mock_mapping = Mock()
        mock_mapping.rules = [{"local": [], "remote": []}]
        mock_conn.identity.get_mapping.return_value = mock_mapping

        defaults = {"federation": {"mapping_id": "mapping-123"}}
        all_projects: list[ProjectConfig] = []

        current_rules, mapping_exists, static_rules = _resolve_federation_context(
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
        mock_conn.identity.get_mapping.side_effect = (
            openstack.exceptions.NotFoundException
        )

        defaults = {"federation": {"mapping_id": "new-mapping"}}
        all_projects: list[ProjectConfig] = []

        current_rules, mapping_exists, static_rules = _resolve_federation_context(
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

        defaults: dict[str, Any] = {}
        all_projects = [
            ProjectConfig.from_dict(
                {
                    "name": "proj1",
                    "resource_prefix": "proj1",
                    "federation": {"mapping_id": "proj-mapping"},
                }
            ),
        ]

        _, mapping_exists, _ = _resolve_federation_context(
            mock_conn, str(mock_config_dir), defaults, all_projects
        )

        assert mapping_exists is True
        mock_conn.identity.get_mapping.assert_called_once_with("proj-mapping")

    def test_loads_static_rules(self, mock_config_dir: Path) -> None:
        """Should load static federation rules from JSON file."""
        mock_conn = Mock()
        mock_conn.identity.get_mapping.side_effect = (
            openstack.exceptions.NotFoundException
        )

        # Add static rules to the file
        static_path = mock_config_dir / "federation_static.json"
        static_data = [{"local": [{"user": {"name": "admin"}}], "remote": []}]
        static_path.write_text(json.dumps(static_data))

        defaults: dict[str, Any] = {}
        all_projects: list[ProjectConfig] = []

        _, _, static_rules = _resolve_federation_context(
            mock_conn, str(mock_config_dir), defaults, all_projects
        )

        assert static_rules == static_data

    def test_no_mapping_id_in_defaults_or_projects(self, mock_config_dir: Path) -> None:
        """Should handle case when no mapping_id is configured anywhere."""
        mock_conn = Mock()

        defaults: dict[str, Any] = {}
        all_projects = [
            ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "proj1"}),
        ]

        current_rules, mapping_exists, static_rules = _resolve_federation_context(
            mock_conn, str(mock_config_dir), defaults, all_projects
        )

        assert current_rules == []
        assert mapping_exists is False
        assert static_rules == []
        # Should not call get_mapping when no mapping_id is found
        mock_conn.identity.get_mapping.assert_not_called()

    def test_skips_projects_without_federation_config(
        self, mock_config_dir: Path
    ) -> None:
        """Should skip projects that have federation=None when searching for mapping_id."""
        mock_conn = Mock()
        mock_mapping = Mock()
        mock_mapping.rules = []
        mock_conn.identity.get_mapping.return_value = mock_mapping

        defaults: dict[str, Any] = {}
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

        _, mapping_exists, _ = _resolve_federation_context(
            mock_conn, str(mock_config_dir), defaults, all_projects
        )

        assert mapping_exists is True
        mock_conn.identity.get_mapping.assert_called_once_with("found-mapping")

    def test_uses_first_project_mapping_id_when_multiple_exist(
        self, mock_config_dir: Path
    ) -> None:
        """Should use first project's mapping_id when multiple projects have federation config."""
        mock_conn = Mock()
        mock_mapping = Mock()
        mock_mapping.rules = []
        mock_conn.identity.get_mapping.return_value = mock_mapping

        defaults: dict[str, Any] = {}
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

        _resolve_federation_context(
            mock_conn, str(mock_config_dir), defaults, all_projects
        )

        # Should only call with the first mapping_id found
        mock_conn.identity.get_mapping.assert_called_once_with("first-mapping")


class TestSetupContext:
    """Tests for _setup_context helper."""

    def test_offline_dry_run_mode_skips_connection(self, mock_config_dir: Path) -> None:
        """Should create context with None connection in offline dry-run mode."""
        ctx = _setup_context(
            dry_run=True,
            offline=True,
            config_dir=str(mock_config_dir),
            defaults={},
            all_projects=[],
        )

        assert ctx.conn is None
        assert ctx.dry_run is True

    @patch("src.main._connect")
    @patch("src.main._build_external_network_map")
    @patch("src.main._resolve_default_external_network")
    @patch("src.main.resolve_external_subnet", return_value="subnet-456")
    @patch("src.main._resolve_federation_context")
    def test_online_dry_run_connects(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should connect to OpenStack in online dry-run mode (no --offline)."""
        mock_conn = Mock()
        mock_connect.return_value = mock_conn
        mock_build_map.return_value = {"public": "net-123", "net-123": "net-123"}
        mock_resolve_default.return_value = "net-123"
        mock_resolve_fed.return_value = ([], False, [])

        ctx = _setup_context(
            dry_run=True,
            config_dir=str(mock_config_dir),
            defaults={},
            all_projects=[],
        )

        assert ctx.conn is mock_conn
        assert ctx.dry_run is True
        mock_connect.assert_called_once()

    @patch("src.main._connect")
    @patch("src.main._build_external_network_map")
    @patch("src.main._resolve_default_external_network")
    @patch("src.main.resolve_external_subnet", return_value="subnet-456")
    @patch("src.main._resolve_federation_context")
    def test_connects_and_resolves_resources(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should connect to OpenStack and resolve resources in normal mode."""
        mock_conn = Mock()
        mock_connect.return_value = mock_conn
        net_map = {"public": "net-123", "net-123": "net-123"}
        mock_build_map.return_value = net_map
        mock_resolve_default.return_value = "net-123"
        mock_resolve_fed.return_value = (
            [{"local": [], "remote": []}],
            True,
            [{"local": [{"user": {"name": "static"}}], "remote": []}],
        )

        ctx = _setup_context(
            dry_run=False,
            config_dir=str(mock_config_dir),
            defaults={},
            all_projects=[],
        )

        # Verify the context was populated with all resolved resources
        assert ctx.conn is mock_conn
        assert ctx.dry_run is False
        assert ctx.external_net_id == "net-123"
        assert ctx.external_subnet_id == "subnet-456"
        assert ctx.external_network_map == net_map
        assert ctx.current_mapping_rules == [{"local": [], "remote": []}]
        assert ctx.mapping_exists is True
        assert ctx.static_mapping_rules == [
            {"local": [{"user": {"name": "static"}}], "remote": []}
        ]
        # Verify connection was established
        mock_connect.assert_called_once_with(cloud=None)
        # Verify both resolution functions were called
        mock_build_map.assert_called_once()
        mock_resolve_default.assert_called_once()
        mock_resolve_subnet.assert_called_once()
        mock_resolve_fed.assert_called_once()

    @patch("src.main._connect")
    @patch("src.main._build_external_network_map", return_value={})
    @patch("src.main._resolve_default_external_network", return_value="")
    @patch("src.main._resolve_federation_context")
    def test_passes_cloud_to_connect(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should pass cloud parameter to _connect()."""
        mock_connect.return_value = Mock()
        mock_resolve_fed.return_value = ([], False, [])

        _setup_context(
            dry_run=False,
            config_dir=str(mock_config_dir),
            defaults={},
            all_projects=[],
            cloud="mycloud",
        )

        mock_connect.assert_called_once_with(cloud="mycloud")

    @patch("src.main._connect")
    def test_exits_when_connection_fails(
        self, mock_connect: Mock, mock_config_dir: Path
    ) -> None:
        """Should raise ProvisionerError when OpenStack connection fails."""
        mock_connect.side_effect = Exception("Connection failed")

        with pytest.raises(ProvisionerError):
            _setup_context(
                dry_run=False,
                config_dir=str(mock_config_dir),
                defaults={},
                all_projects=[],
            )

    @patch("src.main._connect")
    @patch("src.main._build_external_network_map")
    def test_exits_when_external_network_resolution_fails(
        self,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should raise ProvisionerError when external network resolution fails."""
        mock_connect.return_value = Mock()
        mock_build_map.side_effect = Exception("Network lookup failed")

        with pytest.raises(ProvisionerError):
            _setup_context(
                dry_run=False,
                config_dir=str(mock_config_dir),
                defaults={},
                all_projects=[],
            )

    @patch("src.main._connect")
    @patch("src.main._build_external_network_map", return_value={})
    @patch("src.main._resolve_default_external_network", return_value="")
    @patch("src.main._resolve_federation_context")
    def test_exits_when_federation_resolution_fails(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should raise ProvisionerError when federation resolution fails."""
        mock_connect.return_value = Mock()
        mock_resolve_fed.side_effect = Exception("Federation failed")

        with pytest.raises(ProvisionerError):
            _setup_context(
                dry_run=False,
                config_dir=str(mock_config_dir),
                defaults={},
                all_projects=[],
            )

    @patch("src.main._connect")
    @patch("src.main._build_external_network_map", return_value={})
    @patch("src.main._resolve_default_external_network", return_value="")
    @patch("src.main._resolve_federation_context")
    def test_continues_when_external_network_not_found(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should continue with empty external_net_id when network not found."""
        mock_connect.return_value = Mock()
        mock_resolve_fed.return_value = ([], False, [])

        ctx = _setup_context(
            dry_run=False,
            config_dir=str(mock_config_dir),
            defaults={},
            all_projects=[],
        )

        assert ctx.external_net_id == ""
        assert ctx.conn is not None


class TestPrintSummary:
    """Tests for _print_summary helper."""

    def test_prints_dry_run_header_offline(self, capsys: pytest.CaptureFixture) -> None:
        """Should print offline dry-run header when conn is None."""
        ctx = SharedContext(conn=None, dry_run=True)

        exit_code = _print_summary(ctx, dry_run=True)

        captured = capsys.readouterr()
        assert "Dry-run: planned changes (offline)" in captured.out
        assert exit_code == 0

    def test_prints_dry_run_header_online(self, capsys: pytest.CaptureFixture) -> None:
        """Should print online dry-run header when conn is present."""
        ctx = SharedContext(conn=Mock(), dry_run=True)

        exit_code = _print_summary(ctx, dry_run=True)

        captured = capsys.readouterr()
        assert "Dry-run: planned changes (live cloud reads)" in captured.out
        assert exit_code == 0

    def test_prints_action_summary(self, capsys: pytest.CaptureFixture) -> None:
        """Should print all actions with status and details."""
        ctx = SharedContext(conn=Mock(), dry_run=False)
        ctx.record(ActionStatus.CREATED, "project", "proj1", "created successfully")
        ctx.record(ActionStatus.SKIPPED, "network", "net1", "already exists")

        _print_summary(ctx, dry_run=False)

        captured = capsys.readouterr()
        # Verify both actions are printed with their details
        assert "CREATED" in captured.out
        assert "project: proj1" in captured.out
        assert "created successfully" in captured.out
        assert "SKIPPED" in captured.out
        assert "network: net1" in captured.out
        assert "already exists" in captured.out

    def test_prints_counts(self, capsys: pytest.CaptureFixture) -> None:
        """Should print summary counts of all action statuses."""
        ctx = SharedContext(conn=Mock(), dry_run=False)
        ctx.record(ActionStatus.CREATED, "project", "p1", "")
        ctx.record(ActionStatus.CREATED, "network", "n1", "")
        ctx.record(ActionStatus.UPDATED, "quota", "q1", "")
        ctx.record(ActionStatus.SKIPPED, "sg", "sg1", "")
        ctx.record(ActionStatus.FAILED, "fip", "f1", "error")

        _print_summary(ctx, dry_run=False)

        captured = capsys.readouterr()
        assert "2 created, 1 updated, 0 deleted, 1 skipped, 1 failed" in captured.out

    def test_returns_1_when_projects_failed(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Should return exit code 1 when any projects failed."""
        ctx = SharedContext(conn=Mock(), dry_run=False)
        ctx.failed_projects.append("project1")

        exit_code = _print_summary(ctx, dry_run=False)

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Failed projects: project1" in captured.err

    def test_returns_0_when_no_failures(self) -> None:
        """Should return exit code 0 when all projects succeeded."""
        ctx = SharedContext(conn=Mock(), dry_run=False)
        ctx.record(ActionStatus.CREATED, "project", "p1", "")

        exit_code = _print_summary(ctx, dry_run=False)

        assert exit_code == 0

    def test_returns_0_with_all_skipped(self, capsys: pytest.CaptureFixture) -> None:
        """Should return exit code 0 when all actions are SKIPPED."""
        ctx = SharedContext(conn=Mock(), dry_run=False)
        ctx.record(ActionStatus.SKIPPED, "project", "p1", "already exists")
        ctx.record(ActionStatus.SKIPPED, "network", "n1", "already exists")
        ctx.record(ActionStatus.SKIPPED, "quotas", None, "no changes")

        exit_code = _print_summary(ctx, dry_run=False)

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "0 created, 0 updated, 0 deleted, 3 skipped, 0 failed" in captured.out

    def test_returns_1_with_mixed_created_and_failed_actions(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Should return exit code 1 when FAILED actions exist in failed_projects."""
        ctx = SharedContext(conn=Mock(), dry_run=False)
        ctx.record(ActionStatus.CREATED, "network", "net1", "created successfully")
        ctx.record(ActionStatus.FAILED, "fip", "fip1", "allocation failed")
        ctx.failed_projects.append("proj1")

        exit_code = _print_summary(ctx, dry_run=False)

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "1 created, 0 updated, 0 deleted, 0 skipped, 1 failed" in captured.out
        assert "Failed projects: proj1" in captured.err


class TestSetupLogging:
    """Tests for verbosity flag and logging setup."""

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    @patch("src.main.setup_logging")
    def test_verbose_flag_sets_info_level(
        self,
        mock_setup_logging: Mock,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should call setup_logging with verbose=1 when -v is specified."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        main(["--config-dir", str(mock_config_dir), "--dry-run", "-v"])

        mock_setup_logging.assert_called_once_with(1)

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    @patch("src.main.setup_logging")
    def test_double_verbose_sets_debug_level(
        self,
        mock_setup_logging: Mock,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should call setup_logging with verbose=2 when -vv is specified."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        main(["--config-dir", str(mock_config_dir), "--dry-run", "-vv"])

        mock_setup_logging.assert_called_once_with(2)

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    @patch("src.main.setup_logging")
    def test_no_verbose_flag_sets_default_level(
        self,
        mock_setup_logging: Mock,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should call setup_logging with verbose=0 when no -v flag is specified."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        main(["--config-dir", str(mock_config_dir), "--dry-run"])

        mock_setup_logging.assert_called_once_with(0)


class TestMainIntegration:
    """Integration tests for main() entry point."""

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_main_dry_run_mode(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Should run in dry-run mode without connecting to OpenStack."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run"])

        assert exit_code == 0
        # Verify _setup_context was called with dry_run=True
        setup_kwargs = mock_setup.call_args[1]
        assert setup_kwargs["dry_run"] is True
        assert setup_kwargs["cloud"] is None
        # Verify reconcile was called with all projects (no filtering)
        reconcile_args = mock_reconcile.call_args[0]
        assert len(reconcile_args[0]) == 1
        assert reconcile_args[0][0].name == "proj1"

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_main_filters_single_project(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should filter to single project when --project is specified."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        proj2 = ProjectConfig.from_dict({"name": "proj2", "resource_prefix": "p2"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1, proj2], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        exit_code = main(
            ["--config-dir", str(mock_config_dir), "--dry-run", "--project", "proj1"]
        )

        assert exit_code == 0
        # Verify reconcile was called with ONLY the filtered project
        reconcile_args = mock_reconcile.call_args[0]
        filtered_projects = reconcile_args[0]
        all_projects = reconcile_args[1]
        assert len(filtered_projects) == 1
        assert filtered_projects[0].name == "proj1"
        # Verify all_projects still contains both (for federation context)
        assert len(all_projects) == 2

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_main_os_cloud_flag(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should pass --os-cloud value to _setup_context."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        exit_code = main(
            ["--config-dir", str(mock_config_dir), "--dry-run", "--os-cloud", "mycloud"]
        )

        assert exit_code == 0
        # Verify cloud= kwarg was passed through to _setup_context
        setup_kwargs = mock_setup.call_args[1]
        assert setup_kwargs["cloud"] == "mycloud"
        assert setup_kwargs["dry_run"] is True

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_main_verbosity_flags(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should accept verbosity flags -v and -vv."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        # Single -v should work
        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "-v"])
        assert exit_code == 0

        # Double -vv should work
        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "-vv"])
        assert exit_code == 0

    def test_main_returns_1_when_filtered_project_not_found(
        self,
        mock_config_dir: Path,
    ) -> None:
        """Should return 1 when filtered project doesn't exist."""
        exit_code = main(
            [
                "--config-dir",
                str(mock_config_dir),
                "--dry-run",
                "--project",
                "nonexistent",
            ]
        )

        assert exit_code == 1

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_main_returns_1_when_projects_fail(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Should return exit code 1 when any projects fail."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_ctx.failed_projects.append("proj1")
        mock_setup.return_value = mock_ctx

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run"])

        assert exit_code == 1
        # Verify the failure was reported to stderr
        captured = capsys.readouterr()
        assert "Failed projects: proj1" in captured.err

    def test_version_flag(self, capsys: pytest.CaptureFixture) -> None:
        """Test --version flag displays version."""
        from src import __version__

        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])

        # argparse --version calls sys.exit(0)
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert f"tenantctl {__version__}" in captured.out

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_connection_closed_after_reconcile(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should close connection after reconcile completes."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_conn = Mock()
        mock_ctx = SharedContext(conn=mock_conn, dry_run=False)
        mock_setup.return_value = mock_ctx

        exit_code = main(["--config-dir", str(mock_config_dir)])

        assert exit_code == 0
        # Verify reconcile was called BEFORE close
        assert mock_reconcile.call_count == 1
        mock_conn.close.assert_called_once()

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_connection_closed_even_when_reconcile_raises(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should close connection even when reconcile raises exception."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_conn = Mock()
        mock_ctx = SharedContext(conn=mock_conn, dry_run=False)
        mock_setup.return_value = mock_ctx
        mock_reconcile.side_effect = Exception("Reconcile failed")

        with pytest.raises(Exception, match="Reconcile failed"):
            main(["--config-dir", str(mock_config_dir)])

        # Verify connection was closed despite the exception (finally block)
        mock_conn.close.assert_called_once()

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_dry_run_mode_no_connection_to_close(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should not attempt to close connection when conn is None (dry-run mode)."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run"])

        assert exit_code == 0
        # Verify reconcile was called even in dry-run mode
        assert mock_reconcile.call_count == 1
        # No assertion on close() since conn is None - just verify it doesn't crash


class TestStateStoreIntegration:
    """Tests for state store integration through the main pipeline."""

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_state_store_passed_to_load_all_projects(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should pass state_store to load_all_projects for state key resolution."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        main(["--config-dir", str(mock_config_dir), "--dry-run"])

        # Verify state_store was passed to load_all_projects
        call_kwargs = mock_load.call_args[1]
        assert "state_store" in call_kwargs
        assert call_kwargs["state_store"] is not None
        # Verify it's a YamlFileStateStore instance
        from src.state_store import YamlFileStateStore

        assert isinstance(call_kwargs["state_store"], YamlFileStateStore)

    @patch("src.main.reconcile")
    @patch("src.main._setup_context")
    @patch("src.main.load_all_projects")
    def test_state_store_passed_to_setup_context(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should pass state_store to _setup_context for SharedContext."""
        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        # load_all_projects returns (projects, defaults) 2-tuple
        mock_load.return_value = ([proj1], {})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = mock_ctx

        main(["--config-dir", str(mock_config_dir), "--dry-run"])

        # Verify state_store was passed to _setup_context
        call_kwargs = mock_setup.call_args[1]
        assert "state_store" in call_kwargs
        assert call_kwargs["state_store"] is not None
        # Verify it's the same instance passed to load_all_projects
        from src.state_store import YamlFileStateStore

        assert isinstance(call_kwargs["state_store"], YamlFileStateStore)


class TestLoadAndFilterEdgeCases:
    """Edge cases for project loading and filtering."""

    @patch("src.main.load_all_projects")
    def test_filter_returns_empty_list_when_no_match(
        self, mock_load: Mock, mock_config_dir: Path
    ) -> None:
        """Should raise ProvisionerError when filter finds no matching projects."""
        mock_load.return_value = (
            [
                ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"}),
                ProjectConfig.from_dict({"name": "proj2", "resource_prefix": "p2"}),
            ],
            {},
        )

        with pytest.raises(ProvisionerError, match="project 'nonexistent' not found"):
            _load_and_filter_projects(str(mock_config_dir), "nonexistent")


class TestResolveDefaultExternalNetworkEdgeCases:
    """Additional edge cases for default external network resolution."""

    def test_configured_network_found_returns_id(self) -> None:
        """Should return network ID when configured network is in the map."""
        net_map = {
            "public-network": "net-456",
            "net-456": "net-456",
        }
        defaults = {"external_network_name": "public-network"}

        result = _resolve_default_external_network(net_map, defaults)

        assert result == "net-456"

    def test_empty_defaults_dict_auto_discovers(self) -> None:
        """Should auto-discover when defaults dict is empty and map has one network."""
        net_map = {
            "auto-discovered": "net-auto-123",
            "net-auto-123": "net-auto-123",
        }
        defaults: dict[str, Any] = {}

        result = _resolve_default_external_network(net_map, defaults)

        assert result == "net-auto-123"
