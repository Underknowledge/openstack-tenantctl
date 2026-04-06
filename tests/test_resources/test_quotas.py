"""Tests for quota provisioning — ensure_quotas."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.models import ProjectConfig
from src.resources.quotas import ensure_quotas
from src.utils import ActionStatus, SharedContext


class TestEnsureQuotas:
    """Core quota provisioning behavior: update, skip, and error handling."""

    def test_update_compute_quotas(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """When compute quotas differ, update_quota_set is called with correct values."""
        # Compute: current cores=10, desired cores=20 → UPDATED
        compute_quota = MagicMock()
        compute_quota.cores = 10
        compute_quota.ram = 51200
        compute_quota.instances = 10
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network quotas match so they don't distract
        net_quota = MagicMock()
        net_quota.subnets = 1
        net_quota.routers = 1
        net_quota.ports = 50
        net_quota.security_groups = 10
        net_quota.security_group_rules = 100
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage quotas match
        bs_quota = MagicMock()
        bs_quota.gigabytes = 500
        bs_quota.volumes = 20
        bs_quota.snapshots = 10
        bs_quota.to_dict.return_value = {
            "gigabytes": 500,
            "volumes": 20,
            "snapshots": 10,
        }
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(sample_project_cfg, "proj-123", shared_ctx)

        # Only compute should be updated (network and block-storage match)
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "compute_quota"

        # Verify the API received correct values (from sample_project_cfg)
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123",
            cores=20,
            ram=51200,
            instances=10,
        )

    def test_skip_matching_quotas(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """When all quotas match, no API updates are called and action explains current values."""
        # Compute quotas match desired
        compute_quota = MagicMock()
        compute_quota.cores = 20
        compute_quota.ram = 51200
        compute_quota.instances = 10
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network quotas match (floating_ips=0 and networks=1 are excluded)
        net_quota = MagicMock()
        net_quota.subnets = 1
        net_quota.routers = 1
        net_quota.ports = 50
        net_quota.security_groups = 10
        net_quota.security_group_rules = 100
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage quotas match
        bs_quota = MagicMock()
        bs_quota.gigabytes = 500
        bs_quota.volumes = 20
        bs_quota.snapshots = 10
        bs_quota.to_dict.return_value = {
            "gigabytes": 500,
            "volumes": 20,
            "snapshots": 10,
        }
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(sample_project_cfg, "proj-123", shared_ctx)

        # Single skipped action with details
        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "quotas"
        assert "already up to date" in actions[0].details

        # No API updates were made
        shared_ctx.conn.compute.update_quota_set.assert_not_called()
        shared_ctx.conn.network.update_quota.assert_not_called()
        shared_ctx.conn.block_storage.update_quota_set.assert_not_called()

    def test_dry_run_reads_and_reports(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Online dry-run reads current quotas, reports diffs, no writes."""
        actions = ensure_quotas(sample_project_cfg, "proj-123", dry_run_ctx)

        # 3 actions: compute, network, block_storage (each reads + compares)
        assert len(actions) == 3
        # Reads happened
        dry_run_ctx.conn.compute.get_quota_set.assert_called_once()
        # No writes
        dry_run_ctx.conn.compute.update_quota_set.assert_not_called()
        dry_run_ctx.conn.block_storage.update_quota_set.assert_not_called()

    def test_offline_dry_run_skips(
        self,
        offline_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Offline mode → SKIPPED with no API calls."""
        actions = ensure_quotas(sample_project_cfg, "proj-123", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details

    @pytest.mark.parametrize(
        ("fip_count", "openstack_current"), [(1, 5), (3, 0), (10, 999)]
    )
    def test_excludes_floating_ips_unconditionally(
        self,
        shared_ctx: SharedContext,
        fip_count: int,
        openstack_current: int,
    ) -> None:
        """floating_ips excluded regardless of value — locked-FIP module manages it."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {
                        "floating_ips": fip_count,
                        "subnets": 5,
                        "routers": 2,
                    },
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network: floating_ips differs but should be excluded
        net_quota = MagicMock()
        net_quota.floating_ips = openstack_current  # Different from config
        net_quota.subnets = 5
        net_quota.routers = 2
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # floating_ips excluded, remaining keys match → all SKIPPED
        assert all(a.status == ActionStatus.SKIPPED for a in actions)
        shared_ctx.conn.network.update_quota.assert_not_called()


