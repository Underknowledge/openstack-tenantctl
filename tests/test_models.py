"""Tests for typed configuration models (src.models)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from src.models import (
    AllocationPool,
    FederationConfig,
    FederationRoleAssignment,
    FipEntry,
    GroupRoleAssignment,
    NetworkConfig,
    ProjectConfig,
    ProjectState,
    QuotaConfig,
    ReleasedFipEntry,
    ReleasedRouterIpEntry,
    RouterIpEntry,
    SecurityGroupConfig,
    SecurityGroupRule,
    SubnetConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def full_project_dict() -> dict[str, Any]:
    """Return a fully-merged project config dict matching conftest.sample_project_cfg."""
    return {
        "name": "test_project",
        "resource_prefix": "testproject",
        "_state_key": "test_project",
        "_config_path": "/tmp/projects/test_project.yaml",
        "description": "Test project",
        "enabled": True,
        "state": "present",
        "domain_id": "default",
        "reclaim_floating_ips": False,
        "network": {
            "mtu": 1500,
            "subnet": {
                "cidr": "192.168.1.0/24",
                "gateway_ip": "192.168.1.254",
                "allocation_pools": [
                    {"start": "192.168.1.1", "end": "192.168.1.253"},
                ],
                "dns_nameservers": ["8.8.8.8"],
                "enable_dhcp": True,
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
                {"idp_group": "member", "roles": ["member", "load-balancer_member"]},
                {"idp_group": "reader", "roles": ["reader"]},
            ],
        },
        "preallocated_fips": [
            {"id": "fip-1", "address": "10.0.0.1"},
        ],
        "released_fips": [],
        "router_ips": [
            {"id": "r-1", "name": "testproject-router", "external_ip": "10.0.0.100"},
        ],
        "released_router_ips": [],
    }


@pytest.fixture
def minimal_project_dict() -> dict[str, Any]:
    """Return the minimum viable project config dict (no optional sections)."""
    return {
        "name": "minimal",
        "resource_prefix": "minimal",
    }


# ---------------------------------------------------------------------------
# Round-trip: full dict → ProjectConfig → verify all fields
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    """Verify that a complete config dict converts to ProjectConfig with all fields correct."""

    def test_top_level_scalars(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)

        assert cfg.name == "test_project"
        assert cfg.resource_prefix == "testproject"
        assert cfg.description == "Test project"
        assert cfg.enabled is True
        assert cfg.state == ProjectState.PRESENT
        assert cfg.state == "present"  # StrEnum comparison with str
        assert cfg.domain_id == "default"
        assert cfg.domain is None
        assert cfg.reclaim_floating_ips is False
        assert cfg.config_path == "/tmp/projects/test_project.yaml"
        assert cfg.state_key == "test_project"

    def test_network_section(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)

        assert cfg.network is not None
        assert cfg.network.mtu == 1500
        assert cfg.network.subnet.cidr == "192.168.1.0/24"
        assert cfg.network.subnet.gateway_ip == "192.168.1.254"
        assert cfg.network.subnet.allocation_pools == [
            AllocationPool(start="192.168.1.1", end="192.168.1.253"),
        ]
        assert cfg.network.subnet.dns_nameservers == ["8.8.8.8"]
        assert cfg.network.subnet.enable_dhcp is True

    def test_quotas_section(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)

        assert cfg.quotas is not None
        # Verify compute quota (all 3 fields)
        assert cfg.quotas.compute["cores"] == 20
        assert cfg.quotas.compute["ram"] == 51200
        assert cfg.quotas.compute["instances"] == 10
        # Verify network quota (all 7 fields from fixture)
        assert cfg.quotas.network["floating_ips"] == 0
        assert cfg.quotas.network["networks"] == 1
        assert cfg.quotas.network["subnets"] == 1
        assert cfg.quotas.network["routers"] == 1
        assert cfg.quotas.network["ports"] == 50
        assert cfg.quotas.network["security_groups"] == 10
        assert cfg.quotas.network["security_group_rules"] == 100
        # Verify block_storage quota (all 3 fields)
        assert cfg.quotas.block_storage["gigabytes"] == 500
        assert cfg.quotas.block_storage["volumes"] == 20
        assert cfg.quotas.block_storage["snapshots"] == 10

    def test_security_group_section(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)

        assert cfg.security_group is not None
        assert cfg.security_group.name == "default"
        assert len(cfg.security_group.rules) == 2
        # Verify ICMP rule structure
        assert cfg.security_group.rules[0].direction == "ingress"
        assert cfg.security_group.rules[0].protocol == "icmp"
        assert cfg.security_group.rules[0].remote_ip_prefix == "0.0.0.0/0"
        assert cfg.security_group.rules[0].description == "Allow ICMP"
        # Verify SSH rule structure
        assert cfg.security_group.rules[1].direction == "ingress"
        assert cfg.security_group.rules[1].protocol == "tcp"
        assert cfg.security_group.rules[1].port_range_min == 22
        assert cfg.security_group.rules[1].port_range_max == 22
        assert cfg.security_group.rules[1].remote_ip_prefix == "0.0.0.0/0"
        assert cfg.security_group.rules[1].description == "Allow SSH"

    def test_federation_section(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)

        assert cfg.federation is not None
        assert cfg.federation.issuer == "https://myidp.corp/realms/myrealm"
        assert cfg.federation.mapping_id == "my-mapping"
        assert cfg.federation.group_prefix == "/services/openstack/"
        assert len(cfg.federation.role_assignments) == 2
        # Verify first role assignment (all fields)
        assert cfg.federation.role_assignments[0].idp_group == "member"
        assert cfg.federation.role_assignments[0].roles == [
            "member",
            "load-balancer_member",
        ]
        # Verify second role assignment (all fields)
        assert cfg.federation.role_assignments[1].idp_group == "reader"
        assert cfg.federation.role_assignments[1].roles == ["reader"]

    def test_group_role_assignments(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)

        assert len(cfg.group_role_assignments) == 1
        assert cfg.group_role_assignments[0].group == "test-admin-group"
        assert cfg.group_role_assignments[0].roles == ["admin", "member"]
        assert cfg.group_role_assignments[0].state == "present"

    def test_state_fields(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)

        assert cfg.preallocated_fips == [
            FipEntry(id="fip-1", address="10.0.0.1"),
        ]
        assert cfg.released_fips == []
        assert cfg.router_ips == [
            RouterIpEntry(
                id="r-1", name="testproject-router", external_ip="10.0.0.100"
            ),
        ]
        assert cfg.released_router_ips == []


# ---------------------------------------------------------------------------
# Minimal config — optional sections absent
# ---------------------------------------------------------------------------


class TestMinimalConfig:
    """Verify that a minimal config (no optional sections) produces correct defaults."""

    def test_optional_sections_are_none(
        self, minimal_project_dict: dict[str, Any]
    ) -> None:
        cfg = ProjectConfig.from_dict(minimal_project_dict)

        assert cfg.name == "minimal"
        assert cfg.resource_prefix == "minimal"
        assert cfg.network is None
        assert cfg.quotas is None
        assert cfg.security_group is None
        assert cfg.federation is None

    def test_default_scalars(self, minimal_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(minimal_project_dict)

        assert cfg.description == ""
        assert cfg.enabled is True
        assert cfg.state == ProjectState.PRESENT
        assert cfg.domain_id == "default"
        assert cfg.domain is None
        assert cfg.reclaim_floating_ips is False
        assert cfg.config_path == ""
        assert cfg.state_key == ""

    def test_default_lists(self, minimal_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(minimal_project_dict)

        assert cfg.group_role_assignments == []
        assert cfg.preallocated_fips == []
        assert cfg.released_fips == []
        assert cfg.router_ips == []
        assert cfg.released_router_ips == []


# ---------------------------------------------------------------------------
# State field key mapping (_state_key → state_key, _config_path → config_path)
# ---------------------------------------------------------------------------


class TestUnderscorePrefixMapping:
    """Verify that _state_key and _config_path map to state_key and config_path."""

    def test_underscore_keys(self) -> None:
        data: dict[str, Any] = {
            "name": "test",
            "resource_prefix": "test",
            "_state_key": "my-state-key",
            "_config_path": "/path/to/config.yaml",
        }
        cfg = ProjectConfig.from_dict(data)

        assert cfg.state_key == "my-state-key"
        assert cfg.config_path == "/path/to/config.yaml"

    def test_plain_keys_fallback(self) -> None:
        data: dict[str, Any] = {
            "name": "test",
            "resource_prefix": "test",
            "state_key": "plain-key",
            "config_path": "/plain/path.yaml",
        }
        cfg = ProjectConfig.from_dict(data)

        assert cfg.state_key == "plain-key"
        assert cfg.config_path == "/plain/path.yaml"

    def test_underscore_takes_precedence(self) -> None:
        data: dict[str, Any] = {
            "name": "test",
            "resource_prefix": "test",
            "_state_key": "underscore-wins",
            "state_key": "plain-loses",
            "_config_path": "/underscore/wins.yaml",
            "config_path": "/plain/loses.yaml",
        }
        cfg = ProjectConfig.from_dict(data)

        assert cfg.state_key == "underscore-wins"
        assert cfg.config_path == "/underscore/wins.yaml"


# ---------------------------------------------------------------------------
# ProjectState enum
# ---------------------------------------------------------------------------


class TestProjectState:
    """Verify ProjectState enum values and string comparison."""

    def test_values(self) -> None:
        assert ProjectState.PRESENT == "present"
        assert ProjectState.LOCKED == "locked"
        assert ProjectState.ABSENT == "absent"

    def test_from_string(self) -> None:
        assert ProjectState("present") == ProjectState.PRESENT
        assert ProjectState("locked") == ProjectState.LOCKED
        assert ProjectState("absent") == ProjectState.ABSENT

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="is not a valid"):
            ProjectState("invalid")


# ---------------------------------------------------------------------------
# dataclasses.replace() patterns (reconciler mutation)
# ---------------------------------------------------------------------------


class TestDataclassReplace:
    """Verify that dataclasses.replace() works for reconciler mutation patterns."""

    def test_replace_enabled(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)
        locked = dataclasses.replace(cfg, enabled=False)

        assert locked.enabled is False
        assert cfg.enabled is True  # original unchanged

    def test_replace_state(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)
        absent = dataclasses.replace(cfg, state=ProjectState.ABSENT)

        assert absent.state == ProjectState.ABSENT
        assert cfg.state == ProjectState.PRESENT

    def test_replace_group_role_assignments_to_absent(
        self, full_project_dict: dict[str, Any]
    ) -> None:
        """Simulate the reconciler's revoke pattern for absent state."""
        cfg = ProjectConfig.from_dict(full_project_dict)
        revoked = dataclasses.replace(
            cfg,
            group_role_assignments=[
                dataclasses.replace(entry, state="absent")
                for entry in cfg.group_role_assignments
            ],
        )

        assert all(e.state == "absent" for e in revoked.group_role_assignments)
        assert cfg.group_role_assignments[0].state == "present"  # original unchanged

    def test_replace_router_ips(self, full_project_dict: dict[str, Any]) -> None:
        cfg = ProjectConfig.from_dict(full_project_dict)
        new_entry = RouterIpEntry(
            id="r-new", name="new-router", external_ip="10.0.0.200"
        )
        updated = dataclasses.replace(
            cfg,
            router_ips=[new_entry],
        )

        assert updated.router_ips == [new_entry]
        assert len(cfg.router_ips) == 1  # original unchanged


