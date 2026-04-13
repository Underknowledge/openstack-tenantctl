"""Federation configuration models."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class FederationRoleAssignment:
    """A single IDP-group-to-roles mapping for federation."""

    idp_group: str | list[str]
    roles: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FederationRoleAssignment:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        return cls(
            idp_group=data["idp_group"],
            roles=data["roles"],
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
        return cls(
            idp_group=idp_group if idp_group is not None else "",
            roles=roles if isinstance(roles, list) else [],
        )


@dataclasses.dataclass(frozen=True)
class FederationConfig:
    """Keystone federation mapping configuration."""

    issuer: str = ""
    mapping_id: str = ""
    group_prefix: str = "/services/openstack/"
    user_type: str = ""
    role_assignments: list[FederationRoleAssignment] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FederationConfig:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        return cls(
            issuer=data.get("issuer", ""),
            mapping_id=data.get("mapping_id", ""),
            group_prefix=data.get("group_prefix", "/services/openstack/"),
            user_type=data.get("user_type", ""),
            role_assignments=[FederationRoleAssignment.from_dict(a) for a in data.get("role_assignments", [])],
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> FederationConfig:
        """Validate *data* and return a ``FederationConfig`` (always constructible)."""
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
            role_assignments=validated_assignments,
        )
