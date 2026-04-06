"""Parse human-readable unit strings for quota values.

Supports decimal units (KB, MB, GB, TB, PB) and binary units (KiB, MiB, GiB, TiB, PiB)
for quota values that represent memory or storage.

Examples:
    >>> parse_quota_value("50GB", "MB", "ram", [], "test")
    50000
    >>> parse_quota_value("50GiB", "MB", "ram", [], "test")
    51200
    >>> parse_quota_value(51200, "MB", "ram", [], "test")
    51200
"""

from __future__ import annotations

import re
from typing import Literal

# Decimal units (base 10, powers of 1000)
DECIMAL_UNITS: dict[str, int] = {
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
}

# Binary units (base 2, powers of 1024)
BINARY_UNITS: dict[str, int] = {
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "PiB": 1024**5,
}

# Shorthand aliases (map to binary units)
UNIT_ALIASES: dict[str, str] = {
    "K": "KiB",
    "M": "MiB",
    "G": "GiB",
    "T": "TiB",
    "P": "PiB",
}

# All valid units combined
ALL_UNITS: dict[str, int] = {**DECIMAL_UNITS, **BINARY_UNITS}

# Target unit conversion factors
TARGET_UNITS: dict[str, int] = {
    "MB": DECIMAL_UNITS["MB"],
    "GB": DECIMAL_UNITS["GB"],
}

# Maximum quota value (OpenStack uses 32-bit signed integers)
MAX_QUOTA_VALUE = 2**31 - 1

# Pattern to match value and unit: "50GB", "50 GB", "50.5 GB", "-10GB" (for error detection)
UNIT_PATTERN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z]+)\s*$")


def parse_quota_value(
    value: int | str,
    target_unit: Literal["MB", "GB"],
    field_name: str,
    errors: list[str],
    label: str,
) -> int:
    """Parse quota value (int or unit string) and convert to target unit.

    Args:
        value: Integer or unit string (e.g., "50GB", "50 GB", "50GiB")
        target_unit: Target unit for conversion ("MB" for RAM, "GB" for storage)
        field_name: Field name for error messages (e.g., "compute.ram")
        errors: List to append error messages to
        label: Label for error messages (e.g., "config/dev_2.yaml")

    Returns:
        Integer value in target unit. On error, returns 0 and appends to errors list.

    Examples:
        >>> errors: list[str] = []
        >>> parse_quota_value("50GB", "MB", "ram", errors, "test")
        50000
        >>> parse_quota_value("50GiB", "MB", "ram", errors, "test")
        51200
        >>> parse_quota_value(51200, "MB", "ram", errors, "test")
        51200
        >>> parse_quota_value(-1, "MB", "ram", errors, "test")  # unlimited
        -1
    """
    # Handle integer passthrough (backward compatibility)
    if isinstance(value, int):
        # Special value -1 means unlimited
        if value == -1:
            return -1
        # Negative values (except -1) are invalid
        if value < 0:
            errors.append(
                f"{label}: quota '{field_name}' must be -1 (unlimited) "
                f"or a non-negative integer, got {value}"
            )
            return 0
        # Non-negative integers pass through unchanged
        return value

    # Handle string units
    if not isinstance(value, str):
        errors.append(
            f"{label}: quota '{field_name}' must be an integer or unit string, "
            f"got {type(value).__name__}"
        )
        return 0

    # Parse the unit string
    match = UNIT_PATTERN.match(value)
    if not match:
        errors.append(
            f"{label}: quota '{field_name}' has invalid format {value!r}. "
            f"Expected format: number + unit (e.g., '50GB', '50 GB')"
        )
        return 0

    numeric_part, unit_part = match.groups()

    # Check for negative values with units (ambiguous)
    if numeric_part.startswith("-"):
        errors.append(
            f"{label}: quota '{field_name}' cannot use negative values with units. "
            f"Use -1 (without units) for unlimited, got {value!r}"
        )
        return 0

    # Resolve unit aliases (G → GiB)
    resolved_unit = UNIT_ALIASES.get(unit_part, unit_part)

    # Check if unit is valid
    if resolved_unit not in ALL_UNITS:
        valid_units = ", ".join(
            sorted([*DECIMAL_UNITS.keys(), *BINARY_UNITS.keys(), *UNIT_ALIASES.keys()])
        )
        errors.append(
            f"{label}: quota '{field_name}' has unknown unit {unit_part!r}. "
            f"Valid units: {valid_units}"
        )
        return 0

    # Convert to bytes (intermediate representation)
    try:
        numeric_value = float(numeric_part)
    except ValueError:
        errors.append(f"{label}: quota '{field_name}' has invalid numeric value {numeric_part!r}")
        return 0

    bytes_value = numeric_value * ALL_UNITS[resolved_unit]

    # Convert from bytes to target unit
    target_factor = TARGET_UNITS[target_unit]
    result = bytes_value / target_factor

    # Round to nearest integer
    result_int = round(result)

    # Validate against maximum quota value
    if result_int > MAX_QUOTA_VALUE:
        errors.append(
            f"{label}: quota '{field_name}' value {value!r} is too large "
            f"(max {MAX_QUOTA_VALUE} {target_unit})"
        )
        return 0

    return result_int
