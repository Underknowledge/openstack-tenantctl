"""Reconciler — orchestrates per-project resource provisioning, then federation.

Phase 3 of the provisioning pipeline.  Each project is reconciled
independently (with error isolation) using a state dispatch mechanism,
followed by a single shared federation-mapping update that considers
ALL project configs.

Supported states:
- ``present``: full provisioning (+ unshelve on locked->present transition only)
- ``locked``: disable project, shelve VMs, skip network/quota/SG
- ``absent``: safety-checked teardown in reverse dependency order
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from src.models import ProjectConfig
from src.resources.compute import shelve_all_servers, unshelve_all_servers
from src.resources.federation import augment_group_role_assignments, ensure_federation_mapping
from src.resources.group_roles import ensure_group_role_assignments
from src.resources.keystone_groups import ensure_keystone_groups
from src.resources.network import ensure_network_stack, track_router_ips
from src.resources.prealloc import ensure_preallocated_fips, ensure_preallocated_network
from src.resources.project import (
    ensure_project,
    find_existing_project,
)
from src.resources.quotas import ensure_quotas
from src.resources.security_group import ensure_baseline_sg
from src.resources.teardown import safety_check, teardown_project
from src.utils import (
    Action,
    ActionStatus,
    SafetyCheckError,
    SharedContext,
    resolve_project_external_network,
)

logger = logging.getLogger(__name__)

_PHASE_KEYSTONE_GROUPS = "__keystone_groups__"
_PHASE_FEDERATION = "__federation__"

# Type alias for state handler functions.
StateHandler = Callable[[ProjectConfig, SharedContext], None]


class ReconcileScope(StrEnum):
    """Selectable resource scopes for partial reconciliation.

    When passed to ``reconcile()`` via the *scopes* parameter, only the
    listed handlers run during the ``present`` state.  ``None`` (the
    default) means "run everything" — full backward compatibility.
    """

    ROLES = "roles"
    NETWORK = "network"
    FIPS = "fips"
    PREALLOC_NETWORK = "prealloc_network"
    QUOTAS = "quotas"
    SECURITY_GROUPS = "security_groups"
    KEYSTONE_GROUPS = "keystone_groups"
    FEDERATION = "federation"


_SCOPE_DEPENDENCIES: dict[ReconcileScope, set[ReconcileScope]] = {
    ReconcileScope.FIPS: {ReconcileScope.NETWORK},
    ReconcileScope.PREALLOC_NETWORK: {ReconcileScope.NETWORK},
    ReconcileScope.ROLES: {ReconcileScope.KEYSTONE_GROUPS},
}


def _expand_scope_dependencies(scopes: set[ReconcileScope]) -> set[ReconcileScope]:
    """Add prerequisite scopes until the set stabilises (fixed-point).

    Iterates at most ``len(ReconcileScope)`` times (one per possible
    addition).  Each auto-included scope is logged at INFO level.
    """
    expanded = set(scopes)
    for _ in range(len(ReconcileScope)):
        additions: set[ReconcileScope] = set()
        for scope in expanded:
            for dep in _SCOPE_DEPENDENCIES.get(scope, set()):
                if dep not in expanded:
                    additions.add(dep)
        if not additions:
            break
        for dep in sorted(additions, key=lambda s: s.value):
            logger.info("auto-including prerequisite scope %r (required by scopes in --only)", dep.value)
        expanded |= additions
    return expanded


def validate_scopes(
    scopes: set[ReconcileScope] | None,
    *,
    auto_expand_deps: bool = False,
) -> set[ReconcileScope] | None:
    """Normalize, validate, and optionally expand a scope set.

    Returns ``None`` unchanged (meaning "all scopes").  A non-empty set is
    returned with every element coerced to :class:`ReconcileScope` (so
    callers may pass plain strings).  Raises :class:`ValueError` on empty
    sets or unknown values.

    When *auto_expand_deps* is ``False`` (the default), missing prerequisite
    scopes cause a :class:`ValueError`.  When ``True``, prerequisites are
    added automatically with an INFO-level log message.
    """
    if scopes is None:
        return None
    if not scopes:
        msg = "scopes must be None (all) or a non-empty set of ReconcileScope values"
        raise ValueError(msg)
    coerced = {ReconcileScope(s) for s in scopes}

    if auto_expand_deps:
        return _expand_scope_dependencies(coerced)

    # Fail-fast: check all dependencies are satisfied.
    missing: list[str] = [
        f"scope {scope.value!r} requires {dep.value!r}"
        for scope in sorted(coerced, key=lambda s: s.value)
        for dep in sorted(_SCOPE_DEPENDENCIES.get(scope, set()), key=lambda s: s.value)
        if dep not in coerced
    ]
    if missing:
        details = "; ".join(missing)
        msg = f"Missing scope dependencies: {details} — include them explicitly or use auto_expand_deps=True"
        raise ValueError(msg)

    return coerced


def _persist_project_metadata(cfg: ProjectConfig, project_id: str, ctx: SharedContext) -> None:
    """Persist project_id and domain_id to the state file."""
    if ctx.dry_run:
        return
    if ctx.state_store is None:
        return
    state_key = cfg.state_key
    if not state_key:
        return
    ctx.state_store.save(state_key, ["metadata", "project_id"], project_id)
    ctx.state_store.save(state_key, ["metadata", "domain_id"], cfg.domain_id)


def _should_unshelve(cfg: ProjectConfig, ctx: SharedContext, *, was_disabled: bool | None) -> bool:
    """Decide whether to unshelve servers for a present-state project.

    Two-layer detection:
    1. State store (primary): if ``last_reconciled_state`` exists, use it.
       Only unshelve when previous state was ``"locked"``.
    2. API fallback: use ``was_disabled`` (the pre-update ``is_enabled``
       flag observed by ``ensure_project``).  Re-enabling a previously
       disabled project indicates a locked→present transition.

    In both cases, ``cfg.enabled`` must be ``True`` — we only unshelve
    when actually re-enabling the project.
    """
    if not cfg.enabled:
        return False

    # Layer 1: state store metadata (no extra API call).
    if ctx.state_store is not None and cfg.state_key:
        data = ctx.state_store.load(cfg.state_key)
        previous_state = data.get("metadata", {}).get("last_reconciled_state")
        if previous_state is not None:
            return bool(previous_state == "locked")

    # Layer 2: pre-update project state from ensure_project — no extra round-trip.
    return bool(was_disabled)


def _preflight_present(cfg: ProjectConfig, ctx: SharedContext) -> None:
    """Validate prerequisites before creating any resources.

    Raises on misconfiguration so the pipeline fails cleanly without
    leaving orphaned resources (e.g. project created but network step
    fails because the external network doesn't exist).
    """
    # If a network is configured, verify the external network is reachable.
    if cfg.network and ctx.conn is not None and not ctx.dry_run:
        resolve_project_external_network(cfg, ctx)


def _reconcile_present(
    cfg: ProjectConfig,
    ctx: SharedContext,
    *,
    scopes: set[ReconcileScope] | None = None,
) -> None:
    """Full provisioning pipeline + conditional unshelve.

    When *scopes* is ``None`` (default), every handler runs — full backward
    compatibility.  When a non-empty set is supplied, only the listed
    resource handlers execute.  ``ensure_project``, preflight checks, and
    metadata persistence always run regardless of *scopes*.

    Unshelve is suppressed when *scopes* is set because it is a state
    transition, not a resource update.
    """
    _preflight_present(cfg, ctx)

    _action, project_id, was_disabled = ensure_project(cfg, ctx)
    ctx.current_project_id = project_id

    _persist_project_metadata(cfg, project_id, ctx)

    # Unshelve only on full reconciliation (scopes=None).
    should_unshelve = scopes is None and _should_unshelve(cfg, ctx, was_disabled=was_disabled)

    def _in_scope(scope: ReconcileScope) -> bool:
        return scopes is None or scope in scopes

    if _in_scope(ReconcileScope.ROLES):
        effective_cfg = augment_group_role_assignments(cfg)
        ensure_group_role_assignments(effective_cfg, project_id, ctx)
    if _in_scope(ReconcileScope.NETWORK):
        ensure_network_stack(cfg, project_id, ctx)
        track_router_ips(cfg, project_id, ctx)
    if _in_scope(ReconcileScope.FIPS):
        ensure_preallocated_fips(cfg, project_id, ctx)
    if _in_scope(ReconcileScope.PREALLOC_NETWORK):
        ensure_preallocated_network(cfg, project_id, ctx)
    if _in_scope(ReconcileScope.QUOTAS):
        ensure_quotas(cfg, project_id, ctx)
    if _in_scope(ReconcileScope.SECURITY_GROUPS):
        ensure_baseline_sg(cfg, project_id, ctx)

    if should_unshelve:
        unshelve_all_servers(cfg, project_id, ctx)


def _reconcile_locked(cfg: ProjectConfig, ctx: SharedContext) -> None:
    """Disable project and shelve all VMs.

    - Forces ``enabled=False`` on the project.
    - Shelves ACTIVE servers.
    - Skips network, quota, and security group provisioning.
    - Group role assignments are kept intact.
    """
    # Override enabled to False for locked state.
    cfg_locked = dataclasses.replace(cfg, enabled=False)

    _action, project_id, _was_disabled = ensure_project(cfg_locked, ctx)
    ctx.current_project_id = project_id

    _persist_project_metadata(cfg, project_id, ctx)

    shelve_all_servers(cfg, project_id, ctx)


def _reconcile_absent(cfg: ProjectConfig, ctx: SharedContext) -> None:
    """Safety-checked teardown of a project.

    1. Look up existing project (skip if not found).
    2. Run safety check (fail if VMs/volumes exist).
    3. Revoke all group role assignments.
    4. Tear down resources in reverse dependency order.
    """
    project_name = cfg.name

    # Offline mode: no connection available, skip entirely.
    if ctx.conn is None:
        ctx.record(
            ActionStatus.SKIPPED,
            "teardown",
            project_name,
            "would tear down project (offline)",
        )
        return

    project_id, _domain_id = find_existing_project(cfg, ctx)

    if project_id is None:
        ctx.record(
            ActionStatus.SKIPPED,
            "project",
            project_name,
            "already absent",
        )
        return

    ctx.current_project_id = project_id

    # Safety check: refuse if VMs or volumes exist.
    errors = safety_check(ctx.conn, project_id, project_name)
    if errors:
        error_msg = "; ".join(errors)
        if ctx.dry_run:
            ctx.record(
                ActionStatus.FAILED,
                "teardown",
                project_name,
                f"safety check would block: {error_msg}",
            )
            return
        msg = f"Cannot tear down project {project_name!r}: {error_msg}"
        raise SafetyCheckError(msg)

    if ctx.dry_run:
        ctx.record(
            ActionStatus.DELETED,
            "teardown",
            project_name,
            f"would tear down project (id={project_id})",
        )
        return

    # Revoke all group role assignments before deletion.
    # Mark all assignments as absent so they get revoked.
    effective_cfg = augment_group_role_assignments(cfg)
    if effective_cfg.group_role_assignments:
        revoke_cfg = dataclasses.replace(
            effective_cfg,
            group_role_assignments=[
                dataclasses.replace(entry, state="absent") for entry in effective_cfg.group_role_assignments
            ],
        )
        ensure_group_role_assignments(revoke_cfg, project_id, ctx)

    teardown_project(cfg, project_id, ctx)


def reconcile(
    projects: list[ProjectConfig],
    all_projects: list[ProjectConfig],
    ctx: SharedContext,
    *,
    scopes: set[ReconcileScope] | None = None,
) -> list[Action]:
    """Phase 3: per-project resources, then shared federation mapping.

    Projects are reconciled **sequentially by design**.  Parallelization was
    assessed and rejected because:

    * We care for the OpenStack API. Therefore we want to limit/cap the
      amount of request we do.
      The practical throughput gain, sould be measurable, but the whole
      project does run quite fast compared to other solutions.
    * ``SharedContext`` (actions list, failed_projects, current_project_*
      fields) is not thread-safe — concurrent appends would require locking
      or per-project copies.
    * The underlying ``openstack.connection.Connection`` is not thread-safe;
      parallel use would need per-project connections or a connection pool.
    * Federation mapping reads all project configs and must run after every
      project has been reconciled, requiring a barrier/join step.
    * OpenStack API rate limits (Keystone, Neutron, Nova) cap the practical
      throughput gain, especially since idempotent operations mostly skip
      on steady-state runs.

    At the scale we test (~25 projects, ~7 s total) the overhead is negligible.
    If the project count grows significantly (100+), revisit with
    ``concurrent.futures.ThreadPoolExecutor`` and per-project context
    instances.

    Args:
        projects: Projects to reconcile (may be filtered by --project flag).
        all_projects: ALL project configs (needed for federation mapping
            even with --project filter).
        ctx: SharedContext with connection, dry_run flag, etc.
        scopes: When ``None`` (default), every handler runs.  When a
            non-empty set of :class:`ReconcileScope` values, only matching
            handlers execute for ``present``-state projects.  ``locked``
            and ``absent`` states always run their full pipeline.

    Returns:
        List of all actions taken (same as ``ctx.actions``).
    """
    # Create Keystone groups needed by group-mode federation before
    # per-project reconciliation (groups must exist before role assignment).
    if scopes is None or ReconcileScope.KEYSTONE_GROUPS in scopes:
        ctx.current_project_name = _PHASE_KEYSTONE_GROUPS
        try:
            ensure_keystone_groups(all_projects, ctx)
        except Exception as exc:
            logger.exception("Failed to ensure Keystone groups: %s", exc)
            ctx.failed_projects.append(_PHASE_KEYSTONE_GROUPS)

    for cfg in projects:
        project_name: str = cfg.name
        state: str = cfg.state
        logger.info("Reconciling project: %s (state=%s)", project_name, state)
        ctx.current_project_name = project_name

        try:
            if state == "present":
                _reconcile_present(cfg, ctx, scopes=scopes)
            elif state == "locked":
                _reconcile_locked(cfg, ctx)
            elif state == "absent":
                _reconcile_absent(cfg, ctx)
            else:
                logger.error("Unknown state %r for project %s", state, project_name)
                ctx.failed_projects.append(project_name)
                continue
        except Exception as exc:
            logger.exception(
                "Failed to reconcile project %s (%s): %s",
                project_name,
                type(exc).__name__,
                exc,
            )
            logger.debug("Project config at time of failure: %s", cfg)
            ctx.failed_projects.append(project_name)
            continue

        # Persist metadata on successful reconciliation.
        if not ctx.dry_run and ctx.state_store is not None and cfg.state_key:
            ctx.state_store.save(
                cfg.state_key,
                ["metadata", "last_reconciled_at"],
                datetime.now(UTC).isoformat(),
            )
            ctx.state_store.save(
                cfg.state_key,
                ["metadata", "last_reconciled_state"],
                str(state),
            )

    # Federation mapping is a shared resource built from ALL projects,
    # regardless of the --project filter.
    if scopes is None or ReconcileScope.FEDERATION in scopes:
        ctx.current_project_id = ""
        ctx.current_project_name = ""
        logger.info("Reconciling federation mapping")
        try:
            ensure_federation_mapping(all_projects, ctx)
        except Exception as exc:
            logger.exception(
                "Failed to reconcile federation mapping (%s): %s",
                type(exc).__name__,
                exc,
            )
            ctx.failed_projects.append(_PHASE_FEDERATION)

    return ctx.actions
