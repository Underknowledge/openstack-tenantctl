"""Tests for unit_parser module — human-readable quota units."""

from __future__ import annotations

from src.unit_parser import parse_quota_value


class TestIntegerPassthrough:
    """Backward compatibility: integers pass through unchanged."""

    def test_positive_integer_passthrough(self) -> None:
        """Positive integers pass through unchanged."""
        errors: list[str] = []
        result = parse_quota_value(51200, "MB", "compute.ram", errors, "test")
        assert result == 51200
        assert errors == []

    def test_zero_passthrough(self) -> None:
        """Zero passes through unchanged."""
        errors: list[str] = []
        result = parse_quota_value(0, "MB", "compute.ram", errors, "test")
        assert result == 0
        assert errors == []

    def test_unlimited_minus_one(self) -> None:
        """Special value -1 (unlimited) passes through."""
        errors: list[str] = []
        result = parse_quota_value(-1, "MB", "compute.ram", errors, "test")
        assert result == -1
        assert errors == []

    def test_negative_integer_error(self) -> None:
        """Negative integers (except -1) produce error."""
        errors: list[str] = []
        result = parse_quota_value(-10, "MB", "compute.ram", errors, "test")
        assert result == 0
        assert len(errors) == 1
        assert "must be -1 (unlimited) or a non-negative integer" in errors[0]


class TestDecimalUnits:
    """Decimal units (KB, MB, GB, TB, PB) use base-10."""

    def test_kilobytes_to_mb(self) -> None:
        """1000 KB = 1 MB (decimal)."""
        errors: list[str] = []
        result = parse_quota_value("1000KB", "MB", "compute.ram", errors, "test")
        assert result == 1
        assert errors == []

    def test_megabytes_to_mb(self) -> None:
        """50 MB = 50 MB."""
        errors: list[str] = []
        result = parse_quota_value("50MB", "MB", "compute.ram", errors, "test")
        assert result == 50
        assert errors == []

    def test_gigabytes_to_mb(self) -> None:
        """50 GB = 50000 MB (decimal)."""
        errors: list[str] = []
        result = parse_quota_value("50GB", "MB", "compute.ram", errors, "test")
        assert result == 50000
        assert errors == []

    def test_terabytes_to_mb(self) -> None:
        """2 TB = 2000000 MB (decimal)."""
        errors: list[str] = []
        result = parse_quota_value("2TB", "MB", "compute.ram", errors, "test")
        assert result == 2000000
        assert errors == []

    def test_petabytes_to_mb(self) -> None:
        """1 PB = 1000000000 MB (decimal)."""
        errors: list[str] = []
        result = parse_quota_value("1PB", "MB", "compute.ram", errors, "test")
        assert result == 1000000000
        assert errors == []

    def test_gigabytes_to_gb(self) -> None:
        """500 GB = 500 GB (storage target unit)."""
        errors: list[str] = []
        result = parse_quota_value("500GB", "GB", "block_storage.gigabytes", errors, "test")
        assert result == 500
        assert errors == []

    def test_terabytes_to_gb(self) -> None:
        """2 TB = 2000 GB (decimal)."""
        errors: list[str] = []
        result = parse_quota_value("2TB", "GB", "block_storage.gigabytes", errors, "test")
        assert result == 2000
        assert errors == []


class TestBinaryUnits:
    """Binary units (KiB, MiB, GiB, TiB, PiB) use base-2."""

    def test_kibibytes_to_mb(self) -> None:
        """1024 KiB = 1.024 MB ≈ 1 MB (rounded)."""
        errors: list[str] = []
        result = parse_quota_value("1024KiB", "MB", "compute.ram", errors, "test")
        assert result == 1
        assert errors == []

    def test_mebibytes_to_mb(self) -> None:
        """50 MiB = 52.4288 MB ≈ 52 MB."""
        errors: list[str] = []
        result = parse_quota_value("50MiB", "MB", "compute.ram", errors, "test")
        assert result == 52
        assert errors == []

    def test_gibibytes_to_mb(self) -> None:
        """50 GiB = 53687.09... MB ≈ 53687 MB."""
        errors: list[str] = []
        result = parse_quota_value("50GiB", "MB", "compute.ram", errors, "test")
        assert result == 53687
        assert errors == []

    def test_tebibytes_to_mb(self) -> None:
        """2 TiB = 2199023.255... MB ≈ 2199023 MB."""
        errors: list[str] = []
        result = parse_quota_value("2TiB", "MB", "compute.ram", errors, "test")
        assert result == 2199023
        assert errors == []

    def test_pebibytes_to_mb(self) -> None:
        """1 PiB = 1125899906.8... MB ≈ 1125899907 MB."""
        errors: list[str] = []
        result = parse_quota_value("1PiB", "MB", "compute.ram", errors, "test")
        assert result == 1125899907
        assert errors == []

    def test_gibibytes_to_gb(self) -> None:
        """2 TiB = 2199.02... GB ≈ 2199 GB (storage)."""
        errors: list[str] = []
        result = parse_quota_value("2TiB", "GB", "block_storage.gigabytes", errors, "test")
        assert result == 2199
        assert errors == []


