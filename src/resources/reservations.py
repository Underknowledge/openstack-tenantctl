"""Time-limited private-flavor access via reservations (DD-027).

Each run computes the union of all active reservation spans, matches their
flavor patterns against the cloud's private flavors, and diffs the result
against the grants tracked in the state file: missing access is granted,
tracked grants no longer covered are revoked.  Manual grants are never
tracked and therefore never touched.  Revocations move to the
``revoked_flavor_access`` audit trail, reconciled like the released IP
lists (DD-026).
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openstack.compute.v2.flavor import Flavor
    from openstack.connection import Connection

    from src.models import ProjectConfig
    from src.models.reservations import ReservationConfig

from openstack.exceptions import ConflictException, NotFoundException

from src.models import GrantedFlavorAccessEntry, RevokedFlavorAccessEntry
from src.period_parser import spans_active, spans_all_past
from src.utils import (
    Action,
    ActionStatus,
    DryRunUnsupportedError,
    SharedContext,
    retry,
)

logger = logging.getLogger(__name__)

_UNMATCHED_PATTERNS_PATH = ["metadata", "unmatched_flavor_patterns"]

_TEARDOWN_REASON = "project teardown"


@retry()
def _list_private_flavors_api(conn: Connection) -> list[Flavor]:
    """Fetch all private flavors from Nova (admin-only listing)."""
    # is_public=False asks Nova for private flavors only; the client-side
    # filter is belt-and-braces (Nova rejects access entries on public
    # flavors, so a public flavor must never enter the desired set).
    return [f for f in conn.compute.flavors(is_public=False) if not f.is_public]


def _get_private_flavors(ctx: SharedContext) -> list[Flavor]:
    """Return the run-wide private flavor list, fetching it lazily once (DD-006)."""
    if ctx.private_flavors is None:
        if ctx.conn is None:
            msg = "cannot list private flavors without a connection"
            raise DryRunUnsupportedError(msg)
        ctx.private_flavors = _list_private_flavors_api(ctx.conn)
        logger.info("Fetched %d private flavor(s) (cached for this run)", len(ctx.private_flavors))
    return ctx.private_flavors


@retry()
def _grant_access(conn: Connection, flavor_id: str, project_id: str) -> None:
    conn.compute.flavor_add_tenant_access(flavor_id, project_id)


@retry()
def _revoke_access(conn: Connection, flavor_id: str, project_id: str) -> None:
    conn.compute.flavor_remove_tenant_access(flavor_id, project_id)


@retry()
def _access_project_ids(conn: Connection, flavor_id: str) -> set[str]:
    """Return the project IDs currently granted access to *flavor_id*."""
    ids: set[str] = set()
    for entry in conn.compute.get_flavor_access(flavor_id):
        tenant = entry.get("tenant_id") if isinstance(entry, dict) else getattr(entry, "tenant_id", None)
        if tenant:
            ids.add(str(tenant))
    return ids


def _matches(flavor_name: str, pattern: str) -> bool:
    """Exact name or fnmatch wildcard match."""
    return flavor_name == pattern or fnmatch.fnmatchcase(flavor_name, pattern)


def _desired_grants(
    active_entries: list[ReservationConfig],
    private_flavors: list[Flavor],
) -> dict[str, tuple[str, str]]:
    """Map flavor_id → (flavor_name, reservation name) for all covered flavors.

    A flavor covered by several active entries keeps the first covering
    entry's name (entry order); coverage itself is a union.
    """
    desired: dict[str, tuple[str, str]] = {}
    for entry in active_entries:
        for flavor in private_flavors:
            if str(flavor.id) in desired:
                continue
            if any(_matches(flavor.name, pattern) for pattern in entry.flavors):
                desired[str(flavor.id)] = (str(flavor.name), entry.name)
    return desired


def _warn_unmatched_patterns(
    cfg: ProjectConfig,
    ctx: SharedContext,
    active_entries: list[ReservationConfig],
    private_flavors: list[Flavor],
) -> None:
    """Warn about patterns matching no private flavor — on first detection only.

    The tool runs on a cadence, so repeating the warning every run is noise:
    the currently-unmatched pattern set is persisted in state metadata and
    only newly-unmatched patterns are logged.  A pattern that matches again
    is pruned, so it warns afresh if it becomes unmatched later.
    """
    flavor_names = [str(f.name) for f in private_flavors]
    unmatched = sorted(
        {
            f"{entry.label}:{pattern}"
            for entry in active_entries
            for pattern in entry.flavors
            if not any(_matches(name, pattern) for name in flavor_names)
        }
    )

    previous: list[str] = []
    if ctx.state_store is not None and cfg.state_key:
        metadata = ctx.state_store.load(cfg.state_key).get("metadata", {})
        stored = metadata.get("unmatched_flavor_patterns")
        if isinstance(stored, list):
            previous = stored

    for key in unmatched:
        if key not in previous:
            entry_label, _, pattern = key.partition(":")
            logger.warning(
                "Project %s reservation %s: flavor pattern %r matches no private flavor",
                cfg.name,
                entry_label,
                pattern,
            )

    if not ctx.dry_run and ctx.state_store is not None and cfg.state_key and unmatched != previous:
        ctx.state_store.save(cfg.state_key, _UNMATCHED_PATTERNS_PATH, unmatched)


def _persist_grants(cfg: ProjectConfig, ctx: SharedContext, grants: list[GrantedFlavorAccessEntry]) -> None:
    """Write the tracked grant list to the project state file."""
    if ctx.state_store is None:
        msg = "state_store is None — cannot persist flavor grants"
        raise DryRunUnsupportedError(msg)
    ctx.state_store.save(cfg.state_key, ["granted_flavor_access"], [g.to_dict() for g in grants])


def _reconcile_revoked(
    cfg: ProjectConfig,
    ctx: SharedContext,
    final_grants: list[GrantedFlavorAccessEntry],
    newly_revoked: list[RevokedFlavorAccessEntry],
) -> None:
    """Merge new revocations and prune entries that are active again (DD-026)."""
    active_ids = {g.flavor_id for g in final_grants}
    all_revoked = [r for r in [*cfg.revoked_flavor_access, *newly_revoked] if r.flavor_id not in active_ids]
    if all_revoked != cfg.revoked_flavor_access:
        if ctx.state_store is None:
            msg = "state_store is None — cannot persist revoked flavor grants"
            raise DryRunUnsupportedError(msg)
        ctx.state_store.save(cfg.state_key, ["revoked_flavor_access"], [r.to_dict() for r in all_revoked])


def _revocation_reason(grant: GrantedFlavorAccessEntry, cfg: ProjectConfig) -> str:
    """Distinguish a removed config entry from an entry whose period ended."""
    config_names = {entry.name for entry in cfg.reservations if entry.name}
    if grant.reservation and grant.reservation not in config_names:
        return "reservation entry removed from config"
    return "no active reservation covers this flavor"


def ensure_reservations(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Reconcile flavor-access grants for all reservation entries.

    Level-triggered: grants missing access covered by an active span, verifies
    and heals access for covered tracked grants, and revokes tracked grants
    that are no longer covered (period ended or entry deleted).  Only tracked
    grants are ever revoked (DD-027).
    """
    tracked = cfg.granted_flavor_access
    if not cfg.reservations and not tracked:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "reservation",
                "all",
                "no reservations configured",
            )
        ]

    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "reservation",
                cfg.name,
                "would reconcile flavor access (offline)",
            )
        ]

    now = datetime.now(UTC)
    actions: list[Action] = []

    active_entries: list[ReservationConfig] = []
    for entry in cfg.reservations:
        if spans_active(entry.period, now):
            active_entries.append(entry)
        elif spans_all_past(entry.period, now):
            logger.warning(
                "Project %s reservation %s: all periods are in the past — dead config, remove the entry",
                cfg.name,
                entry.label,
            )

    # Resolved UTC spans per entry — dry-run visibility for boundary checks.
    if ctx.dry_run:
        for entry in cfg.reservations:
            span_desc = "; ".join(span.describe() for span in entry.period)
            state = "active" if entry in active_entries else "inactive"
            actions.append(
                ctx.record(
                    ActionStatus.SKIPPED,
                    "reservation",
                    entry.label,
                    f"period {span_desc} — currently {state}",
                )
            )

    # The flavor list is only needed when an active entry exists; with no
    # active entries the desired set is empty and every tracked grant is
    # revoked without listing anything.
    desired: dict[str, tuple[str, str]] = {}
    if active_entries:
        private_flavors = _get_private_flavors(ctx)
        desired = _desired_grants(active_entries, private_flavors)
        _warn_unmatched_patterns(cfg, ctx, active_entries, private_flavors)

    tracked_ids = {g.flavor_id for g in tracked}
    to_grant = {fid: meta for fid, meta in desired.items() if fid not in tracked_ids}
    covered = [g for g in tracked if g.flavor_id in desired]
    to_revoke = [g for g in tracked if g.flavor_id not in desired]

    if ctx.dry_run:
        for fid, (flavor_name, reservation) in sorted(to_grant.items()):
            actions.append(
                ctx.record(
                    ActionStatus.CREATED,
                    "flavor_access",
                    flavor_name,
                    f"would grant via reservation {reservation or '<unnamed>'!r} (id={fid})",
                )
            )
        actions.extend(
            ctx.record(
                ActionStatus.DELETED,
                "flavor_access",
                grant.flavor_name,
                f"would revoke — {_revocation_reason(grant, cfg)}",
            )
            for grant in to_revoke
        )
        return actions

    # --- Grant missing access ---
    new_grants: list[GrantedFlavorAccessEntry] = []
    for fid, (flavor_name, reservation) in sorted(to_grant.items()):
        if project_id in _access_project_ids(ctx.conn, fid):
            # Access existed before we did anything — a manual grant.
            # Deliberately NOT tracked, so it is never revoked when the
            # reservation ends.
            actions.append(
                ctx.record(
                    ActionStatus.SKIPPED,
                    "flavor_access",
                    flavor_name,
                    "access already exists (manual grant — not tracked)",
                )
            )
            continue
        try:
            _grant_access(ctx.conn, fid, project_id)
        except ConflictException:
            # The pre-check saw no access, so this conflict is our own call
            # retried after a lost response (or a race won by someone else).
            # Track it: revoking a racing manual grant when the reservation
            # ends beats leaking our own grant forever.
            logger.info(
                "Flavor %s access already present after clean pre-check — tracking as our grant",
                flavor_name,
            )
        new_grants.append(
            GrantedFlavorAccessEntry(
                flavor_id=fid,
                flavor_name=flavor_name,
                reservation=reservation,
                granted_at=now.isoformat(),
            )
        )
        actions.append(
            ctx.record(
                ActionStatus.CREATED,
                "flavor_access",
                flavor_name,
                f"granted via reservation {reservation or '<unnamed>'!r} (id={fid})",
            )
        )
        logger.info("Granted flavor %s (%s) to project %s", flavor_name, fid, cfg.name)

    # Persist new grants before the verify/revoke phases: a crash from here
    # on must not leave fresh grants untracked (they would classify as
    # manual grants next run and never be revoked).
    if new_grants:
        _persist_grants(cfg, ctx, [*tracked, *new_grants])

    # --- Verify covered tracked grants (one call per grant, heals drift) ---
    for grant in covered:
        if project_id not in _access_project_ids(ctx.conn, grant.flavor_id):
            _grant_access(ctx.conn, grant.flavor_id, project_id)
            actions.append(
                ctx.record(
                    ActionStatus.UPDATED,
                    "flavor_access",
                    grant.flavor_name,
                    "re-granted (access was removed outside tenantctl)",
                )
            )
            logger.warning(
                "Re-granted flavor %s to project %s — tracked access was missing",
                grant.flavor_name,
                cfg.name,
            )

    # --- Revoke tracked grants no longer covered ---
    newly_revoked: list[RevokedFlavorAccessEntry] = []
    for grant in to_revoke:
        reason = _revocation_reason(grant, cfg)
        try:
            _revoke_access(ctx.conn, grant.flavor_id, project_id)
            detail = f"revoked — {reason}"
        except NotFoundException:
            # Flavor or access entry already gone — the grant is moot either way.
            detail = f"revoked (already gone) — {reason}"
        newly_revoked.append(
            RevokedFlavorAccessEntry(
                flavor_id=grant.flavor_id,
                flavor_name=grant.flavor_name,
                reservation=grant.reservation,
                revoked_at=now.isoformat(),
                reason=reason,
            )
        )
        actions.append(ctx.record(ActionStatus.DELETED, "flavor_access", grant.flavor_name, detail))
        logger.info("Revoked flavor %s from project %s (%s)", grant.flavor_name, cfg.name, reason)

    # --- Persist: one tail write per list (DD-026) ---
    final_grants = [*covered, *new_grants]
    if final_grants != tracked:
        _persist_grants(cfg, ctx, final_grants)
    _reconcile_revoked(cfg, ctx, final_grants, newly_revoked)

    if not actions:
        actions.append(
            ctx.record(
                ActionStatus.SKIPPED,
                "reservation",
                cfg.name,
                f"flavor access converged ({len(covered)} tracked grant(s))",
            )
        )
    return actions


