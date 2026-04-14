"""Example: Update quotas for existing projects without touching network/SG.

Demonstrates calling ensure_quotas() directly for incremental updates.
"""

from __future__ import annotations

import openstack

from src import SharedContext, TenantCtl, ensure_quotas, find_existing_project


def update_quotas_only(config_dir: str):
    """Update quotas without touching network or security groups."""
    # Load configs
    client = TenantCtl.from_config_dir(config_dir)
    all_projects, _defaults = client._load_projects()

    # Connect to OpenStack
    conn = openstack.connect(cloud="prod")
    ctx = SharedContext(conn=conn)

    # Update just quotas for existing projects
    for cfg in all_projects:
        if cfg.state == "present" and cfg.quotas:
            project_id, _ = find_existing_project(cfg, ctx)
            if project_id:
                actions = ensure_quotas(cfg, project_id, ctx)
                for action in actions:
                    print(f"{action.status}: {action.resource_type} - {action.details}")
