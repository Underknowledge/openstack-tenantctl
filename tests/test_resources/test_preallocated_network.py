"""Tests for pre-allocated network provisioning — ensure_preallocated_network."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.models import ProjectConfig
from src.resources.prealloc.network import ensure_preallocated_network
from src.utils import ActionStatus, SharedContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(
    networks: int = 1,
    subnets: int = 1,
    routers: int = 1,
    *,
    include_network: bool = True,
) -> ProjectConfig:
    """Return a project config with the given network quota values."""
    quotas: dict = {
        "network": {"networks": networks, "subnets": subnets, "routers": routers}
    }
    data: dict = {
        "name": "test_project",
        "resource_prefix": "testproject",
        "quotas": quotas,
    }
    if include_network:
        data["network"] = {
            "mtu": 1500,
            "subnet": {
                "cidr": "192.168.1.0/24",
                "gateway_ip": "192.168.1.254",
                "allocation_pools": [{"start": "192.168.1.1", "end": "192.168.1.253"}],
                "dns_nameservers": ["8.8.8.8"],
                "enable_dhcp": True,
            },
        }
    return ProjectConfig.from_dict(data)


def _cfg_no_quotas() -> ProjectConfig:
    """Return a project config with no quotas section at all."""
    return ProjectConfig.from_dict(
        {
            "name": "test_project",
            "resource_prefix": "testproject",
        }
    )


def _mock_net(name: str) -> MagicMock:
    """Return a mock network object."""
    net = MagicMock()
    net.name = name
    net.id = f"{name}-id"
    return net


# ---------------------------------------------------------------------------
# No quotas configured → SKIPPED
# ---------------------------------------------------------------------------


class TestNoQuotasConfigured:
    """When no quotas.network is configured, skip immediately."""

    def test_returns_skipped(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg_no_quotas()
        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].details == "no quotas.network configured"
        # Verify no API calls were made
        shared_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


class TestDryRunNetworksLessThan2:
    """Online dry-run with networks < 2 → reads network, reports quotas."""

    def test_existing_network_skipped(self, dry_run_ctx: SharedContext) -> None:
        """Network exists → SKIPPED, quotas not written."""
        cfg = _cfg(networks=1)
        existing_net = _mock_net("testproject-network")

        with patch(
            "src.resources.prealloc.network.find_network", return_value=existing_net
        ):
            actions = ensure_preallocated_network(cfg, "proj-id", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "already exists" in actions[0].details
        # No quota writes in dry-run
        dry_run_ctx.conn.network.update_quota.assert_not_called()

    def test_offline_returns_skipped(self, offline_ctx: SharedContext) -> None:
        """Offline mode → SKIPPED with offline message."""
        cfg = _cfg(networks=1)
        actions = ensure_preallocated_network(cfg, "proj-id", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details


class TestDryRunNetworksZero:
    """Online dry-run with networks=0 → quota set skipped."""

    def test_returns_skipped_quota_set(self, dry_run_ctx: SharedContext) -> None:
        cfg = _cfg(networks=0)
        actions = ensure_preallocated_network(cfg, "proj-id", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "networks=0" in actions[0].details
        # No writes in dry-run
        dry_run_ctx.conn.network.update_quota.assert_not_called()


class TestDryRunNetworksGte2:
    """Dry-run with networks >= 2 → quotas handled by ensure_quotas."""

    def test_returns_skipped_ensure_quotas(self, dry_run_ctx: SharedContext) -> None:
        cfg = _cfg(networks=3)
        actions = ensure_preallocated_network(cfg, "proj-id", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "networks quota is 3" in actions[0].details
        assert "ensure_quotas" in actions[0].details
        # Verify no API calls were made
        dry_run_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# networks >= 2 → skip (non-dry-run)
# ---------------------------------------------------------------------------


class TestNetworksGte2Skips:
    """When networks >= 2, this module skips and lets ensure_quotas handle it."""

    def test_returns_skipped_at_threshold(self, shared_ctx: SharedContext) -> None:
        """networks=2 triggers the >= 2 threshold, quotas handled by ensure_quotas."""
        cfg = _cfg(networks=2)
        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert "networks quota is 2" in actions[0].details
        assert "ensure_quotas" in actions[0].details
        # Verify this module does NOT touch quotas when networks >= 2
        shared_ctx.conn.network.update_quota.assert_not_called()

    def test_returns_skipped_above_threshold(self, shared_ctx: SharedContext) -> None:
        """networks > 2 also triggers skip, delegating to ensure_quotas."""
        cfg = _cfg(networks=5)
        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert "networks quota is 5" in actions[0].details
        assert "ensure_quotas" in actions[0].details
        # Verify no quota API calls
        shared_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# networks == 0 → set quota, skip creation
# ---------------------------------------------------------------------------


class TestNetworksZeroQuotaOnly:
    """When networks=0, set quota but don't create any network."""

    def test_sets_quota_returns_skipped(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=0, subnets=0, routers=0)
        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].details == "networks=0, no network requested — quota set"
        # Verify the correct quota values were sent to the API
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=0, subnets=0, routers=0
        )


