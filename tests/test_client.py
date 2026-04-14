"""Tests for the TenantCtl library API (src/client.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from src.client import RunResult, TenantCtl
from src.models import DefaultsConfig, ProjectConfig
from src.utils import ActionStatus, ProvisionerError


class TestTenantCtlInit:
    """Tests for TenantCtl constructor."""

    def test_init_stores_all_parameters(self) -> None:
        """__init__ should store all provided keyword-only arguments."""
        mock_store = Mock()
        client = TenantCtl(cloud="mycloud", config_dir="/some/path", state_store=mock_store)
        assert client._config_dir == "/some/path"
        assert client._cloud == "mycloud"
        assert client._state_store is mock_store

        # Bare init defaults
        bare = TenantCtl()
        assert bare._config_dir is None
        assert bare._state_store is None
        assert bare._cloud is None


class TestFromConfigDir:
    """Tests for TenantCtl.from_config_dir classmethod."""

    def test_creates_yaml_state_store_by_default(self) -> None:
        """from_config_dir should create YamlFileStateStore when none provided."""
        from src.state_store import YamlFileStateStore

        client = TenantCtl.from_config_dir("/some/path")
        assert isinstance(client._state_store, YamlFileStateStore)

    def test_stores_config_dir_and_cloud(self) -> None:
        """from_config_dir should store config_dir and cloud."""
        client = TenantCtl.from_config_dir("/some/path", cloud="mycloud")
        assert client._config_dir == "/some/path"
        assert client._cloud == "mycloud"

    def test_accepts_custom_state_store(self) -> None:
        """from_config_dir should use provided state_store."""
        mock_store = Mock()
        client = TenantCtl.from_config_dir("/some/path", state_store=mock_store)
        assert client._state_store is mock_store


class TestFromCloud:
    """Tests for TenantCtl.from_cloud classmethod."""

    def test_from_cloud_defaults(self) -> None:
        """from_cloud should store cloud and default config_dir/state_store to None."""
        client = TenantCtl.from_cloud("mycloud")
        assert client._cloud == "mycloud"
        assert client._config_dir is None
        assert client._state_store is None

    def test_custom_state_store(self) -> None:
        """from_cloud should accept a custom state_store."""
        mock_store = Mock()
        client = TenantCtl.from_cloud(state_store=mock_store)
        assert client._state_store is mock_store

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    def test_run_direct_injection_succeeds(
        self,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """from_cloud() client should work with direct-injection run()."""
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, False)

        client = TenantCtl.from_cloud()
        result = client.run(projects=[proj], all_projects=[proj], dry_run=True, offline=True)

        assert isinstance(result, RunResult)
        assert result.had_connection is False
        assert result.failed_projects == []
        mock_reconcile.assert_called_once()


class TestLoadAndFilterProjects:
    """Tests for TenantCtl._load_and_filter_projects."""

    def test_raises_without_config_dir(self) -> None:
        """Should raise ProvisionerError when config_dir is None."""
        client = TenantCtl()
        with pytest.raises(ProvisionerError, match="config_dir is required"):
            client._load_and_filter_projects(None)

    def test_run_raises_without_config_dir(self) -> None:
        """run() should raise ProvisionerError on bare-init client without config_dir."""
        client = TenantCtl()
        with pytest.raises(ProvisionerError, match="config_dir is required"):
            client.run()

    def test_loads_all_projects_when_no_filter(self, mock_config_dir: Path) -> None:
        """Should load all projects when no filter is specified."""
        client = TenantCtl.from_config_dir(str(mock_config_dir))
        projects, all_projects, defaults = client._load_and_filter_projects(None)

        assert len(projects) == 2
        assert len(all_projects) == 2
        # Verify both projects are present
        project_names = {p.name for p in projects}
        assert project_names == {"project1", "project2"}
        # Verify defaults were loaded as DefaultsConfig
        assert isinstance(defaults, DefaultsConfig)

    def test_filters_to_single_project(self, mock_config_dir: Path) -> None:
        """Should filter to single project when filter is specified."""
        client = TenantCtl.from_config_dir(str(mock_config_dir))
        projects, all_projects, _defaults = client._load_and_filter_projects("project1")

        assert len(projects) == 1
        assert projects[0].name == "project1"
        assert len(all_projects) == 2  # all_projects should still contain both

    def test_raises_when_filtered_project_not_found(self, mock_config_dir: Path) -> None:
        """Should raise ProvisionerError when filtered project doesn't exist."""
        client = TenantCtl.from_config_dir(str(mock_config_dir))
        with pytest.raises(ProvisionerError, match="project 'nonexistent' not found"):
            client._load_and_filter_projects("nonexistent")


