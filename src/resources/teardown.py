"""Reverse-order teardown for absent project state.

Performs safety checks and deletes project resources in dependency-safe
reverse order.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable

    from openstack.block_storage.v3.snapshot import Snapshot
    from openstack.block_storage.v3.volume import Volume
    from openstack.connection import Connection
    from openstack.network.v2.floating_ip import FloatingIP
    from openstack.network.v2.network import Network
    from openstack.network.v2.port import Port
    from openstack.network.v2.router import Router
    from openstack.network.v2.security_group import SecurityGroup
    from openstack.network.v2.subnet import Subnet

    from src.models import ProjectConfig

from openstack.exceptions import EndpointNotFound, NotFoundException

from src.resources.compute import list_project_servers
from src.utils import (
    Action,
    ActionStatus,
    SharedContext,
    TeardownError,
    retry,
)

logger = logging.getLogger(__name__)


def safety_check(conn: Connection, project_id: str, project_name: str) -> list[str]:
    """Return a list of reasons the project cannot be safely torn down.

    An empty list means **all checks passed** and found no blocking resources.
    Each resource check is individually wrapped so that a failure in one
    (e.g. Cinder unavailable) does not prevent the others from running.

    - ``EndpointNotFound`` → service absent, skip (no resources possible).
    - Any other exception → inconclusive, added as an error (not safe).
    """
    errors: list[str] = []

    # --- Servers (Nova) ---
    try:
        servers = list_project_servers(conn, project_id)
        if servers:
            names = ", ".join(s.name for s in servers)
            errors.append(f"project {project_name!r} has {len(servers)} server(s): {names}")
    except EndpointNotFound:
        logger.debug("Compute service unavailable, skipping server check")
    except Exception:
        logger.exception("Server check inconclusive for %r", project_name)
        errors.append(f"project {project_name!r}: server check inconclusive (API error)")

    # --- Volumes (Cinder) ---
    try:
        volumes = _list_volumes(conn, project_id)
        if volumes:
            names = ", ".join(v.name or v.id for v in volumes)
            errors.append(f"project {project_name!r} has {len(volumes)} volume(s): {names}")
    except EndpointNotFound:
        logger.debug("Block-storage service unavailable, skipping volume check")
    except Exception:
        logger.exception("Volume check inconclusive for %r", project_name)
        errors.append(f"project {project_name!r}: volume check inconclusive (API error)")

    return errors


@retry()
def _list_volumes(conn: Connection, project_id: str) -> list[Volume]:
    """List all volumes in a project."""
    return list(conn.block_storage.volumes(details=True, project_id=project_id))


@retry()
def _list_floating_ips(conn: Connection, project_id: str) -> list[FloatingIP]:
    """List all floating IPs in a project."""
    return list(conn.network.ips(project_id=project_id))


@retry()
def _list_snapshots(conn: Connection, project_id: str) -> list[Snapshot]:
    """List all snapshots in a project."""
    return list(conn.block_storage.snapshots(details=True, project_id=project_id))


@retry()
def _list_routers(conn: Connection, project_id: str) -> list[Router]:
    """List all routers in a project."""
    return list(conn.network.routers(project_id=project_id))


@retry()
def _list_ports(conn: Connection, router_id: str) -> list[Port]:
    """List all ports on a router (device_id filter)."""
    return list(conn.network.ports(device_id=router_id))


@retry()
def _list_subnets(conn: Connection, project_id: str) -> list[Subnet]:
    """List all subnets in a project."""
    return list(conn.network.subnets(project_id=project_id))


@retry()
def _list_networks(conn: Connection, project_id: str) -> list[Network]:
    """List all networks in a project."""
    return list(conn.network.networks(project_id=project_id))


@retry()
def _list_security_groups(conn: Connection, project_id: str) -> list[SecurityGroup]:
    """List all security groups in a project."""
    return list(conn.network.security_groups(project_id=project_id))


@retry()
def _delete_ip(conn: Connection, ip_id: str) -> None:
    conn.network.delete_ip(ip_id)


@retry()
def _delete_snapshot(conn: Connection, snapshot_id: str) -> None:
    conn.block_storage.delete_snapshot(snapshot_id)


@retry()
def _remove_interface(conn: Connection, router_id: str, subnet_id: str) -> None:
    conn.network.remove_interface_from_router(router_id, subnet_id=subnet_id)


@retry()
def _clear_router_gateway(conn: Connection, router_id: str) -> None:
    """Clear the external gateway on a router before deletion."""
    conn.network.update_router(router_id, external_gateway_info=None)


@retry()
def _delete_router(conn: Connection, router_id: str) -> None:
    conn.network.delete_router(router_id)


@retry()
def _delete_subnet(conn: Connection, subnet_id: str) -> None:
    conn.network.delete_subnet(subnet_id)


@retry()
def _delete_network(conn: Connection, network_id: str) -> None:
    conn.network.delete_network(network_id)


@retry()
def _delete_security_group(conn: Connection, sg_id: str) -> None:
    conn.network.delete_security_group(sg_id)


@retry()
def _delete_project(conn: Connection, project_id: str) -> None:
    conn.identity.delete_project(project_id)


# ---------------------------------------------------------------------------
# Generic delete helper & table-driven teardown
# ---------------------------------------------------------------------------


def _safe_delete(
    ctx: SharedContext,
    resource_type: str,
    label: str,
    delete_fn: Callable[[], None],
    actions: list[Action],
    failures: list[str],
    *,
    pre_delete: Callable[[], None] | None = None,
    success_detail: str = "deleted",
) -> None:
    """Delete one resource with standard NotFoundException / Exception handling."""
    try:
        if pre_delete:
            pre_delete()
        delete_fn()
        logger.info("Deleted %s %s", resource_type, label)
        actions.append(ctx.record(ActionStatus.DELETED, resource_type, label, success_detail))
    except NotFoundException:
        logger.debug("%s %s already deleted, skipping", resource_type, label)
        actions.append(ctx.record(ActionStatus.DELETED, resource_type, label, "already gone"))
    except Exception:
        logger.exception("Failed to delete %s %s", resource_type, label)
        actions.append(ctx.record(ActionStatus.FAILED, resource_type, label, "delete failed"))
        failures.append(f"{resource_type}:{label}")


def _prepare_router_deletion(conn: Connection, router: Router) -> None:
    """Detach all interfaces and clear external gateway before router deletion."""
    label = router.name or router.id
    for port in _list_ports(conn, router.id):
        if port.device_owner == "network:router_interface":
            for fixed_ip in port.fixed_ips or []:
                subnet_id = fixed_ip.get("subnet_id")
                if subnet_id:
                    _remove_interface(conn, router.id, subnet_id)
                    logger.info("Detached subnet %s from router %s", subnet_id, label)
    _clear_router_gateway(conn, router.id)


class _TeardownStep(NamedTuple):
    """One resource-type entry in the teardown sequence."""

    resource_type: str
    list_func: Callable[[Connection, str], list[Any]]
    delete_func: Callable[[Connection, str], None]
    label_func: Callable[[Any], str]
    skip_func: Callable[[Any], bool] | None = None
    pre_delete: Callable[[Connection, Any], None] | None = None


_TEARDOWN_STEPS: list[_TeardownStep] = [
    _TeardownStep(
        "floating_ip",
        _list_floating_ips,
        _delete_ip,
        lambda r: r.floating_ip_address or r.id,
    ),
    _TeardownStep(
        "snapshot",
        _list_snapshots,
        _delete_snapshot,
        lambda r: r.name or r.id,
    ),
    _TeardownStep(
        "router",
        _list_routers,
        _delete_router,
        lambda r: r.name or r.id,
        pre_delete=_prepare_router_deletion,
    ),
    _TeardownStep(
        "subnet",
        _list_subnets,
        _delete_subnet,
        lambda r: r.name or r.id,
    ),
    _TeardownStep(
        "network",
        _list_networks,
        _delete_network,
        lambda r: r.name or r.id,
    ),
    _TeardownStep(
        "security_group",
        _list_security_groups,
        _delete_security_group,
        lambda r: r.name or r.id,
        skip_func=lambda r: r.name == "default",
    ),
]


def teardown_project(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Delete all project resources in reverse dependency order.

    Order: floating IPs -> snapshots -> router interfaces -> routers ->
    subnets -> networks -> non-default security groups -> project.

    Each resource deletion is individually error-handled so that a single
    failure does not abort the entire teardown.  ``NotFoundException`` is
    treated as success (resource already gone).  After all resources are
    processed, if any failures occurred a summary ``TeardownError`` is raised.

    Returns a list of actions taken.
    """
    project_name = cfg.name

    # Offline mode: no connection available.
    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "teardown",
                project_name,
                "would tear down project resources (offline)",
            )
        ]

    # Online dry-run: list resources that would be deleted.
    if ctx.dry_run:
        actions: list[Action] = []
        for step in _TEARDOWN_STEPS:
            try:
                resources = step.list_func(ctx.conn, project_id)
            except EndpointNotFound:
                continue
            for resource in resources:
                label = step.label_func(resource)
                if step.skip_func and step.skip_func(resource):
                    continue
                actions.append(
                    ctx.record(
                        ActionStatus.DELETED,
                        step.resource_type,
                        label,
                        "would delete",
                    )
                )
        actions.append(
            ctx.record(
                ActionStatus.DELETED,
                "project",
                project_name,
                f"would delete (id={project_id})",
            )
        )
        return actions

    actions = []
    failures: list[str] = []

    # Delete resources in dependency-safe reverse order
    for step in _TEARDOWN_STEPS:
        try:
            resources = step.list_func(ctx.conn, project_id)
        except EndpointNotFound:
            logger.info("Service unavailable for %s, skipping step", step.resource_type)
            continue

        for resource in resources:
            label = step.label_func(resource)
            if step.skip_func and step.skip_func(resource):
                continue
            pre_del = partial(step.pre_delete, ctx.conn, resource) if step.pre_delete else None
            _safe_delete(
                ctx,
                step.resource_type,
                label,
                partial(step.delete_func, ctx.conn, resource.id),
                actions,
                failures,
                pre_delete=pre_del,
            )

    # Final step: delete the project itself
    _safe_delete(
        ctx,
        "project",
        project_name,
        partial(_delete_project, ctx.conn, project_id),
        actions,
        failures,
        success_detail=f"id={project_id}",
    )

    if failures:
        raise TeardownError(f"Teardown of {project_name!r} had {len(failures)} failure(s): " + ", ".join(failures))

    return actions
