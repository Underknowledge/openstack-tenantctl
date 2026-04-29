"""Nova server operations for project lifecycle management.

Provides shelve/unshelve operations for project state transitions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openstack.compute.v2.server import Server
    from openstack.connection import Connection

    from src.models import ProjectConfig

from src.utils import Action, ActionStatus, SharedContext, retry

logger = logging.getLogger(__name__)


@retry()
def _list_servers(conn: Connection, project_id: str) -> list[Server]:
    """List all servers in a project."""
    return list(conn.compute.servers(details=True, project_id=project_id))


@retry()
def _shelve_server(conn: Connection, server_id: str) -> None:
    """Shelve a single server."""
    conn.compute.shelve_server(server_id)


@retry()
def _unshelve_server(conn: Connection, server_id: str) -> None:
    """Unshelve a single server."""
    conn.compute.unshelve_server(server_id)


def list_project_servers(conn: Connection, project_id: str) -> list[Server]:
    """List all servers in the given project."""
    result: list[Server] = _list_servers(conn, project_id)
    return result


def shelve_all_servers(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Shelve all ACTIVE servers in the project.

    Servers already in SHELVED or SHELVED_OFFLOADED state are skipped.

    Returns a list of actions taken.
    """
    project_label = f"{cfg.name} ({project_id})"

    # Offline mode: no connection available.
    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "server_shelve",
                "all",
                "would shelve active servers (offline)",
            )
        ]

    servers = _list_servers(ctx.conn, project_id)

    if not servers:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "server_shelve",
                "all",
                "no servers in project",
            )
        ]

    # Online dry-run: report what would happen.
    if ctx.dry_run:
        active = [s for s in servers if s.status == "ACTIVE"]
        if not active:
            return [
                ctx.record(
                    ActionStatus.SKIPPED,
                    "server_shelve",
                    "all",
                    "no active servers to shelve",
                )
            ]
        names = ", ".join(s.name for s in active)
        return [
            ctx.record(
                ActionStatus.UPDATED,
                "server_shelve",
                "all",
                f"would shelve {len(active)} server(s): {names}",
            )
        ]

    actions: list[Action] = []
    for server in servers:
        status = server.status
        name = server.name
        server_id = server.id

        if status == "ACTIVE":
            try:
                _shelve_server(ctx.conn, server_id)
                logger.info(
                    "Shelved server %s (%s) in project %s",
                    name,
                    server_id,
                    project_label,
                )
                actions.append(
                    ctx.record(
                        ActionStatus.UPDATED,
                        "server_shelve",
                        name,
                        f"shelved (was {status})",
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Failed to shelve server %s (%s) in project %s (%s): %s",
                    name,
                    server_id,
                    project_label,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                actions.append(
                    ctx.record(
                        ActionStatus.FAILED,
                        "server_shelve",
                        name,
                        f"shelve failed (was {status})",
                    )
                )
        else:
            logger.debug("Skipping server %s (%s) in state %s", name, server_id, status)

    if not actions:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "server_shelve",
                "all",
                "no active servers to shelve",
            )
        ]

    return actions


def unshelve_all_servers(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Unshelve all SHELVED/SHELVED_OFFLOADED servers in the project.

    Used during locked -> present transition.

    Returns a list of actions taken.
    """
    project_label = f"{cfg.name} ({project_id})"

    # Offline mode: no connection available.
    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "server_unshelve",
                "all",
                "would unshelve shelved servers (offline)",
            )
        ]

    servers = _list_servers(ctx.conn, project_id)

    if not servers:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "server_unshelve",
                "all",
                "no servers in project",
            )
        ]

    shelved_states = {"SHELVED", "SHELVED_OFFLOADED"}

    # Online dry-run: report what would happen.
    if ctx.dry_run:
        shelved = [s for s in servers if s.status in shelved_states]
        if not shelved:
            return [
                ctx.record(
                    ActionStatus.SKIPPED,
                    "server_unshelve",
                    "all",
                    "no shelved servers to unshelve",
                )
            ]
        names = ", ".join(s.name for s in shelved)
        return [
            ctx.record(
                ActionStatus.UPDATED,
                "server_unshelve",
                "all",
                f"would unshelve {len(shelved)} server(s): {names}",
            )
        ]

    actions: list[Action] = []

    for server in servers:
        status = server.status
        name = server.name
        server_id = server.id

        if status in shelved_states:
            try:
                _unshelve_server(ctx.conn, server_id)
                logger.info(
                    "Unshelved server %s (%s) in project %s",
                    name,
                    server_id,
                    project_label,
                )
                actions.append(
                    ctx.record(
                        ActionStatus.UPDATED,
                        "server_unshelve",
                        name,
                        f"unshelved (was {status})",
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Failed to unshelve server %s (%s) in project %s (%s): %s",
                    name,
                    server_id,
                    project_label,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                actions.append(
                    ctx.record(
                        ActionStatus.FAILED,
                        "server_unshelve",
                        name,
                        f"unshelve failed (was {status})",
                    )
                )
        else:
            logger.debug("Skipping server %s (%s) in state %s", name, server_id, status)

    if not actions:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "server_unshelve",
                "all",
                "no shelved servers to unshelve",
            )
        ]

    return actions
