"""Network stack provisioning for OpenStack projects.

Creates the first network, subnet, and router per project. Idempotent:
if the network already exists the entire stack is skipped — network
changes require manual migration.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openstack.connection import Connection
    from openstack.network.v2.network import Network
    from openstack.network.v2.router import Router
    from openstack.network.v2.subnet import Subnet

    from src.models import ProjectConfig

import openstack.exceptions

from src.models import ReleasedRouterIpEntry, RouterIpEntry
from src.utils import (
    Action,
    ActionStatus,
    DryRunUnsupportedError,
    SharedContext,
    find_network,
    resolve_project_external_network,
    retry,
)

logger = logging.getLogger(__name__)


@retry()
def _create_network(
    conn: Connection,
    net_name: str,
    project_id: str,
    mtu: int,
) -> Network:
    """Create a new Neutron network."""
    kwargs: dict[str, Any] = {
        "name": net_name,
        "project_id": project_id,
    }
    if mtu > 0:
        kwargs["mtu"] = mtu
    return conn.network.create_network(**kwargs)  # type: ignore[no-any-return]


@retry()
def _create_subnet(
    conn: Connection,
    subnet_name: str,
    network_id: str,
    project_id: str,
    cidr: str,
    gateway_ip: str,
    allocation_pools: list[dict[str, str]],
    dns_nameservers: list[str],
    *,
    enable_dhcp: bool,
) -> Subnet:
    """Create a new Neutron subnet on the given network."""
    return conn.network.create_subnet(  # type: ignore[no-any-return]
        name=subnet_name,
        network_id=network_id,
        project_id=project_id,
        cidr=cidr,
        gateway_ip=gateway_ip,
        allocation_pools=allocation_pools,
        dns_nameservers=dns_nameservers,
        enable_dhcp=enable_dhcp,
        ip_version=4,
    )


@retry()
def _create_router(
    conn: Connection,
    router_name: str,
    project_id: str,
    external_net_id: str,
    external_subnet_id: str = "",
    external_fixed_ip: str = "",
) -> Router:
    """Create a new Neutron router, optionally with an external gateway."""
    kwargs: dict[str, Any] = {
        "name": router_name,
        "project_id": project_id,
    }
    if external_net_id:
        gateway_info: dict[str, Any] = {"network_id": external_net_id}

        if external_fixed_ip:
            # Reclaim: request exact IP (optionally on a specific subnet).
            fixed_ip: dict[str, str] = {"ip_address": external_fixed_ip}
            if external_subnet_id:
                fixed_ip["subnet_id"] = external_subnet_id
            gateway_info["external_fixed_ips"] = [fixed_ip]
            logger.debug(
                "Creating router with reclaimed IP %s on network %s",
                external_fixed_ip,
                external_net_id,
            )
        elif external_subnet_id:
            # Normal: just pin to the subnet.
            gateway_info["external_fixed_ips"] = [{"subnet_id": external_subnet_id}]
            logger.debug(
                "Creating router with external gateway on network %s, subnet %s",
                external_net_id,
                external_subnet_id,
            )

        kwargs["external_gateway_info"] = gateway_info
    return conn.network.create_router(**kwargs)  # type: ignore[no-any-return]


@retry()
def _add_interface_to_router(conn: Connection, router_id: str, subnet_id: str) -> None:
    """Attach a subnet to a router as an internal interface."""
    conn.network.add_interface_to_router(router_id, subnet_id=subnet_id)


def _find_previous_router_ip(cfg: ProjectConfig, router_name: str) -> str:
    """Return the last-known external IP for *router_name*, or ``""``."""
    for entry in cfg.router_ips:
        if entry.name == router_name:
            return entry.external_ip
    return ""


def ensure_network_stack(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> Action:
    """Create network, subnet, router for a project. Idempotent -- skips if network exists."""
    prefix: str = cfg.resource_prefix
    net_name = f"{prefix}-network"
    subnet_name = f"{prefix}-subnet"
    router_name = f"{prefix}-router"

    # Offline mode: no connection available.
    if ctx.conn is None:
        return ctx.record(
            ActionStatus.SKIPPED,
            "network_stack",
            net_name,
            f"would create {net_name}, {subnet_name}, {router_name} (offline)",
        )

    # Extract config values.
    if not cfg.network:
        return ctx.record(
            ActionStatus.SKIPPED,
            "network_stack",
            net_name,
            "no network configured",
        )

    mtu: int = cfg.network.mtu if cfg.network.mtu > 0 else 1500
    cidr: str = cfg.network.subnet.cidr
    gateway_ip: str = cfg.network.subnet.gateway_ip
    pool_dicts = [p.to_dict() for p in cfg.network.subnet.allocation_pools]
    dns_nameservers: list[str] = cfg.network.subnet.dns_nameservers
    enable_dhcp: bool = cfg.network.subnet.enable_dhcp

    # Step 1: check if the network already exists (by expected name).
    existing = find_network(ctx.conn, net_name, project_id)
    if existing is not None:
        logger.debug("Network %s already exists, skipping stack creation", net_name)
        return ctx.record(
            ActionStatus.SKIPPED,
            "network_stack",
            net_name,
            "already exists",
        )

    # Safety: check if the project already owns ANY network (name may differ
    # from what we'd create — e.g. legacy name from a previous provisioner).
    all_project_nets = list(ctx.conn.network.networks(project_id=project_id))
    if all_project_nets:
        found_names = ", ".join(n.name for n in all_project_nets)
        logger.warning(
            "SAFETY: project %s already has network(s) [%s] but none match "
            "expected name '%s' — skipping to avoid duplicate",
            project_id,
            found_names,
            net_name,
        )
        return ctx.record(
            ActionStatus.SKIPPED,
            "network_stack",
            net_name,
            f"project has existing network(s): {found_names}",
        )

    # Step 2: Resolve per-project external network/subnet (validate before
    # creating any resources so we fail cleanly without orphaned state).
    project_ext_net_id, project_ext_subnet_id = resolve_project_external_network(cfg, ctx)

    # Check for a previous router IP to reclaim.
    previous_ip = _find_previous_router_ip(cfg, router_name) if cfg.reclaim_router_ips else ""

    # Online dry-run: report what would be created.
    if ctx.dry_run:
        detail = f"would create {net_name} (mtu={mtu}, cidr={cidr}), {subnet_name}, {router_name}"
        if previous_ip:
            detail += f" (reclaim IP {previous_ip})"
        return ctx.record(
            ActionStatus.CREATED,
            "network_stack",
            net_name,
            detail,
        )

    # Step 3: create network.
    network = _create_network(ctx.conn, net_name, project_id, mtu)
    logger.info("Created network %s (%s)", net_name, network.id)

    # Step 4: create subnet.
    subnet = _create_subnet(
        ctx.conn,
        subnet_name,
        network.id,
        project_id,
        cidr,
        gateway_ip,
        pool_dicts,
        dns_nameservers,
        enable_dhcp=enable_dhcp,
    )
    logger.info("Created subnet %s (%s)", subnet_name, subnet.id)

    # Step 5: create router with external gateway (reclaim if possible).
    router: Router | None = None
    if previous_ip:
        try:
            router = _create_router(
                ctx.conn,
                router_name,
                project_id,
                project_ext_net_id,
                project_ext_subnet_id,
                external_fixed_ip=previous_ip,
            )
            logger.info(
                "Reclaimed router %s with IP %s (%s)",
                router_name,
                previous_ip,
                router.id,
            )
        except openstack.exceptions.ConflictException:
            logger.warning(
                "Could not reclaim IP %s for router %s — address taken, " "falling back to normal allocation",
                previous_ip,
                router_name,
            )
            # Record the lost IP in released_router_ips.
            _persist_released_router_ips(
                cfg,
                ctx,
                [
                    *cfg.released_router_ips,
                    ReleasedRouterIpEntry(
                        address=previous_ip,
                        router_name=router_name,
                        released_at=datetime.now(UTC).isoformat(),
                        reason="address taken during reclaim attempt",
                    ),
                ],
            )
        except openstack.exceptions.BadRequestException as exc:
            msg = (
                f"Router IP reclaim failed for {router_name}: "
                f"tried to reclaim {previous_ip} on subnet "
                f"{project_ext_subnet_id or '(auto)'} — {exc}"
            )
            raise openstack.exceptions.BadRequestException(msg) from exc

    if router is None:
        router = _create_router(
            ctx.conn,
            router_name,
            project_id,
            project_ext_net_id,
            project_ext_subnet_id,
        )
    logger.info("Created router %s (%s)", router_name, router.id)

    # Step 6: attach subnet to router.
    _add_interface_to_router(ctx.conn, router.id, subnet.id)
    logger.info("Attached subnet %s to router %s", subnet_name, router_name)

    return ctx.record(
        ActionStatus.CREATED,
        "network_stack",
        net_name,
        f"network={network.id}, subnet={subnet.id}, router={router.id}",
    )


def _get_router_external_ip(router: Router) -> str | None:
    """Extract the external IP from a router's gateway info.

    Returns ``None`` when the router has no external gateway or the
    gateway structure is unexpected (e.g. a ``MagicMock`` in tests).
    """
    gateway_info = router.external_gateway_info
    if not isinstance(gateway_info, dict):
        return None
    fixed_ips = gateway_info.get("external_fixed_ips")
    if not fixed_ips:
        return None
    ip: str | None = fixed_ips[0].get("ip_address")
    return ip


@retry()
def _list_project_routers(conn: Connection, project_id: str) -> list[Router]:
    """List all routers belonging to *project_id*."""
    return list(conn.network.routers(project_id=project_id))


def _persist_router_ips(
    cfg: ProjectConfig,
    ctx: SharedContext,
    router_entries: list[RouterIpEntry],
) -> None:
    """Write the current router IP snapshot to the project state file."""
    if ctx.state_store is None:
        msg = "state_store is None — cannot persist router IPs"
        raise DryRunUnsupportedError(msg)
    ctx.state_store.save(cfg.state_key, ["router_ips"], [e.to_dict() for e in router_entries])
    logger.info(
        "Persisted %d router IP(s) to state for project %s",
        len(router_entries),
        cfg.name,
    )


def _persist_released_router_ips(
    cfg: ProjectConfig,
    ctx: SharedContext,
    released: list[ReleasedRouterIpEntry],
) -> None:
    """Write released router IPs to the project state file."""
    if ctx.state_store is None:
        msg = "state_store is None — cannot persist released router IPs"
        raise DryRunUnsupportedError(msg)
    ctx.state_store.save(cfg.state_key, ["released_router_ips"], [e.to_dict() for e in released])
    logger.info(
        "Persisted %d released router IP(s) to state for project %s",
        len(released),
        cfg.name,
    )


def track_router_ips(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Snapshot all router external IPs and track changes.

    Observes every router in the project, extracts external (SNAT) IPs,
    compares against the previous snapshot stored in ``cfg["router_ips"]``,
    and records new, removed, or changed routers.  Lost IPs are appended
    to ``released_router_ips`` as an audit trail.
    """
    # Offline mode: no connection available.
    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "router_ip",
                cfg.name,
                "skipping router IP tracking (offline)",
            )
        ]

    # Build current snapshot from OpenStack.
    routers = _list_project_routers(ctx.conn, project_id)
    current: list[RouterIpEntry] = []
    for r in routers:
        ip = _get_router_external_ip(r)
        if ip:
            current.append(RouterIpEntry(id=r.id, name=r.name, external_ip=ip))

    # Check for router gateway / configured external network mismatch.
    configured_ext_net_id, _ = resolve_project_external_network(cfg, ctx)
    gateway_mismatch_actions: list[Action] = []
    if configured_ext_net_id:
        for r in routers:
            gw_info = r.external_gateway_info
            if not isinstance(gw_info, dict):
                continue
            gw_net_id = gw_info.get("network_id")
            if gw_net_id and gw_net_id != configured_ext_net_id:
                logger.warning(
                    "Router %s (%s) gateways through network %s, " "but configured external network is %s",
                    r.name,
                    r.id,
                    gw_net_id,
                    configured_ext_net_id,
                )
                gateway_mismatch_actions.append(
                    ctx.record(
                        ActionStatus.FAILED,
                        "router_gateway",
                        r.name,
                        f"router gateways through {gw_net_id}, "
                        f"but configured external network is {configured_ext_net_id}",
                    )
                )

    # Load previous snapshot.
    previous = cfg.router_ips

    prev_by_id = {e.id: e for e in previous}
    curr_by_id = {e.id: e for e in current}

    actions: list[Action] = list(gateway_mismatch_actions)
    new_releases: list[ReleasedRouterIpEntry] = []

    # Adopt routers not previously tracked.
    actions.extend(
        ctx.record(
            ActionStatus.UPDATED,
            "router_ip",
            entry.name,
            f"adopted, external_ip={entry.external_ip}, id={entry.id}",
        )
        for entry in current
        if entry.id not in prev_by_id
    )

    # Detect removed routers.
    for entry in previous:
        if entry.id not in curr_by_id:
            new_releases.append(
                ReleasedRouterIpEntry(
                    address=entry.external_ip,
                    router_name=entry.name,
                    released_at=datetime.now(UTC).isoformat(),
                    reason="router no longer exists",
                )
            )
            actions.append(
                ctx.record(
                    ActionStatus.UPDATED,
                    "router_ip",
                    entry.name,
                    f"router removed, released {entry.external_ip}",
                )
            )

    # Detect IP changes on existing routers.
    for entry in current:
        if entry.id in prev_by_id and prev_by_id[entry.id].external_ip != entry.external_ip:
            old_ip = prev_by_id[entry.id].external_ip
            new_releases.append(
                ReleasedRouterIpEntry(
                    address=old_ip,
                    router_name=entry.name,
                    released_at=datetime.now(UTC).isoformat(),
                    reason=f"IP changed: {old_ip} -> {entry.external_ip}",
                )
            )
            actions.append(
                ctx.record(
                    ActionStatus.UPDATED,
                    "router_ip",
                    entry.name,
                    f"IP changed: {old_ip} -> {entry.external_ip}",
                )
            )

    # In dry-run mode, skip state persistence.
    if ctx.dry_run:
        if not actions:
            actions.append(
                ctx.record(
                    ActionStatus.SKIPPED,
                    "router_ip",
                    cfg.name,
                    "no changes detected",
                )
            )
        return actions

    # Persist if anything changed.
    if current != previous:
        _persist_router_ips(cfg, ctx, current)

    # Append new releases to existing audit trail.
    if new_releases:
        existing_released = cfg.released_router_ips
        all_released = [*existing_released, *new_releases]
        _persist_released_router_ips(cfg, ctx, all_released)

    return actions