def revoke_all_reservation_grants(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Revoke every tracked grant as part of project teardown.

    Teardown must not skip reservation cleanup: leaving tracked grants in
    place would leave dangling flavor-access entries pointing at a deleted
    project ID in Nova.  Only tracked grants are revoked (DD-027).
    """
    tracked = cfg.granted_flavor_access
    if not tracked:
        return []

    if ctx.conn is None:
        return [
            ctx.record(
                ActionStatus.SKIPPED,
                "flavor_access",
                cfg.name,
                f"would revoke {len(tracked)} tracked grant(s) (offline)",
            )
        ]

    if ctx.dry_run:
        return [
            ctx.record(
                ActionStatus.DELETED,
                "flavor_access",
                grant.flavor_name,
                f"would revoke ({_TEARDOWN_REASON})",
            )
            for grant in tracked
        ]

    now = datetime.now(UTC)
    actions: list[Action] = []
    newly_revoked: list[RevokedFlavorAccessEntry] = []
    for grant in tracked:
        try:
            _revoke_access(ctx.conn, grant.flavor_id, project_id)
            detail = f"revoked ({_TEARDOWN_REASON})"
        except NotFoundException:
            detail = f"revoked (already gone, {_TEARDOWN_REASON})"
        newly_revoked.append(
            RevokedFlavorAccessEntry(
                flavor_id=grant.flavor_id,
                flavor_name=grant.flavor_name,
                reservation=grant.reservation,
                revoked_at=now.isoformat(),
                reason=_TEARDOWN_REASON,
            )
        )
        actions.append(ctx.record(ActionStatus.DELETED, "flavor_access", grant.flavor_name, detail))
        logger.info("Revoked flavor %s from project %s (teardown)", grant.flavor_name, cfg.name)

    _persist_grants(cfg, ctx, [])
    _reconcile_revoked(cfg, ctx, [], newly_revoked)
    return actions