class TestLoadAndFilterEdgeCases:
    """Edge cases for project loading and filtering."""

    @patch("src.client.load_all_projects")
    def test_filter_returns_empty_list_when_no_match(self, mock_load: Mock, mock_config_dir: Path) -> None:
        """Should raise ProvisionerError when filter finds no matching projects."""
        mock_load.return_value = (
            [
                ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"}),
                ProjectConfig.from_dict({"name": "proj2", "resource_prefix": "p2"}),
            ],
            DefaultsConfig(),
        )

        client = TenantCtl.from_config_dir(str(mock_config_dir))
        with pytest.raises(ProvisionerError, match="project 'nonexistent' not found"):
            client._load_and_filter_projects("nonexistent")


class TestSetupContext:
    """Tests for TenantCtl._setup_context."""

    def test_offline_dry_run_mode_skips_connection(self, mock_config_dir: Path) -> None:
        """Should create context with None connection in offline dry-run mode."""
        client = TenantCtl.from_config_dir(str(mock_config_dir))
        ctx, owns_connection = client._setup_context(
            DefaultsConfig(),
            [],
            dry_run=True,
            offline=True,
        )

        assert ctx.conn is None
        assert ctx.dry_run is True
        assert owns_connection is False

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-456")
    @patch("src.client.resolve_federation_context")
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

        client = TenantCtl.from_config_dir(str(mock_config_dir))
        ctx, owns_connection = client._setup_context(
            DefaultsConfig(),
            [],
            dry_run=True,
        )

        assert ctx.conn is mock_conn
        assert ctx.dry_run is True
        assert owns_connection is True
        mock_connect.assert_called_once()

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-456")
    @patch("src.client.resolve_federation_context")
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

        client = TenantCtl.from_config_dir(str(mock_config_dir))
        ctx, owns_connection = client._setup_context(
            DefaultsConfig(),
            [],
            dry_run=False,
        )

        # Verify the context was populated with all resolved resources
        assert ctx.conn is mock_conn
        assert ctx.dry_run is False
        assert owns_connection is True
        assert ctx.external_net_id == "net-123"
        assert ctx.external_subnet_id == "subnet-456"
        assert ctx.external_network_map == net_map
        assert ctx.current_mapping_rules == [{"local": [], "remote": []}]
        assert ctx.mapping_exists is True
        assert ctx.static_mapping_rules == [{"local": [{"user": {"name": "static"}}], "remote": []}]
        # Verify connection was established
        mock_connect.assert_called_once_with()
        # Verify both resolution functions were called
        mock_build_map.assert_called_once()
        mock_resolve_default.assert_called_once()
        mock_resolve_subnet.assert_called_once()
        mock_resolve_fed.assert_called_once()

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map", return_value={})
    @patch("src.client.resolve_default_external_network", return_value="")
    @patch("src.client.resolve_external_subnet", return_value="")
    @patch("src.client.resolve_federation_context")
    def test_passes_cloud_to_connect(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should use cloud stored in constructor."""
        mock_connect.return_value = Mock()
        mock_resolve_fed.return_value = ([], False, [])

        client = TenantCtl.from_config_dir(str(mock_config_dir), cloud="mycloud")
        client._setup_context(
            DefaultsConfig(),
            [],
            dry_run=False,
        )

        # _connect is a method on self now, so it uses self._cloud
        mock_connect.assert_called_once_with()
        assert client._cloud == "mycloud"

    @patch("src.client.TenantCtl._connect")
    def test_exits_when_connection_fails(self, mock_connect: Mock, mock_config_dir: Path) -> None:
        """Should raise ProvisionerError when OpenStack connection fails."""
        mock_connect.side_effect = Exception("Connection failed")

        client = TenantCtl.from_config_dir(str(mock_config_dir))
        with pytest.raises(ProvisionerError):
            client._setup_context(
                DefaultsConfig(),
                [],
                dry_run=False,
            )

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map")
    def test_exits_when_external_network_resolution_fails(
        self,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should raise ProvisionerError when external network resolution fails."""
        mock_connect.return_value = Mock()
        mock_build_map.side_effect = Exception("Network lookup failed")

        client = TenantCtl.from_config_dir(str(mock_config_dir))
        with pytest.raises(ProvisionerError):
            client._setup_context(
                DefaultsConfig(),
                [],
                dry_run=False,
            )

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map", return_value={})
    @patch("src.client.resolve_default_external_network", return_value="")
    @patch("src.client.resolve_external_subnet", return_value="")
    @patch("src.client.resolve_federation_context")
    def test_exits_when_federation_resolution_fails(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should raise ProvisionerError when federation resolution fails."""
        mock_connect.return_value = Mock()
        mock_resolve_fed.side_effect = Exception("Federation failed")

        client = TenantCtl.from_config_dir(str(mock_config_dir))
        with pytest.raises(ProvisionerError):
            client._setup_context(
                DefaultsConfig(),
                [],
                dry_run=False,
            )

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map", return_value={})
    @patch("src.client.resolve_default_external_network", return_value="")
    @patch("src.client.resolve_external_subnet", return_value="")
    @patch("src.client.resolve_federation_context")
    def test_continues_when_external_network_not_found(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should continue with empty external_net_id when network not found."""
        mock_connect.return_value = Mock()
        mock_resolve_fed.return_value = ([], False, [])

        client = TenantCtl.from_config_dir(str(mock_config_dir))
        ctx, owns_connection = client._setup_context(
            DefaultsConfig(),
            [],
            dry_run=False,
        )

        assert ctx.external_net_id == ""
        assert ctx.conn is not None
        assert owns_connection is True


class TestStateStoreIntegration:
    """Tests for state store integration through the client pipeline."""

    def test_default_state_store_passed_to_load(self, mock_config_dir: Path) -> None:
        """Client's default state_store should be used in _load_and_filter_projects."""
        from src.state_store import YamlFileStateStore

        client = TenantCtl.from_config_dir(str(mock_config_dir))
        assert isinstance(client._state_store, YamlFileStateStore)

        # Verify it works end-to-end (load succeeds with state_store)
        projects, _, _ = client._load_and_filter_projects(None)
        assert len(projects) == 2

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map", return_value={})
    @patch("src.client.resolve_default_external_network", return_value="")
    @patch("src.client.resolve_federation_context", return_value=([], False, []))
    def test_state_store_passed_to_setup_context(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Client's state_store should flow into SharedContext in normal mode."""
        mock_connect.return_value = Mock()
        mock_store = Mock()

        client = TenantCtl.from_config_dir(str(mock_config_dir), state_store=mock_store)
        ctx, _ = client._setup_context(DefaultsConfig(), [], dry_run=False)

        assert ctx.state_store is mock_store


class TestTenantCtlRun:
    """Tests for TenantCtl.run() full pipeline."""

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    @patch("src.client.TenantCtl._load_and_filter_projects")
    def test_returns_run_result(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """run() should return a RunResult with actions and failed_projects."""
        from src.utils import SharedContext

        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        mock_load.return_value = ([proj1], [proj1], DefaultsConfig())
        mock_conn = Mock()
        mock_ctx = SharedContext(conn=mock_conn, dry_run=False)
        mock_ctx.record(ActionStatus.CREATED, "project", "proj1")
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl(config_dir="/config")
        result = client.run(dry_run=False)

        assert isinstance(result, RunResult)
        assert len(result.actions) == 1
        assert result.actions[0].status == ActionStatus.CREATED
        assert result.failed_projects == []
        assert result.had_connection is True
        mock_conn.close.assert_called_once()

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    @patch("src.client.TenantCtl._load_and_filter_projects")
    def test_connection_closed_even_on_reconcile_failure(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """run() should close connection even when reconcile raises."""
        from src.utils import SharedContext

        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        mock_load.return_value = ([proj1], [proj1], DefaultsConfig())
        mock_conn = Mock()
        mock_ctx = SharedContext(conn=mock_conn, dry_run=False)
        mock_setup.return_value = (mock_ctx, True)
        mock_reconcile.side_effect = Exception("Reconcile failed")

        client = TenantCtl(config_dir="/config")
        with pytest.raises(Exception, match="Reconcile failed"):
            client.run(dry_run=False)

        mock_conn.close.assert_called_once()

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    @patch("src.client.TenantCtl._load_and_filter_projects")
    def test_offline_dry_run_no_connection_to_close(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """run() should not crash when conn is None (offline dry-run)."""
        from src.utils import SharedContext

        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        mock_load.return_value = ([proj1], [proj1], DefaultsConfig())
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl(config_dir="/config")
        result = client.run(dry_run=True, offline=True)

        assert result.had_connection is False
        assert result.failed_projects == []

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    @patch("src.client.TenantCtl._load_and_filter_projects")
    def test_passes_project_filter(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """run() should pass project filter to _load_and_filter_projects."""
        from src.utils import SharedContext

        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        mock_load.return_value = ([proj1], [proj1], DefaultsConfig())
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl(config_dir="/config")
        client.run(project="proj1", dry_run=True, offline=True)

        mock_load.assert_called_once_with("proj1")

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    @patch("src.client.TenantCtl._load_and_filter_projects")
    def test_failed_projects_in_result(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """run() should propagate failed_projects from SharedContext."""
        from src.utils import SharedContext

        proj1 = ProjectConfig.from_dict({"name": "proj1", "resource_prefix": "p1"})
        mock_load.return_value = ([proj1], [proj1], DefaultsConfig())
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_ctx.failed_projects.append("proj1")
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl(config_dir="/config")
        result = client.run(dry_run=True, offline=True)

        assert result.failed_projects == ["proj1"]


class TestRunValidation:
    """Tests for run() parameter validation (mutual exclusivity, co-requirements)."""

    def test_project_and_projects_mutually_exclusive(self) -> None:
        """Passing both 'project' (str) and 'projects' (list) should raise."""
        client = TenantCtl()
        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        with pytest.raises(ProvisionerError, match="mutually exclusive"):
            client.run(project="p1", projects=[proj], all_projects=[proj])

    def test_projects_without_all_projects_raises(self) -> None:
        """Passing 'projects' without 'all_projects' should raise."""
        client = TenantCtl()
        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        with pytest.raises(ProvisionerError, match="must be provided together"):
            client.run(projects=[proj])

    def test_all_projects_without_projects_raises(self) -> None:
        """Passing 'all_projects' without 'projects' should raise."""
        client = TenantCtl()
        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        with pytest.raises(ProvisionerError, match="must be provided together"):
            client.run(all_projects=[proj])

    def test_defaults_without_projects_raises(self) -> None:
        """Passing 'defaults' without 'projects'/'all_projects' should raise."""
        client = TenantCtl(config_dir="/config")
        with pytest.raises(ProvisionerError, match="can only be used with"):
            client.run(defaults=DefaultsConfig())


class TestRunDirectInjection:
    """Tests for run() direct-injection mode (bypasses YAML loading)."""

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    @patch("src.client.TenantCtl._load_and_filter_projects")
    def test_skips_config_loading(
        self,
        mock_load: Mock,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """Direct-injection mode should NOT call _load_and_filter_projects."""
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl()
        client.run(projects=[proj], all_projects=[proj], dry_run=True, offline=True)

        mock_load.assert_not_called()

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    def test_defaults_to_empty_defaults_config(
        self,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """Omitting 'defaults' in direct mode should use DefaultsConfig()."""
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl()
        client.run(projects=[proj], all_projects=[proj], dry_run=True, offline=True)

        # _setup_context receives defaults as first positional arg
        call_args = mock_setup.call_args
        assert call_args[0][0] == DefaultsConfig()

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    def test_with_explicit_defaults(
        self,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """Explicit DefaultsConfig should be passed through to _setup_context."""
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        custom_defaults = DefaultsConfig(external_network_name="my-ext-net")
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl()
        client.run(
            projects=[proj],
            all_projects=[proj],
            defaults=custom_defaults,
            dry_run=True,
            offline=True,
        )

        call_args = mock_setup.call_args
        assert call_args[0][0] is custom_defaults

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    def test_returns_run_result(
        self,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """Direct-injection run should return a populated RunResult."""
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        mock_conn = Mock()
        mock_ctx = SharedContext(conn=mock_conn, dry_run=False)
        mock_ctx.record(ActionStatus.CREATED, "project", "p1")
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl()
        result = client.run(projects=[proj], all_projects=[proj])

        assert isinstance(result, RunResult)
        assert len(result.actions) == 1
        assert result.had_connection is True
        mock_conn.close.assert_called_once()

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    def test_connection_closed_on_failure(
        self,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """Connection should be closed even when reconcile raises."""
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        mock_conn = Mock()
        mock_ctx = SharedContext(conn=mock_conn, dry_run=False)
        mock_setup.return_value = (mock_ctx, True)
        mock_reconcile.side_effect = Exception("boom")

        client = TenantCtl()
        with pytest.raises(Exception, match="boom"):
            client.run(projects=[proj], all_projects=[proj])

        mock_conn.close.assert_called_once()


class TestSetupContextConfigDir:
    """Tests for _setup_context handling of config_dir=None."""

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map", return_value={})
    @patch("src.client.resolve_default_external_network", return_value="")
    @patch("src.client.resolve_external_subnet", return_value="")
    @patch("src.client.resolve_federation_context", return_value=([], False, []))
    def test_no_config_dir_no_static_files_succeeds(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
    ) -> None:
        """config_dir=None with no static mapping files should succeed."""
        mock_connect.return_value = Mock()

        client = TenantCtl()  # no config_dir
        ctx, owns_connection = client._setup_context(DefaultsConfig(), [], dry_run=False)

        assert ctx.conn is not None
        assert owns_connection is True
        # resolve_federation_context receives config_dir=None
        mock_resolve_fed.assert_called_once_with(mock_connect.return_value, None, DefaultsConfig(), [])

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map", return_value={})
    @patch("src.client.resolve_default_external_network", return_value="")
    @patch("src.client.resolve_external_subnet", return_value="")
    def test_no_config_dir_with_static_files_raises(
        self,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
    ) -> None:
        """config_dir=None with static mapping file patterns should raise."""
        mock_connect.return_value = Mock()

        defaults = DefaultsConfig(federation_static_mapping_files=("mappings/*.json",))
        client = TenantCtl()  # no config_dir
        with pytest.raises(ProvisionerError, match="Failed to resolve federation mapping") as exc_info:
            client._setup_context(defaults, [], dry_run=False)
        # The root cause should mention the static mapping files
        assert "federation_static_mapping_files" in str(exc_info.value.__cause__)


class TestRunWithScopes:
    """Tests for run() with the ``only`` parameter."""

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    def test_only_passed_to_reconcile(
        self,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """run(only={QUOTAS}) passes scopes={QUOTAS} to reconcile()."""
        from src.reconciler import ReconcileScope
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl()
        client.run(
            projects=[proj],
            all_projects=[proj],
            dry_run=True,
            offline=True,
            only={ReconcileScope.QUOTAS},
        )

        mock_reconcile.assert_called_once_with(
            [proj],
            [proj],
            mock_ctx,
            scopes={ReconcileScope.QUOTAS},
        )

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    def test_only_none_default(
        self,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """run() without only passes scopes=None to reconcile()."""
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl()
        client.run(projects=[proj], all_projects=[proj], dry_run=True, offline=True)

        mock_reconcile.assert_called_once_with(
            [proj],
            [proj],
            mock_ctx,
            scopes=None,
        )

    def test_invalid_only_raises_before_connect(self) -> None:
        """run(only={"bogus"}) raises ValueError without making any API calls."""
        client = TenantCtl()
        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        with pytest.raises(ValueError, match="bogus"):
            client.run(projects=[proj], all_projects=[proj], only={"bogus"})


class TestConnectionInjection:
    """Tests for connection injection feature (connection parameter to run())."""

    @patch("src.client.reconcile")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_provided_connection_used_and_not_closed(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """When connection is provided, TenantCtl uses it but does NOT close it."""
        mock_conn = Mock()
        mock_build_map.return_value = {"public": "net-123"}
        mock_resolve_default.return_value = "net-123"
        mock_resolve_fed.return_value = ([], False, [])

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        client = TenantCtl.from_cloud()
        result = client.run(
            projects=[proj],
            all_projects=[proj],
            connection=mock_conn,
        )

        # Verify connection was used
        assert result.had_connection is True
        mock_build_map.assert_called_once_with(mock_conn)

        # Verify connection was NOT closed (caller owns it)
        mock_conn.close.assert_not_called()

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_no_connection_creates_and_closes(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """When no connection provided, TenantCtl creates and closes it."""
        mock_conn = Mock()
        mock_connect.return_value = mock_conn
        mock_build_map.return_value = {"public": "net-123"}
        mock_resolve_default.return_value = "net-123"
        mock_resolve_fed.return_value = ([], False, [])

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        client = TenantCtl.from_cloud()
        client.run(projects=[proj], all_projects=[proj])

        # Verify connection was created
        mock_connect.assert_called_once()

        # Verify connection was closed (TenantCtl owns it)
        mock_conn.close.assert_called_once()

    def test_connection_and_offline_mutually_exclusive(self) -> None:
        """Passing both connection and offline=True should raise error."""
        mock_conn = Mock()
        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        client = TenantCtl.from_cloud()

        with pytest.raises(ProvisionerError, match="mutually exclusive"):
            client.run(
                projects=[proj],
                all_projects=[proj],
                connection=mock_conn,
                offline=True,
            )

    @patch("src.client.reconcile")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_connection_in_dry_run(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """Provided connection works in dry-run mode."""
        mock_conn = Mock()
        mock_build_map.return_value = {}
        mock_resolve_default.return_value = ""
        mock_resolve_fed.return_value = ([], False, [])

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        client = TenantCtl.from_cloud()
        result = client.run(
            projects=[proj],
            all_projects=[proj],
            connection=mock_conn,
            dry_run=True,
        )

        assert result.had_connection is True
        mock_conn.close.assert_not_called()

    @patch("src.client.reconcile")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_connection_with_cloud_warns(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_reconcile: Mock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When both connection and cloud are provided, warn user."""
        mock_conn = Mock()
        mock_build_map.return_value = {}
        mock_resolve_default.return_value = ""
        mock_resolve_fed.return_value = ([], False, [])

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        client = TenantCtl.from_cloud(cloud="mycloud")
        client.run(
            projects=[proj],
            all_projects=[proj],
            connection=mock_conn,
        )

        # Should warn about ignoring cloud
        assert "ignoring cloud" in caplog.text.lower()
        assert "mycloud" in caplog.text

    @patch("src.client.reconcile")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_provided_connection_not_closed_on_reconcile_error(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """Even when reconcile fails, provided connection is NOT closed."""
        mock_conn = Mock()
        mock_build_map.return_value = {}
        mock_resolve_default.return_value = ""
        mock_resolve_fed.return_value = ([], False, [])
        mock_reconcile.side_effect = Exception("Reconcile failed")

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        client = TenantCtl.from_cloud()

        with pytest.raises(Exception, match="Reconcile failed"):
            client.run(
                projects=[proj],
                all_projects=[proj],
                connection=mock_conn,
            )

        # Verify connection was NOT closed (caller still owns it)
        mock_conn.close.assert_not_called()

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_created_connection_closed_on_reconcile_error(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """When TenantCtl creates connection, it's closed even on error."""
        mock_conn = Mock()
        mock_connect.return_value = mock_conn
        mock_build_map.return_value = {}
        mock_resolve_default.return_value = ""
        mock_resolve_fed.return_value = ([], False, [])
        mock_reconcile.side_effect = Exception("Reconcile failed")

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        client = TenantCtl.from_cloud()

        with pytest.raises(Exception, match="Reconcile failed"):
            client.run(projects=[proj], all_projects=[proj])

        # Verify connection WAS closed (TenantCtl owns it)
        mock_conn.close.assert_called_once()


class TestSetupContextConnectionInjection:
    """Tests for _setup_context with connection parameter."""

    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_provided_connection_returns_false_ownership(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
    ) -> None:
        """When connection is provided, _setup_context returns owns_connection=False."""
        mock_conn = Mock()
        mock_build_map.return_value = {}
        mock_resolve_default.return_value = ""
        mock_resolve_fed.return_value = ([], False, [])

        client = TenantCtl.from_cloud()
        ctx, owns_connection = client._setup_context(
            DefaultsConfig(),
            [],
            connection=mock_conn,
            dry_run=False,
        )

        assert ctx.conn is mock_conn
        assert owns_connection is False

    @patch("src.client.TenantCtl._connect")
    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_created_connection_returns_true_ownership(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
        mock_connect: Mock,
    ) -> None:
        """When connection is created, _setup_context returns owns_connection=True."""
        mock_conn = Mock()
        mock_connect.return_value = mock_conn
        mock_build_map.return_value = {}
        mock_resolve_default.return_value = ""
        mock_resolve_fed.return_value = ([], False, [])

        client = TenantCtl.from_cloud()
        ctx, owns_connection = client._setup_context(
            DefaultsConfig(),
            [],
            dry_run=False,
        )

        assert ctx.conn is mock_conn
        assert owns_connection is True

    def test_offline_mode_returns_false_ownership(self) -> None:
        """Offline mode returns owns_connection=False (no connection created)."""
        client = TenantCtl.from_cloud()
        ctx, owns_connection = client._setup_context(
            DefaultsConfig(),
            [],
            dry_run=True,
            offline=True,
        )

        assert ctx.conn is None
        assert owns_connection is False

    @patch("src.client.build_external_network_map")
    @patch("src.client.resolve_default_external_network")
    @patch("src.client.resolve_external_subnet", return_value="subnet-123")
    @patch("src.client.resolve_federation_context")
    def test_provided_connection_in_dry_run(
        self,
        mock_resolve_fed: Mock,
        mock_resolve_subnet: Mock,
        mock_resolve_default: Mock,
        mock_build_map: Mock,
    ) -> None:
        """Provided connection works in dry-run mode."""
        mock_conn = Mock()
        mock_build_map.return_value = {}
        mock_resolve_default.return_value = ""
        mock_resolve_fed.return_value = ([], False, [])

        client = TenantCtl.from_cloud()
        ctx, owns_connection = client._setup_context(
            DefaultsConfig(),
            [],
            connection=mock_conn,
            dry_run=True,
        )

        assert ctx.conn is mock_conn
        assert ctx.dry_run is True
        assert owns_connection is False


class TestRunScopeDependencies:
    """Tests for run() with scope dependency enforcement."""

    def test_run_raises_on_missing_scope_deps(self) -> None:
        """run(only={FIPS}) raises ValueError because NETWORK is missing."""
        from src.reconciler import ReconcileScope

        client = TenantCtl()
        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        with pytest.raises(ValueError, match="network"):
            client.run(projects=[proj], all_projects=[proj], only={ReconcileScope.FIPS})

    @patch("src.client.reconcile")
    @patch("src.client.TenantCtl._setup_context")
    def test_run_auto_expand_deps(
        self,
        mock_setup: Mock,
        mock_reconcile: Mock,
    ) -> None:
        """run(only={FIPS}, auto_expand_deps=True) succeeds with expanded scopes."""
        from src.reconciler import ReconcileScope
        from src.utils import SharedContext

        proj = ProjectConfig.from_dict({"name": "p1", "resource_prefix": "p1"})
        mock_ctx = SharedContext(conn=None, dry_run=True)
        mock_setup.return_value = (mock_ctx, True)

        client = TenantCtl()
        client.run(
            projects=[proj],
            all_projects=[proj],
            dry_run=True,
            offline=True,
            only={ReconcileScope.FIPS},
            auto_expand_deps=True,
        )

        mock_reconcile.assert_called_once_with(
            [proj],
            [proj],
            mock_ctx,
            scopes={ReconcileScope.FIPS, ReconcileScope.NETWORK},
        )
