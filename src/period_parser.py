"""Parse truncated ISO 8601 periods for reservations and lifetime deadlines.

A period value is written as truncated ISO 8601 (CONFIG-SCHEMA.md, "Period
Syntax").  A bare string denotes its whole stated calendar span down to day
granularity (``"2027"``, ``"2026-06"``, ``"2026-06-01"``); an explicit
``from``/``until`` mapping accepts truncations down to seconds, where ``from``
resolves to the start of its stated span and ``until`` to the end (inclusive).
Naive times are UTC; fixed offsets are allowed.

PyYAML types unquoted scalars before this module sees them: ``2027`` arrives
as ``int``, ``2026-06-01`` as ``datetime.date``, and a full timestamp as
``datetime.datetime``.  Unambiguous values (integer years, date objects) are
normalized to their calendar span; datetime objects are rejected with a hint
to quote the value.
"""

from __future__ import annotations

import calendar
import dataclasses
import datetime as dt
import re
from typing import Any

__all__ = [
    "PeriodSpan",
    "parse_bare_span",
    "parse_period",
    "parse_point",
    "spans_active",
    "spans_all_past",
]

_QUOTE_HINT = 'quote the value (e.g. "2026-06-01") so YAML does not coerce it'

# Truncated ISO 8601: YYYY[-MM[-DD[THH[:MM[:SS]][<offset>]]]].
# An offset (Z or ±HH:MM) is only meaningful once a time component exists.
_POINT_RE = re.compile(
    r"^(?P<year>\d{4})"
    r"(?:-(?P<month>\d{2})"
    r"(?:-(?P<day>\d{2})"
    r"(?:T(?P<hour>\d{2})"
    r"(?::(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2}))?)?"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})?"
    r")?)?)?$"
)


@dataclasses.dataclass(frozen=True)
class PeriodSpan:
    """One contiguous time span, inclusive on both ends.

    ``start`` / ``end`` are timezone-aware UTC datetimes.  ``None`` means
    open-ended in that direction (``{from: ...}`` without ``until`` and
    vice versa).
    """

    start: dt.datetime | None
    end: dt.datetime | None

    def contains(self, now: dt.datetime) -> bool:
        """Return True when *now* falls inside this span (inclusive)."""
        if self.start is not None and now < self.start:
            return False
        return not (self.end is not None and now > self.end)

    def is_past(self, now: dt.datetime) -> bool:
        """Return True when this span ended before *now*."""
        return self.end is not None and now > self.end

    def describe(self) -> str:
        """Human-readable UTC span for logs and dry-run output."""
        start = self.start.isoformat() if self.start is not None else "open"
        end = self.end.isoformat() if self.end is not None else "open"
        return f"{start} .. {end}"


def _parse_offset(offset: str) -> dt.timezone:
    """Convert an ISO offset suffix (``Z`` or ``±HH:MM``) to a tzinfo."""
    if offset == "Z":
        return dt.UTC
    sign = 1 if offset[0] == "+" else -1
    hours, minutes = int(offset[1:3]), int(offset[4:6])
    return dt.timezone(sign * dt.timedelta(hours=hours, minutes=minutes))


def _resolve_string_point(
    text: str,
    *,
    end: bool,
    errors: list[str],
    label: str,
) -> tuple[dt.datetime | None, bool]:
    """Parse a truncated ISO 8601 string to a span boundary.

    Returns ``(datetime, has_time)`` where *has_time* indicates the value
    carried time-of-day components (finer than day granularity).  ``end=True``
    fills missing components with their span maximum (end of stated span),
    ``end=False`` with the minimum (start of stated span).
    """
    match = _POINT_RE.match(text.strip())
    if match is None:
        errors.append(
            f"{label}: {text!r} is not a valid truncated ISO 8601 value (e.g. '2027', '2026-06', '2026-06-01')"
        )
        return None, False

    parts = match.groupdict()
    has_time = parts["hour"] is not None

    year = int(parts["year"])
    try:
        # An offset of 24h or more raises ValueError in dt.timezone().
        tz = _parse_offset(parts["offset"]) if parts["offset"] else dt.UTC
        if end:
            month = int(parts["month"]) if parts["month"] else 12
            day = int(parts["day"]) if parts["day"] else calendar.monthrange(year, month)[1]
            hour = int(parts["hour"]) if parts["hour"] else 23
            minute = int(parts["minute"]) if parts["minute"] else 59
            second = int(parts["second"]) if parts["second"] else 59
        else:
            month = int(parts["month"]) if parts["month"] else 1
            day = int(parts["day"]) if parts["day"] else 1
            hour = int(parts["hour"]) if parts["hour"] else 0
            minute = int(parts["minute"]) if parts["minute"] else 0
            second = int(parts["second"]) if parts["second"] else 0
        # astimezone overflows (not ValueError) when an offset pushes the
        # result past datetime.min/max, e.g. 9999-12-31T23:59:59-05:00.
        point = dt.datetime(year, month, day, hour, minute, second, tzinfo=tz).astimezone(dt.UTC)
    except (ValueError, OverflowError) as exc:
        errors.append(f"{label}: {text!r} is not a valid date/time: {exc}")
        return None, False

    return point, has_time