class TestBlockStorageQuotas:
    """Block-storage quota handling: merge strategy and metadata filtering."""

    def test_sends_merged_keys(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 1000, "volumes": 50},
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage: gigabytes differs; also has extra key backups
        bs_quota = MagicMock()
        bs_quota.gigabytes = 500
        bs_quota.volumes = 50
        bs_quota.backups = 5
        bs_quota.to_dict.return_value = {"gigabytes": 500, "volumes": 50, "backups": 5}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        ensure_quotas(cfg, "proj-123", shared_ctx)

        shared_ctx.conn.block_storage.update_quota_set.assert_called_once()
        call_kwargs = shared_ctx.conn.block_storage.update_quota_set.call_args
        # The merged payload should contain both desired keys and current keys
        assert call_kwargs[1]["gigabytes"] == 1000  # desired override
        assert call_kwargs[1]["volumes"] == 50  # desired matches current
        assert call_kwargs[1]["backups"] == 5  # current key preserved

    def test_filters_out_metadata_keys(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Metadata keys from to_dict() must never leak into update_quota_set kwargs."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 1000},
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage: to_dict returns metadata keys that must be filtered
        bs_quota = MagicMock()
        bs_quota.gigabytes = 500
        bs_quota.to_dict.return_value = {
            "gigabytes": 500,
            "volumes": 10,
            "project_id": "proj-123",
            "id": "quota-id-456",
            "reservation": 0,
            "usage": 42,
            "name": "some-name",
            "location": "region-1",
        }
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        ensure_quotas(cfg, "proj-123", shared_ctx)

        shared_ctx.conn.block_storage.update_quota_set.assert_called_once()
        call_kwargs = shared_ctx.conn.block_storage.update_quota_set.call_args[1]

        # Desired key overridden
        assert call_kwargs["gigabytes"] == 1000
        # Existing quota key preserved
        assert call_kwargs["volumes"] == 10
        # Metadata keys must NOT appear
        assert "project_id" not in call_kwargs
        assert "id" not in call_kwargs
        assert "reservation" not in call_kwargs
        assert "usage" not in call_kwargs
        assert "name" not in call_kwargs
        assert "location" not in call_kwargs


class TestNetworkQuotasWithLoadBalancer:
    """Network quotas with mixed-in load balancer quotas: Neutron + Octavia coordination."""

    def test_updates_both_neutron_and_octavia_quotas(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Should update both Neutron and Octavia quotas when in network section."""
        # Mock Neutron network quota
        mock_net_quota = MagicMock()
        mock_net_quota.floating_ips = 5  # Current
        mock_net_quota.ports = 50
        shared_ctx.conn.network.get_quota.return_value = mock_net_quota

        # Mock Octavia load balancer quota
        mock_lb_quota = MagicMock()
        mock_lb_quota.load_balancers = 5  # Current
        mock_lb_quota.listeners = 10
        shared_ctx.conn.load_balancer.get_quota.return_value = mock_lb_quota

        # Mock compute and block_storage to avoid interference
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        cfg = ProjectConfig.from_dict(
            {
                "name": "testproject",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {
                        # Neutron quotas
                        "ports": 100,  # Changed from 50
                        # Load balancer quotas (mixed in)
                        "load_balancers": 10,  # Changed from 5
                        "listeners": 20,  # Changed from 10
                    },
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        actions = ensure_quotas(cfg, "project-id-123", shared_ctx)

        # Should have updated network quotas
        network_action = next(a for a in actions if a.resource_type == "network_quota")
        assert network_action.status == ActionStatus.UPDATED
        assert "network:" in network_action.details
        assert "load_balancer:" in network_action.details

        # Verify API calls
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "project-id-123", ports=100
        )
        shared_ctx.conn.load_balancer.update_quota.assert_called_once_with(
            "project-id-123", load_balancers=10, listeners=20
        )

    def test_skips_when_load_balancer_quotas_match(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Should skip when both network and load balancer quotas match."""
        mock_net_quota = MagicMock()
        mock_net_quota.ports = 100
        shared_ctx.conn.network.get_quota.return_value = mock_net_quota

        mock_lb_quota = MagicMock()
        mock_lb_quota.load_balancers = 10
        shared_ctx.conn.load_balancer.get_quota.return_value = mock_lb_quota

        # Mock compute and block_storage
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        cfg = ProjectConfig.from_dict(
            {
                "name": "testproject",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {
                        "ports": 100,  # Same as current
                        "load_balancers": 10,  # Same as current
                    },
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        actions = ensure_quotas(cfg, "project-id-123", shared_ctx)

        # All quotas match, should be skipped
        assert all(a.status == ActionStatus.SKIPPED for a in actions)

        shared_ctx.conn.network.update_quota.assert_not_called()
        shared_ctx.conn.load_balancer.update_quota.assert_not_called()

    def test_handles_octavia_service_unavailable(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Gracefully degrades when Octavia unavailable — Neutron still updates correctly."""
        mock_net_quota = MagicMock()
        mock_net_quota.ports = 50
        shared_ctx.conn.network.get_quota.return_value = mock_net_quota

        # Octavia service unavailable
        shared_ctx.conn.load_balancer.get_quota.side_effect = Exception(
            "Service not found"
        )

        # Mock compute and block_storage
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        cfg = ProjectConfig.from_dict(
            {
                "name": "testproject",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {
                        "ports": 100,  # Changed from 50
                        "load_balancers": 10,  # Will be skipped due to service unavailable
                    },
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Should not raise - handles Octavia unavailability gracefully
        actions = ensure_quotas(cfg, "project-id-123", shared_ctx)

        # Network quotas should still be updated despite LB failure
        network_action = next(a for a in actions if a.resource_type == "network_quota")
        assert network_action.status == ActionStatus.UPDATED
        assert "network:" in network_action.details

        # Verify Neutron received correct value
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "project-id-123", ports=100
        )
        # load_balancer.update_quota should NOT be called (get_quota failed)
        shared_ctx.conn.load_balancer.update_quota.assert_not_called()

    def test_updates_only_network_when_no_lb_quotas(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Should work normally when no load balancer quotas configured."""
        mock_net_quota = MagicMock()
        mock_net_quota.ports = 50
        shared_ctx.conn.network.get_quota.return_value = mock_net_quota

        # Mock compute and block_storage
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        cfg = ProjectConfig.from_dict(
            {
                "name": "testproject",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {
                        "ports": 100,  # Only network quotas
                    },
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        ensure_quotas(cfg, "project-id-123", shared_ctx)

        # Should only call network API
        shared_ctx.conn.network.update_quota.assert_called_once()
        shared_ctx.conn.load_balancer.get_quota.assert_not_called()


class TestQuotaEdgeCases:
    """Edge cases: no quotas configured, single-service configs, service unavailable."""

    def test_no_quotas_configured(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """When cfg.quotas is None, ensure_quotas returns SKIPPED."""
        import dataclasses

        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
            }
        )
        # Remove quotas section entirely
        cfg = dataclasses.replace(cfg, quotas=None)

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "quotas"
        assert "no quotas configured" in actions[0].details

        # No API calls should be made
        shared_ctx.conn.compute.get_quota_set.assert_not_called()
        shared_ctx.conn.network.get_quota.assert_not_called()
        shared_ctx.conn.block_storage.get_quota_set.assert_not_called()

    def test_only_compute_quotas_configured(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """When only compute quotas present, other services not updated."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20, "ram": 51200},
                },
            }
        )

        # Compute quotas differ
        compute_quota = MagicMock()
        compute_quota.cores = 10
        compute_quota.ram = 51200
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network section is empty dict in config → no updates
        net_quota = MagicMock()
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage section is empty dict
        bs_quota = MagicMock()
        bs_quota.to_dict.return_value = {}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # Should only update compute
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "compute_quota"

        shared_ctx.conn.compute.update_quota_set.assert_called_once()
        shared_ctx.conn.network.update_quota.assert_not_called()
        shared_ctx.conn.block_storage.update_quota_set.assert_not_called()

    def test_only_network_quotas_configured(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """When only network quotas present, other services not updated."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "network": {"subnets": 5, "routers": 2},
                },
            }
        )

        # Compute section empty → matches
        compute_quota = MagicMock()
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network quotas differ
        net_quota = MagicMock()
        net_quota.subnets = 1
        net_quota.routers = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage empty
        bs_quota = MagicMock()
        bs_quota.to_dict.return_value = {}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "network_quota"

        shared_ctx.conn.network.update_quota.assert_called_once()
        shared_ctx.conn.compute.update_quota_set.assert_not_called()
        shared_ctx.conn.block_storage.update_quota_set.assert_not_called()

    def test_block_storage_service_unavailable(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Block-storage service unavailable is gracefully handled."""
        from openstack.exceptions import EndpointNotFound

        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 1000},
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage service unavailable
        shared_ctx.conn.block_storage.get_quota_set.side_effect = EndpointNotFound(
            "Cinder service not found"
        )

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # When all three services are skipped, returns single "quotas" action
        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "quotas"

        # Should NOT call update
        shared_ctx.conn.block_storage.update_quota_set.assert_not_called()

    def test_compute_service_unavailable_raises(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Compute service unavailable raises and propagates exception."""
        from openstack.exceptions import EndpointNotFound

        # Compute service unavailable
        shared_ctx.conn.compute.get_quota_set.side_effect = EndpointNotFound(
            "Nova service not found"
        )

        # Network and block-storage won't be reached
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 500
        bs_quota.to_dict.return_value = {"gigabytes": 500}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        # Should raise because compute is not wrapped in try/except
        with pytest.raises(EndpointNotFound):
            ensure_quotas(sample_project_cfg, "proj-123", shared_ctx)

    def test_empty_compute_section(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Empty quota sub-section (compute: {} with no keys) behaves correctly."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {},
                    "network": {"subnets": 5},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute: empty desired → getattr on empty dict → current matches desired (both empty)
        compute_quota = MagicMock()
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 5
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # All skipped (empty compute means no keys to compare)
        assert all(a.status == ActionStatus.SKIPPED for a in actions)
        shared_ctx.conn.compute.update_quota_set.assert_not_called()


class TestNetworkQuotaExclusions:
    """Network quota exclusions: floating_ips and networks when prealloc owns them."""

    def test_networks_eq_1_excluded(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """networks=1 excluded because prealloc module owns it."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {
                        "networks": 1,
                        "subnets": 5,
                    },
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network: networks differs but should be excluded
        net_quota = MagicMock()
        net_quota.networks = 10  # Different from config, but excluded
        net_quota.subnets = 5
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # All match (networks excluded)
        assert all(a.status == ActionStatus.SKIPPED for a in actions)
        shared_ctx.conn.network.update_quota.assert_not_called()

    def test_floating_ips_and_networks_both_excluded(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Both floating_ips and networks excluded when prealloc owns them."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {
                        "floating_ips": 5,
                        "networks": 1,
                        "subnets": 3,
                    },
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network: floating_ips and networks differ but both excluded
        net_quota = MagicMock()
        net_quota.floating_ips = 0
        net_quota.networks = 10
        net_quota.subnets = 3
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # All skipped
        assert all(a.status == ActionStatus.SKIPPED for a in actions)
        shared_ctx.conn.network.update_quota.assert_not_called()

    def test_all_three_quota_types_differ(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """When compute, network, and block_storage all differ, all three are updated."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 50},
                    "network": {"subnets": 10},
                    "block_storage": {"gigabytes": 2000},
                },
            }
        )

        # All three differ from current
        compute_quota = MagicMock()
        compute_quota.cores = 10
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 500
        bs_quota.to_dict.return_value = {"gigabytes": 500}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # Should have exactly 3 UPDATED actions
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 3

        resource_types = {a.resource_type for a in updated}
        assert resource_types == {
            "compute_quota",
            "network_quota",
            "block_storage_quota",
        }

        # Verify each service updated with correct values
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123", cores=50
        )
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-123", subnets=10
        )
        call_kwargs = shared_ctx.conn.block_storage.update_quota_set.call_args[1]
        assert call_kwargs["gigabytes"] == 2000


class TestQuotaUnits:
    """Human-readable unit support for RAM and storage quotas."""

    def test_ram_with_gb_unit(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """RAM quota accepts '50GB' and converts to 50000 MB for Nova API."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {
                        "ram": "50GB",  # Decimal: 50GB = 50000 MB
                        "cores": 20,
                    },
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute: ram differs (current 51200 MB != desired 50000 MB)
        compute_quota = MagicMock()
        compute_quota.ram = 51200
        compute_quota.cores = 20

        # Usage mock: ram usage is below desired so lowering is allowed
        # SDK stores usage in .usage dict
        usage_quota = MagicMock()
        usage_quota.usage = {"ram": 30000, "cores": 15}

        shared_ctx.conn.compute.get_quota_set.side_effect = lambda pid, **kw: (
            usage_quota if kw.get("usage") else compute_quota
        )

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # Only compute should be updated
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "compute_quota"

        # Verify API received 50000 MB (converted from "50GB")
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123",
            ram=50000,
            cores=20,
        )

    def test_ram_with_gib_unit(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """RAM quota accepts '50GiB' and converts to 53687 MB for Nova API."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {
                        "ram": "50GiB",  # Binary: 50GiB ≈ 53687 MB
                        "cores": 20,
                    },
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute: ram differs
        compute_quota = MagicMock()
        compute_quota.ram = 51200
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # Only compute should be updated
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1

        # Verify API received 53687 MB (converted from "50GiB")
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123",
            ram=53687,
            cores=20,
        )

    def test_storage_with_tb_unit(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Block storage gigabytes accepts '2TB' and converts to 2000 GB for Cinder API."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {"subnets": 1},
                    "block_storage": {
                        "gigabytes": "2TB",  # Decimal: 2TB = 2000 GB
                        "volumes": 20,
                    },
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage: gigabytes differs
        bs_quota = MagicMock()
        bs_quota.gigabytes = 1000
        bs_quota.volumes = 20
        bs_quota.to_dict.return_value = {"gigabytes": 1000, "volumes": 20}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # Only block_storage should be updated
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "block_storage_quota"

        # Verify API received 2000 GB (converted from "2TB")
        call_kwargs = shared_ctx.conn.block_storage.update_quota_set.call_args[1]
        assert call_kwargs["gigabytes"] == 2000
        assert call_kwargs["volumes"] == 20

    def test_backup_gigabytes_with_unit(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Block storage backup_gigabytes accepts '500GB' and converts to 500 GB."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {"subnets": 1},
                    "block_storage": {
                        "backup_gigabytes": "500GB",  # 500GB = 500 GB
                        "gigabytes": 1000,
                    },
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage: backup_gigabytes differs
        bs_quota = MagicMock()
        bs_quota.backup_gigabytes = 100
        bs_quota.gigabytes = 1000
        bs_quota.to_dict.return_value = {
            "backup_gigabytes": 100,
            "gigabytes": 1000,
        }
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # Only block_storage should be updated
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1

        # Verify API received 500 GB (converted from "500GB")
        call_kwargs = shared_ctx.conn.block_storage.update_quota_set.call_args[1]
        assert call_kwargs["backup_gigabytes"] == 500
        assert call_kwargs["gigabytes"] == 1000

    def test_mixed_integer_and_string_quotas(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Can mix plain integers and unit strings in same config."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {
                        "ram": "100GB",  # String with unit
                        "cores": 20,  # Plain integer
                        "instances": 10,  # Plain integer
                    },
                    "network": {"subnets": 5},
                    "block_storage": {
                        "gigabytes": "2TB",  # String with unit
                        "volumes": 50,  # Plain integer
                    },
                },
            }
        )

        # All quotas differ
        compute_quota = MagicMock()
        compute_quota.ram = 51200
        compute_quota.cores = 10
        compute_quota.instances = 5
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 1000
        bs_quota.volumes = 20
        bs_quota.to_dict.return_value = {"gigabytes": 1000, "volumes": 20}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # All three services should be updated
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 3

        # Verify compute: ram converted, cores/instances pass through
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123",
            ram=100000,  # "100GB" → 100000 MB
            cores=20,
            instances=10,
        )

        # Verify block_storage: gigabytes converted, volumes pass through
        call_kwargs = shared_ctx.conn.block_storage.update_quota_set.call_args[1]
        assert call_kwargs["gigabytes"] == 2000  # "2TB" → 2000 GB
        assert call_kwargs["volumes"] == 50

    def test_ram_gibibytes_convenience_key(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """ram_gibibytes: 50 converts to ram=51200 MiB for Nova API."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {
                        "ram_gibibytes": 50,  # 50 GiB = 51200 MiB
                        "cores": 20,
                    },
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute: ram differs (current 32768 != desired 51200)
        compute_quota = MagicMock()
        compute_quota.ram = 32768
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "compute_quota"

        # Verify API received 51200 MiB (50 GiB * 1024)
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123",
            ram=51200,
            cores=20,
        )

    def test_backward_compatibility_all_integers(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Existing configs with plain integers continue to work unchanged."""
        # sample_project_cfg uses plain integers (no units)
        # Verify it still works

        # All quotas differ
        compute_quota = MagicMock()
        compute_quota.cores = 10
        compute_quota.ram = 25600
        compute_quota.instances = 5
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        net_quota = MagicMock()
        net_quota.subnets = 2
        net_quota.routers = 2
        net_quota.ports = 25
        net_quota.security_groups = 5
        net_quota.security_group_rules = 50
        shared_ctx.conn.network.get_quota.return_value = net_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 250
        bs_quota.volumes = 10
        bs_quota.snapshots = 5
        bs_quota.to_dict.return_value = {
            "gigabytes": 250,
            "volumes": 10,
            "snapshots": 5,
        }
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(sample_project_cfg, "proj-123", shared_ctx)

        # All three services should be updated
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 3

        # Verify integers pass through unchanged
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123",
            cores=20,
            ram=51200,  # Plain integer, unchanged
            instances=10,
        )

        call_kwargs = shared_ctx.conn.block_storage.update_quota_set.call_args[1]
        assert call_kwargs["gigabytes"] == 500  # Plain integer, unchanged


