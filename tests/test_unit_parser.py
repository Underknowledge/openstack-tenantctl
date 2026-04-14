"""Tests for unit_parser module — human-readable quota units."""

from __future__ import annotations

import pytest

from src.unit_parser import parse_quota_value


def _parse(value: object, target: str = "MB", field: str = "compute.ram", label: str = "test") -> tuple[int, list[str]]:
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
            ("50MB", "MB", "compute.ram", 50),
            ("50GB", "MB", "compute.ram", 50000),
            ("2TB", "MB", "compute.ram", 2000000),
            ("1PB", "MB", "compute.ram", 1000000000),
            ("500GB", "GB", "block_storage.gigabytes", 500),
            ("2TB", "GB", "block_storage.gigabytes", 2000),
            # Binary units (base-2)
            ("1024KiB", "MB", "compute.ram", 1),
            ("50MiB", "MB", "compute.ram", 52),
            ("50GiB", "MB", "compute.ram", 53687),
            ("2TiB", "MB", "compute.ram", 2199023),
            ("1PiB", "MB", "compute.ram", 1125899907),
            ("2TiB", "GB", "block_storage.gigabytes", 2199),
            # Shorthand aliases (binary)
            ("1024K", "MB", "compute.ram", 1),
            ("50M", "MB", "compute.ram", 52),
            ("50G", "MB", "compute.ram", 53687),
            ("2T", "MB", "compute.ram", 2199023),
            ("1P", "MB", "compute.ram", 1125899907),
            # backup_gigabytes target (unique field)
            ("500GB", "GB", "block_storage.backup_gigabytes", 500),
            # Edge cases
            ("0.001GB", "MB", "compute.ram", 1),
            ("1GiB", "MB", "compute.ram", 1074),
        ],
        ids=[
            "1000KB->MB",
            "50MB->MB",
            "50GB->MB",
            "2TB->MB",
            "1PB->MB",
            "500GB->GB",
            "2TB->GB",
            "1024KiB->MB",
            "50MiB->MB",
            "50GiB->MB",
            "2TiB->MB",
            "1PiB->MB",
            "2TiB->GB",
            "1024K->MB",
            "50M->MB",
            "50G->MB",
            "2T->MB",
            "1P->MB",
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
        assert result == 50000
        assert errors == []


class TestFractionalValues:
    """Fractional values are parsed and rounded."""

    @pytest.mark.parametrize(
        ("value", "target", "field", "expected"),
        [
            ("1.5GB", "MB", "compute.ram", 1500),
            ("1.4GB", "MB", "compute.ram", 1400),
            ("1.029GB", "MB", "compute.ram", 1029),
            ("1.5TB", "GB", "block_storage.gigabytes", 1500),
            ("1.029GB", "GB", "block_storage.gigabytes", 1),
        ],
        ids=["1.5GB->MB", "1.4GB->MB", "1.029GB->MB", "1.5TB->GB", "1.029GB->GB"],
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
        assert decimal_result == 100000
        assert binary_result == 107374
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
        parse_quota_value("invalid", "MB", field, errors, label)
        assert expected_fragment in errors[0]

    def test_error_includes_invalid_value(self) -> None:
        """Error message includes the invalid value."""
        _, errors = _parse("50XB")
        assert "50XB" in errors[0] or "XB" in errors[0]
