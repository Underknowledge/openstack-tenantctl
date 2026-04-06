from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openstack.connection import Connection
    from openstack.identity.v3.project import Project

    from src.models import ProjectConfig

from src.utils import Action, ActionStatus, SharedContext, retry

logger = logging.getLogger(__name__)


@retry()
def _resolve_domain(conn: Connection, domain_ref: str) -> str:
    """Resolve a domain name or ID to its UUID.

    Uses ``find_domain`` which accepts both names and UUIDs.

    Raises:
        ValueError: If the domain cannot be found.
    """
    domain = conn.identity.find_domain(domain_ref)
    if domain is None:
        msg = f"Could not find domain: {domain_ref}"
        raise ValueError(msg)
    domain_id: str = domain.id
    return domain_id


@retry()
def _find_project(
    conn: Connection,
    name: str,
    domain_id: str,
) -> Project | None:
    """Look up a project by name in the specified domain."""
    return conn.identity.find_project(name, domain_id=domain_id)  # type: ignore[no-any-return]


def find_existing_project(cfg: ProjectConfig, ctx: SharedContext) -> tuple[str | None, str | None]:
    """Look up an existing project by name/domain without creating it.

    Returns ``(project_id, domain_id)`` if found, or ``(None, None)``
    if the project does not exist or no connection is available.
    """
    name: str = cfg.name
    domain_ref: str = cfg.domain_id

    if ctx.conn is None:
        return None, None

    domain_id = _resolve_domain(ctx.conn, domain_ref)
    project = _find_project(ctx.conn, name, domain_id)

    if project is None:
        return None, domain_id

    return project.id, domain_id


def is_project_disabled(cfg: ProjectConfig, ctx: SharedContext) -> bool:
    """Check whether the project exists in OpenStack and is currently disabled.

    Used as a fallback to detect locked->present transitions when no
    state-store metadata is available.  Returns ``False`` if the project
    does not exist or no connection is available.
    """
    if ctx.conn is None:
        return False
    domain_id = _resolve_domain(ctx.conn, cfg.domain_id)
    project = _find_project(ctx.conn, cfg.name, domain_id)
    if project is None:
        return False
    return not project.is_enabled


def ensure_project(cfg: ProjectConfig, ctx: SharedContext) -> tuple[Action, str]:
    """Ensure the project exists with correct settings.

    Returns (Action, project_id).
    """
    name: str = cfg.name
    description: str = cfg.description
    enabled: bool = cfg.enabled
    domain_ref: str = cfg.domain_id

    # Offline mode: no connection available.
    if ctx.conn is None:
        action = ctx.record(
            ActionStatus.SKIPPED,
            "project",
            name,
            "would create/update project (offline)",
        )
        return action, ""

    domain_id = _resolve_domain(ctx.conn, domain_ref)
    project = _find_project(ctx.conn, name, domain_id)

    # Online dry-run: read state, compute diff, but don't write.
    if ctx.dry_run:
        if project is None:
            action = ctx.record(
                ActionStatus.CREATED,
                "project",
                name,
                f"would create (description={description!r}, enabled={enabled})",
            )
            return action, ""
        changes: list[str] = []
        if project.description != description:
            changes.append(f"description: {project.description!r} → {description!r}")
        if project.is_enabled != enabled:
            changes.append(f"enabled: {project.is_enabled} → {enabled}")
        if changes:
            action = ctx.record(
                ActionStatus.UPDATED,
                "project",
                name,
                f"would update ({', '.join(changes)})",
                project_id=project.id,
            )
            return action, project.id
        action = ctx.record(
            ActionStatus.SKIPPED,
            "project",
            name,
            f"up to date (id={project.id})",
            project_id=project.id,
        )
        return action, project.id

    # Normal mode: create or update.
    if project is None:
        project = ctx.conn.identity.create_project(
            name=name,
            domain_id=domain_id,
            description=description,
            is_enabled=enabled,
        )
        logger.info("Created project %s (%s) in domain %s", name, project.id, domain_id)
        action = ctx.record(
            ActionStatus.CREATED,
            "project",
            name,
            f"id={project.id}",
            project_id=project.id,
        )
        return action, project.id

    needs_update = project.description != description or project.is_enabled != enabled

    if needs_update:
        project = ctx.conn.identity.update_project(
            project.id,
            description=description,
            is_enabled=enabled,
        )
        logger.info("Updated project %s (%s)", name, project.id)
        action = ctx.record(
            ActionStatus.UPDATED,
            "project",
            name,
            f"id={project.id}",
            project_id=project.id,
        )
        return action, project.id

    logger.debug("Project %s already up to date", name)
    action = ctx.record(
        ActionStatus.SKIPPED,
        "project",
        name,
        "already up to date",
        project_id=project.id,
    )
    return action, project.id
