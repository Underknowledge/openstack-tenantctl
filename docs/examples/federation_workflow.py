"""Example: Federation mapping with Keystone group management.

Shows the complete federation workflow with proper ordering.
"""

from __future__ import annotations

import openstack

from src import (
    ProjectConfig,
    SharedContext,
    augment_group_role_assignments,
    ensure_federation_mapping,
    ensure_group_role_assignments,
    ensure_keystone_groups,
    ensure_project,
)


def setup_federated_projects(all_configs: list[ProjectConfig]) -> None:
    """Set up projects with federated access.

    Args:
        all_configs: ALL project configs (not just filtered subset).
                     Required for federation mapping aggregation.
    """
    conn = openstack.connect(cloud="prod")
    ctx = SharedContext(conn=conn, dry_run=False)

    # Phase 1: Create Keystone groups (BEFORE projects)
    print("Creating Keystone groups...")
    group_actions = ensure_keystone_groups(all_configs, ctx)
    for action in group_actions:
        print(f"  {action.status}: {action.name}")

    # Phase 2: Per-project provisioning
    print("\nProvisioning projects...")
    for cfg in all_configs:
        # Augment with federation-derived role assignments
        cfg = augment_group_role_assignments(cfg)

        action, project_id, _was_disabled = ensure_project(cfg, ctx)
        print(f"  {action.status}: {cfg.name} ({project_id})")

        if project_id:
            # Assign roles (includes federation-derived assignments)
            ensure_group_role_assignments(cfg, project_id, ctx)

    # Phase 3: Update federation mapping (AFTER all projects)
    print("\nUpdating federation mapping...")
    mapping_action = ensure_federation_mapping(all_configs, ctx)
    print(f"  {mapping_action.status}: {mapping_action.details}")


def example_group_mode_federation() -> None:
    """Example: Group-mode federation with SAML identity provider."""
    configs = [
        ProjectConfig.build(
            name="ml-research",
            state="present",
            domain_id="default",
            federation=[
                {
                    "idp_group": "ml-team",
                    "role": "member",
                    "mode": "group",
                }
            ],
        ),
        ProjectConfig.build(
            name="web-services",
            state="present",
            domain_id="default",
            federation=[
                {
                    "idp_group": "web-admins",
                    "role": "_member_",
                    "mode": "group",
                },
                {
                    "idp_group": "web-developers",
                    "role": "member",
                    "mode": "group",
                },
            ],
        ),
    ]

    setup_federated_projects(configs)


def example_combined_mode_federation() -> None:
    """Example: Combined mode — both project roles and group membership.

    Use ["project", "group"] when application credentials require direct
    project-scoped roles alongside Keystone group membership.
    """
    configs = [
        ProjectConfig.build(
            name="app-credentials-project",
            state="present",
            domain_id="default",
            federation=[
                {
                    "idp_group": "service-accounts",
                    "role": "member",
                    "mode": ["project", "group"],
                }
            ],
        ),
    ]

    setup_federated_projects(configs)
