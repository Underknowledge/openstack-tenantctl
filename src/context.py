"""Context-building helpers for shared-resource resolution.

Discovers external networks, resolves federation mappings, and loads
static mapping files.  Used by both the CLI entry-point (``main.py``)
and the future library API (``TenantCtl``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import openstack.connection
import openstack.exceptions
import openstack.resource

from src.utils import ProvisionerError, identity_v3, retry

if TYPE_CHECKING:
    from src.models import ProjectConfig
    from src.models.defaults import DefaultsConfig

logger = logging.getLogger(__name__)


@retry()
def discover_external_networks(
    conn: openstack.connection.Connection,
) -> list[openstack.resource.Resource]:
    """List all networks marked as external (router:external=True)."""
    return list(conn.network.networks(**{"router:external": True}))


@retry()
def get_federation_mapping(
    conn: openstack.connection.Connection,
    mapping_id: str,
) -> openstack.resource.Resource:
    """Fetch an existing federation mapping."""
    logger.info("Fetching federation mapping '%s'...", mapping_id)
    result: openstack.resource.Resource = identity_v3(conn).get_mapping(mapping_id)
    logger.info("Successfully fetched federation mapping '%s'", mapping_id)
    return result


def build_external_network_map(
    conn: openstack.connection.Connection,
) -> dict[str, str]:
    """Discover all external networks and build a name→id / id→id map.

    Returns:
        Dictionary mapping network name → id and id → id for all external
        networks.  Enables O(1) lookup by either name or UUID.
    """
    external_nets = discover_external_networks(conn)
    net_map: dict[str, str] = {}
    for net in external_nets:
        net_id = str(net.id)
        net_map[str(net.name)] = net_id
        net_map[net_id] = net_id
    return net_map


def resolve_default_external_network(
    net_map: dict[str, str],
    defaults: DefaultsConfig,
) -> str:
    """Pick the default external network from the pre-built map.

    Returns:
        External network ID or empty string if not resolvable.
    """
    configured_name = defaults.external_network_name
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
            "Multiple external networks found [%s] — set 'external_network_name'" " in defaults.yaml to pick one",
            names,
        )
    else:
        logger.warning("No external networks found")

    return ""


def load_static_mapping_files(
    config_dir: str,
    patterns: tuple[str, ...],
) -> list:
    """Load and concatenate static federation mapping rules from glob patterns.

    Each pattern is resolved relative to *config_dir* via ``Path.glob()``.
    Matched files are sorted for deterministic ordering and their JSON
    contents are concatenated into a single list.

    Returns an empty list when *patterns* is empty.
    """
    if not patterns:
        return []

    all_rules: list = []
    base = Path(config_dir)
    for pattern in patterns:
        matched = sorted(base.glob(pattern))
        if not matched:
            logger.warning("No files matched static mapping pattern '%s'", pattern)
            continue
        for path in matched:
            data = json.loads(path.read_text(encoding="utf-8"))
            all_rules.extend(data)
            logger.info("Loaded static mapping rules from %s", path)
    return all_rules


def resolve_federation_context(
    conn: openstack.connection.Connection,
    config_dir: str | None,
    defaults: DefaultsConfig,
    all_projects: list[ProjectConfig],
) -> tuple[list, bool, list]:
    """Resolve federation mapping and static rules.

    Args:
        conn: OpenStack connection.
        config_dir: Path to the config directory.  May be ``None`` when
            projects are injected directly (no YAML config dir).
        defaults: Pipeline-level defaults.
        all_projects: All loaded project configs.

    Returns:
        (current_mapping_rules, mapping_exists, static_mapping_rules) tuple

    Raises:
        ProvisionerError: If static mapping file patterns are configured but
            *config_dir* is ``None`` (no filesystem to resolve them against).
    """
    logger.info("Resolving federation context...")
    mapping_id = defaults.federation_mapping_id or None
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
            mapping = get_federation_mapping(conn, mapping_id)
            current_rules = mapping.rules
            mapping_exists = True
            logger.info("Loaded existing federation mapping '%s'", mapping_id)
        except openstack.exceptions.NotFoundException:
            logger.info(
                "Federation mapping '%s' does not exist yet; will create on first push",
                mapping_id,
            )

    patterns = defaults.federation_static_mapping_files
    if config_dir is None and patterns:
        msg = (
            "federation_static_mapping_files are configured but no config_dir "
            "is available to resolve them; use TenantCtl.from_config_dir() "
            "or remove the static mapping file patterns from defaults"
        )
        raise ProvisionerError(msg)

    # config_dir is guaranteed non-None here: the guard above raises when
    # config_dir is None and patterns is non-empty, and load_static_mapping_files
    # returns [] immediately when patterns is empty.
    static_rules = load_static_mapping_files(config_dir if config_dir is not None else "", patterns)

    return current_rules, mapping_exists, static_rules
