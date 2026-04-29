"""Tests for unit_parser module — human-readable quota units."""

from __future__ import annotations

import pytest

from src.unit_parser import parse_quota_value


def _parse(
    value: object, target: str = "MiB", field: str = "compute.ram", label: str = "test"
) -> tuple[int, list[str]]:
    """Helper: call parse_quota_value, return (result, errors)."""
    errors: list[str] = []
    result = parse_quota_value(value, target, field, errors, label)
    return result, errors


class TestIntegerPassthrough:
    """Backward compatibility: integers pass through unchanged."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (51200, 51200),
            (0, 0),
            (-1, -1),
        ],
        ids=["positive", "zero", "unlimited"],
    )
    def test_valid_integers_pass_through(self, value: int, expected: int) -> None:
        result, errors = _parse(value)
        assert result == expected
        assert errors == []

    def test_negative_integer_error(self) -> None:
        """Negative integers (except -1) produce error."""
        result, errors = _parse(-10)
        assert result == 0
        assert len(errors) == 1
        assert "must be -1 (unlimited) or a non-negative integer" in errors[0]


class TestUnitConversions:
    """Decimal, binary, and shorthand conversions all in one parametrized test."""

    @pytest.mark.parametrize(
        ("value", "target", "field", "expected"),
        [
            # Decimal units (base-10)
            ("1000KB", "MB", "compute.ram", 1),
            ("50MB", "MiB", "compute.ram", 48),
            ("50GB", "MiB", "compute.ram", 47684),
            ("2TB", "MiB", "compute.ram", 1907349),
            ("1PB", "MiB", "compute.ram", 953674316),
            ("500GB", "GB", "block_storage.gigabytes", 500),
            ("2TB", "GB", "block_storage.gigabytes", 2000),
            # Binary units (base-2)
            ("1024KiB", "MiB", "compute.ram", 1),
            ("50MiB", "MiB", "compute.ram", 50),
            ("50GiB", "MiB", "compute.ram", 51200),
            ("2TiB", "MiB", "compute.ram", 2097152),
            ("1PiB", "MiB", "compute.ram", 1073741824),
            ("2TiB", "GB", "block_storage.gigabytes", 2199),
            # Shorthand aliases (binary)
            ("1024K", "MiB", "compute.ram", 1),
            ("50M", "MiB", "compute.ram", 50),
            ("50G", "MiB", "compute.ram", 51200),
            ("2T", "MiB", "compute.ram", 2097152),
            ("1P", "MiB", "compute.ram", 1073741824),
            # backup_gigabytes target (unique field)
            ("500GB", "GB", "block_storage.backup_gigabytes", 500),
            # Edge cases
            ("0.001GB", "MiB", "compute.ram", 1),
            ("1GiB", "MiB", "compute.ram", 1024),
        ],
        ids=[
            "1000KB->MB",
            "50MB->MiB",
            "50GB->MiB",
            "2TB->MiB",
            "1PB->MiB",
            "500GB->GB",
            "2TB->GB",
            "1024KiB->MiB",
            "50MiB->MiB",
            "50GiB->MiB",
            "2TiB->MiB",
            "1PiB->MiB",
            "2TiB->GB",
            "1024K->MiB",
            "50M->MiB",
            "50G->MiB",
            "2T->MiB",
            "1P->MiB",
            "500GB->GB-backup",
            "tiny-fractional",
            "exact-power-of-2",
        ],
    )
    def test_conversion(self, value: str, target: str, field: str, expected: int) -> None:
        result, errors = _parse(value, target, field)
        assert result == expected
        assert errors == []


class TestWhitespaceHandling:
    """Whitespace variations are handled correctly."""

    @pytest.mark.parametrize(
        "value",
        ["50GB", "50 GB", "50   GB", "  50GB", "50GB  "],
        ids=["none", "single", "multiple", "leading", "trailing"],
    )
    def test_whitespace_variants(self, value: str) -> None:
        result, errors = _parse(value)
        assert result == 47684
        assert errors == []


class TestFractionalValues:
    """Fractional values are parsed and rounded."""

    @pytest.mark.parametrize(
        ("value", "target", "field", "expected"),
        [
            ("1.5GB", "MiB", "compute.ram", 1431),
            ("1.4GB", "MiB", "compute.ram", 1335),
            ("1.029GB", "MiB", "compute.ram", 981),
            ("1.5TB", "GB", "block_storage.gigabytes", 1500),
            ("1.029GB", "GB", "block_storage.gigabytes", 1),
        ],
        ids=["1.5GB->MiB", "1.4GB->MiB", "1.029GB->MiB", "1.5TB->GB", "1.029GB->GB"],
    )
    def test_fractional_conversion(self, value: str, target: str, field: str, expected: int) -> None:
        result, errors = _parse(value, target, field)
        assert result == expected
        assert errors == []


class TestErrorCases:
    """Invalid inputs produce clear error messages."""

    @pytest.mark.parametrize(
        ("value", "expected_fragment"),
        [
            ("fifty gigabytes", "invalid format"),
            ("50", "invalid format"),
            ("-10GB", "cannot use negative values with units"),
            ("999PB", "too large"),
        ],
        ids=["text-only", "number-without-unit", "negative-with-unit", "overflow"],
    )
    def test_invalid_input(self, value: str, expected_fragment: str) -> None:
        result, errors = _parse(value)
        assert result == 0
        assert len(errors) == 1
        assert expected_fragment in errors[0]

    def test_unknown_unit_suggests_alternatives(self) -> None:
        """Unknown unit produces error with suggestions."""
        result, errors = _parse("50XB")
        assert result == 0
        assert len(errors) == 1
        assert "unknown unit" in errors[0]
        assert "XB" in errors[0]
        assert "GB" in errors[0]
        assert "GiB" in errors[0]

    @pytest.mark.parametrize(
        "value",
        [[50], {"value": 50}],
        ids=["list", "dict"],
    )
    def test_wrong_type(self, value: object) -> None:
        result, errors = _parse(value)  # type: ignore[arg-type]
        assert result == 0
        assert len(errors) == 1
        assert "must be an integer or unit string" in errors[0]

    def test_overflow_includes_max_value(self) -> None:
        """Overflow error mentions MAX_QUOTA_VALUE."""
        _, errors = _parse("999PB")
        assert "2147483647" in errors[0]

    def test_negative_with_unit_hints_unlimited(self) -> None:
        """Negative-with-unit error suggests using -1."""
        _, errors = _parse("-10GB")
        assert "Use -1 (without units) for unlimited" in errors[0]


class TestDecimalVsBinaryDifference:
    """Decimal vs binary units produce different results."""

    def test_decimal_vs_binary_difference(self) -> None:
        decimal_result, _ = _parse("100GB")
        binary_result, _ = _parse("100GiB")
        assert decimal_result == 95367
        assert binary_result == 102400
        assert binary_result > decimal_result


class TestErrorMessagesQuality:
    """Error messages include helpful context."""

    @pytest.mark.parametrize(
        ("label", "field", "expected_fragment"),
        [
            ("config/dev.yaml", "compute.ram", "config/dev.yaml"),
            ("test", "compute.ram", "compute.ram"),
        ],
        ids=["includes-label", "includes-field"],
    )
    def test_error_context(self, label: str, field: str, expected_fragment: str) -> None:
        errors: list[str] = []
        parse_quota_value("invalid", "MiB", field, errors, label)
        assert expected_fragment in errors[0]

    def test_error_includes_invalid_value(self) -> None:
        """Error message includes the invalid value."""
        _, errors = _parse("50XB")
        assert "50XB" in errors[0] or "XB" in errors[0]