# ---------------------------------------------------------------------------
# Frozen enforcement
# ---------------------------------------------------------------------------


class TestFrozenEnforcement:
    """Verify that frozen dataclasses reject attribute mutation.

    All models use dataclasses.dataclass(frozen=True). This test verifies
    one representative model - testing all models would just verify Python's
    dataclass implementation multiple times.
    """

    def test_project_config_is_frozen(
        self, minimal_project_dict: dict[str, Any]
    ) -> None:
        """Representative test: frozen dataclasses reject attribute mutation."""
        cfg = ProjectConfig.from_dict(minimal_project_dict)

        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Nested from_dict: individual models
# ---------------------------------------------------------------------------


class TestSubnetConfigFromDict:
    """SubnetConfig.from_dict() handles defaults and dhcp alias."""

    def test_full(self) -> None:
        """Verify from_dict extracts ALL SubnetConfig fields correctly."""
        data = {
            "cidr": "10.0.0.0/24",
            "gateway_ip": "10.0.0.1",
            "allocation_pools": [{"start": "10.0.0.2", "end": "10.0.0.254"}],
            "dns_nameservers": ["1.1.1.1"],
            "enable_dhcp": False,
        }
        subnet = SubnetConfig.from_dict(data)
        # Verify ALL 5 fields
        assert subnet.cidr == "10.0.0.0/24"
        assert subnet.gateway_ip == "10.0.0.1"
        assert subnet.allocation_pools == [
            AllocationPool(start="10.0.0.2", end="10.0.0.254")
        ]
        assert subnet.dns_nameservers == ["1.1.1.1"]
        assert subnet.enable_dhcp is False

    def test_defaults(self) -> None:
        """Verify from_dict applies correct defaults for optional fields."""
        data = {
            "cidr": "10.0.0.0/24",
            "gateway_ip": "10.0.0.1",
            "allocation_pools": [],
        }
        subnet = SubnetConfig.from_dict(data)
        # Verify required fields
        assert subnet.cidr == "10.0.0.0/24"
        assert subnet.gateway_ip == "10.0.0.1"
        assert subnet.allocation_pools == []
        # Verify defaults
        assert subnet.dns_nameservers == []
        assert subnet.enable_dhcp is True

    def test_dhcp_alias(self) -> None:
        """The conftest fixture uses 'dhcp' instead of 'enable_dhcp'."""
        data = {
            "cidr": "10.0.0.0/24",
            "gateway_ip": "10.0.0.1",
            "allocation_pools": [],
            "dhcp": False,
        }
        subnet = SubnetConfig.from_dict(data)
        assert subnet.enable_dhcp is False


