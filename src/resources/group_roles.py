"""Project group role assignments for OpenStack projects.

Ensures that Keystone groups have the correct roles assigned on a project.
Supports both granting (state=present) and revoking (state=absent) of
group-role assignments.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openstack.connection import Connection
    from openstack.identity.v3.group import Group
    from openstack.identity.v3.role import Role

    from src.models import ProjectConfig

from openstack.exceptions import NotFoundException

from src.utils import Action, ActionStatus, SharedContext, identity_v3, retry

logger = logging.getLogger(__name__)


@retry()
def _find_group(conn: Connection, name: str) -> Group | None:
    """Resolve a group name to a Keystone group resource."""
    return identity_v3(conn).find_group(name)  # type: ignore[no-any-return]


@retry()
def _find_role(conn: Connection, name: str) -> Role | None:
    """Resolve a role name to a Keystone role resource."""
    return identity_v3(conn).find_role(name)  # type: ignore[no-any-return]


@retry()
def _check_group_role(conn: Connection, project_id: str, group_id: str, role_id: str) -> bool:
    """Return True if the group already has the role on the project.

    The SDK's ``validate_group_has_project_role`` returns ``True`` on
    204 and ``False`` on 404 (without raising).  Some SDK versions may
    raise ``NotFoundException`` instead, so we handle both paths.
    """
    try:
        result = identity_v3(conn).validate_group_has_project_role(project_id, group_id, role_id)
        return bool(result)
    except NotFoundException:
        return False


@retry()
def _assign_group_role(conn: Connection, project_id: str, group_id: str, role_id: str) -> None:
    """Grant a role to a group on a project."""
    identity_v3(conn).assign_project_role_to_group(project_id, group_id, role_id)


@retry()
def _unassign_group_role(conn: Connection, project_id: str, group_id: str, role_id: str) -> None:
    """Revoke a role from a group on a project."""
    identity_v3(conn).unassign_project_role_from_group(project_id, group_id, role_id)


def ensure_group_role_assignments(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Ensure group role assignments match the desired state.

    For each entry in ``group_role_assignments``:
    - ``state: present`` (default): grant any missing roles
    - ``state: absent``: revoke any existing roles

    Returns a list of actions taken, or a single SKIPPED action if
    nothing changed.
    """
    assignments = cfg.group_role_assignments
    if not assignments:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "group_role_assignment",
                "all",
                "no group_role_assignments configured",
            )
        ]

    # Offline mode: no connection available.
    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "group_role_assignment",
                "all",
                f"would process {len(assignments)} group assignment(s) (offline)",
            )
        ]

    # Cache group and role lookups to avoid redundant API calls.
    group_cache: dict[str, str] = {}
    role_cache: dict[str, str] = {}

    actions: list[Action] = []

    for entry in assignments:
        group_name: str = entry.group
        roles: list[str] = entry.roles
        state: str = entry.state

        # Resolve group name → ID (cached).
        if group_name not in group_cache:
            group_obj = _find_group(ctx.conn, group_name)
            if group_obj is None:
                if ctx.dry_run:
                    for role_name in roles:
                        label = f"{group_name}+{role_name}"
                        actions.append(
                            ctx.record(
                                ActionStatus.CREATED,
                                "group_role_assignment",
                                label,
                                f"would grant {role_name} to {group_name} (group pending creation)",
                            )
                        )
                    continue
                msg = f"Group not found: {group_name!r}"
                raise ValueError(msg)
            group_cache[group_name] = group_obj.id

        group_id = group_cache[group_name]

        for role_name in roles:
            # Resolve role name → ID (cached).
            if role_name not in role_cache:
                role_obj = _find_role(ctx.conn, role_name)
                if role_obj is None:
                    msg = f"Role not found: {role_name!r}"
                    raise ValueError(msg)
                role_cache[role_name] = role_obj.id

            role_id = role_cache[role_name]
            label = f"{group_name}+{role_name}"
            has_role = _check_group_role(ctx.conn, project_id, group_id, role_id)

            if state == "present":
                if not has_role:
                    if ctx.dry_run:
                        actions.append(
                            ctx.record(
                                ActionStatus.CREATED,
                                "group_role_assignment",
                                label,
                                f"would grant {role_name} to {group_name}",
                            )
                        )
                    else:
                        _assign_group_role(ctx.conn, project_id, group_id, role_id)
                        logger.info(
                            "Granted role %s to group %s on project %s",
                            role_name,
                            group_name,
                            project_id,
                        )
                        actions.append(
                            ctx.record(
                                ActionStatus.CREATED,
                                "group_role_assignment",
                                label,
                                f"granted {role_name} to {group_name}",
                            )
                        )
            else:  # state == "absent"
                if has_role:
                    if ctx.dry_run:
                        actions.append(
                            ctx.record(
                                ActionStatus.UPDATED,
                                "group_role_assignment",
                                label,
                                f"would revoke {role_name} from {group_name}",
                            )
                        )
                    else:
                        _unassign_group_role(ctx.conn, project_id, group_id, role_id)
                        logger.info(
                            "Revoked role %s from group %s on project %s",
                            role_name,
                            group_name,
                            project_id,
                        )
                        actions.append(
                            ctx.record(
                                ActionStatus.UPDATED,
                                "group_role_assignment",
                                label,
                                f"revoked {role_name} from {group_name}",
                            )
                        )

    if not actions:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "group_role_assignment",
                "all",
                "all assignments already in desired state",
            )
        ]

    return actions