# ---------------------------------------------------------------------------
# networks == 1, network already exists → set quotas, skip creation
# ---------------------------------------------------------------------------


class TestNetworkAlreadyExists:
    """When the expected network already exists, just set quotas."""

    def test_sets_quota_returns_skipped(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1, subnets=1, routers=1)
        existing_net = _mock_net("testproject-network")

        with patch(
            "src.resources.prealloc.network.find_network", return_value=existing_net
        ):
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].name == "testproject-network"
        assert actions[0].details == "already exists, quotas set"
        # Verify the correct quota values were sent to the API
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=1, subnets=1, routers=1
        )


# ---------------------------------------------------------------------------
# Safety check: project owns network(s) with different name
# ---------------------------------------------------------------------------


class TestSafetyCheckDifferentName:
    """Project owns network(s) that don't match expected name → skip creation, set quotas."""

    def test_existing_different_name_sets_quota(
        self, shared_ctx: SharedContext
    ) -> None:
        cfg = _cfg(networks=1, subnets=1, routers=1)
        legacy_net = _mock_net("legacy-network")

        with patch("src.resources.prealloc.network.find_network", return_value=None):
            shared_ctx.conn.network.networks.return_value = [legacy_net]
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].name == "testproject-network"
        # Verify the safety message contains the legacy network name
        assert "project has existing network(s): legacy-network" in actions[0].details
        assert "quotas set" in actions[0].details
        # Verify the correct quota values were sent to the API
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=1, subnets=1, routers=1
        )

    def test_multiple_existing_networks(self, shared_ctx: SharedContext) -> None:
        """Project has multiple legacy networks — all names listed in details."""
        cfg = _cfg(networks=1, subnets=2, routers=1)
        nets = [_mock_net("net-a"), _mock_net("net-b")]

        with patch("src.resources.prealloc.network.find_network", return_value=None):
            shared_ctx.conn.network.networks.return_value = nets
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        # Verify both network names appear in the safety message
        assert "net-a" in actions[0].details
        assert "net-b" in actions[0].details
        assert "quotas set" in actions[0].details
        # Verify quotas were still set with correct values
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=1, subnets=2, routers=1
        )


# ---------------------------------------------------------------------------
# Fallback: no network exists → calls ensure_network_stack + sets quota
# ---------------------------------------------------------------------------


class TestFallbackCreatesNetworkStack:
    """When no network exists, falls back to ensure_network_stack."""

    def test_calls_ensure_network_stack(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1, subnets=1, routers=1)
        mock_action = MagicMock()
        mock_action.status.value = "CREATED"

        with (
            patch("src.resources.prealloc.network.find_network", return_value=None),
            patch(
                "src.resources.prealloc.network.ensure_network_stack",
                return_value=mock_action,
            ) as mock_ens,
        ):
            shared_ctx.conn.network.networks.return_value = []  # no existing nets
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        # Verify ensure_network_stack was called with correct arguments
        mock_ens.assert_called_once_with(cfg, "proj-id", shared_ctx)
        # Verify the action from ensure_network_stack is returned
        assert len(actions) == 1
        assert actions[0] is mock_action
        # Verify quotas were set AFTER network creation with correct values
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=1, subnets=1, routers=1
        )

    def test_quota_values_forwarded(self, shared_ctx: SharedContext) -> None:
        """Custom quota values are forwarded to _set_network_quotas."""
        cfg = _cfg(networks=1, subnets=3, routers=2)
        mock_action = MagicMock()
        mock_action.status.value = "CREATED"

        with (
            patch("src.resources.prealloc.network.find_network", return_value=None),
            patch(
                "src.resources.prealloc.network.ensure_network_stack",
                return_value=mock_action,
            ),
        ):
            shared_ctx.conn.network.networks.return_value = []
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        # Verify the custom quota values (not defaults) were sent to the API
        assert len(actions) == 1
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=1, subnets=3, routers=2
        )


# ---------------------------------------------------------------------------
# Default quota values (missing from config)
# ---------------------------------------------------------------------------


