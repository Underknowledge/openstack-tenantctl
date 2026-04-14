"""Tests for network stack provisioning — ensure_network_stack and track_router_ips."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import openstack.exceptions
import pytest

from src.models import ReleasedRouterIpEntry, RouterIpEntry
from src.resources.network import (
    _get_router_external_ip,
    ensure_network_stack,
    track_router_ips,
)
from src.utils import (
    ActionStatus,
    ProvisionerError,
    SharedContext,
    resolve_project_external_network,
)

if TYPE_CHECKING:
    from src.models import ProjectConfig


class TestCreateFullStack:
    """When no network exists, the full stack is created."""

    def test_create_full_stack(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        # find_network returns None → network does not exist
        shared_ctx.conn.network.find_network.return_value = None
        shared_ctx.conn.network.networks.return_value = []

        mock_network = MagicMock()
        mock_network.id = "net-001"
        shared_ctx.conn.network.create_network.return_value = mock_network

        mock_subnet = MagicMock()
        mock_subnet.id = "subnet-001"
        shared_ctx.conn.network.create_subnet.return_value = mock_subnet

        mock_router = MagicMock()
        mock_router.id = "router-001"
        shared_ctx.conn.network.create_router.return_value = mock_router

        action = ensure_network_stack(sample_project_cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert "net-001" in action.details
        assert "subnet-001" in action.details
        assert "router-001" in action.details

        # Verify create_network received correct parameters (not just "was called")
        net_kwargs = shared_ctx.conn.network.create_network.call_args[1]
        assert net_kwargs["name"] == "testproject-network"
        assert net_kwargs["project_id"] == "proj-123"
        assert net_kwargs["mtu"] == 1500

        # Verify create_subnet received correct parameters
        subnet_kwargs = shared_ctx.conn.network.create_subnet.call_args[1]
        assert subnet_kwargs["name"] == "testproject-subnet"
        assert subnet_kwargs["network_id"] == "net-001"
        assert subnet_kwargs["project_id"] == "proj-123"

        # Verify create_router received correct parameters (including external gateway)
        router_kwargs = shared_ctx.conn.network.create_router.call_args[1]
        assert router_kwargs["name"] == "testproject-router"
        assert router_kwargs["project_id"] == "proj-123"
        expected_gateway = {
            "network_id": "ext-net-id-123",
            "external_fixed_ips": [{"subnet_id": "ext-subnet-123"}],
        }
        assert router_kwargs["external_gateway_info"] == expected_gateway

        shared_ctx.conn.network.add_interface_to_router.assert_called_once_with("router-001", subnet_id="subnet-001")


class TestSkipExistingNetwork:
    """When the network already exists, the entire stack is skipped."""

    def test_skip_existing_network(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        existing_network = MagicMock()
        existing_network.id = "net-existing"
        shared_ctx.conn.network.find_network.return_value = existing_network

        action = ensure_network_stack(sample_project_cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.SKIPPED
        shared_ctx.conn.network.create_network.assert_not_called()
        shared_ctx.conn.network.create_subnet.assert_not_called()
        shared_ctx.conn.network.create_router.assert_not_called()
        shared_ctx.conn.network.add_interface_to_router.assert_not_called()


class TestDryRunSkips:
    """Online dry-run reads state but makes no writes."""

    def test_dry_run_network_not_found(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Network not found → CREATED with creation parameters."""
        dry_run_ctx.conn.network.find_network.return_value = None
        dry_run_ctx.conn.network.networks.return_value = []

        action = ensure_network_stack(sample_project_cfg, "proj-123", dry_run_ctx)

        assert action.status == ActionStatus.CREATED
        assert "would create" in action.details
        # Reads happened
        dry_run_ctx.conn.network.find_network.assert_called_once()
        # No writes
        dry_run_ctx.conn.network.create_network.assert_not_called()
        dry_run_ctx.conn.network.create_subnet.assert_not_called()
        dry_run_ctx.conn.network.create_router.assert_not_called()

    def test_dry_run_network_exists(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Network exists → SKIPPED."""
        existing = MagicMock()
        existing.id = "net-existing"
        dry_run_ctx.conn.network.find_network.return_value = existing

        action = ensure_network_stack(sample_project_cfg, "proj-123", dry_run_ctx)

        assert action.status == ActionStatus.SKIPPED
        dry_run_ctx.conn.network.create_network.assert_not_called()

    def test_offline_dry_run_skips(
        self,
        offline_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Offline mode → SKIPPED with no API calls."""
        action = ensure_network_stack(sample_project_cfg, "proj-123", offline_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert "offline" in action.details


class TestUsesExternalNetIdForRouter:
    """The router is created with the external gateway from ctx.external_net_id."""

    def test_uses_external_net_id_for_router(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: dict,
    ) -> None:
        shared_ctx.conn.network.find_network.return_value = None

        mock_network = MagicMock()
        mock_network.id = "net-001"
        shared_ctx.conn.network.create_network.return_value = mock_network

        mock_subnet = MagicMock()
        mock_subnet.id = "subnet-001"
        shared_ctx.conn.network.create_subnet.return_value = mock_subnet

        mock_router = MagicMock()
        mock_router.id = "router-001"
        shared_ctx.conn.network.create_router.return_value = mock_router

        ensure_network_stack(sample_project_cfg, "proj-123", shared_ctx)

        shared_ctx.conn.network.create_router.assert_called_once_with(
            name="testproject-router",
            project_id="proj-123",
            external_gateway_info={
                "network_id": "ext-net-id-123",
                "external_fixed_ips": [{"subnet_id": "ext-subnet-123"}],
            },
        )


# ---------------------------------------------------------------------------
# Helpers for track_router_ips tests
# ---------------------------------------------------------------------------


def _make_router(
    router_id: str,
    name: str,
    external_ip: str | None = None,
    gateway_network_id: str | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like an OpenStack Router resource."""
    router = MagicMock()
    router.id = router_id
    router.name = name
    if external_ip is not None:
        gw_info: dict = {
            "external_fixed_ips": [{"ip_address": external_ip}],
        }
        if gateway_network_id is not None:
            gw_info["network_id"] = gateway_network_id
        router.external_gateway_info = gw_info
    else:
        router.external_gateway_info = None
    return router


# ---------------------------------------------------------------------------
# _get_router_external_ip
# ---------------------------------------------------------------------------


class TestGetRouterExternalIp:
    """Pure function: extract external IP from router gateway info."""

    def test_returns_ip_from_gateway(self) -> None:
        router = _make_router("r1", "router1", "203.0.113.1")
        assert _get_router_external_ip(router) == "203.0.113.1"

    def test_returns_none_when_gateway_is_none(self) -> None:
        router = _make_router("r1", "router1", None)
        assert _get_router_external_ip(router) is None

    def test_returns_none_for_magicmock_gateway(self) -> None:
        """MagicMock gateway (not a dict) should return None."""
        router = MagicMock()
        # external_gateway_info is auto-created as a MagicMock, not a dict
        assert _get_router_external_ip(router) is None


# ---------------------------------------------------------------------------
# track_router_ips
# ---------------------------------------------------------------------------


class TestTrackRouterIpsFirstRun:
    """No previous config, routers found → UPDATED (adopted) actions."""

    def test_first_run_adopts_routers(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
        )
        r1 = _make_router("r1", "router-a", "10.0.0.1")
        r2 = _make_router("r2", "router-b", "10.0.0.2")
        shared_ctx.conn.network.routers.return_value = [r1, r2]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 2
        assert all(a.status == ActionStatus.UPDATED for a in actions)
        assert all("adopted" in a.details for a in actions)

        # router_ips persisted via state_store
        save_calls = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["router_ips"]]
        assert len(save_calls) == 1
        assert save_calls[0][0][2] == [
            {"id": "r1", "name": "router-a", "external_ip": "10.0.0.1"},
            {"id": "r2", "name": "router-b", "external_ip": "10.0.0.2"},
        ]


class TestTrackRouterIpsNoChange:
    """Previous matches current → no actions, no write."""

    def test_no_change_no_write(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            router_ips=[
                RouterIpEntry(id="r1", name="router-a", external_ip="10.0.0.1"),
            ],
        )
        r1 = _make_router("r1", "router-a", "10.0.0.1")
        shared_ctx.conn.network.routers.return_value = [r1]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        assert actions == []
        shared_ctx.state_store.save.assert_not_called()


class TestTrackRouterIpsNewRouter:
    """1 previous, 2 current → 1 UPDATED (adopted) for new router."""

    def test_new_router_adopted(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
            router_ips=[
                RouterIpEntry(id="r1", name="router-a", external_ip="10.0.0.1"),
            ],
        )
        r1 = _make_router("r1", "router-a", "10.0.0.1")
        r2 = _make_router("r2", "router-b", "10.0.0.2")
        shared_ctx.conn.network.routers.return_value = [r1, r2]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "adopted" in actions[0].details
        assert "router-b" in actions[0].name


class TestTrackRouterIpsRemovedRouter:
    """2 previous, 1 current → 1 UPDATED (removed), released_router_ips persisted."""

    def test_removed_router_releases_ip(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
            router_ips=[
                RouterIpEntry(id="r1", name="router-a", external_ip="10.0.0.1"),
                RouterIpEntry(id="r2", name="router-b", external_ip="10.0.0.2"),
            ],
        )
        r1 = _make_router("r1", "router-a", "10.0.0.1")
        shared_ctx.conn.network.routers.return_value = [r1]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "removed" in actions[0].details

        # released_router_ips should be persisted
        released_call = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["released_router_ips"]]
        assert len(released_call) == 1
        released_entries = released_call[0][0][2]
        assert len(released_entries) == 1
        assert released_entries[0]["address"] == "10.0.0.2"
        assert released_entries[0]["router_name"] == "router-b"


class TestTrackRouterIpsIpChanged:
    """Same router ID, different IP → UPDATED, old IP in released_router_ips."""

    def test_ip_change_detected(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
            router_ips=[
                RouterIpEntry(id="r1", name="router-a", external_ip="10.0.0.1"),
            ],
        )
        r1 = _make_router("r1", "router-a", "10.0.0.99")
        shared_ctx.conn.network.routers.return_value = [r1]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "10.0.0.1" in actions[0].details
        assert "10.0.0.99" in actions[0].details

        # Old IP released
        released_call = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["released_router_ips"]]
        assert len(released_call) == 1
        assert released_call[0][0][2][0]["address"] == "10.0.0.1"


class TestTrackRouterIpsDryRun:
    """Online dry-run reads routers, reports changes, skips persists."""

    def test_dry_run_reads_routers(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Online dry-run reads routers but does not persist."""
        r1 = _make_router("r1", "router-a", "10.0.0.1")
        dry_run_ctx.conn.network.routers.return_value = [r1]

        track_router_ips(sample_project_cfg, "proj-123", dry_run_ctx)

        # Reads happened
        dry_run_ctx.conn.network.routers.assert_called_once()
        # No persists
        dry_run_ctx.state_store.save.assert_not_called()

    def test_offline_dry_run_skips(
        self,
        offline_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Offline mode → SKIPPED with no API calls."""
        actions = track_router_ips(sample_project_cfg, "proj-123", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details


class TestTrackRouterIpsNoRouters:
    """No routers in project → empty router_ips, previous entries released."""

    def test_no_routers_releases_all(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
            router_ips=[
                RouterIpEntry(id="r1", name="router-a", external_ip="10.0.0.1"),
            ],
        )
        shared_ctx.conn.network.routers.return_value = []

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED

        # router_ips persisted as empty
        router_ips_call = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["router_ips"]]
        assert len(router_ips_call) == 1
        assert router_ips_call[0][0][2] == []

        # released_router_ips persisted
        released_call = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["released_router_ips"]]
        assert len(released_call) == 1
        assert released_call[0][0][2][0]["address"] == "10.0.0.1"


class TestReleasedRouterIpsMerge:
    """Existing released_router_ips + new removal → appended, not overwritten."""

    def test_released_ips_appended(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
            router_ips=[
                RouterIpEntry(id="r1", name="router-a", external_ip="10.0.0.1"),
            ],
            released_router_ips=[
                ReleasedRouterIpEntry(
                    address="10.0.0.99",
                    router_name="old-router",
                    released_at="2026-01-01T00:00:00+00:00",
                    reason="router no longer exists",
                ),
            ],
        )
        shared_ctx.conn.network.routers.return_value = []

        track_router_ips(cfg, "proj-123", shared_ctx)

        released_call = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["released_router_ips"]]
        assert len(released_call) == 1
        all_released = released_call[0][0][2]
        assert len(all_released) == 2
        assert all_released[0]["address"] == "10.0.0.99"
        assert all_released[1]["address"] == "10.0.0.1"


class TestMtuZeroUsesDefault:
    """MTU=0 in config → falls back to 1500 (line 133 in source)."""

    def test_mtu_zero_uses_default_1500(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        from src.models import NetworkConfig, SubnetConfig

        cfg = dataclasses.replace(
            sample_project_cfg,
            network=NetworkConfig(
                mtu=0,  # 0 means use default 1500
                subnet=SubnetConfig(
                    cidr="10.0.0.0/24",
                    gateway_ip="10.0.0.1",
                    allocation_pools=[],
                    dns_nameservers=["8.8.8.8"],
                    enable_dhcp=True,
                ),
            ),
        )
        shared_ctx.conn.network.find_network.return_value = None
        shared_ctx.conn.network.networks.return_value = []

        mock_network = MagicMock()
        mock_network.id = "net-001"
        shared_ctx.conn.network.create_network.return_value = mock_network

        mock_subnet = MagicMock()
        mock_subnet.id = "subnet-001"
        shared_ctx.conn.network.create_subnet.return_value = mock_subnet

        mock_router = MagicMock()
        mock_router.id = "router-001"
        shared_ctx.conn.network.create_router.return_value = mock_router

        ensure_network_stack(cfg, "proj-123", shared_ctx)

        # Verify that create_network was called with mtu=1500 (default fallback)
        call_kwargs = shared_ctx.conn.network.create_network.call_args[1]
        assert call_kwargs["mtu"] == 1500
        assert call_kwargs["name"] == "testproject-network"
        assert call_kwargs["project_id"] == "proj-123"


class TestSafetyNetDifferentNetworkName:
    """Project has existing network with different name → SKIPPED (safety net)."""

    def test_existing_network_different_name_triggers_safety(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        # find_network returns None (expected name not found)
        shared_ctx.conn.network.find_network.return_value = None

        # BUT project has a network with different name
        existing_network = MagicMock()
        existing_network.name = "legacy-network"
        existing_network.id = "net-legacy"
        shared_ctx.conn.network.networks.return_value = [existing_network]

        action = ensure_network_stack(sample_project_cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert "legacy-network" in action.details
        shared_ctx.conn.network.create_network.assert_not_called()


class TestEnableDhcpFalse:
    """Subnet created with enable_dhcp=False when configured."""

    def test_enable_dhcp_false_passed_to_create_subnet(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        from src.models import NetworkConfig, SubnetConfig

        cfg = dataclasses.replace(
            sample_project_cfg,
            network=NetworkConfig(
                mtu=1500,
                subnet=SubnetConfig(
                    cidr="10.0.0.0/24",
                    gateway_ip="10.0.0.1",
                    allocation_pools=[],
                    dns_nameservers=["8.8.8.8"],
                    enable_dhcp=False,
                ),
            ),
        )
        shared_ctx.conn.network.find_network.return_value = None
        shared_ctx.conn.network.networks.return_value = []

        mock_network = MagicMock()
        mock_network.id = "net-001"
        shared_ctx.conn.network.create_network.return_value = mock_network

        mock_subnet = MagicMock()
        mock_subnet.id = "subnet-001"
        shared_ctx.conn.network.create_subnet.return_value = mock_subnet

        mock_router = MagicMock()
        mock_router.id = "router-001"
        shared_ctx.conn.network.create_router.return_value = mock_router

        ensure_network_stack(cfg, "proj-123", shared_ctx)

        # Verify enable_dhcp=False was passed
        call_kwargs = shared_ctx.conn.network.create_subnet.call_args[1]
        assert call_kwargs["enable_dhcp"] is False


class TestEmptyDnsNameservers:
    """Subnet created with empty dns_nameservers list when configured."""

    def test_empty_dns_nameservers_passed_to_create_subnet(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        from src.models import NetworkConfig, SubnetConfig

        cfg = dataclasses.replace(
            sample_project_cfg,
            network=NetworkConfig(
                mtu=1500,
                subnet=SubnetConfig(
                    cidr="10.0.0.0/24",
                    gateway_ip="10.0.0.1",
                    allocation_pools=[],
                    dns_nameservers=[],
                    enable_dhcp=True,
                ),
            ),
        )
        shared_ctx.conn.network.find_network.return_value = None
        shared_ctx.conn.network.networks.return_value = []

        mock_network = MagicMock()
        mock_network.id = "net-001"
        shared_ctx.conn.network.create_network.return_value = mock_network

        mock_subnet = MagicMock()
        mock_subnet.id = "subnet-001"
        shared_ctx.conn.network.create_subnet.return_value = mock_subnet

        mock_router = MagicMock()
        mock_router.id = "router-001"
        shared_ctx.conn.network.create_router.return_value = mock_router

        ensure_network_stack(cfg, "proj-123", shared_ctx)

        # Verify empty list was passed
        call_kwargs = shared_ctx.conn.network.create_subnet.call_args[1]
        assert call_kwargs["dns_nameservers"] == []


class TestMinimalNetworkConfig:
    """Network created with minimal config (mtu=0, empty pools, empty dns)."""

    def test_minimal_network_config_creates_stack(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        from src.models import NetworkConfig, SubnetConfig

        cfg = dataclasses.replace(
            sample_project_cfg,
            network=NetworkConfig(
                mtu=0,  # Will use default 1500
                subnet=SubnetConfig(
                    cidr="10.0.0.0/24",
                    gateway_ip="10.0.0.1",
                    allocation_pools=[],
                    dns_nameservers=[],
                    enable_dhcp=True,
                ),
            ),
        )
        shared_ctx.conn.network.find_network.return_value = None
        shared_ctx.conn.network.networks.return_value = []

        mock_network = MagicMock()
        mock_network.id = "net-001"
        shared_ctx.conn.network.create_network.return_value = mock_network

        mock_subnet = MagicMock()
        mock_subnet.id = "subnet-001"
        shared_ctx.conn.network.create_subnet.return_value = mock_subnet

        mock_router = MagicMock()
        mock_router.id = "router-001"
        shared_ctx.conn.network.create_router.return_value = mock_router

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        # Verify network created with default mtu=1500
        net_call_kwargs = shared_ctx.conn.network.create_network.call_args[1]
        assert net_call_kwargs["mtu"] == 1500
        # Verify subnet created with empty lists
        subnet_call_kwargs = shared_ctx.conn.network.create_subnet.call_args[1]
        assert subnet_call_kwargs["allocation_pools"] == []
        assert subnet_call_kwargs["dns_nameservers"] == []


class TestNoNetworkConfigured:
    """Config with network=None → SKIPPED early return (line 125-131)."""

    def test_no_network_configured_skips_early(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(sample_project_cfg, network=None)

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert "no network configured" in action.details
        shared_ctx.conn.network.find_network.assert_not_called()
        shared_ctx.conn.network.create_network.assert_not_called()


class TestGetRouterExternalIpMissingFields:
    """_get_router_external_ip edge cases: missing gateway_info structure."""

    @pytest.mark.parametrize(
        "gateway_info",
        [
            "not-a-dict",
            {"network_id": "ext-net-123"},
            {"external_fixed_ips": []},
            {"external_fixed_ips": [{"subnet_id": "subnet-123"}]},
        ],
        ids=["not-dict", "no-fixed-ips-key", "empty-fixed-ips", "no-ip-address-key"],
    )
    def test_returns_none(self, gateway_info: object) -> None:
        router = MagicMock()
        router.external_gateway_info = gateway_info
        assert _get_router_external_ip(router) is None


class TestTrackRouterIpsMultipleChanges:
    """Multiple router changes in single run: adopted, removed, IP changed."""

    def test_multiple_changes_in_single_run(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
            router_ips=[
                RouterIpEntry(id="r1", name="router-a", external_ip="10.0.0.1"),
                RouterIpEntry(id="r2", name="router-b", external_ip="10.0.0.2"),
            ],
        )
        # r1: IP changed
        # r2: removed
        # r3: adopted (new)
        r1 = _make_router("r1", "router-a", "10.0.0.99")
        r3 = _make_router("r3", "router-c", "10.0.0.3")
        shared_ctx.conn.network.routers.return_value = [r1, r3]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        # 1 adopted, 1 removed, 1 IP changed → 3 actions
        assert len(actions) == 3
        action_details = [a.details for a in actions]
        assert any("adopted" in d for d in action_details)
        assert any("removed" in d for d in action_details)
        assert any("10.0.0.1" in d and "10.0.0.99" in d for d in action_details)

        # Two IPs released: 10.0.0.2 (removed) and 10.0.0.1 (changed)
        released_call = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["released_router_ips"]]
        assert len(released_call) == 1
        released_entries = released_call[0][0][2]
        assert len(released_entries) == 2
        released_ips = {r["address"] for r in released_entries}
        assert released_ips == {"10.0.0.1", "10.0.0.2"}


class TestRouterWithNoExternalGateway:
    """Router without external gateway (no external IP) → excluded from snapshot."""

    def test_router_without_external_gateway_excluded(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
        )
        # r1 has IP, r2 has no external gateway
        r1 = _make_router("r1", "router-a", "10.0.0.1")
        r2 = _make_router("r2", "router-b", None)  # No external gateway
        shared_ctx.conn.network.routers.return_value = [r1, r2]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        # Only r1 adopted
        assert len(actions) == 1
        assert "router-a" in actions[0].name

        # Only r1 persisted
        router_ips_call = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["router_ips"]]
        assert len(router_ips_call) == 1
        persisted_routers = router_ips_call[0][0][2]
        assert len(persisted_routers) == 1
        assert persisted_routers[0]["id"] == "r1"


class TestPerProjectExternalNetworkOverride:
    """Test per-project external_network_name and external_network_subnet overrides."""

    def test_per_project_external_network_override(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Test that per-project external_network_name overrides global default."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            external_network_name="project-specific-network",
            external_network_subnet="project-specific-subnet",
        )

        # Populate the external_network_map with the per-project network
        shared_ctx.external_network_map["project-specific-network"] = "specific-net-id-456"

        shared_ctx.conn.network.find_network.return_value = None
        shared_ctx.conn.network.networks.return_value = []

        mock_specific_subnet = MagicMock()
        mock_specific_subnet.id = "specific-subnet-id-789"
        mock_specific_subnet.network_id = "specific-net-id-456"
        mock_specific_subnet.cidr = "192.168.1.0/24"

        def mock_find_subnet(name, **kwargs):
            if name == "project-specific-subnet":
                return mock_specific_subnet
            return None

        shared_ctx.conn.network.find_subnet = mock_find_subnet

        mock_subnets = [mock_specific_subnet]
        shared_ctx.conn.network.subnets.return_value = mock_subnets

        mock_router = MagicMock(id="router-proj-123")
        mock_network = MagicMock(id="net-proj-123")
        mock_subnet = MagicMock(id="subnet-proj-123")
        shared_ctx.conn.network.create_router.return_value = mock_router
        shared_ctx.conn.network.create_network.return_value = mock_network
        shared_ctx.conn.network.create_subnet.return_value = mock_subnet

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED

        router_kwargs = shared_ctx.conn.network.create_router.call_args[1]
        assert router_kwargs["external_gateway_info"]["network_id"] == "specific-net-id-456"
        assert router_kwargs["external_gateway_info"]["external_fixed_ips"] == [{"subnet_id": "specific-subnet-id-789"}]

    def test_per_project_fallback_to_global(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Test that projects without explicit network/subnet use global defaults."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            external_network_name="",
            external_network_subnet="",
        )

        shared_ctx.conn.network.find_network.return_value = None
        shared_ctx.conn.network.networks.return_value = []

        mock_router = MagicMock(id="router-123")
        mock_network = MagicMock(id="net-123")
        mock_subnet = MagicMock(id="subnet-123")
        shared_ctx.conn.network.create_router.return_value = mock_router
        shared_ctx.conn.network.create_network.return_value = mock_network
        shared_ctx.conn.network.create_subnet.return_value = mock_subnet

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED

        router_kwargs = shared_ctx.conn.network.create_router.call_args[1]
        assert router_kwargs["external_gateway_info"]["network_id"] == "ext-net-id-123"
        assert router_kwargs["external_gateway_info"]["external_fixed_ips"] == [{"subnet_id": "ext-subnet-123"}]


class TestRouterGatewayMismatch:
    """Router gateway / configured external network mismatch detection."""

    def test_router_gateway_matches_no_warning(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Gateway matches configured network → no FAILED action."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
        )
        r1 = _make_router("r1", "router-a", "10.0.0.1", gateway_network_id="ext-net-id-123")
        shared_ctx.conn.network.routers.return_value = [r1]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

    def test_router_gateway_mismatch_warns(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Gateway on 'dmz', config says 'public' → FAILED action."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
        )
        r1 = _make_router("r1", "router-a", "10.0.0.1", gateway_network_id="dmz-net-id")
        shared_ctx.conn.network.routers.return_value = [r1]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].resource_type == "router_gateway"
        assert "dmz-net-id" in failed[0].details
        assert "ext-net-id-123" in failed[0].details
        assert failed[0].name == "router-a"

    def test_router_no_gateway_no_warning(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Router without external gateway → no FAILED action."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
        )
        r1 = _make_router("r1", "router-a", None)  # No gateway
        shared_ctx.conn.network.routers.return_value = [r1]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

    def test_multiple_routers_partial_mismatch(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """1 match + 1 mismatch → 1 FAILED action."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            config_path="/tmp/project.yaml",
            state_key="test_project",
        )
        r1 = _make_router("r1", "router-a", "10.0.0.1", gateway_network_id="ext-net-id-123")
        r2 = _make_router("r2", "router-b", "10.0.0.2", gateway_network_id="dmz-net-id")
        shared_ctx.conn.network.routers.return_value = [r1, r2]

        actions = track_router_ips(cfg, "proj-123", shared_ctx)

        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].name == "router-b"
        assert "dmz-net-id" in failed[0].details


# ---------------------------------------------------------------------------
# resolve_project_external_network error paths
# ---------------------------------------------------------------------------


class TestExternalNetworkNotFoundRaises:
    """Configured external_network_name that doesn't exist → ProvisionerError."""

    def test_external_network_not_found_raises(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            external_network_name="nonexistent",
        )
        # external_network_map is empty by default — "nonexistent" won't be found

        with pytest.raises(ProvisionerError, match="nonexistent"):
            resolve_project_external_network(cfg, shared_ctx)


class TestExternalSubnetNotFoundRaises:
    """Configured external_network_subnet that doesn't exist → ProvisionerError."""

    def test_external_subnet_not_found_raises(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(
            sample_project_cfg,
            external_network_name="real-network",
            external_network_subnet="nonexistent-subnet",
        )
        # Populate external_network_map with the network
        shared_ctx.external_network_map["real-network"] = "real-net-id"
        # find_subnet returns None → resolve_external_subnet raises ProvisionerError
        shared_ctx.conn.network.find_subnet.return_value = None

        with pytest.raises(ProvisionerError, match="nonexistent-subnet"):
            resolve_project_external_network(cfg, shared_ctx)


# ---------------------------------------------------------------------------
# reclaim_router_ips
# ---------------------------------------------------------------------------


def _setup_empty_project(ctx: SharedContext) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Wire mocks so that ensure_network_stack sees an empty project."""
    ctx.conn.network.find_network.return_value = None
    ctx.conn.network.networks.return_value = []

    mock_network = MagicMock(id="net-001")
    ctx.conn.network.create_network.return_value = mock_network

    mock_subnet = MagicMock(id="subnet-001")
    ctx.conn.network.create_subnet.return_value = mock_subnet

    mock_router = MagicMock(id="router-001")
    ctx.conn.network.create_router.return_value = mock_router

    return mock_network, mock_subnet, mock_router


class TestReclaimRouterIps:
    """Router IP reclamation during network stack creation."""

    def test_reclaim_success(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Previous IP available → router created with that IP."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            reclaim_router_ips=True,
            router_ips=[
                RouterIpEntry(id="old-rtr", name="testproject-router", external_ip="198.51.100.5"),
            ],
        )
        _setup_empty_project(shared_ctx)

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        router_kwargs = shared_ctx.conn.network.create_router.call_args[1]
        fixed_ips = router_kwargs["external_gateway_info"]["external_fixed_ips"]
        assert fixed_ips == [{"ip_address": "198.51.100.5", "subnet_id": "ext-subnet-123"}]

    def test_reclaim_conflict_falls_back(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """409 Conflict → normal creation + released_router_ips persisted."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            reclaim_router_ips=True,
            state_key="test_project",
            router_ips=[
                RouterIpEntry(id="old-rtr", name="testproject-router", external_ip="198.51.100.5"),
            ],
        )
        _setup_empty_project(shared_ctx)

        # First call (reclaim) → 409, second call (fallback) → success.
        fallback_router = MagicMock(id="router-fallback")
        shared_ctx.conn.network.create_router.side_effect = [
            openstack.exceptions.ConflictException("address taken"),
            fallback_router,
        ]

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert "router-fallback" in action.details

        # Two create_router calls: reclaim attempt + fallback.
        assert shared_ctx.conn.network.create_router.call_count == 2

        # Fallback call should NOT include ip_address.
        fallback_kwargs = shared_ctx.conn.network.create_router.call_args_list[1][1]
        fixed_ips = fallback_kwargs["external_gateway_info"]["external_fixed_ips"]
        assert fixed_ips == [{"subnet_id": "ext-subnet-123"}]

        # released_router_ips persisted with the lost IP.
        released_call = [c for c in shared_ctx.state_store.save.call_args_list if c[0][1] == ["released_router_ips"]]
        assert len(released_call) == 1
        released_entries = released_call[0][0][2]
        assert released_entries[-1]["address"] == "198.51.100.5"
        assert "taken" in released_entries[-1]["reason"]

    def test_reclaim_bad_request_propagates(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """400 BadRequest → re-raised with diagnostic context."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            reclaim_router_ips=True,
            state_key="test_project",
            router_ips=[
                RouterIpEntry(id="old-rtr", name="testproject-router", external_ip="198.51.100.5"),
            ],
        )
        _setup_empty_project(shared_ctx)

        shared_ctx.conn.network.create_router.side_effect = openstack.exceptions.BadRequestException(
            "invalid IP for subnet"
        )

        with pytest.raises(openstack.exceptions.BadRequestException, match="reclaim") as exc_info:
            ensure_network_stack(cfg, "proj-123", shared_ctx)

        msg = str(exc_info.value)
        # Enriched message includes reclaim context.
        assert "198.51.100.5" in msg
        assert "testproject-router" in msg
        assert "ext-subnet-123" in msg
        # Original Neutron error is included and chained.
        assert "invalid IP for subnet" in msg
        assert exc_info.value.__cause__ is not None

    def test_reclaim_disabled_no_ip_in_request(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """reclaim_router_ips=False → normal creation despite previous IPs."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            reclaim_router_ips=False,
            router_ips=[
                RouterIpEntry(id="old-rtr", name="testproject-router", external_ip="198.51.100.5"),
            ],
        )
        _setup_empty_project(shared_ctx)

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        # Only one create_router call, without ip_address.
        assert shared_ctx.conn.network.create_router.call_count == 1
        router_kwargs = shared_ctx.conn.network.create_router.call_args[1]
        fixed_ips = router_kwargs["external_gateway_info"]["external_fixed_ips"]
        assert fixed_ips == [{"subnet_id": "ext-subnet-123"}]

    def test_reclaim_no_previous_ip(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """reclaim_router_ips=True but empty router_ips → normal creation."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            reclaim_router_ips=True,
            router_ips=[],
        )
        _setup_empty_project(shared_ctx)

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert shared_ctx.conn.network.create_router.call_count == 1
        router_kwargs = shared_ctx.conn.network.create_router.call_args[1]
        fixed_ips = router_kwargs["external_gateway_info"]["external_fixed_ips"]
        assert fixed_ips == [{"subnet_id": "ext-subnet-123"}]

    def test_reclaim_mismatched_router_name(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Previous IP for different router name → no reclaim."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            reclaim_router_ips=True,
            router_ips=[
                RouterIpEntry(
                    id="old-rtr",
                    name="other-router",
                    external_ip="198.51.100.5",
                ),
            ],
        )
        _setup_empty_project(shared_ctx)

        action = ensure_network_stack(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert shared_ctx.conn.network.create_router.call_count == 1
        router_kwargs = shared_ctx.conn.network.create_router.call_args[1]
        fixed_ips = router_kwargs["external_gateway_info"]["external_fixed_ips"]
        assert fixed_ips == [{"subnet_id": "ext-subnet-123"}]

    def test_reclaim_dry_run_includes_note(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Dry-run with reclaim enabled → message includes reclaim note."""
        cfg = dataclasses.replace(
            sample_project_cfg,
            reclaim_router_ips=True,
            router_ips=[
                RouterIpEntry(id="old-rtr", name="testproject-router", external_ip="198.51.100.5"),
            ],
        )
        dry_run_ctx.conn.network.find_network.return_value = None
        dry_run_ctx.conn.network.networks.return_value = []

        action = ensure_network_stack(cfg, "proj-123", dry_run_ctx)

        assert action.status == ActionStatus.CREATED
        assert "reclaim IP 198.51.100.5" in action.details