class TestShorthandAliases:
    """Shorthand aliases (K, M, G, T, P) map to binary units."""

    def test_k_alias_kibibytes(self) -> None:
        """K = KiB (binary)."""
        errors: list[str] = []
        result = parse_quota_value("1024K", "MB", "compute.ram", errors, "test")
        assert result == 1
        assert errors == []

    def test_m_alias_mebibytes(self) -> None:
        """M = MiB (binary)."""
        errors: list[str] = []
        result = parse_quota_value("50M", "MB", "compute.ram", errors, "test")
        assert result == 52
        assert errors == []

    def test_g_alias_gibibytes(self) -> None:
        """G = GiB (binary), most common shorthand."""
        errors: list[str] = []
        result = parse_quota_value("50G", "MB", "compute.ram", errors, "test")
        assert result == 53687
        assert errors == []

    def test_t_alias_tebibytes(self) -> None:
        """T = TiB (binary)."""
        errors: list[str] = []
        result = parse_quota_value("2T", "MB", "compute.ram", errors, "test")
        assert result == 2199023
        assert errors == []

    def test_p_alias_pebibytes(self) -> None:
        """P = PiB (binary)."""
        errors: list[str] = []
        result = parse_quota_value("1P", "MB", "compute.ram", errors, "test")
        assert result == 1125899907
        assert errors == []


class TestWhitespaceHandling:
    """Whitespace variations are handled correctly."""

    def test_no_whitespace(self) -> None:
        """'50GB' (no space)."""
        errors: list[str] = []
        result = parse_quota_value("50GB", "MB", "compute.ram", errors, "test")
        assert result == 50000
        assert errors == []

    def test_single_space(self) -> None:
        """'50 GB' (one space)."""
        errors: list[str] = []
        result = parse_quota_value("50 GB", "MB", "compute.ram", errors, "test")
        assert result == 50000
        assert errors == []

    def test_multiple_spaces(self) -> None:
        """'50   GB' (multiple spaces)."""
        errors: list[str] = []
        result = parse_quota_value("50   GB", "MB", "compute.ram", errors, "test")
        assert result == 50000
        assert errors == []

    def test_leading_whitespace(self) -> None:
        """'  50GB' (leading space)."""
        errors: list[str] = []
        result = parse_quota_value("  50GB", "MB", "compute.ram", errors, "test")
        assert result == 50000
        assert errors == []

    def test_trailing_whitespace(self) -> None:
        """'50GB  ' (trailing space)."""
        errors: list[str] = []
        result = parse_quota_value("50GB  ", "MB", "compute.ram", errors, "test")
        assert result == 50000
        assert errors == []


class TestFractionalValues:
    """Fractional values are parsed and rounded."""

    def test_fractional_gigabytes(self) -> None:
        """1.5 GB = 1500 MB."""
        errors: list[str] = []
        result = parse_quota_value("1.5GB", "MB", "compute.ram", errors, "test")
        assert result == 1500
        assert errors == []

    def test_fractional_rounding_down(self) -> None:
        """1.4 GB = 1400 MB (rounds to 1400)."""
        errors: list[str] = []
        result = parse_quota_value("1.4GB", "MB", "compute.ram", errors, "test")
        assert result == 1400
        assert errors == []

    def test_fractional_rounding_up(self) -> None:
        """1.029 GB = 1029 MB (rounds to 1029)."""
        errors: list[str] = []
        result = parse_quota_value("1.029GB", "MB", "compute.ram", errors, "test")
        assert result == 1029
        assert errors == []

    def test_fractional_storage_target(self) -> None:
        """1.5 TB = 1500 GB (storage target unit)."""
        errors: list[str] = []
        result = parse_quota_value("1.5TB", "GB", "block_storage.gigabytes", errors, "test")
        assert result == 1500
        assert errors == []

    def test_rounding_nearest_integer(self) -> None:
        """1.029 GB rounds to nearest int (1 GB for storage)."""
        errors: list[str] = []
        result = parse_quota_value("1.029GB", "GB", "block_storage.gigabytes", errors, "test")
        assert result == 1
        assert errors == []


