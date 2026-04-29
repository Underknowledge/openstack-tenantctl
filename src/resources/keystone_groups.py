"""Keystone group lifecycle for group-based federation mapping.

Creates Keystone groups referenced by group-mode federation rules before
per-project reconciliation.  Groups are deduplicated across projects so
the same group name is created only once.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openstack.connection import Connection
    from openstack.identity.v3.group import Group

from src.models import ProjectConfig, ProjectState
from src.models.federation import _normalize_modes
from src.resources.federation import _derive_group_name
from src.resources.project import _resolve_domain
from src.utils import Action, ActionStatus, SharedContext, identity_v3, retry

logger = logging.getLogger(__name__)


@retry()
def _find_group(conn: Connection, name: str, domain_id: str | None = None) -> Group | None:
    """Look up a Keystone group by name."""
    kwargs: dict[str, str] = {}
    if domain_id:
        kwargs["domain_id"] = domain_id
    return identity_v3(conn).find_group(name, **kwargs)  # type: ignore[no-any-return]


@retry()
def _create_group(conn: Connection, name: str, domain_id: str) -> None:
    """Create a Keystone group."""
    identity_v3(conn).create_group(name=name, domain_id=domain_id)


def ensure_keystone_groups(
    all_projects: list[ProjectConfig],
    ctx: SharedContext,
) -> list[Action]:
    """Create Keystone groups needed by group-mode federation.

    Iterates PRESENT projects, derives group names from each
    ``role_assignment`` entry whose ``mode == "group"``, deduplicates,
    and creates missing groups.  Idempotent: existing groups are skipped.

    Returns a list of actions taken.
    """
    # Collect unique (group_name, domain_id) pairs across all projects.
    needed: dict[str, str] = {}  # group_name -> domain_id
    for cfg in all_projects:
        if cfg.state != ProjectState.PRESENT:
            continue
        if not cfg.federation:
            continue
        domain_id = cfg.domain_id
        for assignment in cfg.federation.role_assignments:
            if "group" not in _normalize_modes(assignment.mode):
                continue
            ks_group = _derive_group_name(cfg.name, assignment, cfg.federation.group_name_separator)
            if ks_group not in needed:
                needed[ks_group] = domain_id

    if not needed:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "keystone_group",
                "all",
                "no group-mode projects",
            )
        ]

    # Offline mode: no connection available.
    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "keystone_group",
                name,
                f"would create group {name!r} (offline)",
            )
            for name in sorted(needed)
        ]

    # Resolve domain names/IDs to UUIDs (cached to avoid redundant lookups).
    domain_cache: dict[str, str] = {}
    for domain_ref in set(needed.values()):
        domain_cache[domain_ref] = _resolve_domain(ctx.conn, domain_ref)

    actions: list[Action] = []
    for name in sorted(needed):
        domain_id = domain_cache[needed[name]]
        existing = _find_group(ctx.conn, name, domain_id=domain_id)
        if existing is not None:
            actions.append(
                ctx.record(
                    ActionStatus.SKIPPED,
                    "keystone_group",
                    name,
                    "already exists",
                )
            )
            continue

        if ctx.dry_run:
            actions.append(
                ctx.record(
                    ActionStatus.CREATED,
                    "keystone_group",
                    name,
                    f"would create group {name!r}",
                )
            )
            continue

        _create_group(ctx.conn, name, domain_id)
        logger.info("Created Keystone group %r (domain_id=%s)", name, domain_id)
        actions.append(
            ctx.record(
                ActionStatus.CREATED,
                "keystone_group",
                name,
                f"created group {name!r}",
            )
        )

    return actions
