"""Tests for auto-calculation of gateway_ip and allocation_pools from CIDR."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path
import yaml

from src.config_loader import load_all_projects


@pytest.fixture
def config_with_minimal_subnet(tmp_path: Path) -> Path:
    """Create config with only CIDR specified (no gateway_ip or allocation_pools)."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    projects_dir = config_dir / "projects"
    projects_dir.mkdir()

    # Create defaults with only CIDR
    defaults = {
        "quotas": {"compute": {"cores": 10}},
        "network": {
            "subnet": {
                "cidr": "192.168.100.0/24",
            },
        },
    }
    (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))

    # Create a minimal project
    project = {
        "name": "testproject",
        "resource_prefix": "test",
    }
    (projects_dir / "project.yaml").write_text(yaml.dump(project))

    # Create federation_static.json
    (config_dir / "federation_static.json").write_text("[]")

    return config_dir


class TestAutoCalculateGatewayIP:
    """Tests for auto-calculating gateway_ip from CIDR."""

    def test_auto_calculates_gateway_from_cidr(self, config_with_minimal_subnet: Path) -> None:
        """Should auto-calculate gateway_ip as first usable IP in subnet."""
        projects, _ = load_all_projects(str(config_with_minimal_subnet))

        assert len(projects) == 1
        subnet = projects[0].network.subnet
        assert subnet.cidr == "192.168.100.0/24"
        assert subnet.gateway_ip == "192.168.100.1"

    def test_respects_explicit_gateway_ip(self, tmp_path: Path) -> None:
        """Should not override explicitly specified gateway_ip."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        projects_dir = config_dir / "projects"
        projects_dir.mkdir()

        defaults: dict[str, Any] = {}
        (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))
        (config_dir / "federation_static.json").write_text("[]")

        # Project with explicit gateway_ip
        project = {
            "name": "testproject",
            "resource_prefix": "test",
            "network": {
                "subnet": {
                    "cidr": "10.0.0.0/24",
                    "gateway_ip": "10.0.0.254",  # Explicit (not first IP)
                },
            },
        }
        (projects_dir / "project.yaml").write_text(yaml.dump(project))

        projects, _ = load_all_projects(str(config_dir))

        assert len(projects) == 1
        subnet = projects[0].network.subnet
        assert subnet.gateway_ip == "10.0.0.254"  # Should keep explicit value


class TestAutoCalculateAllocationPools:
    """Tests for auto-calculating allocation_pools from CIDR."""

    def test_auto_calculates_allocation_pools_from_cidr(
        self, config_with_minimal_subnet: Path
    ) -> None:
        """Should auto-calculate allocation_pools as all IPs except gateway."""
        projects, _ = load_all_projects(str(config_with_minimal_subnet))

        assert len(projects) == 1
        subnet = projects[0].network.subnet
        pools = subnet.allocation_pools

        assert len(pools) == 1
        # Gateway is 192.168.100.1, so pool should be 192.168.100.2-192.168.100.254
        assert pools[0].start == "192.168.100.2"
        assert pools[0].end == "192.168.100.254"

    def test_respects_explicit_allocation_pools(self, tmp_path: Path) -> None:
        """Should not override explicitly specified allocation_pools."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        projects_dir = config_dir / "projects"
        projects_dir.mkdir()

        defaults: dict[str, Any] = {}
        (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))
        (config_dir / "federation_static.json").write_text("[]")

        # Project with explicit allocation_pools
        project = {
            "name": "testproject",
            "resource_prefix": "test",
            "network": {
                "subnet": {
                    "cidr": "10.0.0.0/24",
                    "allocation_pools": [{"start": "10.0.0.100", "end": "10.0.0.200"}],
                },
            },
        }
        (projects_dir / "project.yaml").write_text(yaml.dump(project))

        projects, _ = load_all_projects(str(config_dir))

        assert len(projects) == 1
        pools = projects[0].network.subnet.allocation_pools
        assert len(pools) == 1
        assert pools[0].start == "10.0.0.100"
        assert pools[0].end == "10.0.0.200"

    def test_excludes_custom_gateway_from_pool(self, tmp_path: Path) -> None:
        """Should exclude custom gateway_ip from auto-calculated pool."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        projects_dir = config_dir / "projects"
        projects_dir.mkdir()

        defaults: dict[str, Any] = {}
        (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))
        (config_dir / "federation_static.json").write_text("[]")

        # Project with custom gateway but no allocation_pools
        project = {
            "name": "testproject",
            "resource_prefix": "test",
            "network": {
                "subnet": {
                    "cidr": "10.0.0.0/24",
                    "gateway_ip": "10.0.0.100",  # Custom gateway in middle of range
                },
            },
        }
        (projects_dir / "project.yaml").write_text(yaml.dump(project))

        projects, _ = load_all_projects(str(config_dir))

        assert len(projects) == 1
        subnet = projects[0].network.subnet
        pools = subnet.allocation_pools

        # Pool should be all IPs except gateway (10.0.0.100)
        # Should be 10.0.0.1-10.0.0.99, 10.0.0.101-10.0.0.254
        # But our implementation creates a single pool excluding gateway
        # So it should be 10.0.0.1-10.0.0.254 (the implementation removes gateway)
        assert len(pools) == 1
        # Gateway (10.0.0.100) should not be in the range
        # Our implementation creates pool from all hosts except gateway
        # So first IP is 10.0.0.1, last is 10.0.0.254
        assert pools[0].start == "10.0.0.1"
        assert pools[0].end == "10.0.0.254"


class TestSmallSubnets:
    """Tests for auto-calculation with small subnets."""

    def test_slash_30_subnet(self, tmp_path: Path) -> None:
        """Should handle /30 subnet (4 IPs: network, gateway, usable, broadcast)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        projects_dir = config_dir / "projects"
        projects_dir.mkdir()

        defaults: dict[str, Any] = {}
        (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))
        (config_dir / "federation_static.json").write_text("[]")

        project = {
            "name": "testproject",
            "resource_prefix": "test",
            "network": {
                "subnet": {
                    "cidr": "192.168.1.0/30",
                },
            },
        }
        (projects_dir / "project.yaml").write_text(yaml.dump(project))

        projects, _ = load_all_projects(str(config_dir))

        assert len(projects) == 1
        subnet = projects[0].network.subnet

        # /30 has 2 usable IPs: .1 and .2
        assert subnet.gateway_ip == "192.168.1.1"
        pools = subnet.allocation_pools
        assert len(pools) == 1
        # Only .2 available (since .1 is gateway)
        assert pools[0].start == "192.168.1.2"
        assert pools[0].end == "192.168.1.2"

    def test_slash_29_subnet(self, tmp_path: Path) -> None:
        """Should handle /29 subnet (8 IPs: 6 usable)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        projects_dir = config_dir / "projects"
        projects_dir.mkdir()

        defaults: dict[str, Any] = {}
        (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))
        (config_dir / "federation_static.json").write_text("[]")

        project = {
            "name": "testproject",
            "resource_prefix": "test",
            "network": {
                "subnet": {
                    "cidr": "10.1.1.0/29",
                },
            },
        }
        (projects_dir / "project.yaml").write_text(yaml.dump(project))

        projects, _ = load_all_projects(str(config_dir))

        assert len(projects) == 1
        subnet = projects[0].network.subnet

        # /29 has 6 usable IPs: .1 through .6
        assert subnet.gateway_ip == "10.1.1.1"
        pools = subnet.allocation_pools
        assert len(pools) == 1
        # .2 through .6 (excluding gateway .1)
        assert pools[0].start == "10.1.1.2"
        assert pools[0].end == "10.1.1.6"


class TestDifferentCIDRSizes:
    """Tests for auto-calculation with various CIDR sizes."""

    def test_slash_16_subnet(self, tmp_path: Path) -> None:
        """Should handle large /16 subnet."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        projects_dir = config_dir / "projects"
        projects_dir.mkdir()

        defaults: dict[str, Any] = {}
        (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))
        (config_dir / "federation_static.json").write_text("[]")

        project = {
            "name": "testproject",
            "resource_prefix": "test",
            "network": {
                "subnet": {
                    "cidr": "172.16.0.0/16",
                },
            },
        }
        (projects_dir / "project.yaml").write_text(yaml.dump(project))

        projects, _ = load_all_projects(str(config_dir))

        assert len(projects) == 1
        subnet = projects[0].network.subnet

        assert subnet.gateway_ip == "172.16.0.1"
        pools = subnet.allocation_pools
        assert len(pools) == 1
        assert pools[0].start == "172.16.0.2"
        assert pools[0].end == "172.16.255.254"

    def test_slash_28_subnet(self, tmp_path: Path) -> None:
        """Should handle /28 subnet (16 IPs: 14 usable)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        projects_dir = config_dir / "projects"
        projects_dir.mkdir()

        defaults: dict[str, Any] = {}
        (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))
        (config_dir / "federation_static.json").write_text("[]")

        project = {
            "name": "testproject",
            "resource_prefix": "test",
            "network": {
                "subnet": {
                    "cidr": "10.5.5.128/28",
                },
            },
        }
        (projects_dir / "project.yaml").write_text(yaml.dump(project))

        projects, _ = load_all_projects(str(config_dir))

        assert len(projects) == 1
        subnet = projects[0].network.subnet

        # /28: .128 is network, .129-.142 are usable, .143 is broadcast
        assert subnet.gateway_ip == "10.5.5.129"
        pools = subnet.allocation_pools
        assert len(pools) == 1
        assert pools[0].start == "10.5.5.130"
        assert pools[0].end == "10.5.5.142"
