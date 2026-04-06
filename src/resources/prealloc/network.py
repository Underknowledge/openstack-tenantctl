"""Network quota enforcement for pre-allocated networks.

When networks <= 1 in config, this module owns the network-related quotas
(``ensure_quotas`` skips them).  The actual network stack is created by
``ensure_network_stack()`` earlier in the reconciler pipeline — this module
only sets quotas to the configured values.

When networks >= 2, this module skips entirely and ``ensure_quotas`` handles
the network quotas through the normal path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openstack.connection import Connection

    from src.models import ProjectConfig

from src.resources.network import ensure_network_stack
from src.utils import (
    Action,
    ActionStatus,
    SharedContext,
    find_network,
    retry,
)

logger = logging.getLogger(__name__)


@retry()
def _set_network_quotas(
    conn: Connection, project_id: str, networks: int, subnets: int, routers: int
) -> None:
    """Set network, subnet, and router quotas to the specified values."""
    conn.network.update_quota(project_id, networks=networks, subnets=subnets, routers=routers)
    logger.info(
        "Set network quotas for project %s: networks=%d, subnets=%d, routers=%d",
        project_id,
        networks,
        subnets,
        routers,
    )


def ensure_preallocated_network(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Enforce network quotas for the pre-allocated case (networks <= 1).

    When networks=0: set quota to 0, no network needed.
    When networks=1: set network quotas from config (network already created
    by ``ensure_network_stack`` earlier in the pipeline).
    When networks >= 2: skip — quotas handled by ``ensure_quotas``.
    """
    net_quotas = cfg.quotas.network if cfg.quotas else None
    if not net_quotas:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                "all",
                "no quotas.network configured",
            )
        ]
    networks_quota: int = net_quotas.get("networks", 1)
    subnets_quota: int = net_quotas.get("subnets", 1)
    routers_quota: int = net_quotas.get("routers", 1)

    if networks_quota >= 2:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                "",
                f"networks quota is {networks_quota}, quotas handled by ensure_quotas",
            )
        ]

    # Offline mode: no connection available.
    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                cfg.name,
                f"networks={networks_quota}, would set quota (offline)",
            )
        ]

    if networks_quota == 0:
        if not ctx.dry_run:
            _set_network_quotas(
                ctx.conn,
                project_id,
                networks=networks_quota,
                subnets=subnets_quota,
                routers=routers_quota,
            )
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                "",
                "networks=0, no network requested — quota set",
            )
        ]

    prefix: str = cfg.resource_prefix
    net_name = f"{prefix}-network"

    # Check whether the network already exists.
    existing = find_network(ctx.conn, net_name, project_id)

    if existing is not None:
        # Network already provisioned -- just set quotas.
        if not ctx.dry_run:
            _set_network_quotas(
                ctx.conn,
                project_id,
                networks=networks_quota,
                subnets=subnets_quota,
                routers=routers_quota,
            )
        logger.debug(
            "Network %s already exists for project %s, quotas set",
            net_name,
            project_id,
        )
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                net_name,
                "already exists, quotas set",
            )
        ]

    # Safety: check if the project already owns ANY network (name may differ
    # from what we'd create — e.g. legacy name from a previous provisioner).
    all_project_nets = list(ctx.conn.network.networks(project_id=project_id))
    if all_project_nets:
        found_names = ", ".join(n.name for n in all_project_nets)
        logger.warning(
            "SAFETY: project %s already has network(s) [%s] but none match "
            "expected name '%s' — setting quotas without creating",
            project_id,
            found_names,
            net_name,
        )
        if not ctx.dry_run:
            _set_network_quotas(
                ctx.conn,
                project_id,
                networks=networks_quota,
                subnets=subnets_quota,
                routers=routers_quota,
            )
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                net_name,
                f"project has existing network(s): {found_names}, quotas set",
            )
        ]

    # Online dry-run: report what would happen.
    if ctx.dry_run:
        return [
            ctx.record(
                ActionStatus.CREATED,
                "preallocated_network",
                net_name,
                f"would create network stack and set quotas "
                f"(networks={networks_quota}, subnets={subnets_quota}, "
                f"routers={routers_quota})",
            )
        ]

    # Network does not exist yet — fallback: create via ensure_network_stack
    # (normally already created earlier in the pipeline).
    stack_action = ensure_network_stack(cfg, project_id, ctx)
    logger.info("Network stack creation returned: %s", stack_action.status.value)

    # Set quotas to the configured values.
    _set_network_quotas(
        ctx.conn,
        project_id,
        networks=networks_quota,
        subnets=subnets_quota,
        routers=routers_quota,
    )

    return [stack_action]
