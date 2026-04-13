from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import openstack.exceptions

from src.utils import (
    Action,
    ActionStatus,
    DryRunUnsupportedError,
    ProvisionerError,
    SharedContext,
    retry,
)

if TYPE_CHECKING:
    from src.models import ProjectConfig

logger = logging.getLogger(__name__)

# Load balancer quota keys (Octavia service)
LOAD_BALANCER_QUOTA_KEYS = {
    "load_balancers",
    "listeners",
    "pools",
    "health_monitors",
    "members",
}

# Keys from QuotaSet.to_dict() that are NOT quota limits.
_QUOTA_SET_META_KEYS = frozenset({"id", "name", "project_id", "location", "reservation", "usage"})


@retry()
def _ensure_compute_quotas(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Ensure compute quotas match the desired configuration."""
    if ctx.conn is None:
        msg = "ctx.conn is None — not available in offline mode"
        raise DryRunUnsupportedError(msg)
    if cfg.quotas is None:
        msg = "cfg.quotas is None — should have been checked by caller"
        raise ProvisionerError(msg)
    desired: dict[str, int] = dict(cfg.quotas.compute)

    current_quota = ctx.conn.compute.get_quota_set(project_id)
    current: dict[str, int] = {k: getattr(current_quota, k) for k in desired}

    if any(desired[k] != current[k] for k in desired):
        # Check if any key is being lowered — may need usage check.
        lowered_keys = [k for k in desired if isinstance(current[k], int) and desired[k] < current[k]]
        if lowered_keys and not ctx.dry_run:
            usage_quota = ctx.conn.compute.get_quota_set(project_id, usage=True)
            usage_dict = usage_quota.usage if isinstance(usage_quota.usage, dict) else {}
            for k in lowered_keys:
                used = usage_dict.get(k, 0)
                if isinstance(used, int) and desired[k] < used:
                    logger.warning(
                        "Cannot lower compute quota %s to %d for project %s — "
                        "current usage is %d, clamping to usage",
                        k,
                        desired[k],
                        project_id,
                        used,
                    )
                    desired[k] = used

        diff = ", ".join(f"{k}: {current[k]} → {desired[k]}" for k in sorted(desired) if desired[k] != current[k])
        if ctx.dry_run:
            return [ctx.record(ActionStatus.UPDATED, "compute_quota", "", f"would update ({diff})")]
        if diff:
            ctx.conn.compute.update_quota_set(project_id, **desired)
            details = ", ".join(f"{k}={v}" for k, v in sorted(desired.items()))
            logger.info("Updated compute quotas for %s: %s", project_id, details)
            return [
                ctx.record(ActionStatus.UPDATED, "compute_quota", "", details),
            ]
        # All differing keys were clamped to current values — no update needed.
        current_details = ", ".join(f"{k}={current[k]}" for k in sorted(desired))
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "compute_quota",
                "",
                f"already up to date, {current_details}",
            )
        ]

    logger.debug("Compute quotas for %s already up to date", project_id)
    current_details = ", ".join(f"{k}={current[k]}" for k in sorted(desired))
    return [
        ctx.record(
            ActionStatus.SKIPPED,
            "compute_quota",
            "",
            f"already up to date, {current_details}",
        )
    ]


@retry()
def _ensure_network_quotas(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Ensure network (Neutron) and load balancer (Octavia) quotas match config.

    Load balancer quotas can be specified in the network section but are handled
    via a separate API (conn.load_balancer) with graceful degradation if unavailable.

    Excludes floating_ips unconditionally because the locked-FIP module
    manages that quota.  Excludes networks when <= 1 (pre-allocated resource).
    """
    if ctx.conn is None:
        msg = "ctx.conn is None — not available in offline mode"
        raise DryRunUnsupportedError(msg)
    if cfg.quotas is None:
        msg = "cfg.quotas is None — should have been checked by caller"
        raise ProvisionerError(msg)
    project_label = cfg.name
    network_cfg: dict[str, int] = dict(cfg.quotas.network)

    # Split network config into Neutron vs Load Balancer quotas
    neutron_quotas: dict[str, int] = {}
    lb_quotas: dict[str, int] = {}

    for key, value in network_cfg.items():
        if key in LOAD_BALANCER_QUOTA_KEYS:
            lb_quotas[key] = value
        else:
            neutron_quotas[key] = value

    # Exclude pre-allocated resource quotas from Neutron quotas
    neutron_quotas.pop("floating_ips", None)

    if neutron_quotas.get("networks", 0) <= 1:
        neutron_quotas.pop("networks", None)

    if not neutron_quotas and not lb_quotas:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "network_quota",
                "",
                "no network quotas configured",
            )
        ]

    # Handle Neutron (network) quotas
    neutron_updated = False
    neutron_diff_parts: list[str] = []
    current_dict: dict[str, int | None] = {}
    if neutron_quotas:
        current = ctx.conn.network.get_quota(project_id)
        current_dict = {k: getattr(current, k, None) for k in neutron_quotas}
        neutron_needs_update = any(neutron_quotas.get(k) != current_dict.get(k) for k in neutron_quotas)

        if neutron_needs_update:
            # Check if any Neutron key is being lowered below usage.
            lowered_keys = [
                k
                for k in neutron_quotas
                if isinstance(current_dict.get(k), int) and neutron_quotas[k] < (current_dict[k] or 0)
            ]
            if lowered_keys and not ctx.dry_run:
                usage = ctx.conn.network.get_quota(project_id, details=True)
                for k in lowered_keys:
                    detail = getattr(usage, k, None)
                    used = detail.get("used", 0) if isinstance(detail, dict) else 0
                    if isinstance(used, int) and neutron_quotas[k] < used:
                        logger.warning(
                            "Cannot lower network quota %s to %d for project %s — "
                            "current usage is %d, clamping to usage",
                            k,
                            neutron_quotas[k],
                            project_id,
                            used,
                        )
                        neutron_quotas[k] = used

            neutron_diff_parts = [
                f"{k}: {current_dict.get(k)} → {neutron_quotas[k]}"
                for k in sorted(neutron_quotas)
                if neutron_quotas.get(k) != current_dict.get(k)
            ]
            if not ctx.dry_run:
                if neutron_diff_parts:
                    ctx.conn.network.update_quota(project_id, **neutron_quotas)
                    logger.info(
                        "Updated network quotas for %s: %s",
                        project_label,
                        neutron_quotas,
                    )
                    neutron_updated = True
            else:
                neutron_updated = True

    # Handle Load Balancer (Octavia) quotas with graceful degradation
    lb_updated = False
    lb_diff_parts: list[str] = []
    current_lb_dict: dict[str, int | None] = {}
    if lb_quotas:
        try:
            current_lb = ctx.conn.load_balancer.get_quota(project_id)
            current_lb_dict = {k: getattr(current_lb, k, None) for k in lb_quotas}
            lb_needs_update = any(lb_quotas.get(k) != current_lb_dict.get(k) for k in lb_quotas)

            if lb_needs_update:
                lb_diff_parts = [
                    f"{k}: {current_lb_dict.get(k)} → {lb_quotas[k]}"
                    for k in sorted(lb_quotas)
                    if lb_quotas.get(k) != current_lb_dict.get(k)
                ]
                if not ctx.dry_run:
                    ctx.conn.load_balancer.update_quota(project_id, **lb_quotas)
                    logger.info(
                        "Updated load_balancer quotas for %s: %s",
                        project_label,
                        lb_quotas,
                    )
                lb_updated = True
        except openstack.exceptions.EndpointNotFound:
            logger.warning(
                "Skipping load_balancer quotas for %s (Octavia service not available)",
                project_label,
            )
        except Exception:
            logger.warning(
                "Skipping load_balancer quotas for %s (unexpected error)",
                project_label,
                exc_info=True,
            )

    # Determine overall status
    if neutron_updated or lb_updated:
        if ctx.dry_run:
            diff_parts: list[str] = []
            if neutron_diff_parts:
                diff_parts.append(f"network: {', '.join(neutron_diff_parts)}")
            if lb_diff_parts:
                diff_parts.append(f"load_balancer: {', '.join(lb_diff_parts)}")
            return [
                ctx.record(
                    ActionStatus.UPDATED,
                    "network_quota",
                    "",
                    f"would update ({'; '.join(diff_parts)})",
                ),
            ]
        details_parts: list[str] = []
        if neutron_updated:
            details_parts.append(f"network: {neutron_quotas}")
        if lb_updated:
            details_parts.append(f"load_balancer: {lb_quotas}")
        return [
            ctx.record(
                ActionStatus.UPDATED,
                "network_quota",
                "",
                "; ".join(details_parts),
            ),
        ]

    logger.debug("Network quotas for %s already up to date", project_label)
    all_current = {**current_dict, **current_lb_dict}
    current_details = ", ".join(f"{k}={v}" for k, v in sorted(all_current.items()))
    return [
        ctx.record(
            ActionStatus.SKIPPED,
            "network_quota",
            "",
            f"already up to date, {current_details}",
        )
    ]


