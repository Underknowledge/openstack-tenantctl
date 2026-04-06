"""Group role assignment models."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class GroupRoleAssignment:
    """A group-to-roles assignment for a project."""

    group: str
    roles: list[str]
    state: str = "present"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupRoleAssignment:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        return cls(
            group=data["group"],
            roles=data["roles"],
            state=data.get("state", "present"),
        )

    @classmethod
    def validate(
        cls, data: dict[str, Any], errors: list[str], label: str
    ) -> GroupRoleAssignment | None:
        """Validate a single group role assignment entry."""
        group = data.get("group")
        if not isinstance(group, str) or len(group) == 0:
            errors.append(f"{label}.group must be a non-empty string")
        roles = data.get("roles")
        if not isinstance(roles, list) or len(roles) == 0:
            errors.append(f"{label}.roles must be a non-empty list")
        elif any(not isinstance(r, str) or len(r) == 0 for r in roles):
            errors.append(f"{label}.roles must contain only non-empty strings")
        entry_state = data.get("state")
        if entry_state is not None and entry_state not in ("present", "absent"):
            errors.append(f"{label}.state must be 'present' or 'absent', got {entry_state!r}")
        return cls(
            group=group if isinstance(group, str) else "",
            roles=roles if isinstance(roles, list) else [],
            state=data.get("state", "present"),
        )
