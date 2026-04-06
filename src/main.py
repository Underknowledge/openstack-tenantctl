"""CLI entry point for the OpenStack provisioner.

Runs three phases: validate -> connect/resolve -> reconcile.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import openstack
import openstack.connection
import openstack.resource

from src import __version__
from src.config_loader import ConfigValidationError, load_all_projects
from src.reconciler import reconcile
from src.state_store import YamlFileStateStore
from src.utils import (
    ActionStatus,
    ProvisionerError,
    SharedContext,
    resolve_external_subnet,
    retry,
    setup_logging,
)

if TYPE_CHECKING:
    from src.models import ProjectConfig

logger = logging.getLogger(__name__)


@retry()
def _connect(cloud: str | None = None) -> openstack.connection.Connection:
    """Create an OpenStack connection with retry on transient failures."""
    conn = openstack.connect(
        cloud=cloud,
        timeout=60,
        app_name="tenantctl",
        app_version=__version__,
    )
    # Force auth/discovery now so connection errors surface here, not later.
    conn.authorize()
    return conn


@retry()
def _discover_external_networks(
    conn: openstack.connection.Connection,
) -> list[openstack.resource.Resource]:
    """List all networks marked as external (router:external=True)."""
    return list(conn.network.networks(**{"router:external": True}))


@retry()
def _get_federation_mapping(
    conn: openstack.connection.Connection,
    mapping_id: str,
) -> openstack.resource.Resource:
    """Fetch an existing federation mapping."""
    logger.info("Fetching federation mapping '%s'...", mapping_id)
    result: openstack.resource.Resource = conn.identity.get_mapping(mapping_id)
    logger.info("Successfully fetched federation mapping '%s'", mapping_id)
    return result


def _load_and_filter_projects(
    config_dir: str,
    project_filter: str | None,
    state_store: YamlFileStateStore | None = None,
) -> tuple[list[ProjectConfig], list[ProjectConfig], dict]:
    """Load all projects from config and filter if requested.

    Returns:
        (filtered_projects, all_projects, defaults) tuple

    Raises:
        ConfigValidationError: If configuration validation fails.
        ProvisionerError: If filtered project not found.
    """
    logger.info("Phase 1: Validating configuration")
    all_projects, defaults = load_all_projects(config_dir, state_store=state_store)

    if project_filter:
        filtered = [p for p in all_projects if p.name == project_filter]
        if not filtered:
            msg = f"project '{project_filter}' not found in configuration"
            logger.error(msg)
            raise ProvisionerError(msg)
        return filtered, all_projects, defaults

    return all_projects, all_projects, defaults


def _build_external_network_map(
    conn: openstack.connection.Connection,
) -> dict[str, str]:
    """Discover all external networks and build a name→id / id→id map.

    Returns:
        Dictionary mapping network name → id and id → id for all external
        networks.  Enables O(1) lookup by either name or UUID.
    """
    external_nets = _discover_external_networks(conn)
    net_map: dict[str, str] = {}
    for net in external_nets:
        net_id = str(net.id)
        net_map[str(net.name)] = net_id
        net_map[net_id] = net_id
    return net_map


def _resolve_default_external_network(
    net_map: dict[str, str],
    defaults: dict,
) -> str:
    """Pick the default external network from the pre-built map.

    Returns:
        External network ID or empty string if not resolvable.
    """
    configured_name = defaults.get("external_network_name", "")
    if configured_name:
        net_id = net_map.get(configured_name)
        if net_id is None:
            logger.warning("Configured external network '%s' not found", configured_name)
            return ""
        logger.info("Resolved external network '%s' -> %s", configured_name, net_id)
        return net_id

    # Auto-select: only if exactly one external network exists.
    # The map contains both name→id and id→id entries, so unique IDs
    # tell us how many distinct networks there are.
    unique_ids = set(net_map.values())
    if len(unique_ids) == 1:
        net_id = next(iter(unique_ids))
        # Find the name entry (key != value means it's name→id)
        net_name = next((k for k, v in net_map.items() if k != v), net_id)
        logger.info(
            "Auto-discovered external network '%s' -> %s",
            net_name,
            net_id,
        )
        return net_id
    if len(unique_ids) > 1:
        names = ", ".join(k for k, v in net_map.items() if k != v)
        logger.warning(
            "Multiple external networks found [%s] — set 'external_network_name'"
            " in defaults.yaml to pick one",
            names,
        )
    else:
        logger.warning("No external networks found")

    return ""


def _resolve_federation_context(
    conn: openstack.connection.Connection,
    config_dir: str,
    defaults: dict,
    all_projects: list[ProjectConfig],
) -> tuple[list, bool, list]:
    """Resolve federation mapping and static rules.

    Returns:
        (current_mapping_rules, mapping_exists, static_mapping_rules) tuple
    """
    logger.info("Resolving federation context...")
    mapping_id = defaults.get("federation", {}).get("mapping_id")
    logger.info("Mapping ID from defaults: %s", mapping_id)
    if mapping_id is None:
        for proj in all_projects:
            federation_cfg = proj.federation
            if federation_cfg:
                mapping_id = federation_cfg.mapping_id
                if mapping_id:
                    break

    current_rules: list = []
    mapping_exists = False

    if mapping_id:
        try:
            mapping = _get_federation_mapping(conn, mapping_id)
            current_rules = mapping.rules
            mapping_exists = True
            logger.info("Loaded existing federation mapping '%s'", mapping_id)
        except openstack.exceptions.NotFoundException:
            logger.info(
                "Federation mapping '%s' does not exist yet; will create on first push",
                mapping_id,
            )

    static_path = Path(config_dir, "federation_static.json")
    static_rules = json.loads(static_path.read_text(encoding="utf-8"))
    logger.info("Loaded static mapping rules from %s", static_path)

    return current_rules, mapping_exists, static_rules


def _setup_context(
    config_dir: str,
    defaults: dict,
    all_projects: list[ProjectConfig],
    *,
    dry_run: bool,
    offline: bool = False,
    cloud: str | None = None,
    state_store: YamlFileStateStore | None = None,
) -> SharedContext:
    """Create SharedContext with connection and resolved resources.

    Returns:
        Initialized SharedContext

    Raises:
        ProvisionerError: If connection or resource resolution fails.
    """
    if dry_run and offline:
        logger.info("Phase 2: Dry-run mode (offline) — skipping OpenStack connection")
        return SharedContext(conn=None, dry_run=True, state_store=None)

    if not dry_run and offline:
        logger.warning("--offline is only meaningful with --dry-run, ignoring")

    if dry_run:
        logger.info("Phase 2: Dry-run mode — connecting to OpenStack for read-only operations")
    else:
        logger.info("Phase 2: Connecting to OpenStack and resolving shared resources")

    try:
        conn = _connect(cloud=cloud)
    except Exception as exc:
        logger.exception("Failed to connect to OpenStack after retries")
        msg = "Failed to connect to OpenStack"
        raise ProvisionerError(msg) from exc

    # In dry-run mode, set state_store to None to prevent any state writes
    ctx_state_store = None if dry_run else state_store
    ctx = SharedContext(conn=conn, dry_run=dry_run, state_store=ctx_state_store)

    try:
        net_map = _build_external_network_map(conn)
        ctx.external_network_map = net_map
        ctx.external_net_id = _resolve_default_external_network(net_map, defaults)
        if not ctx.external_net_id:
            logger.warning(
                "No external network resolved — routers will be created without an external gateway"
            )
    except Exception as exc:
        logger.exception("Failed to look up external network")
        msg = "Failed to look up external network"
        raise ProvisionerError(msg) from exc

    try:
        configured_subnet = defaults.get("external_network_subnet", "")
        ctx.external_subnet_id = resolve_external_subnet(
            conn, ctx.external_net_id, configured_subnet
        )
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
        current_rules, mapping_exists, static_rules = _resolve_federation_context(
            conn, config_dir, defaults, all_projects
        )
        ctx.current_mapping_rules = current_rules
        ctx.mapping_exists = mapping_exists
        ctx.static_mapping_rules = static_rules
    except Exception as exc:
        logger.exception("Failed to resolve federation mapping")
        msg = "Failed to resolve federation mapping"
        raise ProvisionerError(msg) from exc

    return ctx


def _print_summary(ctx: SharedContext, *, dry_run: bool) -> int:
    """Print action summary and return exit code.

    Returns:
        0 on success, 1 if any projects failed
    """
    if dry_run:
        if ctx.conn is not None:
            print("\n--- Dry-run: planned changes (live cloud reads) ---")
        else:
            print("\n--- Dry-run: planned changes (offline) ---")

    for action in ctx.actions:
        parts = [f"  [{action.status.value:>7}]"]
        if action.project_id or action.project_name:
            parts.append(f"{action.project_id} {action.project_name}")
        parts.append(f"{action.resource_type}:")
        if action.name:
            parts[-1] += f" {action.name}"
        if action.details:
            parts.append(f"({action.details})")
        print(" ".join(parts))

    counts: dict[ActionStatus, int] = dict.fromkeys(ActionStatus, 0)
    for action in ctx.actions:
        counts[action.status] += 1

    created = counts[ActionStatus.CREATED]
    updated = counts[ActionStatus.UPDATED]
    skipped = counts[ActionStatus.SKIPPED]
    failed = counts[ActionStatus.FAILED]
    deleted = counts[ActionStatus.DELETED]

    print(
        f"\n{created} created, {updated} updated, {deleted} deleted,"
        f" {skipped} skipped, {failed} failed"
    )

    if ctx.failed_projects:
        print(f"Failed projects: {', '.join(ctx.failed_projects)}", file=sys.stderr)
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point. Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(
        description="Provision OpenStack projects from declarative config.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tenantctl {__version__}",
    )
    parser.add_argument(
        "--config-dir",
        default="config/",
        help="Path to config directory (default: config/)",
    )
    parser.add_argument(
        "--os-cloud",
        default=None,
        help="Named cloud from clouds.yaml",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Filter to a single project name",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print planned actions without making changes",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=False,
        help="Skip OpenStack connection in dry-run mode (no live cloud reads)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (repeat for more: -v=INFO, -vv=DEBUG)",
    )
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    # Create state store for observed runtime state.
    state_store = YamlFileStateStore(Path(args.config_dir) / "state")

    # Phase 1: Load and validate configuration
    try:
        projects_to_run, all_projects, defaults = _load_and_filter_projects(
            args.config_dir, args.project, state_store=state_store
        )
    except ConfigValidationError as exc:
        for err in exc.errors:
            logger.error("  %s", err)
        return 1
    except ProvisionerError:
        return 1

    # Phase 2: Connect to OpenStack and resolve shared resources
    try:
        ctx = _setup_context(
            args.config_dir,
            defaults,
            all_projects,
            dry_run=args.dry_run,
            offline=args.offline,
            cloud=args.os_cloud,
            state_store=state_store,
        )
    except ProvisionerError:
        return 1

    # Phase 3: Reconcile resources
    logger.info("Phase 3: Reconciling resources")
    try:
        reconcile(projects_to_run, all_projects, ctx)
    finally:
        if ctx.conn is not None:
            ctx.conn.close()

    # Print summary and return exit code
    return _print_summary(ctx, dry_run=args.dry_run)


def cli() -> None:
    """Console script entry point."""
    sys.exit(main())
