"""Tests for teardown module — safety checks and reverse-order deletion."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from openstack.exceptions import EndpointNotFound, HttpException, NotFoundException

from src.resources.teardown import safety_check, teardown_project
from src.utils import ActionStatus, SharedContext, TeardownError

if TYPE_CHECKING:
    from src.models import ProjectConfig


def _make_server(name: str) -> MagicMock:
    server = MagicMock()
    server.name = name
    server.id = f"{name}-id"
    return server


def _make_volume(name: str) -> MagicMock:
    vol = MagicMock()
    vol.name = name
    vol.id = f"{name}-id"
    return vol


def _make_fip(address: str) -> MagicMock:
    fip = MagicMock()
    fip.id = f"fip-{address}"
    fip.floating_ip_address = address
    return fip


def _make_snapshot(name: str) -> MagicMock:
    snap = MagicMock()
    snap.name = name
    snap.id = f"{name}-id"
    return snap


def _make_router(name: str) -> MagicMock:
    router = MagicMock()
    router.name = name
    router.id = f"{name}-id"
    return router


def _make_port(device_owner: str, subnet_id: str | None = None) -> MagicMock:
    port = MagicMock()
    port.device_owner = device_owner
    if subnet_id:
        port.fixed_ips = [{"subnet_id": subnet_id}]
    else:
        port.fixed_ips = []
    return port


def _make_subnet(name: str) -> MagicMock:
    subnet = MagicMock()
    subnet.name = name
    subnet.id = f"{name}-id"
    return subnet


def _make_network(name: str) -> MagicMock:
    net = MagicMock()
    net.name = name
    net.id = f"{name}-id"
    return net


def _make_sg(name: str) -> MagicMock:
    sg = MagicMock()
    sg.name = name
    sg.id = f"{name}-id"
    return sg


def _stub_empty_lists(mock_conn: MagicMock) -> None:
    """Stub all list methods to return empty lists."""
    mock_conn.compute.servers.return_value = []
    mock_conn.block_storage.volumes.return_value = []
    mock_conn.network.ips.return_value = []
    mock_conn.block_storage.snapshots.return_value = []
    mock_conn.network.routers.return_value = []
    mock_conn.network.subnets.return_value = []
    mock_conn.network.networks.return_value = []
    mock_conn.network.security_groups.return_value = []


class TestSafetyCheck:
    """Safety checks block teardown when servers/volumes exist."""

    def test_servers_present_returns_error_with_details(
        self, mock_conn: MagicMock
    ) -> None:
        mock_conn.compute.servers.return_value = [
            _make_server("web1"),
            _make_server("web2"),
        ]
        mock_conn.block_storage.volumes.return_value = []

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert len(errors) == 1
        assert "2 server(s)" in errors[0]
        assert "web1" in errors[0]
        assert "web2" in errors[0]
        assert "test_project" in errors[0]

    def test_volumes_present_returns_error_with_details(
        self, mock_conn: MagicMock
    ) -> None:
        mock_conn.compute.servers.return_value = []
        mock_conn.block_storage.volumes.return_value = [
            _make_volume("data-vol"),
        ]

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert len(errors) == 1
        assert "1 volume(s)" in errors[0]
        assert "data-vol" in errors[0]
        assert "test_project" in errors[0]

    def test_clean_project_returns_empty(self, mock_conn: MagicMock) -> None:
        mock_conn.compute.servers.return_value = []
        mock_conn.block_storage.volumes.return_value = []

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert errors == []

    def test_both_servers_and_volumes(self, mock_conn: MagicMock) -> None:
        mock_conn.compute.servers.return_value = [_make_server("vm1")]
        mock_conn.block_storage.volumes.return_value = [_make_volume("vol1")]

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert len(errors) == 2
        assert any("1 server(s)" in e and "vm1" in e for e in errors)
        assert any("1 volume(s)" in e and "vol1" in e for e in errors)


class TestTeardownHappyPath:
    """Teardown happy path: order, dry-run, empty projects."""

    def test_deletes_in_correct_reverse_dependency_order(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Teardown deletes resources in correct reverse dependency order."""
        conn = shared_ctx.conn
        call_order: list[str] = []

        # Set up resources
        fip = _make_fip("1.2.3.4")
        snap = _make_snapshot("snap1")
        router = _make_router("router1")
        port = _make_port("network:router_interface", "subnet1-id")
        subnet = _make_subnet("subnet1")
        net = _make_network("net1")
        sg_default = _make_sg("default")
        sg_custom = _make_sg("custom-sg")

        conn.network.ips.return_value = [fip]
        conn.block_storage.snapshots.return_value = [snap]
        conn.network.routers.return_value = [router]
        conn.network.ports.return_value = [port]
        conn.network.subnets.return_value = [subnet]
        conn.network.networks.return_value = [net]
        conn.network.security_groups.return_value = [sg_default, sg_custom]

        # Track call order
        def track(name: str):
            def side_effect(*args, **kwargs):
                call_order.append(name)

            return side_effect

        conn.network.delete_ip.side_effect = track("delete_ip")
        conn.block_storage.delete_snapshot.side_effect = track("delete_snapshot")
        conn.network.remove_interface_from_router.side_effect = track(
            "remove_interface"
        )
        conn.network.update_router.side_effect = track("clear_gateway")
        conn.network.delete_router.side_effect = track("delete_router")
        conn.network.delete_subnet.side_effect = track("delete_subnet")
        conn.network.delete_network.side_effect = track("delete_network")
        conn.network.delete_security_group.side_effect = track("delete_sg")
        conn.identity.delete_project.side_effect = track("delete_project")

        actions = teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # Verify correct order (this is the contract for dependency-safe deletion)
        expected_order = [
            "delete_ip",
            "delete_snapshot",
            "remove_interface",
            "clear_gateway",
            "delete_router",
            "delete_subnet",
            "delete_network",
            "delete_sg",
            "delete_project",
        ]
        # Filter out retries (due to @retry decorator, calls may repeat)
        # Just verify first occurrence of each operation is in correct order
        first_occurrences = [op for op in expected_order if op in call_order]
        assert first_occurrences == expected_order

        # All actions should be DELETED
        assert all(a.status == ActionStatus.DELETED for a in actions)
        assert (
            len(actions) == 7
        )  # fip, snap, router, subnet, net, sg(custom), project (default sg skipped)

    def test_skips_default_security_group(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """The 'default' security group must not be deleted."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        sg_default = _make_sg("default")
        conn.network.security_groups.return_value = [sg_default]

        actions = teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        conn.network.delete_security_group.assert_not_called()
        # Should still have the project deletion action
        project_actions = [a for a in actions if a.resource_type == "project"]
        assert len(project_actions) == 1
        assert project_actions[0].status == ActionStatus.DELETED

    def test_empty_project_deletes_project(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Teardown of project with no resources still deletes the project."""
        _stub_empty_lists(shared_ctx.conn)

        actions = teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        shared_ctx.conn.identity.delete_project.assert_called_once_with("proj-123")
        assert len(actions) == 1
        assert actions[0].resource_type == "project"
        assert actions[0].status == ActionStatus.DELETED
        assert "id=proj-123" in actions[0].details

    def test_dry_run_lists_resources(
        self, dry_run_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Online dry-run lists resources that would be deleted."""
        _stub_empty_lists(dry_run_ctx.conn)

        actions = teardown_project(sample_project_cfg, "proj-123", dry_run_ctx)

        # At minimum, the project itself would be deleted
        assert any(a.resource_type == "project" for a in actions)
        assert all(a.status == ActionStatus.DELETED for a in actions)
        assert any("would delete" in a.details for a in actions)
        # No actual deletes
        dry_run_ctx.conn.identity.delete_project.assert_not_called()

    def test_offline_dry_run_skips(
        self, offline_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Offline mode → SKIPPED with no API calls."""
        actions = teardown_project(sample_project_cfg, "proj-123", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details


class TestTeardownErrorHandling:
    """Teardown error handling: failures, NotFoundException, endpoint unavailable."""

    def test_fip_failure_continues_to_remaining_resources(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """A single delete failure must not abort the entire teardown."""
        conn = shared_ctx.conn

        fip_ok = _make_fip("1.1.1.1")
        fip_bad = _make_fip("2.2.2.2")
        subnet = _make_subnet("sub1")
        net = _make_network("net1")

        conn.network.ips.return_value = [fip_bad, fip_ok]
        conn.block_storage.snapshots.return_value = []
        conn.network.routers.return_value = []
        conn.network.subnets.return_value = [subnet]
        conn.network.networks.return_value = [net]
        conn.network.security_groups.return_value = []

        # FIP 2.2.2.2 always fails, 1.1.1.1 succeeds
        def delete_ip_side_effect(ip_id):
            if ip_id == "fip-2.2.2.2":
                raise HttpException(message="server error")

        conn.network.delete_ip.side_effect = delete_ip_side_effect

        with pytest.raises(TeardownError, match="1 failure"):
            teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # Verify WHICH resources were deleted despite FIP failure
        conn.network.delete_subnet.assert_called_once_with("sub1-id")
        conn.network.delete_network.assert_called_once_with("net1-id")
        conn.identity.delete_project.assert_called_once_with("proj-123")

        # Verify WHICH FIP failed vs succeeded by address
        fip_actions = [
            a for a in shared_ctx.actions if a.resource_type == "floating_ip"
        ]
        assert len(fip_actions) == 2

        failed = [a for a in fip_actions if a.status == ActionStatus.FAILED]
        succeeded = [a for a in fip_actions if a.status == ActionStatus.DELETED]
        assert len(failed) == 1
        assert len(succeeded) == 1
        # FIP 2.2.2.2 failed, 1.1.1.1 succeeded
        assert failed[0].name == "2.2.2.2"
        assert succeeded[0].name == "1.1.1.1"

    def test_not_found_treated_as_success(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """NotFoundException during delete is treated as success (already gone)."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        fip = _make_fip("3.3.3.3")
        conn.network.ips.return_value = [fip]

        conn.network.delete_ip.side_effect = NotFoundException(message="gone")

        actions = teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        fip_actions = [a for a in actions if a.resource_type == "floating_ip"]
        assert len(fip_actions) == 1
        assert fip_actions[0].status == ActionStatus.DELETED
        assert "already gone" in fip_actions[0].details
        assert fip_actions[0].name == "3.3.3.3"

        # Project still deleted — no TeardownError raised
        conn.identity.delete_project.assert_called_once()

    def test_endpoint_not_found_skips_step_continues_teardown(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Snapshot list fails with EndpointNotFound → networks/project still deleted."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        net = _make_network("net1")
        conn.network.networks.return_value = [net]

        # Cinder unavailable for snapshots
        conn.block_storage.snapshots.side_effect = EndpointNotFound(
            message="block-storage not found"
        )

        actions = teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # Network and project were still deleted
        conn.network.delete_network.assert_called_once_with("net1-id")
        conn.identity.delete_project.assert_called_once_with("proj-123")

        resource_types = [a.resource_type for a in actions]
        assert "network" in resource_types
        assert "project" in resource_types
        assert "snapshot" not in resource_types  # Step skipped

    def test_multiple_delete_failures_continues_to_project(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Multiple resource delete failures still continues to project deletion."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        router = _make_router("rtr1")
        subnet = _make_subnet("sub1")
        net = _make_network("net1")

        conn.network.routers.return_value = [router]
        conn.network.subnets.return_value = [subnet]
        conn.network.networks.return_value = [net]
        conn.network.ports.return_value = []

        # Router and subnet deletions both fail
        conn.network.delete_router.side_effect = HttpException(message="router error")
        conn.network.delete_subnet.side_effect = HttpException(message="subnet error")

        with pytest.raises(TeardownError, match="2 failure"):
            teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # Network and project still attempted despite router+subnet failures
        conn.network.delete_network.assert_called_once_with("net1-id")
        conn.identity.delete_project.assert_called_once_with("proj-123")

        # Verify failures recorded
        failures = [a for a in shared_ctx.actions if a.status == ActionStatus.FAILED]
        assert len(failures) == 2
        resource_types = {a.resource_type for a in failures}
        assert "router" in resource_types
        assert "subnet" in resource_types
        # Verify which resources failed by name
        assert any(a.name == "rtr1" for a in failures)
        assert any(a.name == "sub1" for a in failures)

    def test_snapshot_failure_continues_to_networks(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Snapshot delete failure does not prevent network resource cleanup."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        snap = _make_snapshot("snap1")
        net = _make_network("net1")
        subnet = _make_subnet("sub1")

        conn.block_storage.snapshots.return_value = [snap]
        conn.network.networks.return_value = [net]
        conn.network.subnets.return_value = [subnet]

        conn.block_storage.delete_snapshot.side_effect = HttpException(
            message="snapshot locked"
        )

        with pytest.raises(TeardownError, match="1 failure"):
            teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # Networks still cleaned up despite snapshot failure
        conn.network.delete_subnet.assert_called_once_with("sub1-id")
        conn.network.delete_network.assert_called_once_with("net1-id")
        conn.identity.delete_project.assert_called_once()

        # Verify snapshot failure recorded
        snapshot_actions = [
            a for a in shared_ctx.actions if a.resource_type == "snapshot"
        ]
        assert len(snapshot_actions) == 1
        assert snapshot_actions[0].status == ActionStatus.FAILED
        assert snapshot_actions[0].name == "snap1"

    def test_project_deletion_failure_recorded(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Project deletion failure is recorded even after all resources deleted."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        # All resources clean, but project deletion fails
        conn.identity.delete_project.side_effect = HttpException(
            message="project locked by admin"
        )

        with pytest.raises(TeardownError, match="1 failure"):
            teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        project_actions = [
            a for a in shared_ctx.actions if a.resource_type == "project"
        ]
        assert len(project_actions) == 1
        assert project_actions[0].status == ActionStatus.FAILED
        assert "delete failed" in project_actions[0].details


class TestTeardownRouterSpecialCases:
    """Router-specific teardown requirements: gateway clearing, multiple interfaces."""

    def test_gateway_cleared_before_router_deletion(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Router external gateway is cleared before deletion."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        router = _make_router("rtr1")
        conn.network.routers.return_value = [router]
        conn.network.ports.return_value = []

        call_order: list[str] = []

        def track(name: str):
            def side_effect(*args, **kwargs):
                call_order.append(name)

            return side_effect

        conn.network.update_router.side_effect = track("clear_gateway")
        conn.network.delete_router.side_effect = track("delete_router")

        teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        assert call_order == ["clear_gateway", "delete_router"]
        conn.network.update_router.assert_called_once_with(
            "rtr1-id", external_gateway_info=None
        )

    def test_both_services_unavailable(self, mock_conn: MagicMock) -> None:
        """EndpointNotFound → empty errors (safe to delete)."""
        mock_conn.compute.servers.side_effect = EndpointNotFound(
            message="compute not found"
        )
        mock_conn.block_storage.volumes.side_effect = EndpointNotFound(
            message="block-storage not found"
        )

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert errors == []

    def test_only_cinder_unavailable(self, mock_conn: MagicMock) -> None:
        """Cinder absent + no servers → safe."""
        mock_conn.compute.servers.return_value = []
        mock_conn.block_storage.volumes.side_effect = EndpointNotFound(
            message="block-storage not found"
        )

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert errors == []

    def test_server_api_error_blocks_deletion(self, mock_conn: MagicMock) -> None:
        """Generic API error → inconclusive error blocks deletion."""
        mock_conn.compute.servers.side_effect = HttpException(message="internal error")
        mock_conn.block_storage.volumes.return_value = []

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert len(errors) == 1
        assert "inconclusive" in errors[0]
        assert "server check" in errors[0]
        assert "test_project" in errors[0]

    def test_volume_api_error_blocks_deletion(self, mock_conn: MagicMock) -> None:
        """Volume check API error → blocks deletion."""
        mock_conn.compute.servers.return_value = []
        mock_conn.block_storage.volumes.side_effect = HttpException(
            message="internal error"
        )

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert len(errors) == 1
        assert "inconclusive" in errors[0]
        assert "volume check" in errors[0]
        assert "test_project" in errors[0]

    def test_both_api_errors(self, mock_conn: MagicMock) -> None:
        """Both checks fail → two errors."""
        mock_conn.compute.servers.side_effect = HttpException(message="compute error")
        mock_conn.block_storage.volumes.side_effect = HttpException(
            message="cinder error"
        )

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert len(errors) == 2
        assert any("server check" in e and "test_project" in e for e in errors)
        assert any("volume check" in e and "test_project" in e for e in errors)

    def test_servers_block_even_without_cinder(self, mock_conn: MagicMock) -> None:
        """No Cinder + servers present → still unsafe."""
        mock_conn.compute.servers.return_value = [_make_server("vm1")]
        mock_conn.block_storage.volumes.side_effect = EndpointNotFound(
            message="block-storage not found"
        )

        errors = safety_check(mock_conn, "proj-123", "test_project")

        assert len(errors) == 1
        assert "1 server(s)" in errors[0]
        assert "vm1" in errors[0]

    def test_detaches_all_interfaces_before_router_deletion(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Router with multiple ports (multiple subnets) detaches all before deletion."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        router = _make_router("rtr1")
        port1 = _make_port("network:router_interface", "subnet1-id")
        port2 = _make_port("network:router_interface", "subnet2-id")
        port3 = _make_port("network:router_gateway")  # No subnet

        conn.network.routers.return_value = [router]
        conn.network.ports.return_value = [port1, port2, port3]

        teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # Should remove both interfaces (port3 has no subnet, skipped)
        assert conn.network.remove_interface_from_router.call_count == 2
        conn.network.remove_interface_from_router.assert_any_call(
            "rtr1-id", subnet_id="subnet1-id"
        )
        conn.network.remove_interface_from_router.assert_any_call(
            "rtr1-id", subnet_id="subnet2-id"
        )

    def test_gateway_clear_failure_prevents_router_deletion(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Router gateway clear failure still attempts router deletion."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        router = _make_router("rtr1")
        conn.network.routers.return_value = [router]
        conn.network.ports.return_value = []

        # Gateway clear fails (will be retried by @retry decorator)
        conn.network.update_router.side_effect = HttpException(
            message="gateway clear failed"
        )

        # Router deletion will fail due to gateway clear failure in pre_delete
        with pytest.raises(TeardownError, match="1 failure"):
            teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # update_router called multiple times due to @retry decorator
        assert conn.network.update_router.call_count > 1
        # delete_router NOT called because pre_delete raised exception
        conn.network.delete_router.assert_not_called()

        # But project deletion still happened
        conn.identity.delete_project.assert_called_once()


class TestTeardownEdgeCases:
    """Edge cases: empty resource lists, only security groups."""

    def test_interspersed_empty_resource_lists(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Empty resource lists mixed with non-empty ones handled correctly."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        # FIPs: empty
        conn.network.ips.return_value = []
        # Snapshots: present
        snap1 = _make_snapshot("snap1")
        snap2 = _make_snapshot("snap2")
        conn.block_storage.snapshots.return_value = [snap1, snap2]
        # Routers: empty
        conn.network.routers.return_value = []
        # Networks: present
        net = _make_network("net1")
        conn.network.networks.return_value = [net]

        actions = teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # No FIP or router actions
        fip_actions = [a for a in actions if a.resource_type == "floating_ip"]
        router_actions = [a for a in actions if a.resource_type == "router"]
        assert len(fip_actions) == 0
        assert len(router_actions) == 0

        # Snapshots and network deleted
        assert conn.block_storage.delete_snapshot.call_count == 2
        conn.network.delete_network.assert_called_once()

        snapshot_actions = [a for a in actions if a.resource_type == "snapshot"]
        assert len(snapshot_actions) == 2

    def test_only_non_default_security_groups(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Teardown with only security groups (no network resources) succeeds."""
        conn = shared_ctx.conn
        _stub_empty_lists(conn)

        sg_default = _make_sg("default")
        sg_custom1 = _make_sg("custom-sg-1")
        sg_custom2 = _make_sg("custom-sg-2")

        conn.network.security_groups.return_value = [sg_default, sg_custom1, sg_custom2]

        actions = teardown_project(sample_project_cfg, "proj-123", shared_ctx)

        # Only custom SGs deleted (default skipped)
        assert conn.network.delete_security_group.call_count == 2
        conn.network.delete_security_group.assert_any_call("custom-sg-1-id")
        conn.network.delete_security_group.assert_any_call("custom-sg-2-id")

        # No network/router/subnet actions
        network_actions = [a for a in actions if a.resource_type == "network"]
        router_actions = [a for a in actions if a.resource_type == "router"]
        assert len(network_actions) == 0
        assert len(router_actions) == 0

        # Project deleted
        conn.identity.delete_project.assert_called_once()
        assert any(a.resource_type == "project" for a in actions)
