"""Config resolution and enrichment for OpenStack provisioner.

Expands security-group presets into full rule dicts, resolves ``{name}``
placeholders, and auto-populates subnet defaults from CIDR.
"""

from __future__ import annotations

import copy
import ipaddress
import logging
from typing import Any

logger = logging.getLogger(__name__)

PREDEFINED_RULES: dict[str, dict[str, str | int]] = {
    "SSH": {
        "direction": "ingress",
        "protocol": "tcp",
        "port_range_min": 22,
        "port_range_max": 22,
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow SSH",
    },
    "HTTP": {
        "direction": "ingress",
        "protocol": "tcp",
        "port_range_min": 80,
        "port_range_max": 80,
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow HTTP",
    },
    "HTTPS": {
        "direction": "ingress",
        "protocol": "tcp",
        "port_range_min": 443,
        "port_range_max": 443,
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow HTTPS",
    },
    "ICMP": {
        "direction": "ingress",
        "protocol": "icmp",
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow ICMP",
    },
    "All ICMP": {
        "direction": "ingress",
        "protocol": "icmp",
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow ICMP",
    },
    "All TCP": {
        "direction": "ingress",
        "protocol": "tcp",
        "port_range_min": 1,
        "port_range_max": 65535,
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow all TCP",
    },
    "All UDP": {
        "direction": "ingress",
        "protocol": "udp",
        "port_range_min": 1,
        "port_range_max": 65535,
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow all UDP",
    },
    "DNS": {
        "direction": "ingress",
        "protocol": "udp",
        "port_range_min": 53,
        "port_range_max": 53,
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow DNS",
    },
    "RDP": {
        "direction": "ingress",
        "protocol": "tcp",
        "port_range_min": 3389,
        "port_range_max": 3389,
        "remote_ip_prefix": "0.0.0.0/0",
        "description": "Allow RDP",
    },
}


def replace_placeholders(obj: Any, name: str) -> Any:
    """Recursively replace ``{name}`` placeholders in all string values."""
    if isinstance(obj, str):
        return obj.format(name=name)
    if isinstance(obj, dict):
        return {k: replace_placeholders(v, name) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_placeholders(item, name) for item in obj]
    return obj


def expand_security_group_rules(project: dict[str, Any], errors: list[str]) -> None:
    """Expand preset names in ``security_group.rules`` to full rule dicts.

    Handles three rule formats:
    1. String (e.g. ``"SSH"``) — looked up in ``PREDEFINED_RULES``
    2. Dict with ``rule`` key — preset used as base, explicit fields override
    3. Dict without ``rule`` key — left as-is (backward compatible)

    Unknown preset names are appended to *errors*.
    """
    sg = project.get("security_group")
    if not isinstance(sg, dict):
        return
    rules = sg.get("rules")
    if not isinstance(rules, list):
        return

    project_label = project.get("name", "<unknown>")
    expanded: list[dict[str, Any]] = []

    for idx, entry in enumerate(rules):
        if isinstance(entry, str):
            preset = PREDEFINED_RULES.get(entry)
            if preset is None:
                errors.append(
                    f"{project_label}: security_group.rules[{idx}] unknown preset '{entry}'"
                )
                continue
            expanded.append(copy.deepcopy(preset))
        elif isinstance(entry, dict):
            preset_name = entry.get("rule")
            if preset_name is not None:
                preset = PREDEFINED_RULES.get(preset_name)
                if preset is None:
                    errors.append(
                        f"{project_label}: security_group.rules[{idx}] "
                        f"unknown preset '{preset_name}'"
                    )
                    continue
                merged_rule = copy.deepcopy(preset)
                for key, value in entry.items():
                    if key != "rule":
                        merged_rule[key] = value
                expanded.append(merged_rule)
            else:
                expanded.append(entry)
        else:
            errors.append(
                f"{project_label}: security_group.rules[{idx}] "
                f"must be a string or mapping, got {type(entry).__name__}"
            )

    sg["rules"] = expanded


def auto_populate_subnet_defaults(project: dict[str, Any]) -> None:
    """Auto-populate gateway_ip and allocation_pools from CIDR if not specified.

    Follows OpenStack defaults:
    - gateway_ip: First usable IP in the subnet
    - allocation_pools: All IPs except gateway
    """
    subnet = project.get("network", {}).get("subnet")
    if subnet is None:
        return  # No subnet configured
    cidr_str = subnet.get("cidr")

    if not isinstance(cidr_str, str):
        return  # CIDR validation will catch this later

    try:
        network = ipaddress.ip_network(cidr_str, strict=False)
    except ValueError:
        return  # CIDR validation will catch this later

    # Auto-populate gateway_ip if not specified
    if "gateway_ip" not in subnet:
        # Use first usable IP (network address + 1)
        gateway_ip = network.network_address + 1
        subnet["gateway_ip"] = str(gateway_ip)
        logger.debug("Auto-calculated gateway_ip=%s from CIDR %s", gateway_ip, cidr_str)

    # Auto-populate allocation_pools if not specified
    if "allocation_pools" not in subnet:
        # Use all IPs except gateway
        gateway_str = subnet.get("gateway_ip")
        try:
            gateway = ipaddress.ip_address(gateway_str) if gateway_str else None
        except ValueError:
            gateway = None  # Validation will catch this later

        # Calculate pool: all usable IPs except gateway
        all_hosts = list(network.hosts())
        if all_hosts:
            if gateway and gateway in all_hosts:
                # Remove gateway from pool
                all_hosts.remove(gateway)

            if all_hosts:
                # Create pool from remaining IPs
                start_ip = all_hosts[0]
                end_ip = all_hosts[-1]
                subnet["allocation_pools"] = [{"start": str(start_ip), "end": str(end_ip)}]
                logger.debug(
                    "Auto-calculated allocation_pools=[%s-%s] from CIDR %s",
                    start_ip,
                    end_ip,
                    cidr_str,
                )
