"""Tests for truncated ISO 8601 period parsing."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from src.period_parser import (
    PeriodSpan,
    parse_bare_span,
    parse_period,
    parse_point,
    spans_active,
    spans_all_past,
)

UTC = dt.UTC


def _utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


class TestParseBareSpan:
    """Bare period values resolve to their whole stated calendar span."""

    @pytest.mark.parametrize(
        ("value", "expected_start", "expected_end"),
        [
            pytest.param("2027", _utc(2027, 1, 1, 0, 0, 0), _utc(2027, 12, 31, 23, 59, 59), id="year"),
            pytest.param("2026-06", _utc(2026, 6, 1, 0, 0, 0), _utc(2026, 6, 30, 23, 59, 59), id="month"),
            pytest.param("2026-02", _utc(2026, 2, 1, 0, 0, 0), _utc(2026, 2, 28, 23, 59, 59), id="month-feb"),
            pytest.param("2028-02", _utc(2028, 2, 1, 0, 0, 0), _utc(2028, 2, 29, 23, 59, 59), id="month-leap-feb"),
            pytest.param("2026-06-01", _utc(2026, 6, 1, 0, 0, 0), _utc(2026, 6, 1, 23, 59, 59), id="day"),
            # YAML coercions: unquoted 2027 arrives as int, 2026-06-01 as date.
            pytest.param(2027, _utc(2027, 1, 1, 0, 0, 0), _utc(2027, 12, 31, 23, 59, 59), id="yaml-int-year"),
            pytest.param(dt.date(2026, 6, 1), _utc(2026, 6, 1, 0, 0, 0), _utc(2026, 6, 1, 23, 59, 59), id="yaml-date"),
        ],
    )
    def test_valid_spans(self, value: Any, expected_start: dt.datetime, expected_end: dt.datetime) -> None:
        errors: list[str] = []
        span = parse_bare_span(value, errors, "test")
        assert errors == []
        assert span is not None
        assert span.start == expected_start
        assert span.end == expected_end

    @pytest.mark.parametrize(
        ("value", "error_fragment"),
        [
            pytest.param("2026-06-01T09:00:00", "finer than a day", id="bare-timestamp"),
            pytest.param("2026-06-01T09", "finer than a day", id="bare-hour"),
            pytest.param(dt.datetime(2026, 6, 1, 9, 0, 0), "quote the value", id="yaml-datetime"),
            pytest.param("june 2026", "not a valid truncated ISO 8601", id="not-iso"),
            pytest.param("2026-13", "not a valid date/time", id="month-13"),
            pytest.param("2026-06-32", "not a valid date/time", id="day-32"),
            pytest.param(27, "not a 4-digit year", id="int-2-digit"),
            pytest.param(True, "boolean", id="bool"),
            pytest.param(None, "expected a period string", id="none"),
        ],
    )
    def test_rejections(self, value: Any, error_fragment: str) -> None:
        errors: list[str] = []
        span = parse_bare_span(value, errors, "test")
        assert span is None
        assert len(errors) == 1
        assert error_fragment in errors[0]

    def test_time_hint_is_configurable(self) -> None:
        errors: list[str] = []
        parse_bare_span("2026-06-01T09:00:00", errors, "test", time_hint="custom hint")
        assert "custom hint" in errors[0]


class TestParsePoint:
    """from/until values accept truncations down to seconds."""

    @pytest.mark.parametrize(
        ("value", "end", "expected"),
        [
            pytest.param("2027", False, _utc(2027, 1, 1, 0, 0, 0), id="year-start"),
            pytest.param("2027", True, _utc(2027, 12, 31, 23, 59, 59), id="year-end"),
            pytest.param("2027-03", False, _utc(2027, 3, 1, 0, 0, 0), id="month-start"),
            pytest.param("2027-03", True, _utc(2027, 3, 31, 23, 59, 59), id="month-end"),
            pytest.param("2027-03-07", True, _utc(2027, 3, 7, 23, 59, 59), id="day-end"),
            pytest.param("2027-03-07T12", False, _utc(2027, 3, 7, 12, 0, 0), id="hour-start"),
            pytest.param("2027-03-07T12", True, _utc(2027, 3, 7, 12, 59, 59), id="hour-end"),
            pytest.param("2027-03-07T12:30", True, _utc(2027, 3, 7, 12, 30, 59), id="minute-end"),
            pytest.param("2027-03-07T12:30:15", False, _utc(2027, 3, 7, 12, 30, 15), id="second-start"),
            pytest.param("2027-03-07T12:30:15", True, _utc(2027, 3, 7, 12, 30, 15), id="second-end"),
            # Explicit fixed offsets are converted to UTC; naive times are UTC.
            pytest.param("2026-08-15T08:00:00+02:00", False, _utc(2026, 8, 15, 6, 0, 0), id="positive-offset"),
            pytest.param("2026-08-15T08:00:00-05:00", False, _utc(2026, 8, 15, 13, 0, 0), id="negative-offset"),
            pytest.param("2026-08-15T08:00:00Z", False, _utc(2026, 8, 15, 8, 0, 0), id="zulu"),
            pytest.param(2027, True, _utc(2027, 12, 31, 23, 59, 59), id="yaml-int-year-end"),
            pytest.param(dt.date(2027, 3, 7), False, _utc(2027, 3, 7, 0, 0, 0), id="yaml-date-start"),
        ],
    )
    def test_valid_points(self, value: Any, end: bool, expected: dt.datetime) -> None:
        errors: list[str] = []
        point = parse_point(value, end=end, errors=errors, label="test")
        assert errors == []
        assert point == expected
        assert point is not None
        assert point.tzinfo == UTC

    @pytest.mark.parametrize(
        ("value", "error_fragment"),
        [
            pytest.param(dt.datetime(2026, 6, 1, 9, 0, 0), "quote the value", id="yaml-datetime"),
            pytest.param("not-a-date", "not a valid truncated ISO 8601", id="garbage"),
            pytest.param("2026-06-01Z", "not a valid truncated ISO 8601", id="offset-without-time"),
            pytest.param([], "expected a timestamp string", id="list"),
            pytest.param(False, "boolean", id="bool"),
            pytest.param(123, "not a 4-digit year", id="int-3-digit"),
            # The UTC conversion would overflow datetime.max — must be an
            # error, not an uncaught OverflowError.
            pytest.param("9999-12-31T23:59:59-05:00", "not a valid date/time", id="offset-overflow"),
            # dt.timezone() rejects offsets of 24h or more — must be an
            # error, not an uncaught ValueError.
            pytest.param("2026-06-01T00:00:00+25:00", "not a valid date/time", id="offset-out-of-range"),
        ],
    )
    def test_rejections(self, value: Any, error_fragment: str) -> None:
        errors: list[str] = []
        point = parse_point(value, end=True, errors=errors, label="test")
        assert point is None
        assert len(errors) == 1
        assert error_fragment in errors[0]


class TestParsePeriod:
    """Full period values: bare, from/until mapping, or list of either."""

    def test_bare_string(self) -> None:
        errors: list[str] = []
        spans = parse_period("2026-06", errors, "test")
        assert errors == []
        assert spans == (PeriodSpan(start=_utc(2026, 6, 1, 0, 0, 0), end=_utc(2026, 6, 30, 23, 59, 59)),)

    def test_from_until_mapping(self) -> None:
        errors: list[str] = []
        spans = parse_period({"from": "2027-03-01", "until": "2027-03-07"}, errors, "test")
        assert errors == []
        assert spans == (PeriodSpan(start=_utc(2027, 3, 1, 0, 0, 0), end=_utc(2027, 3, 7, 23, 59, 59)),)

    def test_open_ended_forward(self) -> None:
        errors: list[str] = []
        spans = parse_period({"from": "2026-06-01"}, errors, "test")
        assert errors == []
        assert spans == (PeriodSpan(start=_utc(2026, 6, 1, 0, 0, 0), end=None),)

    def test_open_ended_backward(self) -> None:
        errors: list[str] = []
        spans = parse_period({"until": "2026-12-31"}, errors, "test")
        assert errors == []
        assert spans == (PeriodSpan(start=None, end=_utc(2026, 12, 31, 23, 59, 59)),)

    def test_list_mixes_forms_as_union(self) -> None:
        errors: list[str] = []
        spans = parse_period(["2027", {"from": "2028-01-01", "until": "2028-01-31"}], errors, "test")
        assert errors == []
        assert len(spans) == 2

    @pytest.mark.parametrize(
        ("value", "error_fragment"),
        [
            pytest.param({}, "must set 'from', 'until', or both", id="empty-mapping"),
            pytest.param(
                {"from": "2027-03-07", "until": "2027-03-01"},
                "inverted range",
                id="inverted",
            ),
            pytest.param({"from": "2027", "starts": "x"}, "unknown period key", id="unknown-key"),
            pytest.param([], "must not be empty", id="empty-list"),
            pytest.param([["2027"]], "nested period lists", id="nested-list"),
            pytest.param("2026-06-01T09:00:00", "finer than a day", id="bare-timestamp"),
        ],
    )
    def test_rejections(self, value: Any, error_fragment: str) -> None:
        errors: list[str] = []
        spans = parse_period(value, errors, "test")
        assert spans == ()
        assert any(error_fragment in e for e in errors)

    def test_same_point_from_until_is_valid(self) -> None:
        """from == until (one-second span) is not inverted."""
        errors: list[str] = []
        spans = parse_period(
            {"from": "2027-03-07T12:30:15", "until": "2027-03-07T12:30:15"},
            errors,
            "test",
        )
        assert errors == []
        assert spans[0].start == spans[0].end

    def test_list_accumulates_errors_per_item(self) -> None:
        errors: list[str] = []
        spans = parse_period(["2027", "garbage"], errors, "test")
        assert len(spans) == 1
        assert len(errors) == 1
        assert "test[1]" in errors[0]


class TestSpanPredicates:
    """Union/active/past helpers used by the reservation handler."""

    JUNE = PeriodSpan(start=_utc(2026, 6, 1), end=_utc(2026, 6, 30, 23, 59, 59))
    AUGUST = PeriodSpan(start=_utc(2026, 8, 1), end=_utc(2026, 8, 31, 23, 59, 59))

    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            pytest.param(_utc(2026, 6, 15), True, id="inside-first"),
            pytest.param(_utc(2026, 8, 15), True, id="inside-second"),
            pytest.param(_utc(2026, 7, 15), False, id="gap-between"),
            pytest.param(_utc(2026, 6, 1, 0, 0, 0), True, id="start-inclusive"),
            pytest.param(_utc(2026, 6, 30, 23, 59, 59), True, id="end-inclusive"),
            pytest.param(_utc(2025, 1, 1), False, id="before-all"),
            pytest.param(_utc(2027, 1, 1), False, id="after-all"),
        ],
    )
    def test_spans_active_union(self, now: dt.datetime, expected: bool) -> None:
        assert spans_active((self.JUNE, self.AUGUST), now) is expected

    def test_open_ended_span_active(self) -> None:
        open_forward = PeriodSpan(start=_utc(2026, 1, 1), end=None)
        assert spans_active((open_forward,), _utc(2099, 1, 1)) is True
        assert spans_active((open_forward,), _utc(2025, 1, 1)) is False

    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            pytest.param(_utc(2027, 1, 1), True, id="all-past"),
            pytest.param(_utc(2026, 7, 15), False, id="one-still-ahead"),
            pytest.param(_utc(2026, 6, 30, 23, 59, 59), False, id="end-boundary-not-past"),
        ],
    )
    def test_spans_all_past(self, now: dt.datetime, expected: bool) -> None:
        assert spans_all_past((self.JUNE, self.AUGUST), now) is expected

    def test_open_ended_never_past(self) -> None:
        assert spans_all_past((PeriodSpan(start=_utc(2026, 1, 1), end=None),), _utc(2099, 1, 1)) is False

    def test_empty_spans_not_past(self) -> None:
        assert spans_all_past((), _utc(2026, 1, 1)) is False
