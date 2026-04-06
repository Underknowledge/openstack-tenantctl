"""Integration health check test.

Validates that the full config loading pipeline works end-to-end using
dedicated test fixtures.
"""

from __future__ import annotations

from src.config_loader import load_all_projects


def test_config_loading_integration():
    """Integration test: Load projects from test fixtures and validate structure."""
    # Load all projects using the current API
    config_dir = "tests/fixtures/health-check/"
    projects, _defaults = load_all_projects(config_dir)

    # Verify projects loaded
    assert len(projects) == 1, f"Expected 1 project, got {len(projects)}"

    # Verify test_project exists
    test_proj = next((p for p in projects if p.name == "test_project"), None)
    assert test_proj is not None, "test_project not found in loaded projects"

    # Verify basic project attributes
    assert test_proj.name == "test_project"
    assert test_proj.resource_prefix == "test"
    assert test_proj.domain_id == "default"
    assert test_proj.description == "Test project for health check validation"

    # Verify compute quotas loaded correctly
    assert test_proj.quotas is not None, "Quotas not loaded"
    assert test_proj.quotas.compute is not None, "Compute quotas not loaded"
    assert test_proj.quotas.compute["ram"] == 32768
    assert test_proj.quotas.compute["cores"] == 16
    assert test_proj.quotas.compute["instances"] == 10

    # Verify network quotas loaded correctly
    assert test_proj.quotas.network is not None, "Network quotas not loaded"
    assert test_proj.quotas.network["floating_ips"] == 2

    # Verify block storage quotas loaded correctly
    assert test_proj.quotas.block_storage is not None, "Block storage quotas not loaded"
    assert test_proj.quotas.block_storage["gigabytes"] == 500
    assert test_proj.quotas.block_storage["volumes"] == 20

    # Verify network configuration loaded correctly
    assert test_proj.network is not None, "Network config not loaded"
    assert test_proj.network.subnet is not None, "Subnet config not loaded"
    assert test_proj.network.subnet.cidr == "192.168.100.0/24"
    assert test_proj.network.subnet.gateway_ip == "192.168.100.254"
    assert len(test_proj.network.subnet.allocation_pools) == 1
    assert test_proj.network.subnet.allocation_pools[0].start == "192.168.100.1"
    assert test_proj.network.subnet.allocation_pools[0].end == "192.168.100.253"
