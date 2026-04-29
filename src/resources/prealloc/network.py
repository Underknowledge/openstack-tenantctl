"""Pre-allocated network resource lifecycle (network/subnet/router).

When ``quotas.network.networks <= 1`` in config, this module ensures the
project owns the matching network resource.  ``ensure_network_stack``
(called earlier in the NETWORK scope) creates the stack idempotently;
this module is a safety net for the rare case where the stack is missing
when ``PREALLOC_NETWORK`` runs.

This module does NOT write quotas.  All quota writes — including
``networks``, ``subnets``, ``routers`` — are owned by ``ensure_quotas``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models import ProjectConfig

from src.resources.network import ensure_network_stack
from src.utils import (
    Action,
    ActionStatus,
    SharedContext,
    find_network,
)

logger = logging.getLogger(__name__)


def ensure_preallocated_network(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Ensure the project's pre-allocated network resource exists.

    When networks=0: no network needed, skip.
    When networks=1: ensure the network stack exists (fallback to
    ``ensure_network_stack`` if it wasn't created earlier in the pipeline).
    When networks >= 2: skip — handled entirely by the NETWORK scope.

    All quota writes are delegated to ``ensure_quotas``.
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

    if networks_quota >= 2:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                "",
                f"networks quota is {networks_quota}, handled by ensure_quotas",
            )
        ]

    if networks_quota == 0:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                "",
                "networks=0, no network requested",
            )
        ]

    # Offline mode: no connection available.
    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                cfg.name,
                f"networks={networks_quota}, would ensure network exists (offline)",
            )
        ]

    prefix: str = cfg.resource_prefix
    net_name = f"{prefix}-network"
    project_label = f"{cfg.name} ({project_id})"

    # Check whether the network already exists.
    existing = find_network(ctx.conn, net_name, project_id)

    if existing is not None:
        logger.debug(
            "Network %s already exists for project %s",
            net_name,
            project_label,
        )
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                net_name,
                "already exists",
            )
        ]

    # Safety: check if the project already owns ANY network (name may differ
    # from what we'd create — e.g. legacy name from a previous provisioner).
    all_project_nets = list(ctx.conn.network.networks(project_id=project_id))
    if all_project_nets:
        found_names = ", ".join(n.name for n in all_project_nets)
        logger.warning(
            "SAFETY: project %s already has network(s) [%s] but none match "
            "expected name '%s' — leaving existing network(s) in place",
            project_label,
            found_names,
            net_name,
        )
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_network",
                net_name,
                f"project has existing network(s): {found_names}",
            )
        ]

    # Online dry-run: report what would happen.
    if ctx.dry_run:
        return [
            ctx.record(
                ActionStatus.CREATED,
                "preallocated_network",
                net_name,
                f"would create network stack (networks={networks_quota})",
            )
        ]

    # Network does not exist yet — fallback: create via ensure_network_stack
    # (normally already created earlier in the pipeline).
    stack_action = ensure_network_stack(cfg, project_id, ctx)
    logger.info("Network stack creation returned: %s", stack_action.status.value)
    return [stack_action]
