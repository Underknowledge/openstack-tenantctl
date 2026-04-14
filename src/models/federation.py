"""Federation configuration models."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class FederationRoleAssignment:
    """A single IDP-group-to-roles mapping for federation."""

    idp_group: str | list[str]
    roles: list[str]
    keystone_group: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FederationRoleAssignment:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        return cls(
            idp_group=data["idp_group"],
            roles=data["roles"],
            keystone_group=data.get("keystone_group", ""),
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> FederationRoleAssignment:
        """Validate a single federation role assignment entry."""
        idp_group = data.get("idp_group")
        if isinstance(idp_group, str):
            if len(idp_group) == 0:
                errors.append(f"{label}.idp_group must be non-empty, got {idp_group!r}")
        elif isinstance(idp_group, list):
            if len(idp_group) == 0:
                errors.append(f"{label}.idp_group list must not be empty")
            elif any(not isinstance(g, str) or len(g) == 0 for g in idp_group):
                errors.append(f"{label}.idp_group must contain only non-empty strings")
        else:
            errors.append(f"{label}.idp_group must be a non-empty string or list of strings, " f"got {idp_group!r}")
        roles = data.get("roles")
        if not isinstance(roles, list) or len(roles) == 0:
            errors.append(f"{label}.roles must be a non-empty list")
        elif any(not isinstance(r, str) or len(r) == 0 for r in roles):
            errors.append(f"{label}.roles must contain only non-empty strings")
        keystone_group = data.get("keystone_group", "")
        if not isinstance(keystone_group, str):
            errors.append(f"{label}.keystone_group must be a string, got {keystone_group!r}")
            keystone_group = ""
        return cls(
            idp_group=idp_group if idp_group is not None else "",
            roles=roles if isinstance(roles, list) else [],
            keystone_group=keystone_group,
        )


_VALID_MAPPING_MODES: set[str] = {"project", "group"}


@dataclasses.dataclass(frozen=True)
class FederationConfig:
    """Keystone federation mapping configuration."""

    issuer: str = ""
    mapping_id: str = ""
    group_prefix: str = "/services/openstack/"
    user_type: str = ""
    mapping_mode: str = "project"
    group_name_separator: str = " "
    role_assignments: list[FederationRoleAssignment] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FederationConfig:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        return cls(
            issuer=data.get("issuer", ""),
            mapping_id=data.get("mapping_id", ""),
            group_prefix=data.get("group_prefix", "/services/openstack/"),
            user_type=data.get("user_type", ""),
            mapping_mode=data.get("mapping_mode", "project"),
            group_name_separator=data.get("group_name_separator", " "),
            role_assignments=[FederationRoleAssignment.from_dict(a) for a in data.get("role_assignments", [])],
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> FederationConfig:
        """Validate *data* and return a ``FederationConfig`` (always constructible)."""
        mapping_mode = data.get("mapping_mode", "project")
        if mapping_mode not in _VALID_MAPPING_MODES:
            errors.append(
                f"{label}: federation.mapping_mode must be one of "
                f"{sorted(_VALID_MAPPING_MODES)}, got {mapping_mode!r}"
            )

        assignments_data = data.get("role_assignments")
        validated_assignments: list[FederationRoleAssignment] = []
        if isinstance(assignments_data, list):
            for idx, entry in enumerate(assignments_data):
                entry_label = f"{label}: federation.role_assignments[{idx}]"
                if not isinstance(entry, dict):
                    errors.append(f"{entry_label} must be a mapping, got {type(entry).__name__}")
                    continue
                validated_assignments.append(FederationRoleAssignment.validate(entry, errors, entry_label))
        return cls(
            issuer=data.get("issuer", ""),
            mapping_id=data.get("mapping_id", ""),
            group_prefix=data.get("group_prefix", "/services/openstack/"),
            user_type=data.get("user_type", ""),
            mapping_mode=mapping_mode if isinstance(mapping_mode, str) else "project",
            group_name_separator=data.get("group_name_separator", " "),
            role_assignments=validated_assignments,
        )
