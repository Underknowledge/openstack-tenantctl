"""Tests for pre-allocated network provisioning — ensure_preallocated_network.

The module owns network/subnet/router *resource* lifecycle only; quota writes
are owned by ``ensure_quotas``.  Every test asserts that ``update_quota`` is
never called from this module.
"""

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
    quotas: dict = {"network": {"networks": networks, "subnets": subnets, "routers": routers}}
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
# No quotas configured → SKIPPED, no calls
# ---------------------------------------------------------------------------


class TestNoQuotasConfigured:
    def test_returns_skipped(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg_no_quotas()
        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].details == "no quotas.network configured"
        shared_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# networks == 0 → SKIPPED, no quota writes
# ---------------------------------------------------------------------------


class TestNetworksZero:
    def test_returns_skipped_no_quota_write(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=0, subnets=0, routers=0)
        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert "networks=0" in actions[0].details
        shared_ctx.conn.network.update_quota.assert_not_called()

    def test_dry_run_returns_skipped(self, dry_run_ctx: SharedContext) -> None:
        cfg = _cfg(networks=0)
        actions = ensure_preallocated_network(cfg, "proj-id", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "networks=0" in actions[0].details
        dry_run_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# networks >= 2 → SKIPPED, delegated to ensure_quotas
# ---------------------------------------------------------------------------


class TestNetworksGte2Skips:
    def test_at_threshold(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=2)
        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert "networks quota is 2" in actions[0].details
        assert "ensure_quotas" in actions[0].details
        shared_ctx.conn.network.update_quota.assert_not_called()

    def test_above_threshold(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=5)
        actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "networks quota is 5" in actions[0].details
        shared_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# Offline mode → SKIPPED with "offline" message
# ---------------------------------------------------------------------------


class TestOffline:
    def test_returns_skipped(self, offline_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1)
        actions = ensure_preallocated_network(cfg, "proj-id", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details


# ---------------------------------------------------------------------------
# networks == 1, network already exists → SKIPPED, no quota writes
# ---------------------------------------------------------------------------


class TestNetworkAlreadyExists:
    def test_returns_skipped_live(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1)
        existing_net = _mock_net("testproject-network")

        with patch("src.resources.prealloc.network.find_network", return_value=existing_net):
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].name == "testproject-network"
        assert actions[0].details == "already exists"
        shared_ctx.conn.network.update_quota.assert_not_called()

    def test_returns_skipped_dry_run(self, dry_run_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1)
        existing_net = _mock_net("testproject-network")

        with patch("src.resources.prealloc.network.find_network", return_value=existing_net):
            actions = ensure_preallocated_network(cfg, "proj-id", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "already exists" in actions[0].details
        dry_run_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# Safety check: project owns network(s) with different name → SKIPPED
# ---------------------------------------------------------------------------


class TestSafetyCheckDifferentName:
    def test_existing_different_name(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1)
        legacy_net = _mock_net("legacy-network")

        with patch("src.resources.prealloc.network.find_network", return_value=None):
            shared_ctx.conn.network.networks.return_value = [legacy_net]
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].name == "testproject-network"
        assert "legacy-network" in actions[0].details
        shared_ctx.conn.network.update_quota.assert_not_called()

    def test_multiple_existing_networks(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1)
        nets = [_mock_net("net-a"), _mock_net("net-b")]

        with patch("src.resources.prealloc.network.find_network", return_value=None):
            shared_ctx.conn.network.networks.return_value = nets
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "net-a" in actions[0].details
        assert "net-b" in actions[0].details
        shared_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# Dry-run, network missing → CREATED "would create network stack"
# ---------------------------------------------------------------------------


class TestDryRunMissingNetwork:
    def test_reports_would_create(self, dry_run_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1)

        with patch("src.resources.prealloc.network.find_network", return_value=None):
            dry_run_ctx.conn.network.networks.return_value = []
            actions = ensure_preallocated_network(cfg, "proj-id", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.CREATED
        assert actions[0].resource_type == "preallocated_network"
        assert actions[0].name == "testproject-network"
        assert "would create" in actions[0].details
        dry_run_ctx.conn.network.update_quota.assert_not_called()


# ---------------------------------------------------------------------------
# Live, network missing → falls back to ensure_network_stack, no quota writes
# ---------------------------------------------------------------------------


class TestFallbackCreatesNetworkStack:
    def test_calls_ensure_network_stack(self, shared_ctx: SharedContext) -> None:
        cfg = _cfg(networks=1)
        mock_action = MagicMock()
        mock_action.status.value = "CREATED"

        with (
            patch("src.resources.prealloc.network.find_network", return_value=None),
            patch(
                "src.resources.prealloc.network.ensure_network_stack",
                return_value=mock_action,
            ) as mock_ens,
        ):
            shared_ctx.conn.network.networks.return_value = []
            actions = ensure_preallocated_network(cfg, "proj-id", shared_ctx)

        mock_ens.assert_called_once_with(cfg, "proj-id", shared_ctx)
        assert len(actions) == 1
        assert actions[0] is mock_action
        shared_ctx.conn.network.update_quota.assert_not_called()
