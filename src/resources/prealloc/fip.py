"""Pre-allocated floating-IP provisioning with quota enforcement.

Pre-allocates the desired number of floating IPs for a project, persists their
addresses and IDs to the project state file, then sets the quota to the
desired count to limit the total (users can still replace FIPs within this
limit, but cannot exceed the count).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openstack.connection import Connection
    from openstack.network.v2.floating_ip import FloatingIP

    from src.models import ProjectConfig

import openstack.exceptions

from src.models import FipEntry, ReleasedFipEntry
from src.utils import (
    Action,
    ActionStatus,
    DryRunUnsupportedError,
    SharedContext,
    resolve_project_external_network,
    retry,
)

logger = logging.getLogger(__name__)


@retry()
def _list_floating_ips(conn: Connection, project_id: str) -> list[FloatingIP]:
    """List all floating IPs currently allocated to *project_id*."""
    return list(conn.network.ips(project_id=project_id))


def _partition_fips_by_network(
    fips: list[FloatingIP],
    external_net_id: str,
) -> tuple[list[FloatingIP], list[FloatingIP]]:
    """Split FIPs into those matching the configured external network and foreign ones.

    Returns:
        ``(matching, foreign)`` where *matching* have ``floating_network_id == external_net_id``
        and *foreign* belong to a different external network.
    """
    matching: list[FloatingIP] = []
    foreign: list[FloatingIP] = []
    for fip in fips:
        if getattr(fip, "floating_network_id", None) == external_net_id:
            matching.append(fip)
        else:
            foreign.append(fip)
    return matching, foreign


@retry()
def _create_floating_ip(
    conn: Connection,
    external_net_id: str,
    project_id: str,
    external_subnet_id: str = "",
) -> FloatingIP:
    """Allocate a single floating IP from the external network."""
    kwargs: dict[str, Any] = {
        "floating_network_id": external_net_id,
        "project_id": project_id,
    }

    if external_subnet_id:
        kwargs["subnet_id"] = external_subnet_id
        logger.debug(
            "Allocating FIP from network %s, subnet %s",
            external_net_id,
            external_subnet_id,
        )

    return conn.network.create_ip(**kwargs)  # type: ignore[no-any-return]


@retry()
def _delete_floating_ip(conn: Connection, fip_id: str) -> None:
    """Delete a single floating IP by ID."""
    conn.network.delete_ip(fip_id)


@retry()
def _raise_fip_quota(conn: Connection, project_id: str, count: int) -> None:
    """Temporarily raise floating IP quota to *count* to allow allocation."""
    conn.network.update_quota(project_id, floating_ips=count)


@retry()
def _set_fip_quota(
    conn: Connection, project_id: str, desired_count: int
) -> tuple[bool, int | None]:
    """Set floating IP quota to the desired count.

    If the quota cannot be set below current usage (partial scale-down with
    in-use FIPs), falls back to setting quota to current usage and returns
    a flag indicating the failure.

    Returns:
        (success, fallback_quota) where success=False means quota was set to
        current usage instead of desired count. fallback_quota is the usage
        value if we had to fall back, None otherwise.
    """
    try:
        conn.network.update_quota(project_id, floating_ips=desired_count)
        logger.info(
            "Set floating_ips quota to %d for project %s",
            desired_count,
            project_id,
        )
        return (True, None)
    except openstack.exceptions.BadRequestException:
        # Neutron rejects quota < usage. Read usage and set quota to that.
        logger.warning(
            "Cannot set floating_ips quota to %d for project %s - "
            "quota cannot be less than current usage, reading usage and setting to that",
            desired_count,
            project_id,
        )
        quota = conn.network.get_quota(project_id, details=True)
        detail = getattr(quota, "floating_ips", None)
        used = detail.get("used", 0) if isinstance(detail, dict) else 0

        conn.network.update_quota(project_id, floating_ips=used)
        logger.warning(
            "Fallback: set floating_ips quota to %d (current usage) for project %s - "
            "project owner must free up %d FIP(s) before quota can be reduced to %d",
            used,
            project_id,
            used - desired_count,
            desired_count,
        )
        return (False, used)


def _set_fip_quota_and_record(
    conn: Connection,
    project_id: str,
    ctx: SharedContext,
    desired_count: int,
    actual_count: int,
) -> Action | None:
    """Set FIP quota and record a FAILED action if quota exceeds desired.

    Computes ``effective_quota = max(desired_count, actual_count)`` so we
    never ask Neutron to set quota below current usage (avoiding a
    pointless ``BadRequestException`` round-trip).

    Returns:
        A FAILED ``Action`` if quota had to be set above desired, or
        ``None`` on success at the desired count.
    """
    effective_quota = max(desired_count, actual_count)
    success, fallback_quota = _set_fip_quota(conn, project_id, effective_quota)

    if not success:
        # Race condition: even max(desired, actual) was rejected.
        return ctx.record(
            ActionStatus.FAILED,
            "preallocated_fip_quota",
            "",
            f"quota set to {fallback_quota} (current usage) instead of "
            f"desired {desired_count} - project owner must free "
            f"{fallback_quota - desired_count} FIP(s) before quota can be reduced",
        )

    if effective_quota > desired_count:
        return ctx.record(
            ActionStatus.FAILED,
            "preallocated_fip_quota",
            "",
            f"quota set to {effective_quota} (in-use FIPs) instead of "
            f"desired {desired_count} - project owner must free "
            f"{effective_quota - desired_count} FIP(s) before quota can be reduced",
        )

    return None


def _persist_fips(
    cfg: ProjectConfig,
    ctx: SharedContext,
    fips: list[FipEntry],
) -> None:
    """Write the current FIP list to the project state file."""
    if ctx.state_store is None:
        msg = "state_store is None — cannot persist FIPs"
        raise DryRunUnsupportedError(msg)
    ctx.state_store.save(cfg.state_key, ["preallocated_fips"], [f.to_dict() for f in fips])
    logger.info("Persisted %d FIP(s) to state for project %s", len(fips), cfg.name)


def _persist_fip_tracking_snapshot(
    cfg: ProjectConfig,
    ctx: SharedContext,
    quota: int,
    allocated: int,
) -> None:
    """Write FIP tracking metadata snapshot to the project state file."""
    if ctx.state_store is None:
        msg = "state_store is None — cannot persist FIP tracking snapshot"
        raise DryRunUnsupportedError(msg)
    ctx.state_store.save(
        cfg.state_key,
        ["fip_tracking_snapshot"],
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "quota": quota,
            "allocated": allocated,
        },
    )
    logger.info(
        "Persisted FIP tracking snapshot for project %s (quota=%d, allocated=%d)",
        cfg.name,
        quota,
        allocated,
    )


def _detect_fip_drift(
    config_fips: list[FipEntry],
    openstack_fips: list[FloatingIP],
) -> tuple[list[FipEntry], list[FloatingIP]]:
    """Compare persisted FIP list against actual OpenStack state.

    Returns:
        ``(missing, untracked)`` where *missing* are config entries whose
        OpenStack ID no longer exists, and *untracked* are OpenStack FIPs
        not recorded in the config.
    """
    config_ids = {f.id for f in config_fips}
    openstack_ids = {f.id for f in openstack_fips}

    missing = [f for f in config_fips if f.id not in openstack_ids]
    untracked = [f for f in openstack_fips if f.id not in config_ids]
    return missing, untracked


@retry()
def _reclaim_floating_ip(
    conn: Connection,
    external_net_id: str,
    project_id: str,
    address: str,
    external_subnet_id: str = "",
) -> FloatingIP:
    """Re-allocate a floating IP with a specific address.

    A 409 ConflictException (address already taken) is *not* retryable
    and will propagate immediately.  Transient errors are retried normally.
    """
    kwargs: dict[str, Any] = {
        "floating_network_id": external_net_id,
        "project_id": project_id,
        "floating_ip_address": address,
    }

    if external_subnet_id:
        kwargs["subnet_id"] = external_subnet_id

    return conn.network.create_ip(**kwargs)  # type: ignore[no-any-return]


def _persist_released_fips(
    cfg: ProjectConfig,
    ctx: SharedContext,
    released: list[ReleasedFipEntry],
) -> None:
    """Write released FIPs to the project state file."""
    if ctx.state_store is None:
        msg = "state_store is None — cannot persist released FIPs"
        raise DryRunUnsupportedError(msg)
    ctx.state_store.save(cfg.state_key, ["released_fips"], [r.to_dict() for r in released])
    logger.info(
        "Persisted %d released FIP(s) to state for project %s",
        len(released),
        cfg.name,
    )


def _reconcile_fip_drift(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
    config_fips: list[FipEntry],
    openstack_fips: list[FloatingIP],
    external_net_id: str,
    external_subnet_id: str,
) -> list[Action]:
    """Detect and reconcile drift between config and OpenStack FIP state.

    1. Adopt untracked FIPs (in OpenStack but not in config).
    2. Reclaim missing FIPs (in config but deleted from OpenStack).
    3. Record permanently lost FIPs in ``released_fips``.
    """
    missing, untracked = _detect_fip_drift(config_fips, openstack_fips)
    actions: list[Action] = []

    # Online dry-run: report drift without modifying cloud or state.
    if ctx.dry_run:
        if untracked:
            actions.append(
                ctx.record(
                    ActionStatus.UPDATED,
                    "preallocated_fip",
                    cfg.name,
                    f"would adopt {len(untracked)} untracked FIP(s)",
                )
            )
        if missing:
            reclaim_enabled = cfg.reclaim_floating_ips
            if reclaim_enabled:
                actions.append(
                    ctx.record(
                        ActionStatus.UPDATED,
                        "preallocated_fip",
                        cfg.name,
                        f"would reclaim {len(missing)} missing FIP(s)",
                    )
                )
            else:
                actions.append(
                    ctx.record(
                        ActionStatus.UPDATED,
                        "preallocated_fip",
                        cfg.name,
                        f"would release {len(missing)} missing FIP(s)",
                    )
                )
        return actions

    # Adopt untracked FIPs into the config.
    adopted: list[FipEntry] = []
    for fip in untracked:
        adopted.append(FipEntry.from_sdk(fip))
        actions.append(
            ctx.record(
                ActionStatus.UPDATED,
                "preallocated_fip",
                fip.floating_ip_address,
                f"adopted untracked FIP, id={fip.id}",
            )
        )

    if adopted:
        # Merge adopted FIPs into config and persist immediately.
        updated_config_fips = [*config_fips, *adopted]
        _persist_fips(cfg, ctx, updated_config_fips)

    if not missing:
        return actions

    logger.warning(
        "FIP drift detected for project %s: %d FIP(s) missing from OpenStack",
        project_id,
        len(missing),
    )

    reclaim_enabled = cfg.reclaim_floating_ips
    reclaimed: list[FipEntry] = []
    newly_released: list[ReleasedFipEntry] = []

    if reclaim_enabled:
        # Raise quota to allow reclamation.
        desired_count: int = cfg.quotas.network.get("floating_ips", 0) if cfg.quotas else 0
        _raise_fip_quota(ctx.conn, project_id, desired_count + len(missing))

        for entry in missing:
            try:
                fip = _reclaim_floating_ip(
                    ctx.conn,
                    external_net_id,
                    project_id,
                    entry.address,
                    external_subnet_id,
                )
                reclaimed.append(FipEntry.from_sdk(fip))
                actions.append(
                    ctx.record(
                        ActionStatus.UPDATED,
                        "preallocated_fip",
                        entry.address,
                        f"reclaimed with new id={fip.id}",
                    )
                )
                logger.info(
                    "Reclaimed FIP %s (new id=%s) for project %s",
                    entry.address,
                    fip.id,
                    project_id,
                )
            except openstack.exceptions.ConflictException:
                newly_released.append(
                    ReleasedFipEntry(
                        address=entry.address,
                        released_at=datetime.now(UTC).isoformat(),
                        reason="address taken by another project",
                        port_id=entry.port_id,
                        device_id=entry.device_id,
                        device_owner=entry.device_owner,
                    )
                )
                actions.append(
                    ctx.record(
                        ActionStatus.FAILED,
                        "preallocated_fip",
                        entry.address,
                        "address taken by another project, moved to released_fips",
                    )
                )
                logger.warning(
                    "Cannot reclaim FIP %s for project %s — address taken",
                    entry.address,
                    project_id,
                )
    else:
        # Reclamation disabled — release all missing FIPs without attempting
        # to re-allocate the same address.  Normal scale-up will allocate
        # new (different) FIPs if the count is still below desired.
        for entry in missing:
            newly_released.append(
                ReleasedFipEntry(
                    address=entry.address,
                    released_at=datetime.now(UTC).isoformat(),
                    reason="FIP deleted externally",
                    port_id=entry.port_id,
                    device_id=entry.device_id,
                    device_owner=entry.device_owner,
                )
            )
            actions.append(
                ctx.record(
                    ActionStatus.UPDATED,
                    "preallocated_fip",
                    entry.address,
                    "FIP deleted externally, moved to released_fips",
                )
            )
            logger.info(
                "FIP %s missing for project %s — recorded as released "
                "(reclaim_floating_ips disabled)",
                entry.address,
                project_id,
            )

    # Update preallocated_fips: remove missing, add reclaimed (and adopted).
    missing_ids = {f.id for f in missing}
    surviving = [f for f in config_fips if f.id not in missing_ids]
    updated_locked = [*surviving, *reclaimed, *adopted]
    _persist_fips(cfg, ctx, updated_locked)

    # Persist released FIPs (merge with any existing).
    if newly_released:
        existing_released = cfg.released_fips
        all_released = [*existing_released, *newly_released]
        _persist_released_fips(cfg, ctx, all_released)

    return actions


def _scale_down_fips(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
    existing_fips: list[FloatingIP],
    existing_count: int,
    desired_count: int,
) -> list[Action]:
    """Release excess unused FIPs to reach *desired_count*.

    Unused FIPs (``port_id is None``) are deleted first.  If in-use FIPs
    prevent reaching the desired count, a FAILED action is recorded.
    """
    excess = existing_count - desired_count
    unused = [f for f in existing_fips if f.port_id is None]

    # Delete up to *excess* unused FIPs.
    to_delete = unused[:excess]
    actions: list[Action] = []

    for fip in to_delete:
        _delete_floating_ip(ctx.conn, fip.id)
        logger.info(
            "Released unused FIP %s (%s) for project %s",
            fip.floating_ip_address,
            fip.id,
            project_id,
        )
        actions.append(
            ctx.record(
                ActionStatus.UPDATED,
                "preallocated_fip",
                fip.floating_ip_address,
                f"released (unused), id={fip.id}",
            )
        )

    deleted_count = len(to_delete)
    remaining_excess = excess - deleted_count

    if remaining_excess > 0:
        logger.warning(
            "Cannot release %d in-use FIP(s) for project %s — "
            "%d excess FIP(s) remain above desired count %d",
            remaining_excess,
            project_id,
            remaining_excess,
            desired_count,
        )
        actions.append(
            ctx.record(
                ActionStatus.FAILED,
                "preallocated_fip",
                "",
                f"cannot release {remaining_excess} in-use FIP(s), "
                f"wanted {desired_count} but {existing_count - deleted_count} remain",
            )
        )

    # Persist remaining FIPs to state file.
    deleted_ids = {f.id for f in to_delete}
    remaining_fips = [f for f in existing_fips if f.id not in deleted_ids]
    _persist_fips(cfg, ctx, [FipEntry.from_sdk(f) for f in remaining_fips])

    # Set quota to max(desired, remaining) — avoids a rejected API call.
    remaining_count = len(remaining_fips)
    quota_action = _set_fip_quota_and_record(
        ctx.conn,  # type: ignore[arg-type]
        project_id,
        ctx,
        desired_count,
        remaining_count,
    )
    if quota_action:
        actions.append(quota_action)

    return actions


def _scale_up_fips(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
    existing_fips: list[FloatingIP],
    existing_count: int,
    desired_count: int,
    external_net_id: str,
    external_subnet_id: str,
) -> list[Action]:
    """Allocate missing FIPs to reach *desired_count*.

    Raises quota → allocates → persists → sets quota via helper.
    """
    to_allocate = desired_count - existing_count

    # Raise quota to allow allocation.
    _raise_fip_quota(ctx.conn, project_id, desired_count)
    logger.info("Raised floating_ips quota to %d for project %s", desired_count, project_id)

    # Allocate the missing FIPs.
    actions: list[Action] = []
    new_fips: list[FipEntry] = []

    for idx in range(to_allocate):
        fip = _create_floating_ip(ctx.conn, external_net_id, project_id, external_subnet_id)
        logger.info(
            "Allocated FIP %s (%s) for project %s [%d/%d]",
            fip.floating_ip_address,
            fip.id,
            project_id,
            idx + 1,
            to_allocate,
        )
        new_fips.append(FipEntry.from_sdk(fip))
        actions.append(
            ctx.record(
                ActionStatus.CREATED,
                "preallocated_fip",
                fip.floating_ip_address,
                f"id={fip.id}, project={project_id}",
            )
        )

    # Persist FIP info to state file.
    all_fips = [FipEntry.from_sdk(f) for f in existing_fips] + new_fips
    _persist_fips(cfg, ctx, all_fips)

    # Set quota to desired count.
    actual_count = existing_count + len(new_fips)
    quota_action = _set_fip_quota_and_record(
        ctx.conn,  # type: ignore[arg-type]
        project_id,
        ctx,
        desired_count,
        actual_count,
    )
    if quota_action:
        actions.append(quota_action)

    return actions


def _detect_router_gateway(conn: Connection, project_id: str) -> tuple[str, str]:
    """Return ``(network_id, subnet_id)`` from the project's first router gateway.

    Inspects ``external_gateway_info`` on the first router that has one.
    The subnet is extracted from ``external_fixed_ips[0].subnet_id`` when
    available.  Returns ``("", "")`` if the project has no routers or none
    with a gateway set.
    """
    for r in conn.network.routers(project_id=project_id):
        gw = r.external_gateway_info
        if isinstance(gw, dict):
            net_id: str = gw.get("network_id", "")
            if not net_id:
                continue
            subnet_id = ""
            fixed_ips = gw.get("external_fixed_ips")
            if isinstance(fixed_ips, list) and fixed_ips:
                first = fixed_ips[0]
                if isinstance(first, dict):
                    subnet_id = first.get("subnet_id", "")
            return net_id, subnet_id
    return "", ""


def ensure_preallocated_fips(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Pre-allocate and enforce quota for floating IPs.

    If desired count > existing: scale up (allocate missing FIPs).
    If desired count == existing: just set the quota.
    If desired count < existing: scale down (release unused FIPs).
    Always persists remaining FIPs to config and sets quota to desired count.
    """
    net_quotas = cfg.quotas.network if cfg.quotas else None
    if not net_quotas:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_fip",
                "all",
                "no quotas.network configured",
            )
        ]

    # Offline mode: no connection available.
    if ctx.conn is None:
        desired = net_quotas.get("floating_ips", 0)
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_fip",
                cfg.name,
                f"would allocate {desired} FIP(s) and set quota (offline)",
            )
        ]

    desired_count: int = net_quotas.get("floating_ips", 0)
    if desired_count == 0:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_fip",
                "",
                "no floating IPs requested",
            )
        ]

    # Resolve per-project external network/subnet
    external_net_id, external_subnet_id = resolve_project_external_network(cfg, ctx)

    # If a router already exists on a different external network (or a
    # different subnet within the same network), allocate FIPs there
    # instead — the router won't reach FIPs on another network/subnet.
    router_ext_net_id, router_ext_subnet_id = _detect_router_gateway(ctx.conn, project_id)
    if router_ext_net_id and router_ext_net_id != external_net_id:
        logger.warning(
            "Project %s router uses external network %s, "
            "but config resolves to %s — allocating FIPs on router network",
            project_id,
            router_ext_net_id,
            external_net_id,
        )
        external_net_id = router_ext_net_id
        external_subnet_id = router_ext_subnet_id or ""
    elif router_ext_subnet_id and router_ext_subnet_id != external_subnet_id:
        logger.warning(
            "Project %s router uses external subnet %s, "
            "but config resolves to %s — allocating FIPs on router subnet",
            project_id,
            router_ext_subnet_id,
            external_subnet_id,
        )
        external_subnet_id = router_ext_subnet_id

    if not external_net_id:
        logger.warning(
            "Skipping FIP allocation for project %s — no external network available",
            project_id,
        )
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "preallocated_fip",
                "",
                "no external network — cannot allocate FIPs",
            )
        ]

    # Check how many FIPs are already allocated.
    all_fips = _list_floating_ips(ctx.conn, project_id)

    # Partition FIPs: only manage those from the configured external network.
    # Foreign FIPs (from other external networks) are never touched.
    existing_fips, foreign_fips = _partition_fips_by_network(all_fips, external_net_id)
    existing_count = len(existing_fips)

    foreign_actions: list[Action] = []
    if foreign_fips:
        foreign_nets = {getattr(f, "floating_network_id", "unknown") for f in foreign_fips}
        logger.warning(
            "Project %s has %d FIP(s) from foreign external network(s) %s "
            "(configured: %s) — these will not be managed",
            project_id,
            len(foreign_fips),
            foreign_nets,
            external_net_id,
        )
        foreign_actions.append(
            ctx.record(
                ActionStatus.FAILED,
                "preallocated_fip",
                "",
                f"{len(foreign_fips)} FIP(s) from foreign external network(s) "
                f"{foreign_nets} — not managed (configured network: {external_net_id})",
            )
        )

    # Online dry-run: report what would happen without writing.
    if ctx.dry_run:
        if existing_count == desired_count:
            return [
                *foreign_actions,
                ctx.record(
                    ActionStatus.SKIPPED,
                    "preallocated_fip",
                    cfg.name,
                    f"already allocated ({existing_count}/{desired_count}), quota would be set",
                ),
            ]
        if existing_count > desired_count:
            excess = existing_count - desired_count
            return [
                *foreign_actions,
                ctx.record(
                    ActionStatus.UPDATED,
                    "preallocated_fip",
                    cfg.name,
                    f"have {existing_count}, want {desired_count}, would release {excess} FIP(s)",
                ),
            ]
        to_alloc = desired_count - existing_count
        return [
            *foreign_actions,
            ctx.record(
                ActionStatus.CREATED,
                "preallocated_fip",
                cfg.name,
                f"have {existing_count}, want {desired_count}, would allocate {to_alloc} FIP(s)",
            ),
        ]

    # Drift detection: reconcile config vs actual OpenStack state.
    config_fips = cfg.preallocated_fips
    drift_actions: list[Action] = []
    if config_fips:
        drift_actions = _reconcile_fip_drift(
            cfg,
            project_id,
            ctx,
            config_fips,
            existing_fips,
            external_net_id,
            external_subnet_id,
        )
        if drift_actions:
            all_fips = _list_floating_ips(ctx.conn, project_id)
            existing_fips, _ = _partition_fips_by_network(all_fips, external_net_id)

    existing_count = len(existing_fips)
    drift_actions = foreign_actions + drift_actions

    if existing_count == desired_count:
        # Exact match — just set the quota.
        quota_action = _set_fip_quota_and_record(
            ctx.conn,  # type: ignore[arg-type]
            project_id,
            ctx,
            desired_count,
            existing_count,
        )
        logger.debug(
            "Project %s already has %d FIP(s) (desired %d), quota set",
            project_id,
            existing_count,
            desired_count,
        )

        # When track_fip_changes is enabled, persist FIPs on every steady-state
        # run where the tracked IDs don't match actual OpenStack FIPs.  Also
        # writes a tracking metadata snapshot for downstream consumers (NFS).
        if cfg.track_fip_changes:
            tracked_ids = {f.id for f in config_fips}
            actual_ids = {f.id for f in existing_fips}
            if tracked_ids != actual_ids:
                _persist_fips(cfg, ctx, [FipEntry.from_sdk(f) for f in existing_fips])
                _persist_fip_tracking_snapshot(cfg, ctx, desired_count, existing_count)

        actions = [*drift_actions]
        if quota_action:
            actions.append(quota_action)
        else:
            actions.append(
                ctx.record(
                    ActionStatus.SKIPPED,
                    "preallocated_fip",
                    "",
                    f"already allocated ({existing_count}/{desired_count}), quota set",
                )
            )
        return actions

    if existing_count > desired_count:
        # Scale down — release excess unused FIPs.
        return drift_actions + _scale_down_fips(
            cfg, project_id, ctx, existing_fips, existing_count, desired_count
        )

    # Scale up — allocate missing FIPs.
    return drift_actions + _scale_up_fips(
        cfg,
        project_id,
        ctx,
        existing_fips,
        existing_count,
        desired_count,
        external_net_id,
        external_subnet_id,
    )
