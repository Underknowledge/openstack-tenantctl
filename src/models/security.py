"""Security group configuration models."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class SecurityGroupRule:
    """A single security group rule."""

    direction: str = ""
    protocol: str | None = None
    port_range_min: int | None = None
    port_range_max: int | None = None
    remote_ip_prefix: str | None = None
    remote_group_id: str | None = None
    ethertype: str | None = None
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecurityGroupRule:
        """Create from a raw dict, ignoring unknown keys."""
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_api_dict(self) -> dict[str, Any]:
        """Return a dict for OpenStack API calls, excluding None values."""
        result: dict[str, Any] = {}
        for field in dataclasses.fields(self):
            val = getattr(self, field.name)
            if val is not None and val != "":
                result[field.name] = val
        return result


@dataclasses.dataclass(frozen=True)
class SecurityGroupConfig:
    """Baseline security group configuration."""

    name: str
    rules: list[SecurityGroupRule]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecurityGroupConfig:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        return cls(
            name=data["name"],
            rules=[SecurityGroupRule.from_dict(r) for r in data.get("rules", [])],
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> SecurityGroupConfig:
        """Validate *data* and return a ``SecurityGroupConfig`` (always constructible)."""
        sg_rules = data.get("rules")
        if sg_rules is not None and not isinstance(sg_rules, list):
            errors.append(
                f"{label}: security_group.rules must be a list, got {type(sg_rules).__name__}"
            )
        elif isinstance(sg_rules, list):
            for idx, rule in enumerate(sg_rules):
                if not isinstance(rule, dict):
                    errors.append(
                        f"{label}: security_group.rules[{idx}] "
                        f"must be a mapping, got {type(rule).__name__}"
                    )
        # Construct safely: only include dict rules
        valid_rules = [
            SecurityGroupRule.from_dict(r)
            for r in (sg_rules if isinstance(sg_rules, list) else [])
            if isinstance(r, dict)
        ]
        return cls(name=data["name"], rules=valid_rules)
