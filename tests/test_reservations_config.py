"""Tests for the reservation config model and grant-tracking state entries."""

from __future__ import annotations

from typing import Any

import pytest

from src.models import (
    GrantedFlavorAccessEntry,
    ProjectConfig,
    ReservationConfig,
    RevokedFlavorAccessEntry,
)
from src.state_store import STATE_KEYS, InMemoryStateStore


class TestReservationValidate:
    """Manual validation of a single reservation entry."""

    def test_valid_entry(self) -> None:
        errors: list[str] = []
        cfg = ReservationConfig.validate(
            {"name": "gpu-june-2026", "period": "2026-06", "flavors": ["gpu.*", "highmem.xlarge"]},
            errors,
            "test",
        )
        assert errors == []
        assert cfg is not None
        assert cfg.name == "gpu-june-2026"
        assert cfg.flavors == ("gpu.*", "highmem.xlarge")
        assert len(cfg.period) == 1

    def test_period_list_union(self) -> None:
        errors: list[str] = []
        cfg = ReservationConfig.validate(
            {"period": ["2027", "2028-01"], "flavors": ["fpga-*"]},
            errors,
            "test",
        )
        assert errors == []
        assert cfg is not None
        assert len(cfg.period) == 2

    @pytest.mark.parametrize(
        ("data", "error_fragment"),
        [
            pytest.param({"flavors": ["gpu.*"]}, "missing required field 'period'", id="missing-period"),
            pytest.param({"period": "2026-06"}, "at least one grant key", id="period-only-grants-nothing"),
            pytest.param(
                {"period": "2026-06", "flavors": ["*"]},
                "bare '*' flavor pattern is rejected",
                id="bare-star",
            ),
            pytest.param({"period": "2026-06", "flavors": []}, "non-empty list", id="empty-flavors"),
            pytest.param(
                {"name": "gpu:june", "period": "2026-06", "flavors": ["gpu.*"]},
                "name must not contain ':'",
                id="name-with-colon",
            ),
            pytest.param({"period": "2026-06", "flavors": [""]}, "non-empty strings", id="empty-flavor-string"),
            pytest.param(
                {"period": "2026-06", "flavors": ["gpu.*"], "images": ["ubuntu-*"]},
                "'images' is reserved for a future phase",
                id="images-reserved",
            ),
            pytest.param(
                {"period": "2026-06", "flavors": ["gpu.*"], "on_expiry": {"instances": "shelve"}},
                "'on_expiry' is reserved for a future phase",
                id="on-expiry-reserved",
            ),
            pytest.param(
                {"period": "2026-06", "flavors": ["gpu.*"], "until": "2027"},
                "unknown key 'until'",
                id="unknown-key",
            ),
            pytest.param(
                {"period": "2026-06", "flavors": ["gpu.*"], "name": ""},
                "name must be a non-empty string",
                id="empty-name",
            ),
            pytest.param("2026-06", "must be a mapping", id="not-a-mapping"),
        ],
    )
    def test_rejections(self, data: Any, error_fragment: str) -> None:
        errors: list[str] = []
        ReservationConfig.validate(data, errors, "test")
        assert any(error_fragment in e for e in errors), errors


class TestProjectReservationsValidation:
    """Reservations wiring inside ProjectConfig.validate."""

    @staticmethod
    def _base(reservations: Any) -> dict[str, Any]:
        return {
            "name": "acme-eu",
            "resource_prefix": "acmeeu",
            "network": {"subnet": {"cidr": "10.0.1.0/24"}},
            "reservations": reservations,
        }

    def test_duplicate_names_rejected(self) -> None:
        errors: list[str] = []
        ProjectConfig.validate(
            self._base(
                [
                    {"name": "gpu", "period": "2026-06", "flavors": ["gpu.*"]},
                    {"name": "gpu", "period": "2026-07", "flavors": ["highmem.*"]},
                ]
            ),
            errors,
            "acme-eu",
        )
        assert any("not unique" in e for e in errors), errors

    def test_unnamed_entries_do_not_collide(self) -> None:
        errors: list[str] = []
        cfg = ProjectConfig.validate(
            self._base(
                [
                    {"period": "2026-06", "flavors": ["gpu.*"]},
                    {"period": "2026-07", "flavors": ["highmem.*"]},
                ]
            ),
            errors,
            "acme-eu",
        )
        assert errors == []
        assert cfg is not None
        assert len(cfg.reservations) == 2

    def test_reservations_must_be_list(self) -> None:
        errors: list[str] = []
        ProjectConfig.validate(self._base({"period": "2026-06"}), errors, "acme-eu")
        assert any("reservations must be a list" in e for e in errors), errors

    def test_state_entries_loaded_from_dict(self) -> None:
        data = self._base([{"name": "gpu", "period": "2026-06", "flavors": ["gpu.*"]}])
        data["granted_flavor_access"] = [
            {
                "flavor_id": "f1",
                "flavor_name": "gpu.large",
                "reservation": "gpu",
                "granted_at": "2026-06-01T00:00:00+00:00",
            }
        ]
        data["revoked_flavor_access"] = [
            {
                "flavor_id": "f0",
                "flavor_name": "gpu.old",
                "reservation": "gpu",
                "revoked_at": "2026-05-01T00:00:00+00:00",
                "reason": "no active reservation covers this flavor",
            }
        ]
        cfg = ProjectConfig.from_dict(data)
        assert cfg.granted_flavor_access == [
            GrantedFlavorAccessEntry(
                flavor_id="f1",
                flavor_name="gpu.large",
                reservation="gpu",
                granted_at="2026-06-01T00:00:00+00:00",
            )
        ]
        assert cfg.revoked_flavor_access[0].reason == "no active reservation covers this flavor"


class TestGrantStateEntries:
    """Serialization round-trips for the new state entries."""

    def test_granted_round_trip(self) -> None:
        entry = GrantedFlavorAccessEntry(
            flavor_id="f1",
            flavor_name="gpu.large",
            reservation="gpu-june",
            granted_at="2026-06-01T00:00:00+00:00",
        )
        assert GrantedFlavorAccessEntry.from_dict(entry.to_dict()) == entry

    def test_revoked_round_trip(self) -> None:
        entry = RevokedFlavorAccessEntry(
            flavor_id="f1",
            flavor_name="gpu.large",
            reservation="gpu-june",
            revoked_at="2026-07-01T00:00:00+00:00",
            reason="reservation entry removed from config",
        )
        assert RevokedFlavorAccessEntry.from_dict(entry.to_dict()) == entry

    def test_from_dict_ignores_unknown_keys(self) -> None:
        entry = GrantedFlavorAccessEntry.from_dict(
            {
                "flavor_id": "f1",
                "flavor_name": "gpu.large",
                "reservation": "gpu-june",
                "granted_at": "2026-06-01T00:00:00+00:00",
                "future_field": "ignored",
            }
        )
        assert entry.flavor_id == "f1"

    def test_state_keys_include_flavor_access_lists(self) -> None:
        assert "granted_flavor_access" in STATE_KEYS
        assert "revoked_flavor_access" in STATE_KEYS

    def test_state_store_persists_entry_dicts(self) -> None:
        store = InMemoryStateStore()
        entry = GrantedFlavorAccessEntry(
            flavor_id="f1",
            flavor_name="gpu.large",
            reservation="gpu-june",
            granted_at="2026-06-01T00:00:00+00:00",
        )
        store.save("proj", ["granted_flavor_access"], [entry.to_dict()])
        assert store.load("proj")["granted_flavor_access"][0]["flavor_name"] == "gpu.large"