class TestNetworkConfigFromDict:
    """NetworkConfig.from_dict() with and without MTU."""

    def test_with_mtu(self) -> None:
        """Verify from_dict extracts ALL NetworkConfig fields correctly."""
        data = {
            "mtu": 9000,
            "subnet": {
                "cidr": "10.0.0.0/24",
                "gateway_ip": "10.0.0.1",
                "allocation_pools": [],
            },
        }
        net = NetworkConfig.from_dict(data)
        # Verify both fields
        assert net.mtu == 9000
        assert net.subnet.cidr == "10.0.0.0/24"
        assert net.subnet.gateway_ip == "10.0.0.1"

    def test_default_mtu(self) -> None:
        """Verify from_dict applies correct default for mtu."""
        data = {
            "subnet": {
                "cidr": "10.0.0.0/24",
                "gateway_ip": "10.0.0.1",
                "allocation_pools": [],
            },
        }
        net = NetworkConfig.from_dict(data)
        assert net.mtu == 0  # Default when mtu absent
        assert net.subnet.cidr == "10.0.0.0/24"


class TestQuotaConfigFromDict:
    """QuotaConfig.from_dict() with partial sections."""

    def test_full(self) -> None:
        data = {
            "compute": {"cores": 20},
            "network": {"floating_ips": 5},
            "block_storage": {"gigabytes": 100},
        }
        q = QuotaConfig.from_dict(data)
        assert q.compute == {"cores": 20}
        assert q.network == {"floating_ips": 5}
        assert q.block_storage == {"gigabytes": 100}

    def test_missing_sections_default_to_empty(self) -> None:
        q = QuotaConfig.from_dict({})
        assert q.compute == {}
        assert q.network == {}
        assert q.block_storage == {}

    def test_ram_gibibytes_from_dict(self) -> None:
        """from_dict (lenient) converts ram_gibibytes to ram."""
        q = QuotaConfig.from_dict({"compute": {"ram_gibibytes": 50, "cores": 20}})
        assert q.compute["ram"] == 51_200
        assert "ram_gibibytes" not in q.compute
        assert q.compute["cores"] == 20

    def test_ram_gibibytes_from_dict_with_ram_present(self) -> None:
        """from_dict (lenient): when both set, ram wins."""
        q = QuotaConfig.from_dict({"compute": {"ram": 32768, "ram_gibibytes": 50}})
        assert q.compute["ram"] == 32768


class TestFederationConfigFromDict:
    """FederationConfig.from_dict() with and without role_assignments."""

    def test_full(self) -> None:
        """Verify from_dict extracts ALL FederationConfig fields correctly."""
        data = {
            "issuer": "https://idp.example.com",
            "mapping_id": "my-map",
            "group_prefix": "/custom/prefix/",
            "role_assignments": [
                {"idp_group": "devs", "roles": ["member"]},
            ],
        }
        fed = FederationConfig.from_dict(data)
        # Verify all 4 fields
        assert fed.issuer == "https://idp.example.com"
        assert fed.mapping_id == "my-map"
        assert fed.group_prefix == "/custom/prefix/"
        assert len(fed.role_assignments) == 1
        # Verify nested role assignment (all 2 fields)
        assert fed.role_assignments[0].idp_group == "devs"
        assert fed.role_assignments[0].roles == ["member"]

    def test_empty_defaults(self) -> None:
        """Verify from_dict applies correct defaults for all optional fields."""
        fed = FederationConfig.from_dict({})
        # Verify all 4 fields have correct defaults
        assert fed.issuer == ""
        assert fed.mapping_id == ""
        assert fed.group_prefix == "/services/openstack/"
        assert fed.role_assignments == []


class TestFederationRoleAssignmentWithListGroup:
    """FederationRoleAssignment supports idp_group as list[str]."""

    def test_list_idp_group(self) -> None:
        """Verify from_dict handles idp_group as list and extracts ALL fields."""
        data = {"idp_group": ["group-a", "group-b"], "roles": ["admin"]}
        assignment = FederationRoleAssignment.from_dict(data)
        # Verify both fields
        assert assignment.idp_group == ["group-a", "group-b"]
        assert assignment.roles == ["admin"]


class TestGroupRoleAssignmentFromDict:
    """GroupRoleAssignment.from_dict() with and without state."""

    def test_default_state(self) -> None:
        """Verify from_dict applies default state and extracts ALL fields."""
        data = {"group": "admins", "roles": ["admin"]}
        gra = GroupRoleAssignment.from_dict(data)
        # Verify all 3 fields
        assert gra.group == "admins"
        assert gra.roles == ["admin"]
        assert gra.state == "present"  # Default

    def test_explicit_absent(self) -> None:
        """Verify from_dict handles explicit state and extracts ALL fields."""
        data = {"group": "admins", "roles": ["admin"], "state": "absent"}
        gra = GroupRoleAssignment.from_dict(data)
        # Verify all 3 fields
        assert gra.group == "admins"
        assert gra.roles == ["admin"]
        assert gra.state == "absent"


# ---------------------------------------------------------------------------
# validate() classmethod tests
# ---------------------------------------------------------------------------


