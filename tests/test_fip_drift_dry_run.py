"""Tests for FIP drift reconciliation in dry-run mode.

Verifies that drift detection and reconciliation in dry-run mode:
1. Does NOT modify cloud resources (quotas, FIPs)
2. Does NOT write to state files
3. Returns preview actions showing what would happen
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from src.models import FipEntry, ProjectConfig, QuotaConfig
from src.resources.prealloc.fip import _reconcile_fip_drift
from src.utils import ActionStatus

if TYPE_CHECKING:
    from src.utils import SharedContext


@pytest.fixture
def project_cfg_with_fips(sample_project_cfg: ProjectConfig) -> ProjectConfig:
    """Return a project config with preallocated FIPs configured."""
    # Use dataclasses.replace to update the sample config
    return dataclasses.replace(
        sample_project_cfg,
        quotas=QuotaConfig(
            compute={"cores": 20},
            network={"floating_ips": 3},
            block_storage={},
        ),
        preallocated_fips=[
            FipEntry(id="fip-1", address="10.0.0.1"),
            FipEntry(id="fip-2", address="10.0.0.2"),
        ],
        reclaim_floating_ips=True,
    )


def test_reconcile_drift_untracked_fips_dry_run_no_modifications(
    dry_run_ctx: SharedContext,
    project_cfg_with_fips: ProjectConfig,
) -> None:
    """Drift reconciliation with untracked FIPs in dry-run must not modify cloud or state.

    Scenario: Config has 2 FIPs, cloud has 3 (1 untracked).
    Expected: Returns preview action, no state writes, no cloud modifications.
    """
    config_fips = [
        FipEntry(id="fip-1", address="10.0.0.1"),
        FipEntry(id="fip-2", address="10.0.0.2"),
    ]
    project_cfg_with_fips = dataclasses.replace(
        project_cfg_with_fips, preallocated_fips=config_fips
    )

    openstack_fips = [
        MagicMock(id="fip-1", floating_ip_address="10.0.0.1", port_id=None),
        MagicMock(id="fip-2", floating_ip_address="10.0.0.2", port_id=None),
        MagicMock(id="fip-3", floating_ip_address="10.0.0.3", port_id=None),  # Untracked
    ]

    # Call drift reconciliation
    actions = _reconcile_fip_drift(
        project_cfg_with_fips,
        "proj-123",
        dry_run_ctx,
        config_fips,
        openstack_fips,
        "ext-net-id-123",
        "ext-subnet-123",
    )

    # Verify: returns preview action showing what would happen
    assert len(actions) == 1
    assert actions[0].status == ActionStatus.UPDATED
    assert "would adopt" in actions[0].details
    assert "1 untracked FIP" in actions[0].details

    # Verify: NO cloud modifications
    dry_run_ctx.conn.network.update_quota.assert_not_called()
    dry_run_ctx.conn.network.create_ip.assert_not_called()
    dry_run_ctx.conn.network.delete_ip.assert_not_called()

    # Verify: NO state writes
    dry_run_ctx.state_store.save.assert_not_called()


def test_reconcile_drift_missing_fips_reclaim_dry_run_no_modifications(
    dry_run_ctx: SharedContext,
    project_cfg_with_fips: ProjectConfig,
) -> None:
    """Missing FIPs with reclaim enabled in dry-run shows preview without modifications.

    Scenario: Config has 3 FIPs, cloud only has 2 (1 missing), reclaim enabled.
    Expected: Returns preview showing reclaim intent, no quota changes, no FIP creation.
    """
    config_fips = [
        FipEntry(id="fip-1", address="10.0.0.1"),
        FipEntry(id="fip-2", address="10.0.0.2"),
        FipEntry(id="fip-3", address="10.0.0.3"),
    ]
    project_cfg_with_fips = dataclasses.replace(
        project_cfg_with_fips,
        preallocated_fips=config_fips,
        reclaim_floating_ips=True,
    )

    openstack_fips = [
        MagicMock(id="fip-1", floating_ip_address="10.0.0.1", port_id=None),
        MagicMock(id="fip-2", floating_ip_address="10.0.0.2", port_id=None),
    ]

    actions = _reconcile_fip_drift(
        project_cfg_with_fips,
        "proj-123",
        dry_run_ctx,
        config_fips,
        openstack_fips,
        "ext-net-id-123",
        "ext-subnet-123",
    )

    # Verify: preview shows reclaim intent
    assert len(actions) == 1
    reclaim_action = [a for a in actions if "would reclaim" in a.details]
    assert len(reclaim_action) == 1
    assert "1 missing FIP" in reclaim_action[0].details

    # Verify: NO actual reclamation (no quota changes, no FIP creation)
    dry_run_ctx.conn.network.update_quota.assert_not_called()
    dry_run_ctx.conn.network.create_ip.assert_not_called()

    # Verify: NO state writes
    dry_run_ctx.state_store.save.assert_not_called()


def test_reconcile_drift_missing_fips_no_reclaim_dry_run_no_modifications(
    dry_run_ctx: SharedContext,
    project_cfg_with_fips: ProjectConfig,
) -> None:
    """Missing FIPs with reclaim disabled in dry-run shows release preview.

    Scenario: Config has 3 FIPs, cloud only has 2 (1 missing), reclaim disabled.
    Expected: Returns preview showing release intent, no state writes.
    """
    config_fips = [
        FipEntry(id="fip-1", address="10.0.0.1"),
        FipEntry(id="fip-2", address="10.0.0.2"),
        FipEntry(id="fip-3", address="10.0.0.3"),
    ]
    project_cfg_with_fips = dataclasses.replace(
        project_cfg_with_fips,
        preallocated_fips=config_fips,
        reclaim_floating_ips=False,
    )

    openstack_fips = [
        MagicMock(id="fip-1", floating_ip_address="10.0.0.1", port_id=None),
        MagicMock(id="fip-2", floating_ip_address="10.0.0.2", port_id=None),
    ]

    actions = _reconcile_fip_drift(
        project_cfg_with_fips,
        "proj-123",
        dry_run_ctx,
        config_fips,
        openstack_fips,
        "ext-net-id-123",
        "ext-subnet-123",
    )

    # Verify: preview shows release intent
    assert len(actions) == 1
    release_action = [a for a in actions if "would release" in a.details]
    assert len(release_action) == 1
    assert "1 missing FIP" in release_action[0].details

    # Verify: NO cloud modifications
    dry_run_ctx.conn.network.update_quota.assert_not_called()
    dry_run_ctx.conn.network.create_ip.assert_not_called()

    # Verify: NO state writes
    dry_run_ctx.state_store.save.assert_not_called()


def test_reconcile_drift_combined_untracked_and_missing_dry_run(
    dry_run_ctx: SharedContext,
    project_cfg_with_fips: ProjectConfig,
) -> None:
    """Drift with both untracked and missing FIPs in dry-run shows combined preview.

    Scenario: Config has 2 FIPs, cloud has 2 different FIPs (2 missing, 2 untracked).
    Expected: Returns preview for both adoption and reclaim, no modifications.
    """
    config_fips = [
        FipEntry(id="fip-1", address="10.0.0.1"),
        FipEntry(id="fip-2", address="10.0.0.2"),
    ]
    project_cfg_with_fips = dataclasses.replace(
        project_cfg_with_fips,
        preallocated_fips=config_fips,
        reclaim_floating_ips=True,
    )

    openstack_fips = [
        MagicMock(id="fip-3", floating_ip_address="10.0.0.3", port_id=None),  # Untracked
        MagicMock(id="fip-4", floating_ip_address="10.0.0.4", port_id=None),  # Untracked
    ]

    actions = _reconcile_fip_drift(
        project_cfg_with_fips,
        "proj-123",
        dry_run_ctx,
        config_fips,
        openstack_fips,
        "ext-net-id-123",
        "ext-subnet-123",
    )

    # Verify: returns preview actions for both adoption and reclaim
    assert len(actions) == 2
    adopt_actions = [a for a in actions if "would adopt" in a.details]
    reclaim_actions = [a for a in actions if "would reclaim" in a.details]

    assert len(adopt_actions) == 1
    assert "2 untracked FIP" in adopt_actions[0].details

    assert len(reclaim_actions) == 1
    assert "2 missing FIP" in reclaim_actions[0].details

    # Verify: NO cloud modifications
    dry_run_ctx.conn.network.update_quota.assert_not_called()
    dry_run_ctx.conn.network.create_ip.assert_not_called()

    # Verify: NO state writes
    dry_run_ctx.state_store.save.assert_not_called()


def test_reconcile_drift_no_drift_detected_dry_run(
    dry_run_ctx: SharedContext,
    project_cfg_with_fips: ProjectConfig,
) -> None:
    """No drift detected in dry-run returns empty action list.

    Scenario: Config and cloud are perfectly in sync.
    Expected: Returns empty list, no modifications.
    """
    config_fips = [
        FipEntry(id="fip-1", address="10.0.0.1"),
        FipEntry(id="fip-2", address="10.0.0.2"),
    ]
    project_cfg_with_fips = dataclasses.replace(
        project_cfg_with_fips, preallocated_fips=config_fips
    )

    openstack_fips = [
        MagicMock(id="fip-1", floating_ip_address="10.0.0.1", port_id=None),
        MagicMock(id="fip-2", floating_ip_address="10.0.0.2", port_id=None),
    ]

    actions = _reconcile_fip_drift(
        project_cfg_with_fips,
        "proj-123",
        dry_run_ctx,
        config_fips,
        openstack_fips,
        "ext-net-id-123",
        "ext-subnet-123",
    )

    # Verify: no drift, no actions
    assert len(actions) == 0

    # Verify: NO cloud modifications
    dry_run_ctx.conn.network.update_quota.assert_not_called()
    dry_run_ctx.conn.network.create_ip.assert_not_called()

    # Verify: NO state writes
    dry_run_ctx.state_store.save.assert_not_called()
