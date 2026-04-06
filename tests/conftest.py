"""Shared fixtures for all OpenStack provisioner tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.models import ProjectConfig
from src.state_store import StateStore
from src.utils import SharedContext


@pytest.fixture(autouse=True)
def _no_retry_sleep():
    """Disable tenacity retry sleeps in all tests to avoid real delays."""
    with patch("tenacity.nap.time"):
        yield


@pytest.fixture
def mock_conn() -> MagicMock:
    """Return a MagicMock simulating ``openstack.connection.Connection``.

    Sub-mocks are wired for identity, network, compute, and block_storage
    proxies with the methods used by the provisioner resource modules.
    """
    conn = MagicMock(name="Connection")

    # --- Identity proxy ---
    identity = MagicMock(name="identity")
    identity.find_domain = MagicMock(name="find_domain")
    identity.find_project = MagicMock(name="find_project")
    identity.create_project = MagicMock(name="create_project")
    identity.update_project = MagicMock(name="update_project")
    identity.get_mapping = MagicMock(name="get_mapping")
    identity.update_mapping = MagicMock(name="update_mapping")
    identity.find_group = MagicMock(name="find_group")
    identity.find_role = MagicMock(name="find_role")
    identity.validate_group_has_project_role = MagicMock(
        name="validate_group_has_project_role"
    )
    identity.assign_project_role_to_group = MagicMock(
        name="assign_project_role_to_group"
    )
    identity.unassign_project_role_from_group = MagicMock(
        name="unassign_project_role_from_group"
    )
    identity.delete_project = MagicMock(name="delete_project")
    conn.identity = identity

    # --- Network proxy ---
    network = MagicMock(name="network")
    network.find_network = MagicMock(name="find_network")
    network.create_network = MagicMock(name="create_network")
    network.create_subnet = MagicMock(name="create_subnet")
    network.create_router = MagicMock(name="create_router")
    network.add_interface_to_router = MagicMock(name="add_interface_to_router")
    network.find_security_group = MagicMock(name="find_security_group")
    network.create_security_group = MagicMock(name="create_security_group")
    network.create_security_group_rule = MagicMock(name="create_security_group_rule")
    network.get_quota = MagicMock(name="get_quota")
    network.update_quota = MagicMock(name="update_quota")
    network.ips = MagicMock(name="ips")
    network.create_ip = MagicMock(name="create_ip")
    network.routers = MagicMock(name="routers", return_value=[])
    network.delete_ip = MagicMock(name="delete_ip")
    network.ports = MagicMock(name="ports")
    network.remove_interface_from_router = MagicMock(
        name="remove_interface_from_router"
    )
    network.update_router = MagicMock(name="update_router")
    network.delete_router = MagicMock(name="delete_router")
    network.subnets = MagicMock(name="subnets")
    network.delete_subnet = MagicMock(name="delete_subnet")
    network.delete_network = MagicMock(name="delete_network")
    network.security_groups = MagicMock(name="security_groups")
    network.delete_security_group = MagicMock(name="delete_security_group")
    conn.network = network

    # --- Compute proxy ---
    compute = MagicMock(name="compute")
    compute.get_quota_set = MagicMock(name="get_quota_set")
    compute.update_quota_set = MagicMock(name="update_quota_set")
    compute.servers = MagicMock(name="servers")
    compute.shelve_server = MagicMock(name="shelve_server")
    compute.unshelve_server = MagicMock(name="unshelve_server")
    conn.compute = compute

    # --- Block-storage proxy ---
    block_storage = MagicMock(name="block_storage")
    block_storage.get_quota_set = MagicMock(name="get_quota_set")
    block_storage.update_quota_set = MagicMock(name="update_quota_set")
    block_storage.volumes = MagicMock(name="volumes")
    block_storage.snapshots = MagicMock(name="snapshots")
    block_storage.delete_snapshot = MagicMock(name="delete_snapshot")
    conn.block_storage = block_storage

    return conn


@pytest.fixture
def mock_state_store() -> MagicMock:
    """Return a ``MagicMock`` implementing the ``StateStore`` protocol."""
    store = MagicMock(spec=StateStore)
    store.load.return_value = {}
    return store


@pytest.fixture
def shared_ctx(mock_conn: MagicMock, mock_state_store: MagicMock) -> SharedContext:
    """Return a ``SharedContext`` wired to the mock connection (dry_run=False)."""
    return SharedContext(
        conn=mock_conn,
        dry_run=False,
        external_net_id="ext-net-id-123",
        external_subnet_id="ext-subnet-123",
        state_store=mock_state_store,
    )


@pytest.fixture
def dry_run_ctx(mock_conn: MagicMock, mock_state_store: MagicMock) -> SharedContext:
    """Return a ``SharedContext`` wired to the mock connection (dry_run=True)."""
    return SharedContext(
        conn=mock_conn,
        dry_run=True,
        external_net_id="ext-net-id-123",
        external_subnet_id="ext-subnet-123",
        state_store=mock_state_store,
    )


@pytest.fixture
def offline_ctx(mock_state_store: MagicMock) -> SharedContext:
    """Return a ``SharedContext`` with no connection (offline dry-run mode)."""
    return SharedContext(
        conn=None,
        dry_run=True,
        state_store=mock_state_store,
    )


@pytest.fixture
def sample_project_cfg() -> ProjectConfig:
    """Return a complete, fully-merged project config dict for testing."""
    return ProjectConfig.from_dict(
        {
            "name": "test_project",
            "resource_prefix": "testproject",
            "_state_key": "test_project",
            "description": "Test project",
            "enabled": True,
            "state": "present",
            "domain_id": "default",
            "network": {
                "mtu": 1500,
                "subnet": {
                    "cidr": "192.168.1.0/24",
                    "gateway_ip": "192.168.1.254",
                    "allocation_pools": [
                        {"start": "192.168.1.1", "end": "192.168.1.253"},
                    ],
                    "dns_nameservers": ["8.8.8.8"],
                    "dhcp": True,
                },
            },
            "quotas": {
                "compute": {"cores": 20, "ram": 51200, "instances": 10},
                "network": {
                    "floating_ips": 0,
                    "networks": 1,
                    "subnets": 1,
                    "routers": 1,
                    "ports": 50,
                    "security_groups": 10,
                    "security_group_rules": 100,
                },
                "block_storage": {"gigabytes": 500, "volumes": 20, "snapshots": 10},
            },
            "security_group": {
                "name": "default",
                "rules": [
                    {
                        "direction": "ingress",
                        "protocol": "icmp",
                        "remote_ip_prefix": "0.0.0.0/0",
                        "description": "Allow ICMP",
                    },
                    {
                        "direction": "ingress",
                        "protocol": "tcp",
                        "port_range_min": 22,
                        "port_range_max": 22,
                        "remote_ip_prefix": "0.0.0.0/0",
                        "description": "Allow SSH",
                    },
                ],
            },
            "group_role_assignments": [
                {"group": "test-admin-group", "roles": ["admin", "member"]},
            ],
            "federation": {
                "issuer": "https://myidp.corp/realms/myrealm",
                "mapping_id": "my-mapping",
                "group_prefix": "/services/openstack/",
                "role_assignments": [
                    {
                        "idp_group": "member",
                        "roles": ["member", "load-balancer_member"],
                    },
                    {"idp_group": "reader", "roles": ["reader"]},
                ],
            },
        }
    )