class TestErrorCases:
    """Invalid inputs produce clear error messages."""

    def test_invalid_format_text(self) -> None:
        """Text without numbers is invalid."""
        errors: list[str] = []
        result = parse_quota_value("fifty gigabytes", "MB", "compute.ram", errors, "test")
        assert result == 0
        assert len(errors) == 1
        assert "invalid format" in errors[0]

    def test_invalid_format_number_only(self) -> None:
        """Number without unit is invalid when string."""
        errors: list[str] = []
        result = parse_quota_value("50", "MB", "compute.ram", errors, "test")
        assert result == 0
        assert len(errors) == 1
        assert "invalid format" in errors[0]

    def test_unknown_unit(self) -> None:
        """Unknown unit produces error with suggestions."""
        errors: list[str] = []
        result = parse_quota_value("50XB", "MB", "compute.ram", errors, "test")
        assert result == 0
        assert len(errors) == 1
        assert "unknown unit" in errors[0]
        assert "XB" in errors[0]
        # Should suggest valid units
        assert "GB" in errors[0]
        assert "GiB" in errors[0]

    def test_negative_with_unit(self) -> None:
        """Negative values with units are ambiguous."""
        errors: list[str] = []
        result = parse_quota_value("-10GB", "MB", "compute.ram", errors, "test")
        assert result == 0
        assert len(errors) == 1
        assert "cannot use negative values with units" in errors[0]
        assert "Use -1 (without units) for unlimited" in errors[0]

    def test_wrong_type_list(self) -> None:
        """Wrong type (list) produces error."""
        errors: list[str] = []
        result = parse_quota_value([50], "MB", "compute.ram", errors, "test")  # type: ignore[arg-type]
        assert result == 0
        assert len(errors) == 1
        assert "must be an integer or unit string" in errors[0]
        assert "list" in errors[0]

    def test_wrong_type_dict(self) -> None:
        """Wrong type (dict) produces error."""
        errors: list[str] = []
        result = parse_quota_value({"value": 50}, "MB", "compute.ram", errors, "test")  # type: ignore[arg-type]
        assert result == 0
        assert len(errors) == 1
        assert "must be an integer or unit string" in errors[0]
        assert "dict" in errors[0]

    def test_overflow_petabytes(self) -> None:
        """Very large values produce overflow error."""
        errors: list[str] = []
        result = parse_quota_value("999PB", "MB", "compute.ram", errors, "test")
        assert result == 0
        assert len(errors) == 1
        assert "too large" in errors[0]
        assert "2147483647" in errors[0]  # MAX_QUOTA_VALUE


class TestTargetUnitConversion:
    """Verify correct conversion for both target units (MB, GB)."""

    def test_ram_target_mb(self) -> None:
        """RAM quotas convert to MB."""
        errors: list[str] = []
        result = parse_quota_value("50GB", "MB", "compute.ram", errors, "test")
        assert result == 50000
        assert errors == []

    def test_storage_target_gb(self) -> None:
        """Storage quotas convert to GB."""
        errors: list[str] = []
        result = parse_quota_value("2TB", "GB", "block_storage.gigabytes", errors, "test")
        assert result == 2000
        assert errors == []

    def test_backup_storage_target_gb(self) -> None:
        """Backup storage quotas convert to GB."""
        errors: list[str] = []
        result = parse_quota_value("500GB", "GB", "block_storage.backup_gigabytes", errors, "test")
        assert result == 500
        assert errors == []


class TestEdgeCasesAndSpecialValues:
    """Edge cases and special values."""

    def test_very_small_fractional(self) -> None:
        """Very small fractional values round correctly."""
        errors: list[str] = []
        result = parse_quota_value("0.001GB", "MB", "compute.ram", errors, "test")
        assert result == 1  # 1 MB (rounded)
        assert errors == []

    def test_exact_power_of_two(self) -> None:
        """Exact power of 2 conversions."""
        errors: list[str] = []
        # 1 GiB = 1024 MiB = 1073.741824 MB ≈ 1074 MB
        result = parse_quota_value("1GiB", "MB", "compute.ram", errors, "test")
        assert result == 1074
        assert errors == []

    def test_decimal_vs_binary_difference(self) -> None:
        """Decimal vs binary units produce different results."""
        errors_decimal: list[str] = []
        errors_binary: list[str] = []
        decimal_result = parse_quota_value("100GB", "MB", "compute.ram", errors_decimal, "test")
        binary_result = parse_quota_value("100GiB", "MB", "compute.ram", errors_binary, "test")

        # 100 GB = 100000 MB (decimal)
        assert decimal_result == 100000
        # 100 GiB ≈ 107374 MB (binary)
        assert binary_result == 107374
        # Binary is larger
        assert binary_result > decimal_result


class TestErrorMessagesQuality:
    """Error messages include helpful context."""

    def test_error_includes_label(self) -> None:
        """Error message includes the label (file path)."""
        errors: list[str] = []
        parse_quota_value("invalid", "MB", "compute.ram", errors, "config/dev.yaml")
        assert "config/dev.yaml" in errors[0]

    def test_error_includes_field_name(self) -> None:
        """Error message includes the field name."""
        errors: list[str] = []
        parse_quota_value("invalid", "MB", "compute.ram", errors, "test")
        assert "compute.ram" in errors[0]

    def test_error_includes_invalid_value(self) -> None:
        """Error message includes the invalid value."""
        errors: list[str] = []
        parse_quota_value("50XB", "MB", "compute.ram", errors, "test")
        assert "50XB" in errors[0] or "XB" in errors[0]
