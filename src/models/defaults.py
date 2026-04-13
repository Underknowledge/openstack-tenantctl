"""Pipeline-level defaults configuration."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class DefaultsConfig:
    """Typed representation of pipeline-level fields from ``defaults.yaml``.

    Only captures fields consumed *outside* per-project model construction
    (e.g. external network resolution, federation context).  Per-project
    fields (quotas, network, security_groups, …) are deep-merged into each
    project dict before ``ProjectConfig`` construction and are not repeated
    here.
    """

    external_network_name: str = ""
    external_network_subnet: str = ""
    enforce_unique_cidrs: bool = False
    federation_mapping_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DefaultsConfig:
        """Create from the raw defaults dict loaded from YAML."""
        federation = data.get("federation")
        mapping_id = ""
        if isinstance(federation, dict):
            mapping_id = federation.get("mapping_id", "")

        return cls(
            external_network_name=data.get("external_network_name", ""),
            external_network_subnet=data.get("external_network_subnet", ""),
            enforce_unique_cidrs=bool(data.get("enforce_unique_cidrs", False)),
            federation_mapping_id=mapping_id,
        )