def parse_point(
    value: Any,
    *,
    end: bool,
    errors: list[str],
    label: str,
) -> dt.datetime | None:
    """Parse one ``from``/``until`` value to its span boundary (UTC).

    Accepts truncated ISO 8601 strings down to second granularity, plus the
    unambiguous YAML coercions (integer year, date object).  Appends to
    *errors* and returns ``None`` on failure.
    """
    if isinstance(value, bool):
        errors.append(f"{label}: expected a timestamp, got boolean {value!r}")
        return None
    if isinstance(value, int):
        if not 1000 <= value <= 9999:
            errors.append(f"{label}: integer {value} is not a 4-digit year")
            return None
        value = str(value)
    elif isinstance(value, dt.datetime):
        errors.append(f"{label}: YAML coerced this timestamp to a datetime object — {_QUOTE_HINT}")
        return None
    elif isinstance(value, dt.date):
        value = value.isoformat()
    elif not isinstance(value, str):
        errors.append(f"{label}: expected a timestamp string, got {type(value).__name__}")
        return None

    point, _has_time = _resolve_string_point(value, end=end, errors=errors, label=label)
    return point


def parse_bare_span(
    value: Any,
    errors: list[str],
    label: str,
    *,
    time_hint: str = "use a from/until mapping for time-of-day precision",
) -> PeriodSpan | None:
    """Parse a bare period value to its whole calendar span.

    Bare values allow year/month/day granularity only — a bare timestamp
    would denote a one-second span, which reads like "starting at" but never
    is.  *time_hint* tailors the rejection message to the calling field.
    """
    if isinstance(value, bool):
        errors.append(f"{label}: expected a period, got boolean {value!r}")
        return None
    if isinstance(value, int):
        if not 1000 <= value <= 9999:
            errors.append(f"{label}: integer {value} is not a 4-digit year")
            return None
        value = str(value)
    elif isinstance(value, dt.datetime):
        errors.append(f"{label}: YAML coerced this value to a datetime object — {_QUOTE_HINT}")
        return None
    elif isinstance(value, dt.date):
        value = value.isoformat()
    elif not isinstance(value, str):
        errors.append(f"{label}: expected a period string, got {type(value).__name__}")
        return None

    start_errors: list[str] = []
    start, has_time = _resolve_string_point(value, end=False, errors=start_errors, label=label)
    if start_errors:
        errors.extend(start_errors)
        return None
    if has_time:
        errors.append(
            f"{label}: bare value {value!r} is finer than a day and would denote a one-second span — {time_hint}"
        )
        return None

    end, _ = _resolve_string_point(value, end=True, errors=errors, label=label)
    if end is None:
        return None
    return PeriodSpan(start=start, end=end)


def _parse_range(
    value: dict[str, Any],
    errors: list[str],
    label: str,
) -> PeriodSpan | None:
    """Parse a ``from``/``until`` mapping to a span."""
    unknown = sorted(set(value) - {"from", "until"})
    if unknown:
        keys = ", ".join(repr(k) for k in unknown)
        errors.append(f"{label}: unknown period key(s) {keys} — only 'from' and 'until' are allowed")
        return None
    if "from" not in value and "until" not in value:
        errors.append(f"{label}: period mapping must set 'from', 'until', or both")
        return None

    start: dt.datetime | None = None
    end: dt.datetime | None = None
    before = len(errors)
    if "from" in value:
        start = parse_point(value["from"], end=False, errors=errors, label=f"{label}.from")
    if "until" in value:
        end = parse_point(value["until"], end=True, errors=errors, label=f"{label}.until")
    if len(errors) > before:
        return None

    if start is not None and end is not None and start > end:
        errors.append(f"{label}: 'from' ({start.isoformat()}) is after 'until' ({end.isoformat()}) — inverted range")
        return None
    return PeriodSpan(start=start, end=end)


def parse_period(value: Any, errors: list[str], label: str) -> tuple[PeriodSpan, ...]:
    """Parse a full period value: bare span, from/until mapping, or list of either.

    A list is the union of all listed spans.  Appends to *errors* and returns
    an empty tuple when nothing parses.
    """
    if isinstance(value, list):
        if not value:
            errors.append(f"{label}: period list must not be empty")
            return ()
        spans: list[PeriodSpan] = []
        for idx, item in enumerate(value):
            item_label = f"{label}[{idx}]"
            if isinstance(item, list):
                errors.append(f"{item_label}: nested period lists are not allowed")
                continue
            spans.extend(parse_period(item, errors, item_label))
        return tuple(spans)

    if isinstance(value, dict):
        span = _parse_range(value, errors, label)
        return (span,) if span is not None else ()

    span = parse_bare_span(value, errors, label)
    return (span,) if span is not None else ()


def spans_active(spans: tuple[PeriodSpan, ...], now: dt.datetime) -> bool:
    """Return True when *now* falls inside any of *spans* (union semantics)."""
    return any(span.contains(now) for span in spans)


def spans_all_past(spans: tuple[PeriodSpan, ...], now: dt.datetime) -> bool:
    """Return True when every span ended before *now* (dead config)."""
    return bool(spans) and all(span.is_past(now) for span in spans)
