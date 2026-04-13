"""Project configuration and state models."""

from __future__ import annotations

import dataclasses
import re
from enum import StrEnum
from typing import Any

from src.models.access import GroupRoleAssignment
from src.models.federation import FederationConfig
from src.models.network import NetworkConfig
from src.models.quotas import QuotaConfig
from src.models.security import SecurityGroupConfig
from src.models.state import (
    FipEntry,
    ReleasedFipEntry,
    ReleasedRouterIpEntry,
    RouterIpEntry,
)

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_ -]{0,63}$")
_RESOURCE_PREFIX_RE = re.compile(r"^[a-z0-9]+$")
_VALID_STATES: set[str] = {"present", "locked", "absent"}


class ProjectState(StrEnum):
    """Desired lifecycle state for a project."""

    PRESENT = "present"
    LOCKED = "locked"
    ABSENT = "absent"


@dataclasses.dataclass(frozen=True)
class ProjectConfig:
    """Complete typed configuration for a single OpenStack project.

    Replaces the ``dict[str, Any]`` config dicts used throughout the codebase.
    All fields have defaults matching the existing dict-based defaults so that
    ``ProjectConfig.from_dict()`` produces identical behavior.
    """

    name: str
    resource_prefix: str

    description: str = ""
    enabled: bool = True
    state: ProjectState = ProjectState.PRESENT
    domain_id: str = "default"
    domain: str | None = None
    reclaim_floating_ips: bool = False
    reclaim_router_ips: bool = False
    track_fip_changes: bool = False
    external_network_name: str = ""
    external_network_subnet: str = ""

    network: NetworkConfig | None = None
    quotas: QuotaConfig | None = None
    security_group: SecurityGroupConfig | None = None
    federation: FederationConfig | None = None
    group_role_assignments: list[GroupRoleAssignment] = dataclasses.field(default_factory=list)

    config_path: str = ""
    state_key: str = ""

    preallocated_fips: list[FipEntry] = dataclasses.field(default_factory=list)
    released_fips: list[ReleasedFipEntry] = dataclasses.field(default_factory=list)
    router_ips: list[RouterIpEntry] = dataclasses.field(default_factory=list)
    released_router_ips: list[ReleasedRouterIpEntry] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input.

        Handles both underscore-prefixed (``_state_key``, ``_config_path``)
        and plain keys for state metadata.
        """
        network_data = data.get("network")
        quotas_data = data.get("quotas")
        sg_data = data.get("security_group")
        federation_data = data.get("federation")
        gra_data = data.get("group_role_assignments")

        return cls(
            name=data["name"],
            resource_prefix=data["resource_prefix"],
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            state=ProjectState(data.get("state", "present")),
            domain_id=data.get("domain_id") or "default",
            domain=data.get("domain"),
            reclaim_floating_ips=data.get("reclaim_floating_ips", False),
            reclaim_router_ips=data.get("reclaim_router_ips", False),
            track_fip_changes=data.get("track_fip_changes", False),
            external_network_name=data.get("external_network_name", ""),
            external_network_subnet=data.get("external_network_subnet", ""),
            network=(NetworkConfig.from_dict(network_data) if isinstance(network_data, dict) else None),
            quotas=(QuotaConfig.from_dict(quotas_data) if isinstance(quotas_data, dict) else None),
            security_group=(SecurityGroupConfig.from_dict(sg_data) if isinstance(sg_data, dict) else None),
            federation=(FederationConfig.from_dict(federation_data) if isinstance(federation_data, dict) else None),
            group_role_assignments=(
                [GroupRoleAssignment.from_dict(e) for e in gra_data] if isinstance(gra_data, list) else []
            ),
            config_path=data.get("_config_path", data.get("config_path", "")),
            state_key=data.get("_state_key", data.get("state_key", "")),
            preallocated_fips=[FipEntry.from_dict(f) for f in data.get("preallocated_fips", [])],
            released_fips=[ReleasedFipEntry.from_dict(f) for f in data.get("released_fips", [])],
            router_ips=[RouterIpEntry.from_dict(r) for r in data.get("router_ips", [])],
            released_router_ips=[ReleasedRouterIpEntry.from_dict(r) for r in data.get("released_router_ips", [])],
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> ProjectConfig | None:
        """Validate *data* and return a ``ProjectConfig``, or ``None`` if broken.

        Validates all fields, delegates to nested ``validate()`` methods, and
        accumulates errors into *errors*.  Returns a constructed instance when
        possible (even with non-fatal errors) so the caller can proceed with
        further cross-project checks.
        """
        state = data.get("state", "present")

        # --- State validation ---
        if state not in _VALID_STATES:
            errors.append(f"{label}: state must be one of {sorted(_VALID_STATES)}, got {state!r}")

        # --- Required: name ---
        name = data.get("name")
        if not isinstance(name, str) or len(name) == 0:
            errors.append(f"{label}: missing required field 'name'")
            return None

        # --- Required: resource_prefix ---
        prefix = data.get("resource_prefix")
        if prefix is None:
            errors.append(f"{label}: missing required field 'resource_prefix'")

        # --- Name format ---
        if isinstance(name, str) and not _NAME_RE.match(name):
            errors.append(
                f"{label}: name '{name}' is not a valid OpenStack identifier " f"(must match {_NAME_RE.pattern})"
            )

        # --- Resource prefix format ---
        if isinstance(prefix, str) and not _RESOURCE_PREFIX_RE.match(prefix):
            errors.append(
                f"{label}: resource_prefix '{prefix}' is invalid " f"(must match {_RESOURCE_PREFIX_RE.pattern})"
            )

        # --- Domain format ---
        domain_id = data.get("domain_id")
        domain = data.get("domain")

        if domain_id is not None:
            if not isinstance(domain_id, str):
                errors.append(f"{label}: domain_id must be a string, got {type(domain_id).__name__}")
            elif len(domain_id) == 0:
                errors.append(f"{label}: domain_id cannot be an empty string")

        if domain is not None:
            if not isinstance(domain, str):
                errors.append(f"{label}: domain must be a string, got {type(domain).__name__}")
            elif len(domain) == 0:
                errors.append(f"{label}: domain cannot be an empty string")

        # --- Group role assignments (validated even for absent — used in teardown) ---
        gra_data = data.get("group_role_assignments")
        validated_gras: list[GroupRoleAssignment] = []
        if gra_data is not None:
            if not isinstance(gra_data, list):
                errors.append(f"{label}: group_role_assignments must be a list, got {type(gra_data).__name__}")
            else:
                for idx, entry in enumerate(gra_data):
                    entry_label = f"{label}: group_role_assignments[{idx}]"
                    if not isinstance(entry, dict):
                        errors.append(f"{entry_label} must be a mapping, got {type(entry).__name__}")
                        continue
                    gra = GroupRoleAssignment.validate(entry, errors, entry_label)
                    if gra is not None:
                        validated_gras.append(gra)

        # --- reclaim_floating_ips must be boolean ---
        reclaim = data.get("reclaim_floating_ips")
        if reclaim is not None and not isinstance(reclaim, bool):
            errors.append(f"{label}: 'reclaim_floating_ips' must be a boolean, got {reclaim!r}")

        # --- reclaim_router_ips must be boolean ---
        reclaim_rtr = data.get("reclaim_router_ips")
        if reclaim_rtr is not None and not isinstance(reclaim_rtr, bool):
            errors.append(f"{label}: 'reclaim_router_ips' must be a boolean, got {reclaim_rtr!r}")

        # --- track_fip_changes must be boolean ---
        track_fip = data.get("track_fip_changes")
        if track_fip is not None and not isinstance(track_fip, bool):
            errors.append(f"{label}: 'track_fip_changes' must be a boolean, got {track_fip!r}")

        # --- For absent state, skip network/quota/SG/federation validation ---
        validated_network: NetworkConfig | None = None
        validated_quotas: QuotaConfig | None = None
        validated_sg: SecurityGroupConfig | None = None
        validated_federation: FederationConfig | None = None

        if state != "absent":
            # --- Network validation ---
            network_data = data.get("network")
            if isinstance(network_data, dict):
                validated_network = NetworkConfig.validate(network_data, errors, label)
            else:
                # network.subnet.cidr is required for non-absent projects
                errors.append(f"{label}: missing required field 'network.subnet.cidr'")

            # --- Quota validation ---
            quotas_data = data.get("quotas")
            if isinstance(quotas_data, dict):
                validated_quotas = QuotaConfig.validate(quotas_data, errors, label)

            # --- Security group validation ---
            sg_data = data.get("security_group")
            if isinstance(sg_data, dict):
                validated_sg = SecurityGroupConfig.validate(sg_data, errors, label)

            # --- Federation validation ---
            federation_data = data.get("federation")
            if isinstance(federation_data, dict):
                validated_federation = FederationConfig.validate(federation_data, errors, label)
        else:
            # Absent projects: construct nested models without validation
            network_data = data.get("network")
            if isinstance(network_data, dict) and isinstance(network_data.get("subnet"), dict):
                validated_network = NetworkConfig.from_dict(network_data)
            quotas_data = data.get("quotas")
            if isinstance(quotas_data, dict):
                validated_quotas = QuotaConfig.from_dict(quotas_data)
            sg_data = data.get("security_group")
            if isinstance(sg_data, dict):
                validated_sg = SecurityGroupConfig.from_dict(sg_data)
            federation_data = data.get("federation")
            if isinstance(federation_data, dict):
                validated_federation = FederationConfig.from_dict(federation_data)

        # --- Construct the ProjectConfig ---
        try:
            state_enum = ProjectState(state) if state in _VALID_STATES else ProjectState.PRESENT
        except ValueError:
            state_enum = ProjectState.PRESENT

        return cls(
            name=name,
            resource_prefix=prefix if isinstance(prefix, str) else "",
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            state=state_enum,
            domain_id=data.get("domain_id") or "default",
            domain=data.get("domain"),
            reclaim_floating_ips=(reclaim if isinstance(reclaim, bool) else False),
            reclaim_router_ips=(reclaim_rtr if isinstance(reclaim_rtr, bool) else False),
            track_fip_changes=(track_fip if isinstance(track_fip, bool) else False),
            external_network_name=data.get("external_network_name", ""),
            external_network_subnet=data.get("external_network_subnet", ""),
            network=validated_network,
            quotas=validated_quotas,
            security_group=validated_sg,
            federation=validated_federation,
            group_role_assignments=validated_gras,
            config_path=data.get("_config_path", data.get("config_path", "")),
            state_key=data.get("_state_key", data.get("state_key", "")),
            preallocated_fips=[FipEntry.from_dict(f) for f in data.get("preallocated_fips", [])],
            released_fips=[ReleasedFipEntry.from_dict(f) for f in data.get("released_fips", [])],
            router_ips=[RouterIpEntry.from_dict(r) for r in data.get("router_ips", [])],
            released_router_ips=[ReleasedRouterIpEntry.from_dict(r) for r in data.get("released_router_ips", [])],
        )
