"""Reservation model — time-limited access to private flavors (DD-027)."""

from __future__ import annotations

import dataclasses
from typing import Any

from src.period_parser import PeriodSpan, parse_period

_RESERVED_KEYS: set[str] = {"images", "on_expiry"}
_KNOWN_KEYS: set[str] = {"name", "period", "flavors"}
_GRANT_KEYS: set[str] = {"flavors"}


@dataclasses.dataclass(frozen=True)
class ReservationConfig:
    """One reservation entry: a time period paired with what it grants.

    While any of ``period``'s spans is active, access to the matched private
    flavors is granted; outside all spans it is revoked (tracked grants only,
    see DD-027).
    """

    period: tuple[PeriodSpan, ...]
    flavors: tuple[str, ...] = ()
    name: str = ""

    @property
    def label(self) -> str:
        """Name used in logs, action messages, and grant tracking."""
        return self.name or "<unnamed>"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReservationConfig:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        errors: list[str] = []
        spans = parse_period(data["period"], errors, "reservation.period")
        if errors:
            msg = f"invalid reservation period {data['period']!r}: {'; '.join(errors)}"
            raise ValueError(msg)
        return cls(
            period=spans,
            flavors=tuple(data.get("flavors", ())),
            name=data.get("name", ""),
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> ReservationConfig | None:
        """Validate a single reservation entry and return a config, or ``None`` if broken."""
        if not isinstance(data, dict):
            errors.append(f"{label} must be a mapping, got {type(data).__name__}")
            return None

        for key in sorted(set(data) - _KNOWN_KEYS):
            if key in _RESERVED_KEYS:
                errors.append(f"{label}: {key!r} is reserved for a future phase and not yet implemented")
            else:
                errors.append(f"{label}: unknown key {key!r}")

        spans: tuple[PeriodSpan, ...] = ()
        if "period" not in data:
            errors.append(f"{label}: missing required field 'period'")
        else:
            spans = parse_period(data["period"], errors, f"{label}.period")

        if not (_GRANT_KEYS & set(data)):
            errors.append(
                f"{label}: at least one grant key ('flavors') is required — "
                f"an entry with only a period grants nothing"
            )

        name = data.get("name", "")
        if "name" in data and (not isinstance(name, str) or len(name) == 0):
            errors.append(f"{label}: name must be a non-empty string")
            name = ""
        elif ":" in name:
            # ':' is the separator in the unmatched-pattern state keys
            # ("name:pattern") and would garble warnings and tracking.
            errors.append(f"{label}: name must not contain ':'")
            name = ""

        flavors: tuple[str, ...] = ()
        if "flavors" in data:
            raw_flavors = data["flavors"]
            if not isinstance(raw_flavors, list) or len(raw_flavors) == 0:
                errors.append(f"{label}: flavors must be a non-empty list")
            elif any(not isinstance(f, str) or len(f) == 0 for f in raw_flavors):
                errors.append(f"{label}: flavors must contain only non-empty strings")
            elif any(f.strip() == "*" for f in raw_flavors):
                errors.append(
                    f"{label}: a bare '*' flavor pattern is rejected — "
                    f"it would grant every private flavor in the cloud"
                )
            else:
                flavors = tuple(raw_flavors)

        return cls(
            period=spans,
            flavors=flavors,
            name=name if isinstance(name, str) else "",
        )