class TestDefaultQuotaValues:
    """When quota keys are missing, defaults of 1 are used."""

    def test_defaults_to_one(self, shared_ctx: SharedContext) -> None:
        """Config with empty network quotas → defaults to networks=1, subnets=1, routers=1."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "testproject",
                "quotas": {"network": {"ports": 50}},
                "network": {
                    "subnet": {
                        "cidr": "192.168.1.0/24",
                        "gateway_ip": "192.168.1.254",
                        "allocation_pools": [],
                    },
                },
            }
        )
        existing_net = _mock_net("testproject-network")

        with patch(
            "src.resources.prealloc.network.find_network", return_value=existing_net
        ):
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        # Verify the default values (line 72-74) were applied correctly
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=1, subnets=1, routers=1
        )


# ---------------------------------------------------------------------------
# Edge case: quota update failure
# ---------------------------------------------------------------------------


class TestQuotaUpdateFailure:
    """Quota update can fail with HttpException during _set_network_quotas."""

    def test_quota_update_fails_during_networks_zero(
        self, shared_ctx: SharedContext
    ) -> None:
        """When networks=0, quota update failure propagates after retries."""
        from openstack.exceptions import HttpException

        cfg = _cfg(networks=0, subnets=0, routers=0)
        shared_ctx.conn.network.update_quota.side_effect = HttpException(
            message="Quota update failed"
        )

        # @retry exhausts retries, exception propagates
        import pytest

        with pytest.raises(HttpException, match="Quota update failed"):
            ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        # Retry logic means multiple attempts (at least 2)
        assert shared_ctx.conn.network.update_quota.call_count >= 2

    def test_quota_update_fails_after_network_creation(
        self, shared_ctx: SharedContext
    ) -> None:
        """Quota update failure after successful network creation still raises."""
        from openstack.exceptions import HttpException

        cfg = _cfg(networks=1, subnets=1, routers=1)
        mock_action = MagicMock()
        mock_action.status.value = "CREATED"

        shared_ctx.conn.network.update_quota.side_effect = HttpException(
            message="Quota service unavailable"
        )

        with (
            patch("src.resources.prealloc.network.find_network", return_value=None),
            patch(
                "src.resources.prealloc.network.ensure_network_stack",
                return_value=mock_action,
            ),
        ):
            shared_ctx.conn.network.networks.return_value = []

            import pytest

            with pytest.raises(HttpException, match="Quota service unavailable"):
                ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        # Network stack was created, then quota update failed
        assert shared_ctx.conn.network.update_quota.call_count >= 2

    def test_quota_update_fails_with_existing_network(
        self, shared_ctx: SharedContext
    ) -> None:
        """Quota update failure when network already exists propagates exception."""
        from openstack.exceptions import HttpException

        cfg = _cfg(networks=1, subnets=1, routers=1)
        existing_net = _mock_net("testproject-network")

        shared_ctx.conn.network.update_quota.side_effect = HttpException(
            message="Service error"
        )

        with patch(
            "src.resources.prealloc.network.find_network", return_value=existing_net
        ):
            import pytest

            with pytest.raises(HttpException, match="Service error"):
                ensure_preallocated_network(cfg, "proj-id", shared_ctx)


# ---------------------------------------------------------------------------
# Edge case: networks=0 with existing networks in project
# ---------------------------------------------------------------------------


class TestNetworksZeroWithExistingNetworks:
    """When networks=0 is configured but project has existing networks."""

    def test_sets_quota_to_zero_ignores_existing(
        self, shared_ctx: SharedContext
    ) -> None:
        """networks=0 sets quota regardless of existing networks (no safety check).

        The networks=0 branch (line 104) happens BEFORE find_network (line 125),
        so the code never checks for existing networks when networks=0.
        """
        cfg = _cfg(networks=0, subnets=0, routers=0)

        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].details == "networks=0, no network requested — quota set"
        # Verify quota was set to zero
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=0, subnets=0, routers=0
        )
        # find_network should NOT be called for networks=0 path
        # (verified by absence of patch — function would fail if called)


# ---------------------------------------------------------------------------
# Edge case: partial quota config
# ---------------------------------------------------------------------------


class TestPartialQuotaConfig:
    """Config with only one or two network quota keys specified."""

    def test_only_networks_key(self, shared_ctx: SharedContext) -> None:
        """Only networks specified in quota → subnets and routers default to 1."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "testproject",
                "quotas": {"network": {"networks": 1}},
                "network": {
                    "subnet": {
                        "cidr": "192.168.1.0/24",
                        "gateway_ip": "192.168.1.254",
                        "allocation_pools": [],
                    },
                },
            }
        )
        existing_net = _mock_net("testproject-network")

        with patch(
            "src.resources.prealloc.network.find_network", return_value=existing_net
        ):
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        # Verify missing keys (subnets, routers) defaulted to 1 (line 73-74)
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=1, subnets=1, routers=1
        )

    def test_only_subnets_and_routers_keys(self, shared_ctx: SharedContext) -> None:
        """networks key missing → defaults to 1."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "testproject",
                "quotas": {"network": {"subnets": 5, "routers": 2}},
                "network": {
                    "subnet": {
                        "cidr": "192.168.1.0/24",
                        "gateway_ip": "192.168.1.254",
                        "allocation_pools": [],
                    },
                },
            }
        )
        existing_net = _mock_net("testproject-network")

        with patch(
            "src.resources.prealloc.network.find_network", return_value=existing_net
        ):
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        # Verify missing networks key defaulted to 1 (line 72), others use config values
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-id", networks=1, subnets=5, routers=2
        )
