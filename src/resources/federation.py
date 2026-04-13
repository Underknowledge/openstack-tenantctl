"""Federation identity-mapping rule management for OpenStack projects.

Builds a deterministic mapping-rule document from all project configs and
pushes it to Keystone when it differs from the current live state.  Static
admin rules (loaded from ``federation_static.json``) are placed first;
generated per-project rules follow, sorted by project name then IDP group
for stable diffing.

Each rule maps one or more IDP groups to one or more OpenStack roles within a
project.  The ``idp_group`` field can be a single string or a list of strings.
Short names are expanded to ``{group_prefix}{project}/{group}``; paths starting
with ``/`` are used as-is.  When a list is given, all resolved paths are placed
in a single ``any_one_of`` clause.
"""

from __future__ import annotations

import difflib
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openstack.connection import Connection

from src.models import ProjectConfig, ProjectState
from src.utils import Action, ActionStatus, SharedContext, retry

logger = logging.getLogger(__name__)

_DEFAULT_MAPPING_ID = "federated_mapping"
_DEFAULT_GROUP_PREFIX = "/services/openstack/"


def _resolve_group_path(idp_group: str, project_name: str, group_prefix: str) -> str:
    """Expand a short IDP group name to a full path, or return as-is if absolute."""
    if idp_group.startswith("/"):
        return idp_group
    return f"{group_prefix}{project_name}/{idp_group}"


def _build_generated_rules(all_projects: list[ProjectConfig]) -> list[dict[str, Any]]:
    """Create mapping rules for every (project, idp_group) combination.

    Each ``role_assignments`` entry produces one rule.  ``idp_group`` may be
    a single string or a list of strings; when a list is given all resolved
    paths are placed in a single ``any_one_of`` clause.

    Rules are sorted by ``(project_name, first_group_path)`` so the output
    is deterministic across runs.
    """
    rules: list[tuple[str, str, dict[str, Any]]] = []

    for project_cfg in all_projects:
        if project_cfg.state != ProjectState.PRESENT:
            continue
        project_name: str = project_cfg.name
        federation_cfg = project_cfg.federation
        if not federation_cfg:
            continue
        issuer: str = federation_cfg.issuer
        group_prefix: str = federation_cfg.group_prefix
        role_assignments = federation_cfg.role_assignments

        domain_name: str | None = project_cfg.domain
        user_type: str = federation_cfg.user_type

        for assignment in role_assignments:
            raw_group = assignment.idp_group
            roles: list[str] = assignment.roles

            # Normalize to list
            groups: list[str] = [raw_group] if isinstance(raw_group, str) else list(raw_group)
            group_paths = sorted(_resolve_group_path(g, project_name, group_prefix) for g in groups)

            # Build user element (add type only when user_type is explicitly set)
            user_element: dict[str, Any] = {"name": "{0}", "email": "{1}"}
            if user_type:
                user_element["type"] = user_type

            # Build projects element (add domain only when domain is set)
            projects_element: dict[str, Any] = {
                "projects": [
                    {
                        "name": project_name,
                        "roles": [{"name": r} for r in roles],
                    }
                ]
            }
            if domain_name is not None:
                projects_element["domain"] = {"name": domain_name}

            rule: dict[str, Any] = {
                "local": [
                    {"user": user_element},
                    projects_element,
                ],
                "remote": [
                    {"type": "OIDC-preferred_username"},
                    {"type": "OIDC-email"},
                    {"type": "HTTP_OIDC_ISS", "any_one_of": [issuer]},
                    {
                        "type": "OIDC-groups",
                        "any_one_of": group_paths,
                    },
                ],
            }
            # Sort key: use first resolved path (list is already sorted)
            rules.append((project_name, group_paths[0], rule))

    # Sort by project name first, then by first group path for deterministic output.
    rules.sort(key=lambda entry: (entry[0], entry[1]))

    return [entry[2] for entry in rules]


@retry()
def _push_mapping(
    conn: Connection,
    mapping_id: str,
    combined_rules: list[dict[str, Any]],
    *,
    create: bool = False,
) -> None:
    """Create or update the federation mapping in Keystone."""
    if create:
        conn.identity.create_mapping(id=mapping_id, rules=combined_rules)
    else:
        conn.identity.update_mapping(mapping_id, rules=combined_rules)


def ensure_federation_mapping(
    all_projects: list[ProjectConfig],
    ctx: SharedContext,
) -> Action:
    """Build and push the federation mapping from ALL project configs.

    Runs AFTER all per-project reconciliation.

    Static admin rules from ``ctx.static_mapping_rules`` are placed first,
    preserving their original order.  Generated per-project rules follow,
    sorted by ``(project_name, idp_group)`` for deterministic diffing.

    The combined rule set is compared against ``ctx.current_mapping_rules``
    (fetched during Phase 2 setup).  If they match, the action is SKIPPED;
    otherwise the mapping is pushed to Keystone and the action is UPDATED.
    """
    generated_rules = _build_generated_rules(all_projects)

    # Static rules first, then sorted generated rules.
    combined_rules: list[dict[str, Any]] = list(ctx.static_mapping_rules) + generated_rules

    if combined_rules == ctx.current_mapping_rules:
        logger.debug("Federation mapping rules are already up to date")
        return ctx.record(
            ActionStatus.SKIPPED,
            "federation_mapping",
            "mapping",
            f"already up to date, rules={len(combined_rules)}",
        )

    # Online dry-run: report what would change without pushing.
    if ctx.dry_run:
        current_count = len(ctx.current_mapping_rules)
        new_count = len(combined_rules)
        current_json = json.dumps(ctx.current_mapping_rules, indent=2)
        proposed_json = json.dumps(combined_rules, indent=2)
        diff = "\n".join(
            difflib.unified_diff(
                current_json.splitlines(),
                proposed_json.splitlines(),
                fromfile="current",
                tofile="proposed",
                lineterm="",
            )
        )
        logger.info(
            "Dry-run: federation mapping diff (%d → %d rules):\n%s",
            current_count,
            new_count,
            diff,
        )
        return ctx.record(
            ActionStatus.UPDATED,
            "federation_mapping",
            "mapping",
            f"would update ({current_count} → {new_count} rules)",
        )

    # Determine the mapping_id from the first project that defines one.
    mapping_id = _DEFAULT_MAPPING_ID
    for project_cfg in all_projects:
        federation_cfg = project_cfg.federation
        if not federation_cfg:
            continue
        candidate = federation_cfg.mapping_id
        if candidate:
            mapping_id = candidate
            break

    needs_create = not ctx.mapping_exists
    _push_mapping(ctx.conn, mapping_id, combined_rules, create=needs_create)
    verb = "Created" if needs_create else "Updated"
    logger.info(
        "%s federation mapping %s (%d static + %d generated rules)",
        verb,
        mapping_id,
        len(ctx.static_mapping_rules),
        len(generated_rules),
    )

    status = ActionStatus.CREATED if needs_create else ActionStatus.UPDATED
    return ctx.record(
        status,
        "federation_mapping",
        mapping_id,
        f"rules={len(combined_rules)}",
    )
