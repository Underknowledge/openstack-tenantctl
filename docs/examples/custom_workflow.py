"""Example: Custom provisioning workflow with individual resource handlers.

Shows how to call TenantCtl resource handlers individually for custom
workflows like phased provisioning, partial updates, or custom orchestration.
"""

from __future__ import annotations

import openstack

from src import (
    ActionStatus,
    SharedContext,
    ensure_baseline_sg,
    ensure_network_stack,
    ensure_project,
    ensure_quotas,
)


def provision_with_approval(configs):
    """Two-phase provisioning: projects first, network after approval."""
    conn = openstack.connect(cloud="mycloud")
    ctx = SharedContext(conn=conn, dry_run=False)

    # Phase 1: Create projects only
    pending_approval = []
    for cfg in configs:
        action, project_id = ensure_project(cfg, ctx)
        if action.status == ActionStatus.CREATED:
            pending_approval.append((cfg, project_id))
            print(f"Created project {cfg.name}, awaiting approval...")

    # Wait for approval (external process)
    approved_ids = get_approved_projects()

    # Phase 2: Provision infrastructure for approved projects
    for cfg, project_id in pending_approval:
        if project_id in approved_ids:
            ensure_network_stack(cfg, project_id, ctx)
            ensure_baseline_sg(cfg, project_id, ctx)
            ensure_quotas(cfg, project_id, ctx)
            print(f"Provisioned {cfg.name}")


def get_approved_projects():
    """Stub for external approval system."""
    return []
