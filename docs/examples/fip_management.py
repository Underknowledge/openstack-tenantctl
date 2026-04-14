"""Example: Floating IP pre-allocation and drift detection.

Shows how to use ensure_preallocated_fips() for IP management workflows.
"""

from __future__ import annotations

import openstack

from src import (
    ProjectConfig,
    SharedContext,
    ensure_network_stack,
    ensure_preallocated_fips,
    ensure_project,
    find_existing_project,
)


def allocate_fips_with_reclamation(project_name: str, desired_fip_count: int) -> None:
    """Pre-allocate FIPs with address reclamation enabled."""
    cfg = ProjectConfig.build(
        name=project_name,
        state="present",
        domain_id="default",
        quotas={"network": {"floating_ips": desired_fip_count}},
        reclaim_floating_ips=True,  # Enable address reclamation
        track_fip_changes=True,  # Enable drift tracking
    )

    conn = openstack.connect(cloud="prod")
    ctx = SharedContext(conn=conn, dry_run=False)

    # Ensure network stack exists (FIP dependency)
    action, project_id = find_existing_project(cfg, ctx)
    if not project_id:
        action, project_id = ensure_project(cfg, ctx)
        if project_id:
            ensure_network_stack(cfg, project_id, ctx)

    if not project_id:
        raise RuntimeError(f"Failed to find or create project {project_name}")

    # Pre-allocate FIPs
    actions = ensure_preallocated_fips(cfg, project_id, ctx)

    for action in actions:
        print(f"{action.status}: {action.resource_type} - {action.details}")


def detect_fip_drift(project_name: str) -> None:
    """Detect and report FIP drift (untracked or orphaned FIPs)."""
    cfg = ProjectConfig.build(
        name=project_name,
        state="present",
        domain_id="default",
        track_fip_changes=True,
    )

    conn = openstack.connect(cloud="prod")
    ctx = SharedContext(conn=conn, dry_run=True)  # Dry-run mode

    action, project_id = find_existing_project(cfg, ctx)
    if not project_id:
        print(f"Project {project_name} not found")
        return

    # Check for drift
    actions = ensure_preallocated_fips(cfg, project_id, ctx)

    for action in actions:
        if "untracked" in action.details.lower() or "orphaned" in action.details.lower():
            print(f"⚠️ DRIFT: {action.details}")
