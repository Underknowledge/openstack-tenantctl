"""Example: Build SharedContext manually for custom integrations.

Shows how to use context-building utilities for operators who need
full control over external network resolution and context setup.
"""

from __future__ import annotations

import openstack

from src import (
    DefaultsConfig,
    SharedContext,
    build_external_network_map,
    resolve_default_external_network,
    resolve_external_subnet,
)


def setup_custom_context(defaults_dict: dict):
    """Build SharedContext with custom external network logic."""
    conn = openstack.connect(cloud="mycloud")

    # Discover external networks
    net_map = build_external_network_map(conn)
    print(f"Found external networks: {list(net_map.keys())}")

    # Resolve default external network
    defaults = DefaultsConfig.from_dict(defaults_dict)
    ext_net_id = resolve_default_external_network(net_map, defaults)

    # Resolve subnet
    ext_subnet_id = resolve_external_subnet(conn, ext_net_id, defaults.external_network_subnet)

    # Build context
    ctx = SharedContext(
        conn=conn,
        dry_run=False,
        external_net_id=ext_net_id,
        external_subnet_id=ext_subnet_id,
        external_network_map=net_map,
    )

    return ctx