@retry()
def _ensure_block_storage_quotas(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Ensure block-storage quotas match the desired configuration.

    Cinder resets unspecified keys, so we always read current quotas first,
    overlay desired keys, and send all keys back.
    """
    if ctx.conn is None:
        msg = "ctx.conn is None — not available in offline mode"
        raise DryRunUnsupportedError(msg)
    if cfg.quotas is None:
        msg = "cfg.quotas is None — should have been checked by caller"
        raise ProvisionerError(msg)
    desired: dict[str, int] = dict(cfg.quotas.block_storage)

    current_quota = ctx.conn.block_storage.get_quota_set(project_id)
    current: dict[str, int] = {k: getattr(current_quota, k) for k in desired}

    needs_update = any(desired[k] != current[k] for k in desired)

    if needs_update:
        # Check if any key is being lowered below usage.
        lowered_keys = [k for k in desired if isinstance(current[k], int) and desired[k] < current[k]]
        if lowered_keys and not ctx.dry_run:
            usage_quota = ctx.conn.block_storage.get_quota_set(project_id, usage=True)
            usage_dict = usage_quota.usage if isinstance(usage_quota.usage, dict) else {}
            for k in lowered_keys:
                used = usage_dict.get(k, 0)
                if isinstance(used, int) and desired[k] < used:
                    logger.warning(
                        "Cannot lower block_storage quota %s to %d for project %s — "
                        "current usage is %d, clamping to usage",
                        k,
                        desired[k],
                        project_id,
                        used,
                    )
                    desired[k] = used

        diff = ", ".join(f"{k}: {current[k]} → {desired[k]}" for k in sorted(desired) if desired[k] != current[k])
        if ctx.dry_run:
            return [
                ctx.record(
                    ActionStatus.UPDATED,
                    "block_storage_quota",
                    "",
                    f"would update ({diff})",
                )
            ]

        if diff:
            # Overlay desired onto full current state so Cinder keeps other keys.
            raw = current_quota.to_dict()
            merged: dict[str, int] = {
                k: v for k, v in raw.items() if isinstance(v, int) and k not in _QUOTA_SET_META_KEYS
            }
            merged.update(desired)
            merged.pop("project_id", None)
            ctx.conn.block_storage.update_quota_set(project_id, **merged)
            details = ", ".join(f"{k}={v}" for k, v in sorted(desired.items()))
            logger.info("Updated block_storage quotas for %s: %s", project_id, details)
            return [
                ctx.record(ActionStatus.UPDATED, "block_storage_quota", "", details),
            ]
        # All differing keys were clamped to current values — no update needed.
        current_details = ", ".join(f"{k}={current[k]}" for k in sorted(desired))
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "block_storage_quota",
                "",
                f"already up to date, {current_details}",
            )
        ]

    logger.debug("Block-storage quotas for %s already up to date", project_id)
    current_details = ", ".join(f"{k}={current[k]}" for k in sorted(desired))
    return [
        ctx.record(
            ActionStatus.SKIPPED,
            "block_storage_quota",
            "",
            f"already up to date, {current_details}",
        )
    ]


def ensure_quotas(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Ensure compute, network, and block_storage quotas are set correctly.

    Returns list of Actions (one per quota service that was changed).
    """
    if not cfg.quotas:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "quotas",
                "all",
                "no quotas configured",
            )
        ]

    # Offline mode: no connection available.
    if ctx.conn is None:
        return [ctx.record(ActionStatus.SKIPPED, "quotas", "", "would set quotas (offline)")]

    actions: list[Action] = []
    actions.extend(_ensure_compute_quotas(cfg, project_id, ctx))
    actions.extend(_ensure_network_quotas(cfg, project_id, ctx))

    try:
        actions.extend(_ensure_block_storage_quotas(cfg, project_id, ctx))
    except Exception as exc:
        logger.warning(
            "Block-storage quotas skipped for %s (%s): %s",
            project_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        actions.append(
            ctx.record(
                ActionStatus.SKIPPED,
                "block_storage_quota",
                "",
                f"skipped ({type(exc).__name__})",
            )
        )

    changed = [a for a in actions if a.status != ActionStatus.SKIPPED]

    if changed:
        return changed

    return [ctx.record(ActionStatus.SKIPPED, "quotas", "", "already up to date")]
