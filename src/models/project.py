"""Project configuration and state models."""

from __future__ import annotations

import dataclasses
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from src.models.access import GroupRoleAssignment
from src.models.federation import FederationConfig
from src.models.lifetime import LifetimeConfig
from src.models.network import NetworkConfig
from src.models.quotas import QuotaConfig
from src.models.reservations import ReservationConfig
from src.models.security import SecurityGroupConfig
from src.models.state import (
    FipEntry,
    GrantedFlavorAccessEntry,
    ReleasedFipEntry,
    ReleasedRouterIpEntry,
    RevokedFlavorAccessEntry,
    RouterIpEntry,
)

if TYPE_CHECKING:
    import datetime as dt

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


# Tightening order for effective-state computation (DD-028): a lifetime
# deadline only ever raises the state along this order, never lowers it.
_STATE_ORDER: dict[ProjectState, int] = {
    ProjectState.PRESENT: 0,
    ProjectState.LOCKED: 1,
    ProjectState.ABSENT: 2,
}


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
    lifetime: LifetimeConfig | None = None
    group_role_assignments: list[GroupRoleAssignment] = dataclasses.field(default_factory=list)
    reservations: list[ReservationConfig] = dataclasses.field(default_factory=list)

    config_path: str = ""
    state_key: str = ""

    preallocated_fips: list[FipEntry] = dataclasses.field(default_factory=list)
    released_fips: list[ReleasedFipEntry] = dataclasses.field(default_factory=list)
    router_ips: list[RouterIpEntry] = dataclasses.field(default_factory=list)
    released_router_ips: list[ReleasedRouterIpEntry] = dataclasses.field(default_factory=list)
    granted_flavor_access: list[GrantedFlavorAccessEntry] = dataclasses.field(default_factory=list)
    revoked_flavor_access: list[RevokedFlavorAccessEntry] = dataclasses.field(default_factory=list)

    def effective_state(self, now: dt.datetime) -> ProjectState:
        """Return the configured state, tightened by ``lifetime`` once expired (DD-028).

        This is the single place effective state is computed — every code path
        that dispatches on project state must use it instead of raw ``state``.
        Once *now* is past ``lifetime.until``, ``action: lock`` raises the
        state to at least ``locked`` and ``action: delete`` to ``absent``.
        Lifetime only ever tightens (``present < locked < absent``); there is
        no stored timer, so extending ``until`` in config restores the
        configured state on the next run.
        """
        if self.lifetime is None or now <= self.lifetime.until:
            return self.state
        floor = ProjectState.LOCKED if self.lifetime.action == "lock" else ProjectState.ABSENT
        if _STATE_ORDER[self.state] >= _STATE_ORDER[floor]:
            return self.state
        return floor

    @classmethod
    def build(cls, data: dict[str, Any] | None = None, /, **kwargs: Any) -> ProjectConfig:
        """Construct a validated ``ProjectConfig`` from a dict and/or keyword args.

        Merges *data* and *kwargs* (kwargs win), deep-copies to avoid mutating
        caller data, auto-populates universally useful defaults (subnet
        gateway/pools, domain_id, federation entry modes), validates, and
        returns a frozen ``ProjectConfig``.

        Raises :class:`~src.config_validator.ConfigValidationError` on
        validation failure.
        """
        import copy

        from src.config_resolver import auto_populate_subnet_defaults
        from src.config_validator import ConfigValidationError

        raw = copy.deepcopy({**(data or {}), **kwargs})

        # Auto-populate domain_id
        if raw.get("domain_id") is None and raw.get("domain") is None:
            raw["domain_id"] = "default"
        elif raw.get("domain_id") is None and raw.get("domain") is not None:
            raw["domain_id"] = raw["domain"]

        # Auto-populate subnet defaults (gateway_ip, allocation_pools from CIDR)
        if raw.get("state", "present") != "absent":
            auto_populate_subnet_defaults(raw)

        # Resolve federation entry modes from federation-level default
        fed = raw.get("federation")
        if isinstance(fed, dict):
            default_mode = fed.get("mode", "project")
            for entry in fed.get("role_assignments", []):
                if isinstance(entry, dict) and not entry.get("mode"):
                    entry["mode"] = default_mode

        # Validate and construct
        name = raw.get("name")
        label = name if isinstance(name, str) and name else "<build>"
        errors: list[str] = []
        result = cls.validate(raw, errors, label)
        if errors:
            raise ConfigValidationError(errors)
        if result is None:
            raise ConfigValidationError(["Failed to construct ProjectConfig"])
        return result

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
        lifetime_data = data.get("lifetime")
        gra_data = data.get("group_role_assignments")
        reservations_data = data.get("reservations")

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
            lifetime=(LifetimeConfig.from_dict(lifetime_data) if isinstance(lifetime_data, dict) else None),
            group_role_assignments=(
                [GroupRoleAssignment.from_dict(e) for e in gra_data] if isinstance(gra_data, list) else []
            ),
            reservations=(
                [ReservationConfig.from_dict(e) for e in reservations_data]
                if isinstance(reservations_data, list)
                else []
            ),
            config_path=data.get("_config_path", data.get("config_path", "")),
            state_key=data.get("_state_key", data.get("state_key", "")),
            preallocated_fips=[FipEntry.from_dict(f) for f in data.get("preallocated_fips", [])],
            released_fips=[ReleasedFipEntry.from_dict(f) for f in data.get("released_fips", [])],
            router_ips=[RouterIpEntry.from_dict(r) for r in data.get("router_ips", [])],
            released_router_ips=[ReleasedRouterIpEntry.from_dict(r) for r in data.get("released_router_ips", [])],
            granted_flavor_access=[
                GrantedFlavorAccessEntry.from_dict(g) for g in data.get("granted_flavor_access", [])
            ],
            revoked_flavor_access=[
                RevokedFlavorAccessEntry.from_dict(r) for r in data.get("revoked_flavor_access", [])
            ],
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

        # --- Lifetime (validated even for absent — it only ever tightens) ---
        validated_lifetime: LifetimeConfig | None = None
        lifetime_data = data.get("lifetime")
        if lifetime_data is not None:
            validated_lifetime = LifetimeConfig.validate(lifetime_data, errors, label, name)

        # --- Reservations (validated even for absent — config errors are real) ---
        reservations_data = data.get("reservations")
        validated_reservations: list[ReservationConfig] = []
        if reservations_data is not None:
            if not isinstance(reservations_data, list):
                errors.append(f"{label}: reservations must be a list, got {type(reservations_data).__name__}")
            else:
                for idx, entry in enumerate(reservations_data):
                    entry_label = f"{label}: reservations[{idx}]"
                    reservation = ReservationConfig.validate(entry, errors, entry_label)
                    if reservation is not None:
                        validated_reservations.append(reservation)
                seen_names: set[str] = set()
                for reservation in validated_reservations:
                    if reservation.name and reservation.name in seen_names:
                        errors.append(
                            f"{label}: reservation name {reservation.name!r} is not unique within the project"
                        )
                    seen_names.add(reservation.name)

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
            lifetime=validated_lifetime,
            group_role_assignments=validated_gras,
            reservations=validated_reservations,
            config_path=data.get("_config_path", data.get("config_path", "")),
            state_key=data.get("_state_key", data.get("state_key", "")),
            preallocated_fips=[FipEntry.from_dict(f) for f in data.get("preallocated_fips", [])],
            released_fips=[ReleasedFipEntry.from_dict(f) for f in data.get("released_fips", [])],
            router_ips=[RouterIpEntry.from_dict(r) for r in data.get("router_ips", [])],
            released_router_ips=[ReleasedRouterIpEntry.from_dict(r) for r in data.get("released_router_ips", [])],
            granted_flavor_access=[
                GrantedFlavorAccessEntry.from_dict(g) for g in data.get("granted_flavor_access", [])
            ],
            revoked_flavor_access=[
                RevokedFlavorAccessEntry.from_dict(r) for r in data.get("revoked_flavor_access", [])
            ],
        )
