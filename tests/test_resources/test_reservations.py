"""Tests for reservation flavor-access reconciliation — ensure_reservations."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from openstack.exceptions import ConflictException, NotFoundException

from src.models import GrantedFlavorAccessEntry, ReservationConfig, RevokedFlavorAccessEntry
from src.resources.reservations import ensure_reservations, revoke_all_reservation_grants
from src.utils import ActionStatus, SharedContext

if TYPE_CHECKING:
    import pytest

    from src.models import ProjectConfig

PROJECT_ID = "proj-id-123"

ACTIVE_GPU = {"name": "gpu-program", "period": {"from": "2000-01-01"}, "flavors": ["gpu.*"]}
PAST_GPU = {"name": "gpu-program", "period": "2000", "flavors": ["gpu.*"]}


def _flavor(flavor_id: str, name: str, *, is_public: bool = False) -> MagicMock:
    flavor = MagicMock()
    flavor.id = flavor_id
    flavor.name = name
    flavor.is_public = is_public
    return flavor


def _reservation(data: dict[str, Any]) -> ReservationConfig:
    return ReservationConfig.from_dict(data)


def _grant(flavor_id: str, flavor_name: str, reservation: str = "gpu-program") -> GrantedFlavorAccessEntry:
    return GrantedFlavorAccessEntry(
        flavor_id=flavor_id,
        flavor_name=flavor_name,
        reservation=reservation,
        granted_at="2026-01-01T00:00:00+00:00",
    )


def _cfg(sample: ProjectConfig, **overrides: Any) -> ProjectConfig:
    return dataclasses.replace(sample, **overrides)


def _saved_value(store: MagicMock, key: str) -> Any:
    """Return the last value saved under a key path ending in *key*."""
    for call in reversed(store.save.call_args_list):
        if call.args[1][-1] == key:
            return call.args[2]
    return None


class TestGranting:
    def test_grants_matching_private_flavors(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Active entry grants access to every matching private flavor and tracks it."""
        cfg = _cfg(sample_project_cfg, reservations=[_reservation(ACTIVE_GPU)])
        shared_ctx.conn.compute.flavors.return_value = [
            _flavor("f-large", "gpu.large"),
            _flavor("f-small", "gpu.small"),
            _flavor("f-std", "std.small"),
        ]

        actions = ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert {a.name for a in created} == {"gpu.large", "gpu.small"}
        assert shared_ctx.conn.compute.flavor_add_tenant_access.call_count == 2

        saved = _saved_value(shared_ctx.state_store, "granted_flavor_access")
        assert {g["flavor_name"] for g in saved} == {"gpu.large", "gpu.small"}
        assert all(g["reservation"] == "gpu-program" for g in saved)

    def test_preexisting_access_means_manual_grant_and_is_not_tracked(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Access present before we grant is a manual grant — never tracked (DD-027)."""
        cfg = _cfg(sample_project_cfg, reservations=[_reservation(ACTIVE_GPU)])
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-large", "gpu.large")]
        shared_ctx.conn.compute.get_flavor_access.return_value = [{"tenant_id": PROJECT_ID}]

        actions = ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        skipped = [a for a in actions if a.status == ActionStatus.SKIPPED]
        assert any("manual grant" in a.details for a in skipped)
        shared_ctx.conn.compute.flavor_add_tenant_access.assert_not_called()
        saved = _saved_value(shared_ctx.state_store, "granted_flavor_access")
        assert saved is None  # tracked list unchanged — nothing persisted

    def test_conflict_after_clean_precheck_is_tracked(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """A 409 when the pre-check saw no access is our own retried call — tracked.

        Without the pre-check, a grant whose response was lost and retried
        would be misread as a manual grant and leak forever.
        """
        cfg = _cfg(sample_project_cfg, reservations=[_reservation(ACTIVE_GPU)])
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-large", "gpu.large")]
        shared_ctx.conn.compute.flavor_add_tenant_access.side_effect = ConflictException

        actions = ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert {a.name for a in created} == {"gpu.large"}
        saved = _saved_value(shared_ctx.state_store, "granted_flavor_access")
        assert {g["flavor_name"] for g in saved} == {"gpu.large"}

    def test_public_flavor_matching_pattern_is_skipped(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        cfg = _cfg(sample_project_cfg, reservations=[_reservation(ACTIVE_GPU)])
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-pub", "gpu.public", is_public=True)]

        ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        shared_ctx.conn.compute.flavor_add_tenant_access.assert_not_called()

    def test_overlapping_entries_grant_once(self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig) -> None:
        """A flavor covered by two active entries is granted once (union semantics)."""
        cfg = _cfg(
            sample_project_cfg,
            reservations=[
                _reservation(ACTIVE_GPU),
                _reservation({"name": "second", "period": {"from": "2000-01-01"}, "flavors": ["gpu.large"]}),
            ],
        )
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-large", "gpu.large")]

        ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        shared_ctx.conn.compute.flavor_add_tenant_access.assert_called_once_with("f-large", PROJECT_ID)
        saved = _saved_value(shared_ctx.state_store, "granted_flavor_access")
        assert saved[0]["reservation"] == "gpu-program"  # first covering entry wins


class TestRevoking:
    def test_revokes_tracked_grant_when_period_ended(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Entry still in config but inactive: tracked grants are revoked."""
        cfg = _cfg(
            sample_project_cfg,
            reservations=[_reservation(PAST_GPU)],
            granted_flavor_access=[_grant("f-large", "gpu.large")],
        )

        actions = ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        shared_ctx.conn.compute.flavor_remove_tenant_access.assert_called_once_with("f-large", PROJECT_ID)
        deleted = [a for a in actions if a.status == ActionStatus.DELETED]
        assert "no active reservation covers this flavor" in deleted[0].details
        assert _saved_value(shared_ctx.state_store, "granted_flavor_access") == []
        revoked = _saved_value(shared_ctx.state_store, "revoked_flavor_access")
        assert revoked[0]["reason"] == "no active reservation covers this flavor"

    def test_deleted_entry_revokes_its_tracked_grants(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Removing an entry from config revokes its grants on the next run (DD-027)."""
        cfg = _cfg(
            sample_project_cfg,
            reservations=[],  # entry deleted from config
            granted_flavor_access=[_grant("f-large", "gpu.large")],
        )

        actions = ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        shared_ctx.conn.compute.flavor_remove_tenant_access.assert_called_once_with("f-large", PROJECT_ID)
        deleted = [a for a in actions if a.status == ActionStatus.DELETED]
        assert "reservation entry removed from config" in deleted[0].details
        # No active entries → the private flavor list is never fetched.
        shared_ctx.conn.compute.flavors.assert_not_called()

    def test_revocation_tolerates_missing_flavor(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """A flavor deleted from the cloud still moves to the audit trail."""
        cfg = _cfg(
            sample_project_cfg,
            reservations=[],
            granted_flavor_access=[_grant("f-gone", "gpu.gone")],
        )
        shared_ctx.conn.compute.flavor_remove_tenant_access.side_effect = NotFoundException

        actions = ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        deleted = [a for a in actions if a.status == ActionStatus.DELETED]
        assert "already gone" in deleted[0].details
        revoked = _saved_value(shared_ctx.state_store, "revoked_flavor_access")
        assert revoked[0]["flavor_id"] == "f-gone"

    def test_only_tracked_grants_are_revoked(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Manual access (never tracked) is never touched, even with no active entry."""
        cfg = _cfg(
            sample_project_cfg,
            reservations=[],
            granted_flavor_access=[_grant("f-large", "gpu.large")],
        )

        ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        # Exactly one revocation — the tracked grant; nothing pattern-matched.
        shared_ctx.conn.compute.flavor_remove_tenant_access.assert_called_once_with("f-large", PROJECT_ID)

    def test_revoked_entry_pruned_when_access_active_again(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """DD-026: an audit entry is pruned when the same access becomes active again."""
        stale_revoked = RevokedFlavorAccessEntry(
            flavor_id="f-large",
            flavor_name="gpu.large",
            reservation="gpu-program",
            revoked_at="2026-01-01T00:00:00+00:00",
            reason="no active reservation covers this flavor",
        )
        cfg = _cfg(
            sample_project_cfg,
            reservations=[_reservation(ACTIVE_GPU)],
            revoked_flavor_access=[stale_revoked],
        )
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-large", "gpu.large")]

        ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        assert _saved_value(shared_ctx.state_store, "revoked_flavor_access") == []


class TestVerification:
    def test_covered_grant_with_missing_access_is_regranted(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Tracked access removed outside tenantctl is healed (drift)."""
        cfg = _cfg(
            sample_project_cfg,
            reservations=[_reservation(ACTIVE_GPU)],
            granted_flavor_access=[_grant("f-large", "gpu.large")],
        )
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-large", "gpu.large")]
        shared_ctx.conn.compute.get_flavor_access.return_value = [{"tenant_id": "someone-else"}]

        actions = ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(updated) == 1
        assert "re-granted" in updated[0].details
        shared_ctx.conn.compute.flavor_add_tenant_access.assert_called_once_with("f-large", PROJECT_ID)

    def test_converged_state_reports_skip(self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig) -> None:
        cfg = _cfg(
            sample_project_cfg,
            reservations=[_reservation(ACTIVE_GPU)],
            granted_flavor_access=[_grant("f-large", "gpu.large")],
        )
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-large", "gpu.large")]
        shared_ctx.conn.compute.get_flavor_access.return_value = [{"tenant_id": PROJECT_ID}]

        actions = ensure_reservations(cfg, PROJECT_ID, shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "converged" in actions[0].details
        shared_ctx.state_store.save.assert_not_called()


class TestSharedFlavorCache:
    def test_flavor_list_fetched_once_per_run(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """The private flavor list is cloud-global: one fetch shared across projects (DD-006)."""
        cfg_a = _cfg(sample_project_cfg, reservations=[_reservation(ACTIVE_GPU)])
        cfg_b = _cfg(
            sample_project_cfg,
            name="other_project",
            state_key="other_project",
            reservations=[_reservation(ACTIVE_GPU)],
        )
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-large", "gpu.large")]
        shared_ctx.conn.compute.get_flavor_access.return_value = [{"tenant_id": PROJECT_ID}]

        ensure_reservations(cfg_a, PROJECT_ID, shared_ctx)
        ensure_reservations(cfg_b, "proj-id-456", shared_ctx)

        shared_ctx.conn.compute.flavors.assert_called_once()


class TestNoiseControl:
    def test_unmatched_pattern_warns_once(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The unmatched-pattern warning fires on first detection, not every run."""
        cfg = _cfg(sample_project_cfg, reservations=[_reservation(ACTIVE_GPU)])
        shared_ctx.conn.compute.flavors.return_value = [_flavor("f-std", "std.small")]

        with caplog.at_level("WARNING"):
            ensure_reservations(cfg, PROJECT_ID, shared_ctx)
        assert any("matches no private flavor" in r.message for r in caplog.records)
        saved = _saved_value(shared_ctx.state_store, "unmatched_flavor_patterns")
        assert saved == ["gpu-program:gpu.*"]

        # Second run: state already records the unmatched pattern → no new warning.
        shared_ctx.state_store.load.return_value = {"metadata": {"unmatched_flavor_patterns": ["gpu-program:gpu.*"]}}
        caplog.clear()
        with caplog.at_level("WARNING"):
            ensure_reservations(cfg, PROJECT_ID, shared_ctx)
        assert not any("matches no private flavor" in r.message for r in caplog.records)

    def test_all_past_entry_logs_dead_config_notice(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cfg = _cfg(
            sample_project_cfg,
            reservations=[_reservation(PAST_GPU)],
            granted_flavor_access=[_grant("f-large", "gpu.large")],
        )
        with caplog.at_level("WARNING"):
            ensure_reservations(cfg, PROJECT_ID, shared_ctx)
        assert any("dead config" in r.message for r in caplog.records)


class TestDryRunAndOffline:
    def test_dry_run_reports_without_writing(
        self, dry_run_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        cfg = _cfg(
            sample_project_cfg,
            reservations=[_reservation(ACTIVE_GPU)],
            granted_flavor_access=[_grant("f-old", "gpu.old", reservation="removed-entry")],
        )
        dry_run_ctx.conn.compute.flavors.return_value = [
            _flavor("f-large", "gpu.large"),
            _flavor("f-old", "gpu.old"),
        ]

        actions = ensure_reservations(cfg, PROJECT_ID, dry_run_ctx)

        # Resolved span + would-grant for the uncovered flavor; f-old stays covered.
        assert any(a.resource_type == "reservation" and "active" in a.details for a in actions)
        assert any(a.status == ActionStatus.CREATED and "would grant" in a.details for a in actions)
        dry_run_ctx.conn.compute.flavor_add_tenant_access.assert_not_called()
        dry_run_ctx.conn.compute.flavor_remove_tenant_access.assert_not_called()
        dry_run_ctx.state_store.save.assert_not_called()

    def test_dry_run_reports_would_revoke(self, dry_run_ctx: SharedContext, sample_project_cfg: ProjectConfig) -> None:
        cfg = _cfg(
            sample_project_cfg,
            reservations=[],
            granted_flavor_access=[_grant("f-large", "gpu.large")],
        )

        actions = ensure_reservations(cfg, PROJECT_ID, dry_run_ctx)

        assert any(a.status == ActionStatus.DELETED and "would revoke" in a.details for a in actions)
        dry_run_ctx.conn.compute.flavor_remove_tenant_access.assert_not_called()

    def test_offline_skips(self, offline_ctx: SharedContext, sample_project_cfg: ProjectConfig) -> None:
        cfg = _cfg(sample_project_cfg, reservations=[_reservation(ACTIVE_GPU)])

        actions = ensure_reservations(cfg, PROJECT_ID, offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details


class TestTeardownRevocation:
    def test_revokes_all_tracked_grants(self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig) -> None:
        cfg = _cfg(
            sample_project_cfg,
            granted_flavor_access=[_grant("f-a", "gpu.a"), _grant("f-b", "gpu.b")],
        )

        actions = revoke_all_reservation_grants(cfg, PROJECT_ID, shared_ctx)

        assert shared_ctx.conn.compute.flavor_remove_tenant_access.call_count == 2
        assert all(a.status == ActionStatus.DELETED for a in actions)
        assert _saved_value(shared_ctx.state_store, "granted_flavor_access") == []
        revoked = _saved_value(shared_ctx.state_store, "revoked_flavor_access")
        assert {r["reason"] for r in revoked} == {"project teardown"}

    def test_no_tracked_grants_is_a_noop(self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig) -> None:
        assert revoke_all_reservation_grants(sample_project_cfg, PROJECT_ID, shared_ctx) == []
        shared_ctx.conn.compute.flavor_remove_tenant_access.assert_not_called()

    def test_dry_run_reports_would_revoke(self, dry_run_ctx: SharedContext, sample_project_cfg: ProjectConfig) -> None:
        cfg = _cfg(sample_project_cfg, granted_flavor_access=[_grant("f-a", "gpu.a")])

        actions = revoke_all_reservation_grants(cfg, PROJECT_ID, dry_run_ctx)

        assert "would revoke" in actions[0].details
        dry_run_ctx.conn.compute.flavor_remove_tenant_access.assert_not_called()
        dry_run_ctx.state_store.save.assert_not_called()

    def test_tolerates_already_gone_access(self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig) -> None:
        cfg = _cfg(sample_project_cfg, granted_flavor_access=[_grant("f-a", "gpu.a")])
        shared_ctx.conn.compute.flavor_remove_tenant_access.side_effect = NotFoundException

        actions = revoke_all_reservation_grants(cfg, PROJECT_ID, shared_ctx)

        assert "already gone" in actions[0].details
        assert _saved_value(shared_ctx.state_store, "granted_flavor_access") == []
