"""Library API for tenantctl — usable without argparse.

Wraps the three-phase pipeline (load → connect/resolve → reconcile) in a
``TenantCtl`` class.  The CLI entry-point (``main.py``) delegates to
this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import openstack
import openstack.connection

from src import __version__
from src.config_loader import load_all_projects
from src.context import (
    build_external_network_map,
    resolve_default_external_network,
    resolve_federation_context,
)
from src.models.defaults import DefaultsConfig
from src.reconciler import ReconcileScope, reconcile, validate_scopes
from src.state_store import YamlFileStateStore
from src.utils import (
    Action,
    ProvisionerError,
    SharedContext,
    resolve_external_subnet,
    retry,
)

if TYPE_CHECKING:
    from src.models import ProjectConfig
    from src.state_store import StateStore

__all__ = ["RunResult", "TenantCtl"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    """Immutable result of a ``TenantCtl.run()`` invocation."""

    actions: list[Action] = field(default_factory=list)
    failed_projects: list[str] = field(default_factory=list)
    had_connection: bool = False


class TenantCtl:
    """High-level API for the tenantctl provisioning pipeline.

    Owns the three phases: load config → connect/resolve → reconcile.
    """

    def __init__(
        self,
        *,
        cloud: str | None = None,
        state_store: StateStore | None = None,
        config_dir: str | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._cloud = cloud
        self._state_store = state_store

    @classmethod
    def from_config_dir(
        cls,
        config_dir: str,
        *,
        cloud: str | None = None,
        state_store: StateStore | None = None,
    ) -> TenantCtl:
        """Create a ``TenantCtl`` backed by a YAML config directory.

        When *state_store* is ``None`` a :class:`YamlFileStateStore` rooted at
        ``<config_dir>/state`` is created automatically.
        """
        if state_store is None:
            state_store = YamlFileStateStore(Path(config_dir) / "state")
        return cls(cloud=cloud, state_store=state_store, config_dir=config_dir)

    @classmethod
    def from_cloud(
        cls,
        cloud: str | None = None,
        *,
        state_store: StateStore | None = None,
    ) -> TenantCtl:
        """Create a ``TenantCtl`` for programmatic use (no config directory).

        Use this constructor when projects will be supplied directly via
        ``run(projects=..., all_projects=...)`` rather than loaded from YAML
        files on disk.

        For library consumers that manage state externally (CRM database,
        REST API, etc.), pass an :class:`~src.state_store.InMemoryStateStore`
        as *state_store*.  Pre-seed it with current state, then call
        :meth:`InMemoryStateStore.snapshot` after ``run()`` to bulk-read
        updated state for write-back.

        Args:
            cloud: Named cloud from ``clouds.yaml``, or ``None`` for the
                default cloud.
            state_store: Optional state store for tracking reconciliation
                state.  Use :class:`~src.state_store.InMemoryStateStore` for
                programmatic use or ``None`` when state tracking is not
                needed (e.g. dry-run-only callers).
        """
        return cls(cloud=cloud, state_store=state_store)

    @retry()
    def _connect(self) -> openstack.connection.Connection:
        """Create an OpenStack connection with retry on transient failures."""
        conn = openstack.connect(
            cloud=self._cloud,
            timeout=60,
            app_name="tenantctl",
            app_version=__version__,
        )
        # Force auth/discovery now so connection errors surface here, not later.
        conn.authorize()
        return conn

    def _load_and_filter_projects(
        self,
        project_filter: str | None,
    ) -> tuple[list[ProjectConfig], list[ProjectConfig], DefaultsConfig]:
        """Load all projects from config and filter if requested.

        Returns:
            (filtered_projects, all_projects, defaults) tuple

        Raises:
            ConfigValidationError: If configuration validation fails.
            ProvisionerError: If filtered project not found.
        """
        if self._config_dir is None:
            msg = "config_dir is required for YAML-based project loading; use from_config_dir()"
            raise ProvisionerError(msg)

        logger.info("Phase 1: Validating configuration")
        all_projects, defaults = load_all_projects(self._config_dir, state_store=self._state_store)

        if project_filter:
            filtered = [p for p in all_projects if p.name == project_filter]
            if not filtered:
                msg = f"project '{project_filter}' not found in configuration"
                logger.error(msg)
                raise ProvisionerError(msg)
            return filtered, all_projects, defaults

        return all_projects, all_projects, defaults

    def _setup_context(
        self,
        defaults: DefaultsConfig,
        all_projects: list[ProjectConfig],
        *,
        connection: openstack.connection.Connection | None = None,
        dry_run: bool,
        offline: bool = False,
    ) -> tuple[SharedContext, bool]:
        """Create SharedContext with connection and resolved resources.

        Args:
            defaults: Pipeline-level defaults.
            all_projects: All loaded project configs.
            connection: Optional pre-existing OpenStack connection. If provided,
                TenantCtl will use it but NOT close it (caller retains ownership).
            dry_run: If True, read-only mode.
            offline: If True (with dry_run), skip OpenStack connection.

        Returns:
            (SharedContext, owns_connection) - second element indicates whether
            TenantCtl created the connection and should close it.

        Raises:
            ProvisionerError: If connection or resource resolution fails.
        """
        owns_connection = False

        if connection is not None:
            # User provided connection - use it but don't close it
            conn = connection
            if dry_run and not offline:
                logger.info("Phase 2: Using provided connection for dry-run read-only operations")
            elif not dry_run:
                logger.info("Phase 2: Using provided connection")
        elif dry_run and offline:
            logger.info("Phase 2: Dry-run mode (offline) — skipping OpenStack connection")
            conn = None
        else:
            # TenantCtl creates connection - we'll close it
            if not dry_run and offline:
                logger.warning("--offline is only meaningful with --dry-run, ignoring")

            if dry_run:
                logger.info("Phase 2: Dry-run mode — connecting to OpenStack for read-only operations")
            else:
                logger.info("Phase 2: Connecting to OpenStack and resolving shared resources")

            try:
                conn = self._connect()
                owns_connection = True
            except Exception as exc:
                logger.exception("Failed to connect to OpenStack after retries")
                msg = "Failed to connect to OpenStack"
                raise ProvisionerError(msg) from exc

        # In dry-run mode, set state_store to None to prevent any state writes
        ctx_state_store = None if dry_run else self._state_store
        ctx = SharedContext(conn=conn, dry_run=dry_run, state_store=ctx_state_store)

        # Skip resource resolution for offline dry-run mode
        if conn is None:
            return ctx, owns_connection

        try:
            net_map = build_external_network_map(conn)
            ctx.external_network_map = net_map
            ctx.external_net_id = resolve_default_external_network(net_map, defaults)
            if not ctx.external_net_id:
                logger.warning("No external network resolved — routers will be created without an external gateway")
        except Exception as exc:
            logger.exception("Failed to look up external network")
            msg = "Failed to look up external network"
            raise ProvisionerError(msg) from exc

        try:
            configured_subnet = defaults.external_network_subnet
            ctx.external_subnet_id = resolve_external_subnet(conn, ctx.external_net_id, configured_subnet)
            if ctx.external_net_id and not ctx.external_subnet_id:
                logger.warning(
                    "External network resolved but no subnet selected — "
                    "routers and FIPs will be created without subnet hints"
                )
        except Exception as exc:
            logger.exception("Failed to resolve external network subnet")
            msg = "Failed to resolve external network subnet"
            raise ProvisionerError(msg) from exc

        try:
            current_rules, mapping_exists, static_rules = resolve_federation_context(
                conn, self._config_dir, defaults, all_projects
            )
            ctx.current_mapping_rules = current_rules
            ctx.mapping_exists = mapping_exists
            ctx.static_mapping_rules = static_rules
        except Exception as exc:
            logger.exception("Failed to resolve federation mapping")
            msg = "Failed to resolve federation mapping"
            raise ProvisionerError(msg) from exc

        return ctx, owns_connection

    def run(
        self,
        *,
        connection: openstack.connection.Connection | None = None,
        project: str | None = None,
        projects: list[ProjectConfig] | None = None,
        all_projects: list[ProjectConfig] | None = None,
        defaults: DefaultsConfig | None = None,
        dry_run: bool = False,
        offline: bool = False,
        only: set[ReconcileScope] | None = None,
        auto_expand_deps: bool = False,
    ) -> RunResult:
        """Execute the full provisioning pipeline.

        Supports two modes:

        **YAML mode** (default): loads projects from ``config_dir``.
        Pass *project* to filter to a single project by name.

        **Direct-injection mode**: pass *projects* and *all_projects*
        explicitly, bypassing filesystem-based config loading.  Optionally
        pass *defaults*; when omitted an empty ``DefaultsConfig()`` is used.

        Args:
            connection: Optional pre-existing OpenStack connection. If provided,
                TenantCtl will use it but NOT close it (caller retains ownership).
                If None, TenantCtl creates and manages the connection internally.
                Mutually exclusive with ``offline=True``.
            project: Optional single-project name filter (YAML mode only).
            projects: Pre-built list of projects to reconcile (direct mode).
            all_projects: Complete project list for cross-project resolution.
                When using *only*, callers must still pass the **full**
                project list — federation mapping reads every project's
                config to build a single coherent Keystone mapping.
            defaults: Pipeline-level defaults.  Required only when using
                features that depend on defaults (e.g. external network).
            dry_run: If True, print planned actions without making changes.
            offline: If True (with dry_run), skip OpenStack connection.
            only: When ``None`` (default), every resource handler runs.
                Pass a non-empty set of :class:`ReconcileScope` values to
                restrict which handlers execute for ``present``-state
                projects.  ``locked`` and ``absent`` states always run
                their full pipeline regardless of this parameter.
            auto_expand_deps: When ``False`` (default), requesting a scope
                without its prerequisites raises :class:`ValueError`.
                When ``True``, missing prerequisite scopes are added
                automatically (e.g. ``{FIPS}`` becomes ``{FIPS, NETWORK}``).

        Returns:
            RunResult with actions, failed projects, and connection status.

        Raises:
            ConfigValidationError: If configuration validation fails.
            ProvisionerError: If a pipeline phase fails or arguments are
                invalid.
            ValueError: If *only* is an empty set or contains invalid
                scope names.
        """
        # --- Argument validation ---
        scopes = validate_scopes(only, auto_expand_deps=auto_expand_deps)

        if connection is not None and offline:
            msg = "'connection' and 'offline' are mutually exclusive"
            raise ProvisionerError(msg)

        if connection is not None and self._cloud is not None:
            logger.warning(
                "Using provided connection; ignoring cloud='%s' from constructor",
                self._cloud,
            )

        if project is not None and projects is not None:
            msg = "'project' (str filter) and 'projects' (list) are mutually exclusive"
            raise ProvisionerError(msg)

        if (projects is None) != (all_projects is None):
            msg = "'projects' and 'all_projects' must be provided together"
            raise ProvisionerError(msg)

        if defaults is not None and projects is None:
            msg = "'defaults' can only be used with 'projects'/'all_projects' (direct-injection mode)"
            raise ProvisionerError(msg)

        # --- Branch: direct-injection vs YAML mode ---
        if projects is not None:
            # Narrowing for mypy: all_projects is guaranteed non-None here by
            # the co-requirement check above.
            if all_projects is None:  # pragma: no cover
                msg = "all_projects must not be None when projects is provided"
                raise ProvisionerError(msg)
            projects_to_run = projects
            effective_all = all_projects
            effective_defaults = defaults if defaults is not None else DefaultsConfig()
        else:
            projects_to_run, effective_all, effective_defaults = self._load_and_filter_projects(project)

        ctx, owns_connection = self._setup_context(
            effective_defaults,
            effective_all,
            connection=connection,
            dry_run=dry_run,
            offline=offline,
        )

        logger.info("Phase 3: Reconciling resources")
        try:
            reconcile(projects_to_run, effective_all, ctx, scopes=scopes)
        finally:
            # Only close if we created it
            if owns_connection and ctx.conn is not None:
                ctx.conn.close()

        return RunResult(
            actions=ctx.actions,
            failed_projects=ctx.failed_projects,
            had_connection=ctx.conn is not None,
        )