class TestUsageAwareQuotaEnforcement:
    """Usage-aware guards: quotas cannot be lowered below current usage."""

    def test_compute_quota_below_usage_clamped(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """desired=5, usage=10 → clamped to 10, UPDATED action."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 5},
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Current quota is 20 (above desired=5)
        current_quota = MagicMock()
        current_quota.cores = 20

        # Usage is 10 (above desired=5) — SDK stores usage in .usage dict
        usage_quota = MagicMock()
        usage_quota.usage = {"cores": 10}

        shared_ctx.conn.compute.get_quota_set.side_effect = lambda pid, **kw: (
            usage_quota if kw.get("usage") else current_quota
        )

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # No FAILED — clamping produces UPDATED with clamped value
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "compute_quota"

        # Quota set to 10 (clamped to usage), not 5
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123", cores=10
        )

    def test_compute_quota_above_usage_applied(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """desired=15, usage=10 → set to 15, normal."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 15},
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Current is 20, desired is 15 (lowering but above usage)
        current_quota = MagicMock()
        current_quota.cores = 20

        # Usage is only 10 — SDK stores usage in .usage dict
        usage_quota = MagicMock()
        usage_quota.usage = {"cores": 10}

        shared_ctx.conn.compute.get_quota_set.side_effect = lambda pid, **kw: (
            usage_quota if kw.get("usage") else current_quota
        )

        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert any(a.resource_type == "compute_quota" for a in updated)

        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123", cores=15
        )

    def test_compute_quota_raised_no_usage_check(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """desired=30, current=20 → set to 30, no usage call."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 30},
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        current_quota = MagicMock()
        current_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = current_quota

        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

        # get_quota_set called only once (no usage=True call)
        shared_ctx.conn.compute.get_quota_set.assert_called_once_with("proj-123")

    def test_network_quota_below_usage_clamped(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Neutron quota lowered below usage → clamped, UPDATED action."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {"subnets": 2},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network: current=10, desired=2, usage=5
        net_quota = MagicMock()
        net_quota.subnets = 10
        usage_net_quota = MagicMock()
        usage_net_quota.subnets = {"used": 5}
        shared_ctx.conn.network.get_quota.side_effect = lambda pid, **kw: (
            usage_net_quota if kw.get("details") else net_quota
        )

        # Block-storage matches
        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # No FAILED — clamping produces UPDATED with clamped value
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "network_quota"

        # Quota set to 5 (usage), not 2 (desired)
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-123", subnets=5
        )

    def test_block_storage_quota_below_usage_clamped(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Cinder quota lowered below usage → clamped, UPDATED action."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 20},
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Compute matches
        compute_quota = MagicMock()
        compute_quota.cores = 20
        shared_ctx.conn.compute.get_quota_set.return_value = compute_quota

        # Network matches
        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        # Block-storage: current=500, desired=100, usage=300
        # SDK stores usage in .usage dict
        bs_quota = MagicMock()
        bs_quota.gigabytes = 500
        bs_quota.to_dict.return_value = {"gigabytes": 500}

        usage_bs_quota = MagicMock()
        usage_bs_quota.usage = {"gigabytes": 300}

        shared_ctx.conn.block_storage.get_quota_set.side_effect = lambda pid, **kw: (
            usage_bs_quota if kw.get("usage") else bs_quota
        )

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # No FAILED — clamping produces UPDATED with clamped value
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert updated[0].resource_type == "block_storage_quota"

    def test_mixed_keys_partial_clamp(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Some keys below usage, others fine → all included in single UPDATED."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 5, "instances": 20},
                    "network": {"subnets": 1},
                    "block_storage": {"gigabytes": 100},
                },
            }
        )

        # Current: cores=20, instances=10
        current_quota = MagicMock()
        current_quota.cores = 20
        current_quota.instances = 10

        # Usage: cores=10 (above desired=5), instances=3 (below desired=20)
        # SDK stores usage in .usage dict
        usage_quota = MagicMock()
        usage_quota.usage = {"cores": 10, "instances": 3}

        shared_ctx.conn.compute.get_quota_set.side_effect = lambda pid, **kw: (
            usage_quota if kw.get("usage") else current_quota
        )

        net_quota = MagicMock()
        net_quota.subnets = 1
        shared_ctx.conn.network.get_quota.return_value = net_quota

        bs_quota = MagicMock()
        bs_quota.gigabytes = 100
        bs_quota.to_dict.return_value = {"gigabytes": 100}
        shared_ctx.conn.block_storage.get_quota_set.return_value = bs_quota

        actions = ensure_quotas(cfg, "proj-123", shared_ctx)

        # No FAILED — clamping folds into UPDATED
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

        # Update happens with clamped values
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert any(a.resource_type == "compute_quota" for a in updated)

        # cores=10 (clamped), instances=20 (raised)
        shared_ctx.conn.compute.update_quota_set.assert_called_once_with(
            "proj-123", cores=10, instances=20
        )