class TestSubnetConfigValidate:
    """SubnetConfig.validate() type-checks and enforces business rules."""

    def test_valid_cidr(self) -> None:
        data = {
            "cidr": "10.0.0.0/24",
            "gateway_ip": "10.0.0.1",
            "allocation_pools": [{"start": "10.0.0.2", "end": "10.0.0.254"}],
        }
        errors: list[str] = []
        result = SubnetConfig.validate(data, errors, "test")
        assert result is not None
        assert result.cidr == "10.0.0.0/24"
        assert errors == []

    def test_invalid_cidr(self) -> None:
        data = {"cidr": "not-a-cidr"}
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "invalid CIDR" in errors[0]
        assert "not-a-cidr" in errors[0]  # Error includes the bad value

    def test_missing_cidr(self) -> None:
        data: dict[str, Any] = {"gateway_ip": "10.0.0.1"}
        errors: list[str] = []
        result = SubnetConfig.validate(data, errors, "test")
        assert result is None
        assert len(errors) == 1
        assert "missing required field" in errors[0]
        assert "network.subnet.cidr" in errors[0]  # Error names the missing field

    def test_gateway_outside_cidr(self) -> None:
        data = {"cidr": "192.168.1.0/24", "gateway_ip": "10.0.0.1"}
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "not inside CIDR" in errors[0]
        assert "10.0.0.1" in errors[0]  # Error includes the bad gateway
        assert "192.168.1.0/24" in errors[0]  # Error includes the CIDR

    def test_allocation_pool_start_gt_end(self) -> None:
        data = {
            "cidr": "192.168.1.0/24",
            "allocation_pools": [{"start": "192.168.1.200", "end": "192.168.1.100"}],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "start" in errors[0]
        assert "192.168.1.200" in errors[0]  # Error includes start value
        assert "192.168.1.100" in errors[0]  # Error includes end value

    def test_allocation_pool_outside_cidr(self) -> None:
        data = {
            "cidr": "192.168.1.0/24",
            "allocation_pools": [{"start": "10.0.0.1", "end": "10.0.0.2"}],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        # Should produce 2 errors: start outside CIDR AND end outside CIDR
        assert len(errors) == 2
        assert all("not inside CIDR" in e for e in errors)
        assert any("10.0.0.1" in e for e in errors)
        assert any("10.0.0.2" in e for e in errors)

    def test_allocation_pool_entry_not_a_dict(self) -> None:
        data = {
            "cidr": "10.0.0.0/24",
            "allocation_pools": ["not-a-dict"],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "is not a mapping" in errors[0]
        assert "allocation_pools[0]" in errors[0]  # Error identifies the index

    def test_allocation_pool_missing_start_or_end(self) -> None:
        data = {
            "cidr": "10.0.0.0/24",
            "allocation_pools": [{"start": "10.0.0.2"}],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "missing 'start' or 'end'" in errors[0]
        assert "allocation_pools[0]" in errors[0]

    def test_allocation_pool_invalid_start_ip(self) -> None:
        data = {
            "cidr": "10.0.0.0/24",
            "allocation_pools": [{"start": "bad", "end": "10.0.0.254"}],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "start" in errors[0]
        assert "is invalid" in errors[0]
        assert "bad" in errors[0]  # Error includes the bad value

    def test_allocation_pool_invalid_end_ip(self) -> None:
        data = {
            "cidr": "10.0.0.0/24",
            "allocation_pools": [{"start": "10.0.0.2", "end": "bad"}],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "end" in errors[0]
        assert "is invalid" in errors[0]
        assert "bad" in errors[0]

    def test_allocation_pool_start_outside_cidr(self) -> None:
        data = {
            "cidr": "10.0.0.0/24",
            "allocation_pools": [{"start": "172.16.0.1", "end": "10.0.0.254"}],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        # Two errors: start outside CIDR AND start > end (172.16.0.1 > 10.0.0.254 numerically)
        assert len(errors) == 2
        assert any("start" in e and "not inside CIDR" in e for e in errors)
        assert any("172.16.0.1" in e for e in errors)
        assert any("10.0.0.0/24" in e for e in errors)

    def test_allocation_pool_end_outside_cidr(self) -> None:
        data = {
            "cidr": "10.0.0.0/24",
            "allocation_pools": [{"start": "10.0.0.2", "end": "172.16.0.1"}],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "end" in errors[0]
        assert "not inside CIDR" in errors[0]
        assert "172.16.0.1" in errors[0]
        assert "10.0.0.0/24" in errors[0]


class TestNetworkConfigValidate:
    """NetworkConfig.validate() catches type errors on mtu."""

    def test_mtu_as_string_rejected(self) -> None:
        data = {
            "mtu": "1500",
            "subnet": {
                "cidr": "10.0.0.0/24",
                "gateway_ip": "10.0.0.1",
                "allocation_pools": [],
            },
        }
        errors: list[str] = []
        result = NetworkConfig.validate(data, errors, "test")
        assert result is not None
        assert len(errors) == 1
        assert "mtu must be an integer" in errors[0]
        assert "str" in errors[0]  # Error includes the actual type
        assert result.mtu == 0  # falls back to default

    def test_mtu_as_int_accepted(self) -> None:
        data = {
            "mtu": 9000,
            "subnet": {
                "cidr": "10.0.0.0/24",
                "gateway_ip": "10.0.0.1",
                "allocation_pools": [],
            },
        }
        errors: list[str] = []
        result = NetworkConfig.validate(data, errors, "test")
        assert result is not None
        assert result.mtu == 9000
        assert errors == []

    def test_missing_subnet(self) -> None:
        data: dict[str, Any] = {"mtu": 1500}
        errors: list[str] = []
        result = NetworkConfig.validate(data, errors, "test")
        assert result is None
        assert len(errors) == 1
        assert "missing required field" in errors[0]
        assert "network.subnet.cidr" in errors[0]


class TestQuotaConfigValidate:
    """QuotaConfig.validate() rejects non-int and invalid negative values."""

    def test_negative_value(self) -> None:
        data = {"compute": {"cores": -2}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "-1 (unlimited) or a non-negative integer" in errors[0]
        assert "compute.cores" in errors[0]  # Error identifies the quota key
        assert "-2" in errors[0]  # Error includes the bad value

    def test_minus_one_is_valid(self) -> None:
        data = {"compute": {"cores": -1}}
        errors: list[str] = []
        result = QuotaConfig.validate(data, errors, "test")
        assert errors == []
        assert result.compute == {"cores": -1}

    def test_non_int_value(self) -> None:
        data = {"compute": {"cores": "ten"}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "non-negative integer" in errors[0]
        assert "compute.cores" in errors[0]
        assert "'ten'" in errors[0]  # Error includes the bad value

    def test_valid_quotas(self) -> None:
        data = {"compute": {"cores": 20}, "network": {"floating_ips": 5}}
        errors: list[str] = []
        result = QuotaConfig.validate(data, errors, "test")
        assert result.compute == {"cores": 20}
        assert errors == []

    def test_section_not_a_dict(self) -> None:
        data = {"compute": "not-a-dict"}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "must be a mapping" in errors[0]
        assert "quotas.compute" in errors[0]
        assert "str" in errors[0]  # Error includes the actual type

    def test_ram_with_valid_unit(self) -> None:
        """RAM quota accepts unit strings and converts to MB."""
        data = {"compute": {"ram": "50GB", "cores": 20}}
        errors: list[str] = []
        result = QuotaConfig.validate(data, errors, "test")
        assert errors == []
        assert result.compute["ram"] == 50000  # "50GB" → 50000 MB
        assert result.compute["cores"] == 20

    def test_storage_with_valid_unit(self) -> None:
        """Block storage quotas accept unit strings and convert to GB."""
        data = {"block_storage": {"gigabytes": "2TB", "backup_gigabytes": "500GB"}}
        errors: list[str] = []
        result = QuotaConfig.validate(data, errors, "test")
        assert errors == []
        assert result.block_storage["gigabytes"] == 2000  # "2TB" → 2000 GB
        assert result.block_storage["backup_gigabytes"] == 500  # "500GB" → 500 GB

    def test_invalid_unit_string(self) -> None:
        """Invalid unit string produces validation error."""
        data = {"compute": {"ram": "50XB"}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "unknown unit" in errors[0]
        assert "compute.ram" in errors[0]
        assert "XB" in errors[0]
        # Should suggest valid units
        assert "GB" in errors[0] or "GiB" in errors[0]

    def test_negative_value_with_unit(self) -> None:
        """Negative value with unit produces validation error."""
        data = {"compute": {"ram": "-10GB"}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "cannot use negative values with units" in errors[0]
        assert "compute.ram" in errors[0]
        assert "Use -1 (without units) for unlimited" in errors[0]

    def test_overflow_value(self) -> None:
        """Very large value produces overflow error."""
        data = {"compute": {"ram": "999PB"}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "too large" in errors[0]
        assert "compute.ram" in errors[0]
        assert "2147483647" in errors[0]  # MAX_QUOTA_VALUE

    def test_invalid_format_string(self) -> None:
        """String without valid number+unit format produces error."""
        data = {"compute": {"ram": "fifty gigabytes"}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "invalid format" in errors[0]
        assert "compute.ram" in errors[0]

    def test_non_unit_field_still_validates_int(self) -> None:
        """Non-unit fields (cores, instances) still require integers."""
        data = {"compute": {"cores": "20"}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "non-negative integer" in errors[0]
        assert "compute.cores" in errors[0]

    # -- ram_gibibytes convenience alias --------------------------------

    def test_ram_gibibytes_only(self) -> None:
        """ram_gibibytes: 50 → ram = 51200 MiB (50 * 1024)."""
        data = {"compute": {"ram_gibibytes": 50, "cores": 20}}
        errors: list[str] = []
        result = QuotaConfig.validate(data, errors, "test")
        assert errors == []
        assert result.compute["ram"] == 51_200
        assert "ram_gibibytes" not in result.compute

    def test_ram_gibibytes_zero(self) -> None:
        """ram_gibibytes: 0 → ram = 0."""
        data = {"compute": {"ram_gibibytes": 0}}
        errors: list[str] = []
        result = QuotaConfig.validate(data, errors, "test")
        assert errors == []
        assert result.compute["ram"] == 0

    def test_ram_gibibytes_unlimited(self) -> None:
        """ram_gibibytes: -1 → ram = -1 (unlimited, not multiplied)."""
        data = {"compute": {"ram_gibibytes": -1}}
        errors: list[str] = []
        result = QuotaConfig.validate(data, errors, "test")
        assert errors == []
        assert result.compute["ram"] == -1

    def test_ram_gibibytes_and_ram_agree(self) -> None:
        """Both set to equivalent values → no error (defaults + project merge)."""
        data = {"compute": {"ram": 51_200, "ram_gibibytes": 50}}
        errors: list[str] = []
        result = QuotaConfig.validate(data, errors, "test")
        assert errors == []
        assert result.compute["ram"] == 51_200

    def test_ram_gibibytes_and_ram_conflict(self) -> None:
        """Both set to different values → clear error telling operator to pick one."""
        data = {"compute": {"ram": 50000, "ram_gibibytes": 50}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "compute.ram" in errors[0]
        assert "compute.ram_gibibytes" in errors[0]
        assert "50000 MiB" in errors[0]
        assert "51200 MiB" in errors[0]
        assert "Remove one" in errors[0]

    def test_ram_gibibytes_invalid_string(self) -> None:
        """ram_gibibytes must be a plain integer, not a unit string."""
        data = {"compute": {"ram_gibibytes": "50GiB"}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "ram_gibibytes" in errors[0]
        assert "integer (in gibibytes)" in errors[0]

    def test_ram_gibibytes_negative(self) -> None:
        """ram_gibibytes: -2 is rejected (only -1 or ≥ 0 allowed)."""
        data = {"compute": {"ram_gibibytes": -2}}
        errors: list[str] = []
        QuotaConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "ram_gibibytes" in errors[0]
        assert "-2" in errors[0]


class TestSecurityGroupConfigValidate:
    """SecurityGroupConfig.validate() checks rules structure."""

    def test_rules_not_a_list(self) -> None:
        data = {"name": "default", "rules": "not-a-list"}
        errors: list[str] = []
        SecurityGroupConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "must be a list" in errors[0]
        assert "security_group.rules" in errors[0]
        assert "str" in errors[0]

    def test_rule_not_a_dict(self) -> None:
        data = {"name": "default", "rules": [42]}
        errors: list[str] = []
        SecurityGroupConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "must be a mapping" in errors[0]
        assert "security_group.rules[0]" in errors[0]
        assert "int" in errors[0]

    def test_valid_rules(self) -> None:
        data = {
            "name": "default",
            "rules": [{"direction": "ingress", "protocol": "tcp"}],
        }
        errors: list[str] = []
        result = SecurityGroupConfig.validate(data, errors, "test")
        assert result.name == "default"
        assert errors == []


class TestGroupRoleAssignmentValidate:
    """GroupRoleAssignment.validate() checks group/roles/state."""

    def test_missing_group(self) -> None:
        data: dict[str, Any] = {"roles": ["admin"]}
        errors: list[str] = []
        GroupRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "group must be a non-empty string" in errors[0]
        assert "test.group" in errors[0]

    def test_empty_roles(self) -> None:
        data: dict[str, Any] = {"group": "admins", "roles": []}
        errors: list[str] = []
        GroupRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "roles must be a non-empty list" in errors[0]
        assert "test.roles" in errors[0]

    def test_invalid_state(self) -> None:
        data = {"group": "admins", "roles": ["admin"], "state": "invalid"}
        errors: list[str] = []
        GroupRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "must be 'present' or 'absent'" in errors[0]
        assert "test.state" in errors[0]
        assert "'invalid'" in errors[0]  # Error includes the bad value

    def test_valid_entry(self) -> None:
        data = {"group": "admins", "roles": ["admin"]}
        errors: list[str] = []
        result = GroupRoleAssignment.validate(data, errors, "test")
        assert result is not None
        assert result.group == "admins"
        assert errors == []


class TestFederationRoleAssignmentValidate:
    """FederationRoleAssignment.validate() checks idp_group and roles."""

    def test_valid_string_group(self) -> None:
        data = {"idp_group": "devs", "roles": ["member"]}
        errors: list[str] = []
        result = FederationRoleAssignment.validate(data, errors, "test")
        assert result.idp_group == "devs"
        assert result.roles == ["member"]
        assert errors == []

    def test_valid_list_group(self) -> None:
        data = {"idp_group": ["group-a", "group-b"], "roles": ["admin"]}
        errors: list[str] = []
        result = FederationRoleAssignment.validate(data, errors, "test")
        assert result.idp_group == ["group-a", "group-b"]
        assert errors == []

    def test_empty_string_group(self) -> None:
        data = {"idp_group": "", "roles": ["member"]}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "idp_group must be non-empty" in errors[0]
        assert "test.idp_group" in errors[0]

    def test_empty_list_group(self) -> None:
        data: dict[str, Any] = {"idp_group": [], "roles": ["member"]}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "idp_group list must not be empty" in errors[0]
        assert "test.idp_group" in errors[0]

    def test_list_with_empty_string(self) -> None:
        data = {"idp_group": ["ok", ""], "roles": ["member"]}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "non-empty strings" in errors[0]
        assert "test.idp_group" in errors[0]

    def test_non_string_non_list_group(self) -> None:
        data: dict[str, Any] = {"idp_group": 42, "roles": ["member"]}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "must be a non-empty string or list" in errors[0]
        assert "test.idp_group" in errors[0]
        assert "42" in errors[0]

    def test_missing_idp_group(self) -> None:
        data: dict[str, Any] = {"roles": ["member"]}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "idp_group" in errors[0]
        assert "test.idp_group" in errors[0]

    def test_missing_roles(self) -> None:
        data: dict[str, Any] = {"idp_group": "devs"}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "roles must be a non-empty list" in errors[0]
        assert "test.roles" in errors[0]

    def test_empty_roles_list(self) -> None:
        data: dict[str, Any] = {"idp_group": "devs", "roles": []}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "roles must be a non-empty list" in errors[0]
        assert "test.roles" in errors[0]

    def test_roles_with_empty_string(self) -> None:
        data = {"idp_group": "devs", "roles": ["admin", ""]}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "non-empty strings" in errors[0]
        assert "test.roles" in errors[0]

    def test_roles_not_a_list(self) -> None:
        data = {"idp_group": "devs", "roles": "admin"}
        errors: list[str] = []
        FederationRoleAssignment.validate(data, errors, "test")
        assert len(errors) == 1
        assert "roles must be a non-empty list" in errors[0]
        assert "test.roles" in errors[0]


class TestFederationConfigValidate:
    """FederationConfig.validate() integrates role_assignments validation."""

    def test_valid_config(self) -> None:
        data = {
            "issuer": "https://idp.example.com",
            "mapping_id": "my-map",
            "role_assignments": [
                {"idp_group": "devs", "roles": ["member"]},
            ],
        }
        errors: list[str] = []
        result = FederationConfig.validate(data, errors, "test")
        assert result.issuer == "https://idp.example.com"
        assert len(result.role_assignments) == 1
        assert errors == []

    def test_empty_data_uses_defaults(self) -> None:
        errors: list[str] = []
        result = FederationConfig.validate({}, errors, "test")
        assert result.issuer == ""
        assert result.mapping_id == ""
        assert result.group_prefix == "/services/openstack/"
        assert result.role_assignments == []
        assert errors == []

    def test_non_dict_entry_in_assignments(self) -> None:
        data: dict[str, Any] = {"role_assignments": ["not-a-dict"]}
        errors: list[str] = []
        result = FederationConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "must be a mapping" in errors[0]
        assert "role_assignments[0]" in errors[0]
        assert "str" in errors[0]
        assert result.role_assignments == []

    def test_invalid_entry_collects_errors(self) -> None:
        data: dict[str, Any] = {
            "role_assignments": [
                {"idp_group": "", "roles": ["member"]},
                {"idp_group": "ok", "roles": []},
            ],
        }
        errors: list[str] = []
        result = FederationConfig.validate(data, errors, "test")
        assert len(errors) == 2
        # First error: empty idp_group
        assert "role_assignments[0]" in errors[0]
        assert "idp_group must be non-empty" in errors[0]
        # Second error: empty roles list
        assert "role_assignments[1]" in errors[1]
        assert "roles must be a non-empty list" in errors[1]
        assert len(result.role_assignments) == 2

    def test_role_assignments_not_a_list(self) -> None:
        data: dict[str, Any] = {"role_assignments": "not-a-list"}
        errors: list[str] = []
        result = FederationConfig.validate(data, errors, "test")
        assert result.role_assignments == []
        assert errors == []

    def test_custom_group_prefix(self) -> None:
        data = {"group_prefix": "/custom/path/"}
        errors: list[str] = []
        result = FederationConfig.validate(data, errors, "test")
        assert result.group_prefix == "/custom/path/"


class TestProjectConfigValidate:
    """ProjectConfig.validate() integrates all checks."""

    def _minimal(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "name": "validproject",
            "resource_prefix": "validproject",
            "network": {
                "subnet": {
                    "cidr": "192.168.1.0/24",
                    "gateway_ip": "192.168.1.254",
                    "allocation_pools": [
                        {"start": "192.168.1.1", "end": "192.168.1.253"}
                    ],
                },
            },
        }
        base.update(overrides)
        return base

    def test_valid_project(self) -> None:
        errors: list[str] = []
        result = ProjectConfig.validate(self._minimal(), errors, "test")
        assert result is not None
        assert result.name == "validproject"
        assert errors == []

    def test_missing_name(self) -> None:
        data = self._minimal()
        del data["name"]
        errors: list[str] = []
        result = ProjectConfig.validate(data, errors, "<unknown>")
        assert result is None
        assert len(errors) == 1
        assert "missing required field 'name'" in errors[0]
        assert "<unknown>" in errors[0]

    def test_invalid_name_format(self) -> None:
        errors: list[str] = []
        ProjectConfig.validate(self._minimal(name="123invalid"), errors, "123invalid")
        assert len(errors) == 1
        assert "not a valid OpenStack identifier" in errors[0]
        assert "123invalid" in errors[0]
        assert "^[a-zA-Z][a-zA-Z0-9_ -]{0,63}$" in errors[0]  # Includes pattern

    def test_bad_resource_prefix(self) -> None:
        errors: list[str] = []
        ProjectConfig.validate(
            self._minimal(resource_prefix="BAD-PREFIX"), errors, "test"
        )
        assert len(errors) == 1
        assert "resource_prefix" in errors[0]
        assert "invalid" in errors[0]
        assert "BAD-PREFIX" in errors[0]
        assert "^[a-z0-9]+$" in errors[0]  # Includes pattern

    def test_invalid_state(self) -> None:
        errors: list[str] = []
        ProjectConfig.validate(self._minimal(state="invalid"), errors, "test")
        assert len(errors) == 1
        assert "state must be one of" in errors[0]
        assert "'invalid'" in errors[0]
        assert "['absent', 'locked', 'present']" in errors[0]  # Shows valid states

    def test_absent_skips_network_validation(self) -> None:
        data: dict[str, Any] = {
            "name": "goneproject",
            "resource_prefix": "goneproject",
            "state": "absent",
        }
        errors: list[str] = []
        result = ProjectConfig.validate(data, errors, "goneproject")
        assert result is not None
        assert result.state == "absent"
        assert errors == []

    def test_mtu_string_caught(self) -> None:
        """The motivating bug: mtu: '1500' (string) is caught."""
        data = self._minimal()
        data["network"]["mtu"] = "1500"
        errors: list[str] = []
        ProjectConfig.validate(data, errors, "test")
        assert len(errors) == 1
        assert "mtu must be an integer" in errors[0]
        assert "str" in errors[0]

    def test_reclaim_non_boolean(self) -> None:
        errors: list[str] = []
        ProjectConfig.validate(
            self._minimal(reclaim_floating_ips="yes"), errors, "test"
        )
        assert len(errors) == 1
        assert "must be a boolean" in errors[0]
        assert "reclaim_floating_ips" in errors[0]
        assert "'yes'" in errors[0]  # Error includes the bad value

    def test_reclaim_router_ips_non_boolean(self) -> None:
        errors: list[str] = []
        ProjectConfig.validate(self._minimal(reclaim_router_ips="yes"), errors, "test")
        assert len(errors) == 1
        assert "must be a boolean" in errors[0]
        assert "reclaim_router_ips" in errors[0]
        assert "'yes'" in errors[0]

    def test_track_fip_changes_non_boolean(self) -> None:
        errors: list[str] = []
        ProjectConfig.validate(self._minimal(track_fip_changes="yes"), errors, "test")
        assert len(errors) == 1
        assert "must be a boolean" in errors[0]
        assert "track_fip_changes" in errors[0]
        assert "'yes'" in errors[0]


# ---------------------------------------------------------------------------
# Edge Cases: from_dict with missing optional fields
# ---------------------------------------------------------------------------


class TestProjectConfigFromDictEdgeCases:
    """Edge cases for ProjectConfig.from_dict with various missing fields."""

    def test_all_optional_sections_none(self) -> None:
        """No network, quotas, security_group, or federation sections."""
        data = {"name": "bare", "resource_prefix": "bare"}
        cfg = ProjectConfig.from_dict(data)

        assert cfg.name == "bare"
        assert cfg.resource_prefix == "bare"
        assert cfg.network is None
        assert cfg.quotas is None
        assert cfg.security_group is None
        assert cfg.federation is None

    def test_with_state_key_and_config_path_underscore(self) -> None:
        """_state_key and _config_path are mapped correctly."""
        data = {
            "name": "proj",
            "resource_prefix": "proj",
            "_state_key": "custom-key",
            "_config_path": "/custom/path.yaml",
        }
        cfg = ProjectConfig.from_dict(data)

        assert cfg.state_key == "custom-key"
        assert cfg.config_path == "/custom/path.yaml"

    def test_group_role_assignments_none(self) -> None:
        """group_role_assignments absent or None defaults to empty list."""
        data = {"name": "proj", "resource_prefix": "proj"}
        cfg = ProjectConfig.from_dict(data)
        assert cfg.group_role_assignments == []

    def test_state_lists_empty_by_default(self) -> None:
        """All state lists default to empty when absent."""
        data = {"name": "proj", "resource_prefix": "proj"}
        cfg = ProjectConfig.from_dict(data)

        assert cfg.preallocated_fips == []
        assert cfg.released_fips == []
        assert cfg.router_ips == []
        assert cfg.released_router_ips == []

    def test_external_network_name_defaults_empty(self) -> None:
        """external_network_name defaults to empty string."""
        data = {"name": "proj", "resource_prefix": "proj"}
        cfg = ProjectConfig.from_dict(data)
        assert cfg.external_network_name == ""


class TestSubnetConfigFromDictEdgeCases:
    """Edge cases for SubnetConfig.from_dict with dhcp alias."""

    def test_dhcp_alias_false(self) -> None:
        """'dhcp: false' maps to enable_dhcp=False."""
        data = {
            "cidr": "10.0.0.0/24",
            "gateway_ip": "10.0.0.1",
            "allocation_pools": [],
            "dhcp": False,
        }
        subnet = SubnetConfig.from_dict(data)
        assert subnet.enable_dhcp is False

    def test_enable_dhcp_overrides_dhcp(self) -> None:
        """When both 'enable_dhcp' and 'dhcp' present, 'enable_dhcp' wins."""
        data = {
            "cidr": "10.0.0.0/24",
            "gateway_ip": "10.0.0.1",
            "allocation_pools": [],
            "enable_dhcp": True,
            "dhcp": False,
        }
        subnet = SubnetConfig.from_dict(data)
        assert subnet.enable_dhcp is True


class TestAllocationPoolValidation:
    """Edge case: AllocationPool with start > end caught in validate()."""

    def test_start_greater_than_end(self) -> None:
        """SubnetConfig.validate() catches start > end in allocation pools."""
        data = {
            "cidr": "192.168.1.0/24",
            "gateway_ip": "192.168.1.254",
            "allocation_pools": [{"start": "192.168.1.200", "end": "192.168.1.100"}],
        }
        errors: list[str] = []
        SubnetConfig.validate(data, errors, "test")
        assert any("start" in e and "end" in e for e in errors)


class TestFipEntryFromSdk:
    """Edge cases for FipEntry.from_sdk with various SDK object shapes."""

    def test_from_sdk_with_all_fields(self) -> None:
        """FipEntry.from_sdk extracts ALL 9 fields including port_details."""
        from unittest.mock import MagicMock

        fip = MagicMock()
        fip.id = "fip-123"
        fip.floating_ip_address = "10.0.0.5"
        fip.port_id = "port-abc"
        fip.fixed_ip_address = "192.168.1.10"
        fip.status = "ACTIVE"
        fip.router_id = "router-xyz"
        fip.created_at = "2025-01-01T00:00:00Z"
        fip.port_details = {"device_id": "dev-1", "device_owner": "compute:nova"}

        entry = FipEntry.from_sdk(fip)

        # Verify ALL 9 fields
        assert entry.id == "fip-123"
        assert entry.address == "10.0.0.5"
        assert entry.port_id == "port-abc"
        assert entry.fixed_ip_address == "192.168.1.10"
        assert entry.status == "ACTIVE"
        assert entry.router_id == "router-xyz"
        assert entry.created_at == "2025-01-01T00:00:00Z"
        assert entry.device_id == "dev-1"
        assert entry.device_owner == "compute:nova"

    def test_from_sdk_missing_port_details(self) -> None:
        """FipEntry.from_sdk handles missing port_details gracefully - verifies ALL fields."""
        from unittest.mock import MagicMock

        fip = MagicMock()
        fip.id = "fip-456"
        fip.floating_ip_address = "10.0.0.6"
        fip.port_id = None
        fip.fixed_ip_address = None
        fip.status = "DOWN"
        fip.router_id = None
        fip.created_at = None
        # No port_details attribute at all (getattr returns None)
        del fip.port_details

        entry = FipEntry.from_sdk(fip)

        # Verify ALL 9 fields (including Nones)
        assert entry.id == "fip-456"
        assert entry.address == "10.0.0.6"
        assert entry.port_id is None
        assert entry.fixed_ip_address is None
        assert entry.status == "DOWN"
        assert entry.router_id is None
        assert entry.created_at is None
        assert entry.device_id is None
        assert entry.device_owner is None

    def test_from_sdk_empty_port_details(self) -> None:
        """FipEntry.from_sdk handles empty port_details dict - verifies ALL fields."""
        from unittest.mock import MagicMock

        fip = MagicMock()
        fip.id = "fip-789"
        fip.floating_ip_address = "10.0.0.7"
        fip.port_id = None
        fip.fixed_ip_address = None
        fip.status = "DOWN"
        fip.router_id = None
        fip.created_at = None
        fip.port_details = {}

        entry = FipEntry.from_sdk(fip)

        # Verify ALL 9 fields
        assert entry.id == "fip-789"
        assert entry.address == "10.0.0.7"
        assert entry.port_id is None
        assert entry.fixed_ip_address is None
        assert entry.status == "DOWN"
        assert entry.router_id is None
        assert entry.created_at is None
        assert entry.device_id is None
        assert entry.device_owner is None


class TestSecurityGroupRuleFromDict:
    """Edge cases for SecurityGroupRule with all optional fields missing."""

    def test_all_optional_fields_missing(self) -> None:
        """SecurityGroupRule.from_dict with only direction set - verifies ALL 8 fields."""
        data = {"direction": "ingress"}
        rule = SecurityGroupRule.from_dict(data)

        # Verify ALL 8 fields (1 set, 7 defaults)
        assert rule.direction == "ingress"
        assert rule.protocol is None
        assert rule.port_range_min is None
        assert rule.port_range_max is None
        assert rule.remote_ip_prefix is None
        assert rule.remote_group_id is None
        assert rule.ethertype is None
        assert rule.description == ""

    def test_to_api_dict_excludes_none_and_empty(self) -> None:
        """SecurityGroupRule.to_api_dict excludes None and empty string - verifies behavior."""
        rule = SecurityGroupRule(
            direction="egress",
            protocol="tcp",
            port_range_min=80,
            port_range_max=80,
            remote_ip_prefix=None,
            remote_group_id=None,
            ethertype=None,
            description="",
        )
        api_dict = rule.to_api_dict()

        # Verify only non-None, non-empty fields included
        assert api_dict == {
            "direction": "egress",
            "protocol": "tcp",
            "port_range_min": 80,
            "port_range_max": 80,
        }
        # Verify None and empty excluded
        assert "remote_ip_prefix" not in api_dict
        assert "remote_group_id" not in api_dict
        assert "ethertype" not in api_dict
        assert "description" not in api_dict


class TestRouterIpEntryFromDict:
    """Edge cases for RouterIpEntry.from_dict."""

    def test_missing_required_fields_raises(self) -> None:
        """RouterIpEntry.from_dict raises KeyError when required fields missing."""
        with pytest.raises(KeyError):
            RouterIpEntry.from_dict({"id": "r-1"})

    def test_all_fields_present(self) -> None:
        """RouterIpEntry.from_dict constructs correctly - verifies ALL 3 fields."""
        data = {"id": "r-1", "name": "test-router", "external_ip": "10.0.0.100"}
        entry = RouterIpEntry.from_dict(data)

        # Verify ALL 3 fields
        assert entry.id == "r-1"
        assert entry.name == "test-router"
        assert entry.external_ip == "10.0.0.100"


class TestReleasedFipEntryFromDict:
    """Edge cases for ReleasedFipEntry.from_dict with optional fields."""

    def test_required_fields_only(self) -> None:
        """ReleasedFipEntry.from_dict with only required fields - verifies ALL 6 fields."""
        data = {
            "address": "10.0.0.5",
            "released_at": "2025-01-01",
            "reason": "reclaimed",
        }
        entry = ReleasedFipEntry.from_dict(data)

        # Verify ALL 6 fields (3 required, 3 optional defaulting to None)
        assert entry.address == "10.0.0.5"
        assert entry.released_at == "2025-01-01"
        assert entry.reason == "reclaimed"
        assert entry.port_id is None
        assert entry.device_id is None
        assert entry.device_owner is None

    def test_all_fields_present(self) -> None:
        """ReleasedFipEntry.from_dict with all fields - verifies ALL 6 fields."""
        data = {
            "address": "10.0.0.6",
            "released_at": "2025-01-02",
            "reason": "lost",
            "port_id": "port-123",
            "device_id": "dev-1",
            "device_owner": "compute:nova",
        }
        entry = ReleasedFipEntry.from_dict(data)

        # Verify ALL 6 fields
        assert entry.address == "10.0.0.6"
        assert entry.released_at == "2025-01-02"
        assert entry.reason == "lost"
        assert entry.port_id == "port-123"
        assert entry.device_id == "dev-1"
        assert entry.device_owner == "compute:nova"


class TestReleasedRouterIpEntryFromDict:
    """Edge cases for ReleasedRouterIpEntry.from_dict."""

    def test_all_required_fields(self) -> None:
        """ReleasedRouterIpEntry.from_dict with all required fields - verifies ALL 4 fields."""
        data = {
            "address": "10.0.0.100",
            "router_name": "test-router",
            "released_at": "2025-01-01",
            "reason": "deleted",
        }
        entry = ReleasedRouterIpEntry.from_dict(data)

        # Verify ALL 4 fields
        assert entry.address == "10.0.0.100"
        assert entry.router_name == "test-router"
        assert entry.released_at == "2025-01-01"
        assert entry.reason == "deleted"
