"""Project lifetime model — a deadline that tightens the effective state (DD-028)."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from src.period_parser import parse_bare_span

if TYPE_CHECKING:
    import datetime as dt

_VALID_ACTIONS: set[str] = {"lock", "delete"}
_KNOWN_KEYS: set[str] = {"until", "action", "confirm_delete"}

_UNTIL_TIME_HINT = "lifetime.until supports day granularity at most (e.g. '2026-09-30')"


@dataclasses.dataclass(frozen=True)
class LifetimeConfig:
    """A deadline on the whole project.

    Once ``until`` (resolved end of its stated span, UTC, inclusive) has
    passed, the project's effective state tightens to ``locked``
    (``action: lock``) or ``absent`` (``action: delete``).  There is no
    default action and no stored timer — see
    :meth:`src.models.ProjectConfig.effective_state`.
    """

    until: dt.datetime
    action: str
    confirm_delete: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LifetimeConfig:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        errors: list[str] = []
        span = parse_bare_span(data["until"], errors, "lifetime.until", time_hint=_UNTIL_TIME_HINT)
        if span is None or span.end is None:
            msg = f"invalid lifetime.until {data['until']!r}: {'; '.join(errors)}"
            raise ValueError(msg)
        # An unrecognized action must never reach effective_state(), where
        # anything but 'lock' selects the delete branch.
        if data["action"] not in _VALID_ACTIONS:
            msg = f"invalid lifetime.action {data['action']!r}: must be 'lock' or 'delete'"
            raise ValueError(msg)
        return cls(
            until=span.end,
            action=data["action"],
            confirm_delete=data.get("confirm_delete"),
        )

    @classmethod
    def validate(
        cls,
        data: dict[str, Any],
        errors: list[str],
        label: str,
        project_name: str,
    ) -> LifetimeConfig | None:
        """Validate a ``lifetime`` mapping and return a config, or ``None`` if broken."""
        if not isinstance(data, dict):
            errors.append(f"{label}: lifetime must be a mapping, got {type(data).__name__}")
            return None

        errors.extend(f"{label}: lifetime has unknown key {key!r}" for key in sorted(set(data) - _KNOWN_KEYS))

        until: dt.datetime | None = None
        if "until" not in data:
            errors.append(f"{label}: lifetime.until is required")
        else:
            span = parse_bare_span(data["until"], errors, f"{label}: lifetime.until", time_hint=_UNTIL_TIME_HINT)
            if span is not None:
                until = span.end

        action = data.get("action")
        if action not in _VALID_ACTIONS:
            errors.append(f"{label}: lifetime.action must be 'lock' or 'delete' (required, no default), got {action!r}")

        confirm = data.get("confirm_delete")
        if action == "delete":
            if confirm != project_name:
                errors.append(
                    f"{label}: lifetime.confirm_delete must equal the project name "
                    f"{project_name!r} when action is 'delete', got {confirm!r}"
                )
        elif confirm is not None:
            errors.append(f"{label}: lifetime.confirm_delete is only allowed with action 'delete'")

        if until is None or not isinstance(action, str) or action not in _VALID_ACTIONS:
            return None
        return cls(until=until, action=action, confirm_delete=confirm)
