"""Quota configuration models."""

from __future__ import annotations

import dataclasses
from typing import Any

from src.unit_parser import parse_quota_value

# Fields that support human-readable units
RAM_FIELDS = {"ram"}
STORAGE_FIELDS = {"gigabytes", "backup_gigabytes"}

# Convenience alias: ram_gibibytes accepts a plain integer in GiB (binary)
# and converts to MiB for the OpenStack API.  Users who don't want to think
# about unit strings can just write ``ram_gibibytes: 50`` (= 51 200 MiB).
RAM_GIB_ALIAS = "ram_gibibytes"
_GIB_TO_MIB = 1024  # 1 GiB = 1024 MiB (binary / base-2)


@dataclasses.dataclass(frozen=True)
class QuotaConfig:
    """Quota limits for compute, network, and block storage."""

    compute: dict[str, int]
    network: dict[str, int]
    block_storage: dict[str, int]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuotaConfig:
        """Create from dict with unit parsing support.

        Parses human-readable units for RAM and storage fields:
        - compute.ram: "50GB" → 47684 (MiB)
        - block_storage.gigabytes/backup_gigabytes: "2TB" → 2000 (GB)

        This method is lenient (does not raise on errors, just passes through).
        For validation with error reporting, use ``validate()``.
        """
        errors: list[str] = []  # Collect but ignore errors (lenient mode)

        # Parse compute quotas
        compute_dict: dict[str, int] = {}
        for key, val in data.get("compute", {}).items():
            if key in RAM_FIELDS:
                compute_dict[key] = parse_quota_value(val, "MiB", f"compute.{key}", errors, "from_dict")
            elif key == RAM_GIB_ALIAS:
                if isinstance(val, int) and val >= -1:
                    compute_dict[RAM_GIB_ALIAS] = -1 if val == -1 else val * _GIB_TO_MIB
            elif isinstance(val, int):
                compute_dict[key] = val

        # Resolve ram_gibibytes → ram alias
        if RAM_GIB_ALIAS in compute_dict:
            ram_gib_mib = compute_dict.pop(RAM_GIB_ALIAS)
            if "ram" not in compute_dict:
                compute_dict["ram"] = ram_gib_mib

        # Parse network quotas (no unit support yet)
        network_dict: dict[str, int] = {
            key: val for key, val in data.get("network", {}).items() if isinstance(val, int)
        }

        # Parse block_storage quotas
        block_storage_dict: dict[str, int] = {}
        for key, val in data.get("block_storage", {}).items():
            if key in STORAGE_FIELDS:
                block_storage_dict[key] = parse_quota_value(val, "GB", f"block_storage.{key}", errors, "from_dict")
            elif isinstance(val, int):
                block_storage_dict[key] = val

        return cls(
            compute=compute_dict,
            network=network_dict,
            block_storage=block_storage_dict,
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> QuotaConfig:
        """Validate *data* and return a ``QuotaConfig`` (always constructible).

        Supports human-readable units for RAM and storage fields:
        - compute.ram: accepts "50GB" or "50GiB" (converted to MiB)
        - block_storage.gigabytes: accepts "2TB" or "2TiB" (converted to GB)
        - block_storage.backup_gigabytes: accepts "500GB" (converted to GB)
        """
        # Create validated dicts with parsed quota values
        validated_compute: dict[str, int] = {}
        validated_network: dict[str, int] = {}
        validated_block_storage: dict[str, int] = {}

        for section_key, section_val in data.items():
            if not isinstance(section_val, dict):
                errors.append(f"{label}: quotas.{section_key} must be a mapping, " f"got {type(section_val).__name__}")
                continue

            for qkey, qval in section_val.items():
                # Determine target dict and whether field supports units
                target_dict: dict[str, int]
                if section_key == "compute":
                    target_dict = validated_compute
                    if qkey in RAM_FIELDS:
                        # Field supports units - use parser
                        parsed_value = parse_quota_value(qval, "MiB", f"{section_key}.{qkey}", errors, label)
                        target_dict[qkey] = parsed_value
                    elif qkey == RAM_GIB_ALIAS:
                        # Convenience alias: plain GiB integer → MiB
                        if not isinstance(qval, int) or qval < -1:
                            errors.append(
                                f"{label}: quota 'compute.{RAM_GIB_ALIAS}' "
                                f"must be -1 (unlimited) or a non-negative "
                                f"integer (in gibibytes), got {qval!r}"
                            )
                        else:
                            gib_as_mib = -1 if qval == -1 else qval * _GIB_TO_MIB
                            target_dict[RAM_GIB_ALIAS] = gib_as_mib
                    else:
                        # Field does not support units - validate as integer
                        if not isinstance(qval, int) or qval < -1:
                            errors.append(
                                f"{label}: quota '{section_key}.{qkey}' must be -1 "
                                f"(unlimited) or a non-negative integer, got {qval!r}"
                            )
                        else:
                            target_dict[qkey] = qval
                elif section_key == "network":
                    target_dict = validated_network
                    # Network quotas don't support units yet - validate as integer
                    if not isinstance(qval, int) or qval < -1:
                        errors.append(
                            f"{label}: quota '{section_key}.{qkey}' must be -1 "
                            f"(unlimited) or a non-negative integer, got {qval!r}"
                        )
                    else:
                        target_dict[qkey] = qval
                elif section_key == "block_storage":
                    target_dict = validated_block_storage
                    if qkey in STORAGE_FIELDS:
                        # Field supports units - use parser
                        parsed_value = parse_quota_value(qval, "GB", f"{section_key}.{qkey}", errors, label)
                        target_dict[qkey] = parsed_value
                    else:
                        # Field does not support units - validate as integer
                        if not isinstance(qval, int) or qval < -1:
                            errors.append(
                                f"{label}: quota '{section_key}.{qkey}' must be -1 "
                                f"(unlimited) or a non-negative integer, got {qval!r}"
                            )
                        else:
                            target_dict[qkey] = qval
                else:
                    # Unknown section, skip
                    continue

        # Resolve ram_gibibytes → ram alias
        if RAM_GIB_ALIAS in validated_compute:
            ram_gib_mib = validated_compute.pop(RAM_GIB_ALIAS)
            if "ram" in validated_compute:
                # Both set — OK when they agree, error when they differ
                if validated_compute["ram"] != ram_gib_mib:
                    raw_compute = data.get("compute", {})
                    errors.append(
                        f"{label}: 'compute.ram' ({raw_compute.get('ram')!r}) "
                        f"and 'compute.ram_gibibytes' "
                        f"({raw_compute.get(RAM_GIB_ALIAS)!r}) both set but "
                        f"resolve to different values "
                        f"({validated_compute['ram']} MiB vs {ram_gib_mib} MiB"
                        f"). Remove one — use either 'ram' (with optional unit "
                        f"strings) or 'ram_gibibytes' (plain GiB integer)."
                    )
            else:
                validated_compute["ram"] = ram_gib_mib

        # Construct with validated dicts (may be empty if sections missing)
        return cls(
            compute=validated_compute,
            network=validated_network,
            block_storage=validated_block_storage,
        )
