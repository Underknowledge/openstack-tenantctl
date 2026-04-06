"""Tests for pre-allocated floating-IP provisioning — ensure_preallocated_fips."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import openstack.exceptions
import pytest

from src.models import FipEntry, ProjectConfig
from src.resources.prealloc.fip import (
    _detect_fip_drift,
    _detect_router_gateway,
    _reconcile_fip_drift,
    ensure_preallocated_fips,
)
from src.utils import ActionStatus, SharedContext


def _make_fip(
    fip_id: str,
    address: str,
    *,
    port_id: str | None = None,
    fixed_ip_address: str | None = None,
    status: str = "ACTIVE",
    router_id: str | None = None,
    created_at: str | None = "2026-01-15T12:00:00Z",
    port_details: dict[str, str] | None = None,
    floating_network_id: str = "ext-net-id-123",
) -> MagicMock:
    """Return a mock FIP object with the given attributes."""
    fip = MagicMock()
    fip.id = fip_id
    fip.floating_ip_address = address
    fip.port_id = port_id
    fip.fixed_ip_address = fixed_ip_address
    fip.status = status
    fip.router_id = router_id
    fip.created_at = created_at
    fip.port_details = port_details
    fip.floating_network_id = floating_network_id
    return fip


def _cfg(desired: int, config_path: str = "/tmp/proj.yaml") -> ProjectConfig:
    """Return a minimal project config with the given floating_ips count."""
    return ProjectConfig.from_dict(
        {
            "name": "test_project",
            "resource_prefix": "test",
            "quotas": {"network": {"floating_ips": desired}},
            "_config_path": config_path,
            "_state_key": "proj",
        }
    )


def _cfg_with_tracking(
    desired: int,
    *,
    preallocated_fips: list[dict[str, str]] | None = None,
) -> ProjectConfig:
    """Return a project config with track_fip_changes=True."""
    data: dict = {
        "name": "test_project",
        "resource_prefix": "test",
        "quotas": {"network": {"floating_ips": desired}},
        "_config_path": "/tmp/proj.yaml",
        "_state_key": "proj",
        "track_fip_changes": True,
    }
    if preallocated_fips is not None:
        data["preallocated_fips"] = preallocated_fips
    return ProjectConfig.from_dict(data)


def _fip_entry(
    fip_id: str,
    address: str,
    *,
    port_id: str | None = None,
    device_id: str | None = None,
    device_owner: str | None = None,
) -> FipEntry:
    """Return a FipEntry for tests."""
    return FipEntry(
        id=fip_id,
        address=address,
        port_id=port_id,
        device_id=device_id,
        device_owner=device_owner,
    )


class TestScaleUpAndDown:
    """Tests for FIP allocation and release (scale-up, scale-down, exact match)."""

    def test_scale_up_allocates_missing(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """existing=1, desired=3 → 2 new FIPs created."""
        existing = [_make_fip("id-1", "10.0.0.1")]
        shared_ctx.conn.network.ips.return_value = existing

        new_fip_a = _make_fip("id-2", "10.0.0.2")
        new_fip_b = _make_fip("id-3", "10.0.0.3")
        shared_ctx.conn.network.create_ip.side_effect = [new_fip_a, new_fip_b]

        actions = ensure_preallocated_fips(_cfg(3), "proj-123", shared_ctx)

        assert len(actions) == 2
        assert all(a.status == ActionStatus.CREATED for a in actions)

        # Verify create_ip called with correct parameters (external_net_id, project_id, subnet_id).
        assert shared_ctx.conn.network.create_ip.call_count == 2
        for call_args in shared_ctx.conn.network.create_ip.call_args_list:
            _, kwargs = call_args
            assert kwargs["floating_network_id"] == "ext-net-id-123"
            assert kwargs["project_id"] == "proj-123"
            assert kwargs["subnet_id"] == "ext-subnet-123"

        # Quota raised to 3, then set to desired (3).
        quota_calls = shared_ctx.conn.network.update_quota.call_args_list
        assert call("proj-123", floating_ips=3) in quota_calls

        # State persisted with all 3 FIPs (existing + new).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(save_calls) == 1
        written_fips = save_calls[0][0][2]
        assert len(written_fips) == 3
        written_ids = {f["id"] for f in written_fips}
        assert written_ids == {"id-1", "id-2", "id-3"}

    def test_scale_down_releases_unused(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """existing=3, desired=1, 2 unused → 2 deleted, 1 remains."""
        existing = [
            _make_fip("id-1", "10.0.0.1", port_id="port-1"),  # in use
            _make_fip("id-2", "10.0.0.2"),  # unused
            _make_fip("id-3", "10.0.0.3"),  # unused
        ]
        shared_ctx.conn.network.ips.return_value = existing

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        # 2 unused deleted → 2 UPDATED actions
        assert len(actions) == 2
        assert all(a.status == ActionStatus.UPDATED for a in actions)
        updated_addresses = {a.name for a in actions}
        assert updated_addresses == {"10.0.0.2", "10.0.0.3"}

        # Verify delete_ip called for ONLY the unused FIPs (not the in-use one).
        assert shared_ctx.conn.network.delete_ip.call_count == 2
        deleted_ids = {
            call_args[0][0]
            for call_args in shared_ctx.conn.network.delete_ip.call_args_list
        }
        assert deleted_ids == {"id-2", "id-3"}

        # Quota set to desired (1).
        shared_ctx.conn.network.update_quota.assert_called_with(
            "proj-123", floating_ips=1
        )

        # State persisted with only the in-use FIP (id-1).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(save_calls) == 1
        written_fips = save_calls[0][0][2]
        assert len(written_fips) == 1
        assert written_fips[0]["id"] == "id-1"
        assert written_fips[0]["address"] == "10.0.0.1"

    def test_scale_down_partial_in_use(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """existing=3, desired=1, only 1 unused → 1 deleted, FAILED for remaining."""
        existing = [
            _make_fip("id-1", "10.0.0.1", port_id="port-1"),  # in use
            _make_fip("id-2", "10.0.0.2", port_id="port-2"),  # in use
            _make_fip("id-3", "10.0.0.3"),  # unused
        ]
        shared_ctx.conn.network.ips.return_value = existing

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        # 1 UPDATED (deleted unused) + 2 FAILED (1 for can't delete in-use, 1 for quota)
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(updated) == 1
        assert len(failed) == 2

        # Check for both failure types
        in_use_failures = [a for a in failed if a.resource_type == "preallocated_fip"]
        quota_failures = [
            a for a in failed if a.resource_type == "preallocated_fip_quota"
        ]
        assert len(in_use_failures) == 1
        assert len(quota_failures) == 1
        assert "1 in-use" in in_use_failures[0].details
        assert "must free" in quota_failures[0].details

        # Quota set proactively to max(desired=1, remaining=2) = 2.
        shared_ctx.conn.network.update_quota.assert_called_with(
            "proj-123", floating_ips=2
        )

        # State persisted with 2 remaining FIPs (both in-use).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        written_fips = save_calls[-1][0][2]
        assert len(written_fips) == 2

    def test_scale_down_all_in_use(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """existing=3, desired=1, 0 unused → FAILED, nothing deleted."""
        existing = [
            _make_fip("id-1", "10.0.0.1", port_id="port-1"),
            _make_fip("id-2", "10.0.0.2", port_id="port-2"),
            _make_fip("id-3", "10.0.0.3", port_id="port-3"),
        ]
        shared_ctx.conn.network.ips.return_value = existing

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        # 2 FAILED (1 for in-use FIPs, 1 for quota), nothing deleted
        assert len(actions) == 2
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 2

        in_use_failures = [a for a in failed if a.resource_type == "preallocated_fip"]
        quota_failures = [
            a for a in failed if a.resource_type == "preallocated_fip_quota"
        ]
        assert len(in_use_failures) == 1
        assert len(quota_failures) == 1
        assert "2 in-use" in in_use_failures[0].details
        assert "must free" in quota_failures[0].details
        shared_ctx.conn.network.delete_ip.assert_not_called()

        # Quota set proactively to max(desired=1, remaining=3) = 3.
        shared_ctx.conn.network.update_quota.assert_called_with(
            "proj-123", floating_ips=3
        )

        # State persisted with all 3 (can't delete any).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        written_fips = save_calls[-1][0][2]
        assert len(written_fips) == 3

    def test_exact_count_sets_quota_only(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """existing=2, desired=2 → just sets quota to desired count."""
        existing = [
            _make_fip("id-1", "10.0.0.1"),
            _make_fip("id-2", "10.0.0.2"),
        ]
        shared_ctx.conn.network.ips.return_value = existing

        actions = ensure_preallocated_fips(_cfg(2), "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "already allocated" in actions[0].details
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-123", floating_ips=2
        )
        shared_ctx.conn.network.create_ip.assert_not_called()
        shared_ctx.conn.network.delete_ip.assert_not_called()

    def test_steady_state_persists_fips_when_state_empty(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """existing=2, desired=2, state empty, tracking enabled → persists FIPs + snapshot."""
        existing = [
            _make_fip("id-1", "10.0.0.1"),
            _make_fip("id-2", "10.0.0.2"),
        ]
        shared_ctx.conn.network.ips.return_value = existing

        # Config has no preallocated_fips (state lost / corrupted), tracking enabled.
        cfg = _cfg_with_tracking(2)
        assert cfg.preallocated_fips == []

        actions = ensure_preallocated_fips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "already allocated" in actions[0].details

        # FIPs persisted to state (self-healing).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(save_calls) == 1
        written_fips = save_calls[0][0][2]
        assert len(written_fips) == 2
        written_ids = {f["id"] for f in written_fips}
        assert written_ids == {"id-1", "id-2"}

        # FIP tracking snapshot also persisted.
        snapshot_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["fip_tracking_snapshot"]
        ]
        assert len(snapshot_calls) == 1
        snapshot = snapshot_calls[0][0][2]
        assert "timestamp" in snapshot
        assert snapshot["quota"] == 2
        assert snapshot["allocated"] == 2

    def test_steady_state_skips_persist_when_tracking_disabled(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """existing=2, desired=2, state empty, tracking disabled → no persist."""
        existing = [
            _make_fip("id-1", "10.0.0.1"),
            _make_fip("id-2", "10.0.0.2"),
        ]
        shared_ctx.conn.network.ips.return_value = existing

        # Config has no preallocated_fips but track_fip_changes=False (default).
        cfg = _cfg(2)
        assert cfg.track_fip_changes is False

        actions = ensure_preallocated_fips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "already allocated" in actions[0].details

        # No FIPs persisted (tracking disabled).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(save_calls) == 0

    def test_steady_state_skips_persist_when_ids_match(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """existing=2, desired=2, tracking enabled, IDs match → no persist."""
        existing = [
            _make_fip("id-1", "10.0.0.1"),
            _make_fip("id-2", "10.0.0.2"),
        ]
        shared_ctx.conn.network.ips.return_value = existing

        # Config already tracks the correct FIP IDs.
        cfg = _cfg_with_tracking(
            2,
            preallocated_fips=[
                {"id": "id-1", "address": "10.0.0.1"},
                {"id": "id-2", "address": "10.0.0.2"},
            ],
        )

        actions = ensure_preallocated_fips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "already allocated" in actions[0].details

        # No FIPs persisted (IDs already match).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(save_calls) == 0

        # No snapshot persisted either.
        snapshot_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["fip_tracking_snapshot"]
        ]
        assert len(snapshot_calls) == 0

    def test_scale_from_zero_to_n(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """No existing FIPs, desired=3 → allocate all 3 from scratch."""
        # Cloud has no FIPs.
        shared_ctx.conn.network.ips.return_value = []

        new_fip_a = _make_fip("id-1", "10.0.0.1")
        new_fip_b = _make_fip("id-2", "10.0.0.2")
        new_fip_c = _make_fip("id-3", "10.0.0.3")
        shared_ctx.conn.network.create_ip.side_effect = [
            new_fip_a,
            new_fip_b,
            new_fip_c,
        ]

        actions = ensure_preallocated_fips(_cfg(3), "proj-123", shared_ctx)

        # 3 CREATED actions.
        assert len(actions) == 3
        assert all(a.status == ActionStatus.CREATED for a in actions)
        addresses = {a.name for a in actions}
        assert addresses == {"10.0.0.1", "10.0.0.2", "10.0.0.3"}

        # Quota raised to 3, then set to 3.
        quota_calls = shared_ctx.conn.network.update_quota.call_args_list
        assert call("proj-123", floating_ips=3) in quota_calls

        # State persisted with all 3 FIPs.
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        written = save_calls[-1][0][2]
        assert len(written) == 3


class TestSkipConditions:
    """Tests for conditions that skip FIP provisioning."""

    def test_zero_desired_skips(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """desired=0 → SKIPPED."""
        actions = ensure_preallocated_fips(_cfg(0), "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "no floating IPs requested" in actions[0].details
        shared_ctx.conn.network.ips.assert_not_called()

    def test_dry_run_reads_and_reports(
        self,
        dry_run_ctx: SharedContext,
    ) -> None:
        """Online dry-run reads existing FIPs, reports what would change."""
        dry_run_ctx.conn.network.ips.return_value = []

        actions = ensure_preallocated_fips(_cfg(2), "proj-123", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.CREATED
        assert "would allocate 2" in actions[0].details
        # Reads happened
        dry_run_ctx.conn.network.ips.assert_called_once()
        # No writes
        dry_run_ctx.conn.network.create_ip.assert_not_called()
        dry_run_ctx.conn.network.update_quota.assert_not_called()

    def test_offline_dry_run_skips(
        self,
        offline_ctx: SharedContext,
    ) -> None:
        """Offline mode → SKIPPED with no API calls."""
        actions = ensure_preallocated_fips(_cfg(2), "proj-123", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details

    def test_no_quotas_section(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Config with quotas=None → SKIPPED."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                # No quotas section at all.
            }
        )
        actions = ensure_preallocated_fips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "no quotas.network configured" in actions[0].details
        shared_ctx.conn.network.ips.assert_not_called()

    def test_quotas_but_no_network_section(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Config with quotas.network=None → SKIPPED."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "quotas": {
                    "compute": {"cores": 10},
                    # No network section.
                },
            }
        )
        actions = ensure_preallocated_fips(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "no quotas.network configured" in actions[0].details

    def test_no_external_net_id(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """ctx.external_net_id is None → SKIPPED."""
        # Simulate no external network.
        shared_ctx.external_net_id = None

        actions = ensure_preallocated_fips(_cfg(2), "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "no external network" in actions[0].details
        shared_ctx.conn.network.ips.assert_not_called()


class TestFipEntryFromSdk:
    """Verify FipEntry.from_sdk extracts all fields from a FloatingIP object."""

    def test_with_port_details(self) -> None:
        fip = _make_fip(
            "id-1",
            "10.0.0.1",
            port_id="port-1",
            fixed_ip_address="192.168.1.5",
            status="ACTIVE",
            router_id="router-1",
            created_at="2026-03-01T10:00:00Z",
            port_details={
                "device_id": "server-uuid-1",
                "device_owner": "compute:nova",
            },
        )
        result = FipEntry.from_sdk(fip)
        assert result.to_dict() == {
            "id": "id-1",
            "address": "10.0.0.1",
            "port_id": "port-1",
            "fixed_ip_address": "192.168.1.5",
            "status": "ACTIVE",
            "router_id": "router-1",
            "created_at": "2026-03-01T10:00:00Z",
            "device_id": "server-uuid-1",
            "device_owner": "compute:nova",
        }

    def test_without_port_details(self) -> None:
        fip = _make_fip("id-2", "10.0.0.2", status="DOWN")
        result = FipEntry.from_sdk(fip)
        assert result.id == "id-2"
        assert result.address == "10.0.0.2"
        assert result.port_id is None
        assert result.fixed_ip_address is None
        assert result.status == "DOWN"
        assert result.device_id is None
        assert result.device_owner is None

    def test_port_details_none(self) -> None:
        """When port_details is None (extension unavailable), device fields are None."""
        fip = _make_fip("id-3", "10.0.0.3", port_details=None)
        result = FipEntry.from_sdk(fip)
        assert result.device_id is None
        assert result.device_owner is None


# ---------------------------------------------------------------------------
# Drift detection tests
# ---------------------------------------------------------------------------


def _cfg_with_locked(
    desired: int,
    preallocated_fips: list[dict[str, str]],
    config_path: str = "/tmp/proj.yaml",
    released_fips: list[dict[str, str]] | None = None,
    *,
    reclaim_floating_ips: bool = False,
) -> ProjectConfig:
    """Return a project config with preallocated_fips pre-populated."""
    cfg: dict = {
        "name": "test_project",
        "resource_prefix": "test",
        "quotas": {"network": {"floating_ips": desired}},
        "_config_path": config_path,
        "_state_key": "proj",
        "preallocated_fips": preallocated_fips,
        "reclaim_floating_ips": reclaim_floating_ips,
    }
    if released_fips is not None:
        cfg["released_fips"] = released_fips
    return ProjectConfig.from_dict(cfg)


class TestDetectFipDrift:
    """Pure function: compare config FIPs vs OpenStack FIPs by ID."""

    def test_no_drift(self) -> None:
        """IDs match exactly → no missing, no untracked."""
        config_fips = [_fip_entry("id-1", "10.0.0.1")]
        os_fips = [_make_fip("id-1", "10.0.0.1")]
        missing, untracked = _detect_fip_drift(config_fips, os_fips)
        assert missing == []
        assert untracked == []

    def test_missing_only(self) -> None:
        """Config has id-2 but OpenStack doesn't → id-2 is missing."""
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry("id-2", "10.0.0.2"),
        ]
        os_fips = [_make_fip("id-1", "10.0.0.1")]
        missing, untracked = _detect_fip_drift(config_fips, os_fips)
        assert len(missing) == 1
        assert missing[0].id == "id-2"
        assert missing[0].address == "10.0.0.2"
        assert untracked == []

    def test_untracked_only(self) -> None:
        """OpenStack has id-3 but config doesn't → id-3 is untracked."""
        config_fips = [_fip_entry("id-1", "10.0.0.1")]
        os_fips = [
            _make_fip("id-1", "10.0.0.1"),
            _make_fip("id-3", "10.0.0.3"),
        ]
        missing, untracked = _detect_fip_drift(config_fips, os_fips)
        assert missing == []
        assert len(untracked) == 1
        assert untracked[0].id == "id-3"
        assert untracked[0].floating_ip_address == "10.0.0.3"

    def test_mixed(self) -> None:
        """Config has id-2 missing, OpenStack has id-4 untracked → both detected."""
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry("id-2", "10.0.0.2"),
        ]
        os_fips = [
            _make_fip("id-1", "10.0.0.1"),
            _make_fip("id-4", "10.0.0.4"),
        ]
        missing, untracked = _detect_fip_drift(config_fips, os_fips)
        assert len(missing) == 1
        assert missing[0].id == "id-2"
        assert missing[0].address == "10.0.0.2"
        assert len(untracked) == 1
        assert untracked[0].id == "id-4"
        assert untracked[0].floating_ip_address == "10.0.0.4"


class TestDriftReconciliation:
    """Tests for drift detection and reconciliation (adoption, reclamation, release)."""

    def test_adopt_untracked(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Untracked FIP id-3 in OpenStack but not config → adopted into state."""
        config_fips = [_fip_entry("id-1", "10.0.0.1")]
        os_fips = [
            _make_fip("id-1", "10.0.0.1"),
            _make_fip("id-3", "10.0.0.3"),
        ]
        cfg = _cfg_with_locked(2, [{"id": "id-1", "address": "10.0.0.1"}])
        actions = _reconcile_fip_drift(
            cfg,
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )
        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "adopted untracked FIP" in actions[0].details
        assert "id=id-3" in actions[0].details
        # Verify the adopted FIP's address.
        assert actions[0].name == "10.0.0.3"

        # Persisted with both original + adopted (verifies adoption merged into config).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(save_calls) == 1
        written = save_calls[0][0][2]
        assert len(written) == 2
        written_ids = {f["id"] for f in written}
        assert written_ids == {"id-1", "id-3"}

        # Adopted FIP has enriched fields from SDK object.
        adopted = next(f for f in written if f["id"] == "id-3")
        assert adopted["address"] == "10.0.0.3"
        assert adopted["status"] == "ACTIVE"
        assert adopted["created_at"] == "2026-01-15T12:00:00Z"

    def test_reclaim_success(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Missing FIP id-2 (10.0.0.2) reclaimed with same address, new ID."""
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry("id-2", "10.0.0.2"),
        ]
        # id-2 is gone from OpenStack
        os_fips = [_make_fip("id-1", "10.0.0.1")]

        reclaimed = _make_fip("id-new", "10.0.0.2")
        shared_ctx.conn.network.create_ip.return_value = reclaimed

        raw_fips = [
            {"id": "id-1", "address": "10.0.0.1"},
            {"id": "id-2", "address": "10.0.0.2"},
        ]
        actions = _reconcile_fip_drift(
            _cfg_with_locked(2, raw_fips, reclaim_floating_ips=True),
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "reclaimed" in actions[0].details
        assert "id-new" in actions[0].details
        # Verify the reclaimed FIP's address matches the missing entry's address.
        assert actions[0].name == "10.0.0.2"

        # Quota raised to allow reclamation.
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-123", floating_ips=3
        )

        # create_ip called with specific address (10.0.0.2 from missing entry).
        shared_ctx.conn.network.create_ip.assert_called_once_with(
            floating_network_id="ext-net-id-123",
            project_id="proj-123",
            floating_ip_address="10.0.0.2",
            subnet_id="ext-subnet-123",
        )

        # preallocated_fips persisted: id-1 surviving + id-new reclaimed.
        locked_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(locked_writes) == 1
        written = locked_writes[0][0][2]
        written_ids = {f["id"] for f in written}
        assert written_ids == {"id-1", "id-new"}

        # Reclaimed FIP has the SAME address (10.0.0.2) but NEW id (id-new).
        reclaimed_entry = next(f for f in written if f["id"] == "id-new")
        assert reclaimed_entry["address"] == "10.0.0.2"
        assert reclaimed_entry["status"] == "ACTIVE"
        assert reclaimed_entry["created_at"] == "2026-01-15T12:00:00Z"

        # No released_fips written (reclamation succeeded).
        released_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["released_fips"]
        ]
        assert len(released_writes) == 0

    def test_reclaim_conflict(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Missing FIP 10.0.0.2, address taken → FAILED, moved to released_fips."""
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry(
                "id-2",
                "10.0.0.2",
                port_id="port-2",
                device_id="server-2",
                device_owner="compute:nova",
            ),
        ]
        os_fips = [_make_fip("id-1", "10.0.0.1")]

        shared_ctx.conn.network.create_ip.side_effect = (
            openstack.exceptions.ConflictException(message="Conflict")
        )

        raw_fips = [
            {"id": "id-1", "address": "10.0.0.1"},
            {
                "id": "id-2",
                "address": "10.0.0.2",
                "port_id": "port-2",
                "device_id": "server-2",
                "device_owner": "compute:nova",
            },
        ]
        actions = _reconcile_fip_drift(
            _cfg_with_locked(2, raw_fips, reclaim_floating_ips=True),
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.FAILED
        assert "address taken by another project" in actions[0].details
        assert actions[0].name == "10.0.0.2"

        # released_fips persisted with last-known tracking info.
        released_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["released_fips"]
        ]
        assert len(released_writes) == 1
        released = released_writes[0][0][2]
        assert len(released) == 1
        assert released[0]["address"] == "10.0.0.2"
        assert released[0]["reason"] == "address taken by another project"
        assert "released_at" in released[0]
        # Verify last-known tracking info preserved.
        assert released[0]["port_id"] == "port-2"
        assert released[0]["device_id"] == "server-2"
        assert released[0]["device_owner"] == "compute:nova"

        # preallocated_fips only has id-1 (id-2 removed and released).
        locked_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(locked_writes) == 1
        written = locked_writes[0][0][2]
        assert len(written) == 1
        assert written[0]["id"] == "id-1"

    def test_partial_reclamation(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """2 missing FIPs: 1 reclaimed, 1 conflict → 1 UPDATED + 1 FAILED."""
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry("id-2", "10.0.0.2"),
            _fip_entry("id-3", "10.0.0.3"),
        ]
        # Only id-1 survives in OpenStack.
        os_fips = [_make_fip("id-1", "10.0.0.1")]

        reclaimed = _make_fip("id-2-new", "10.0.0.2")
        shared_ctx.conn.network.create_ip.side_effect = [
            reclaimed,
            openstack.exceptions.ConflictException(message="Conflict"),
        ]

        raw_fips = [
            {"id": "id-1", "address": "10.0.0.1"},
            {"id": "id-2", "address": "10.0.0.2"},
            {"id": "id-3", "address": "10.0.0.3"},
        ]
        actions = _reconcile_fip_drift(
            _cfg_with_locked(3, raw_fips, reclaim_floating_ips=True),
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )

        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(updated) == 1
        assert len(failed) == 1
        assert updated[0].name == "10.0.0.2"
        assert failed[0].name == "10.0.0.3"

        # preallocated_fips: id-1 + id-2-new (id-3 lost).
        locked_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        written = locked_writes[-1][0][2]
        written_ids = {f["id"] for f in written}
        assert written_ids == {"id-1", "id-2-new"}

        # released_fips: only 10.0.0.3.
        released_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["released_fips"]
        ]
        assert len(released_writes) == 1
        released = released_writes[0][0][2]
        assert len(released) == 1
        assert released[0]["address"] == "10.0.0.3"

    def test_drift_then_scale_up(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """1 missing FIP reclaimed + still under desired count → reclaim + allocate new."""
        config_fips = [{"id": "id-1", "address": "10.0.0.1"}]
        # id-1 deleted from OpenStack
        os_fips_initial: list[MagicMock] = []

        reclaimed = _make_fip("id-1-new", "10.0.0.1")
        new_fip = _make_fip("id-2", "10.0.0.2")
        shared_ctx.conn.network.create_ip.side_effect = [reclaimed, new_fip]

        # After drift reconciliation, re-list returns reclaimed FIP.
        refreshed = [_make_fip("id-1-new", "10.0.0.1")]
        shared_ctx.conn.network.ips.side_effect = [os_fips_initial, refreshed]

        actions = ensure_preallocated_fips(
            _cfg_with_locked(2, config_fips, reclaim_floating_ips=True),
            "proj-123",
            shared_ctx,
        )

        # 1 UPDATED (reclaimed) + 1 CREATED (scale-up).
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(updated) == 1
        assert len(created) == 1
        assert updated[0].name == "10.0.0.1"
        assert "reclaimed" in updated[0].details
        assert created[0].name == "10.0.0.2"

    def test_no_drift_first_run(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """No preallocated_fips in config → drift detection skipped, normal flow."""
        # No preallocated_fips key — first run.
        cfg = _cfg(2)
        existing = [_make_fip("id-1", "10.0.0.1")]
        shared_ctx.conn.network.ips.return_value = existing

        new_fip = _make_fip("id-2", "10.0.0.2")
        shared_ctx.conn.network.create_ip.return_value = new_fip

        actions = ensure_preallocated_fips(cfg, "proj-123", shared_ctx)

        # Normal scale-up, 1 CREATED.
        assert len(actions) == 1
        assert actions[0].status == ActionStatus.CREATED

        # ips() called only once (no drift refresh).
        shared_ctx.conn.network.ips.assert_called_once()

    def test_released_fips_merge(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Existing released_fips in config + new loss → appended, not overwritten."""
        existing_released = [
            {
                "address": "10.0.0.9",
                "released_at": "2026-01-01T00:00:00+00:00",
                "reason": "old loss",
            }
        ]
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry("id-2", "10.0.0.2"),
        ]
        os_fips = [_make_fip("id-1", "10.0.0.1")]

        shared_ctx.conn.network.create_ip.side_effect = (
            openstack.exceptions.ConflictException(message="Conflict")
        )

        raw_fips = [
            {"id": "id-1", "address": "10.0.0.1"},
            {"id": "id-2", "address": "10.0.0.2"},
        ]
        cfg = _cfg_with_locked(
            2, raw_fips, released_fips=existing_released, reclaim_floating_ips=True
        )
        _reconcile_fip_drift(
            cfg,
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )

        # released_fips persisted with old + new (verifies merge, not overwrite).
        released_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["released_fips"]
        ]
        assert len(released_writes) == 1
        all_released = released_writes[0][0][2]
        assert len(all_released) == 2
        addresses = {r["address"] for r in all_released}
        assert addresses == {"10.0.0.9", "10.0.0.2"}

        # Newly released entry has last-known tracking (None from old-format state).
        new_entry = next(r for r in all_released if r["address"] == "10.0.0.2")
        assert new_entry["port_id"] is None
        assert new_entry["device_id"] is None
        assert new_entry["device_owner"] is None

    def test_all_fips_missing_with_reclaim(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Config has preallocated_fips but cloud has zero FIPs → all missing, all reclaimed."""
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry("id-2", "10.0.0.2"),
        ]
        # Cloud has no FIPs at all.
        os_fips: list[MagicMock] = []

        # Both reclaimed successfully.
        reclaimed_a = _make_fip("id-new-1", "10.0.0.1")
        reclaimed_b = _make_fip("id-new-2", "10.0.0.2")
        shared_ctx.conn.network.create_ip.side_effect = [reclaimed_a, reclaimed_b]

        raw_fips = [
            {"id": "id-1", "address": "10.0.0.1"},
            {"id": "id-2", "address": "10.0.0.2"},
        ]
        actions = _reconcile_fip_drift(
            _cfg_with_locked(2, raw_fips, reclaim_floating_ips=True),
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )

        # 2 UPDATED actions (both reclaimed).
        assert len(actions) == 2
        assert all(a.status == ActionStatus.UPDATED for a in actions)
        assert all("reclaimed" in a.details for a in actions)

        # Quota raised to desired + missing (2 + 2 = 4) to allow reclamation.
        shared_ctx.conn.network.update_quota.assert_called_once_with(
            "proj-123", floating_ips=4
        )

        # Both create_ip calls with specific addresses.
        assert shared_ctx.conn.network.create_ip.call_count == 2
        addresses_requested = {
            c.kwargs["floating_ip_address"]
            for c in shared_ctx.conn.network.create_ip.call_args_list
        }
        assert addresses_requested == {"10.0.0.1", "10.0.0.2"}

        # preallocated_fips persisted with new IDs.
        locked_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        written = locked_writes[-1][0][2]
        written_ids = {f["id"] for f in written}
        assert written_ids == {"id-new-1", "id-new-2"}

    def test_reclaim_fip_status_down(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Reclaimed FIP has status=DOWN instead of ACTIVE → still recorded."""
        config_fips = [_fip_entry("id-1", "10.0.0.1")]
        os_fips: list[MagicMock] = []

        # Reclaimed FIP has status=DOWN.
        reclaimed = _make_fip("id-new", "10.0.0.1", status="DOWN")
        shared_ctx.conn.network.create_ip.return_value = reclaimed

        raw_fips = [{"id": "id-1", "address": "10.0.0.1"}]
        actions = _reconcile_fip_drift(
            _cfg_with_locked(1, raw_fips, reclaim_floating_ips=True),
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )

        # 1 UPDATED action (reclaimed).
        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "reclaimed" in actions[0].details

        # State persisted with status=DOWN (verifies non-ACTIVE status accepted).
        locked_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        written = locked_writes[-1][0][2]
        assert len(written) == 1
        assert written[0]["status"] == "DOWN"
        assert written[0]["id"] == "id-new"


class TestDriftReclamationDisabled:
    """Tests for drift with reclaim_floating_ips=False (default behavior)."""

    def test_missing_fips_released_not_reclaimed(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Missing FIP with reclaim disabled → UPDATED, no create_ip, moved to released_fips."""
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry(
                "id-2",
                "10.0.0.2",
                port_id="port-2",
                device_id="server-2",
                device_owner="compute:nova",
            ),
        ]
        # id-2 deleted from OpenStack
        os_fips = [_make_fip("id-1", "10.0.0.1")]

        raw_fips = [
            {"id": "id-1", "address": "10.0.0.1"},
            {
                "id": "id-2",
                "address": "10.0.0.2",
                "port_id": "port-2",
                "device_id": "server-2",
                "device_owner": "compute:nova",
            },
        ]
        actions = _reconcile_fip_drift(
            _cfg_with_locked(2, raw_fips),  # reclaim_floating_ips=False (default)
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )

        # 1 UPDATED action (not FAILED — system working as configured).
        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "FIP deleted externally" in actions[0].details
        assert actions[0].name == "10.0.0.2"

        # No create_ip called (no reclamation attempt).
        shared_ctx.conn.network.create_ip.assert_not_called()

        # No quota raise (not needed without reclamation).
        shared_ctx.conn.network.update_quota.assert_not_called()

        # released_fips persisted with last-known tracking.
        released_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["released_fips"]
        ]
        assert len(released_writes) == 1
        released = released_writes[0][0][2]
        assert len(released) == 1
        assert released[0]["address"] == "10.0.0.2"
        assert released[0]["reason"] == "FIP deleted externally"
        assert "released_at" in released[0]
        # Verify last-known tracking preserved.
        assert released[0]["port_id"] == "port-2"
        assert released[0]["device_id"] == "server-2"
        assert released[0]["device_owner"] == "compute:nova"

        # preallocated_fips trimmed to only id-1.
        locked_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(locked_writes) == 1
        written = locked_writes[0][0][2]
        assert len(written) == 1
        assert written[0]["id"] == "id-1"

    def test_drift_disabled_then_scale_up(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Missing FIP released (no reclaim) + scale-up allocates new different FIP."""
        config_fips = [{"id": "id-1", "address": "10.0.0.1"}]
        # id-1 deleted from OpenStack
        os_fips_initial: list[MagicMock] = []

        # Only scale-up allocation (no reclamation create_ip).
        new_fip_a = _make_fip("id-a", "10.0.0.99")
        new_fip_b = _make_fip("id-b", "10.0.0.100")
        shared_ctx.conn.network.create_ip.side_effect = [new_fip_a, new_fip_b]

        # After drift reconciliation (no actual reclaim), re-list returns empty.
        shared_ctx.conn.network.ips.side_effect = [os_fips_initial, []]

        actions = ensure_preallocated_fips(
            _cfg_with_locked(2, config_fips),  # reclaim_floating_ips=False
            "proj-123",
            shared_ctx,
        )

        # 1 UPDATED (released) + 2 CREATED (scale-up to desired=2 from 0).
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(updated) == 1
        assert len(created) == 2
        assert updated[0].name == "10.0.0.1"
        assert "deleted externally" in updated[0].details

        # create_ip called WITHOUT floating_ip_address (generic allocation, not reclamation).
        for c in shared_ctx.conn.network.create_ip.call_args_list:
            assert "floating_ip_address" not in c.kwargs
            _, kwargs = c
            assert "floating_ip_address" not in kwargs

    def test_multiple_missing_all_released(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Multiple missing FIPs with reclaim disabled → all released, none reclaimed."""
        config_fips = [
            _fip_entry("id-1", "10.0.0.1"),
            _fip_entry("id-2", "10.0.0.2"),
            _fip_entry("id-3", "10.0.0.3"),
        ]
        # All gone from OpenStack.
        os_fips: list[MagicMock] = []

        raw_fips = [
            {"id": "id-1", "address": "10.0.0.1"},
            {"id": "id-2", "address": "10.0.0.2"},
            {"id": "id-3", "address": "10.0.0.3"},
        ]
        actions = _reconcile_fip_drift(
            _cfg_with_locked(3, raw_fips),  # reclaim_floating_ips=False
            "proj-123",
            shared_ctx,
            config_fips,
            os_fips,
            "ext-net-id-123",
            "ext-subnet-123",
        )

        # 3 UPDATED actions, all "deleted externally".
        assert len(actions) == 3
        assert all(a.status == ActionStatus.UPDATED for a in actions)
        assert all("deleted externally" in a.details for a in actions)
        addresses = {a.name for a in actions}
        assert addresses == {"10.0.0.1", "10.0.0.2", "10.0.0.3"}

        # No reclamation attempted.
        shared_ctx.conn.network.create_ip.assert_not_called()

        # released_fips has all 3 entries.
        released_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["released_fips"]
        ]
        assert len(released_writes) == 1
        released = released_writes[0][0][2]
        assert len(released) == 3

        # preallocated_fips is empty.
        locked_writes = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        written = locked_writes[-1][0][2]
        assert len(written) == 0


class TestEdgeCases:
    """Edge case tests for error conditions and quota failures."""

    def test_allocation_failure_http_exception(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """FIP allocation fails during scale-up → exception propagates after retry exhaustion."""
        existing = [_make_fip("id-1", "10.0.0.1")]
        shared_ctx.conn.network.ips.return_value = existing

        # First FIP succeeds, second fails with HttpException (retry exhausts attempts).
        # Retry decorator tries 5 times (1 initial + 4 retries).
        new_fip = _make_fip("id-2", "10.0.0.2")
        shared_ctx.conn.network.create_ip.side_effect = [
            new_fip,
            openstack.exceptions.HttpException(message="Service unavailable"),
            openstack.exceptions.HttpException(message="Service unavailable"),
            openstack.exceptions.HttpException(message="Service unavailable"),
            openstack.exceptions.HttpException(message="Service unavailable"),
            openstack.exceptions.HttpException(message="Service unavailable"),
        ]

        # Retry decorator will exhaust retries and propagate the exception.
        with pytest.raises(
            openstack.exceptions.HttpException, match="Service unavailable"
        ):
            ensure_preallocated_fips(_cfg(3), "proj-123", shared_ctx)

        # First FIP was created (1 call), then 5 failed attempts on second FIP.
        assert shared_ctx.conn.network.create_ip.call_count == 6

    def test_quota_failure_after_scale_up(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """FIPs allocated successfully but quota update fails → exception propagates."""
        existing = [_make_fip("id-1", "10.0.0.1")]
        shared_ctx.conn.network.ips.return_value = existing

        new_fip = _make_fip("id-2", "10.0.0.2")
        shared_ctx.conn.network.create_ip.return_value = new_fip

        # Quota raise succeeds, but final quota set fails.
        def quota_side_effect(*args, **kwargs):
            # First call (raise quota) succeeds, second call (set quota) fails.
            if shared_ctx.conn.network.update_quota.call_count == 1:
                return
            raise openstack.exceptions.HttpException(message="Quota service error")

        shared_ctx.conn.network.update_quota.side_effect = quota_side_effect

        # Exception propagates after retry exhaustion.
        with pytest.raises(
            openstack.exceptions.HttpException, match="Quota service error"
        ):
            ensure_preallocated_fips(_cfg(2), "proj-123", shared_ctx)

        # FIP was created.
        shared_ctx.conn.network.create_ip.assert_called_once()

        # State was persisted before quota failure (defensive persistence).
        save_calls = [
            c
            for c in shared_ctx.state_store.save.call_args_list
            if c[0][1] == ["preallocated_fips"]
        ]
        assert len(save_calls) == 1

    def test_quota_below_usage_race_condition(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Quota set fails with BadRequestException at max(desired, actual) → fallback."""
        existing = [
            _make_fip("id-1", "10.0.0.1", port_id="port-1"),
            _make_fip("id-2", "10.0.0.2", port_id="port-2"),
            _make_fip("id-3", "10.0.0.3", port_id="port-3"),
        ]
        shared_ctx.conn.network.ips.return_value = existing

        # Simulate race condition: even max(desired=1, actual=3) = 3 is rejected.
        def quota_side_effect(*args, **kwargs):
            fip_quota = kwargs.get("floating_ips")
            if fip_quota == 3:
                # Race: usage increased between list and quota set.
                raise openstack.exceptions.BadRequestException(
                    message="Quota below usage"
                )
            # Fallback to actual usage (4) succeeds.
            return

        shared_ctx.conn.network.update_quota.side_effect = quota_side_effect

        # Mock get_quota to return usage=4 (race: another FIP allocated).
        mock_quota = MagicMock()
        mock_quota.floating_ips = {"used": 4}
        shared_ctx.conn.network.get_quota.return_value = mock_quota

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        # 2 FAILED actions: 1 for in-use FIPs, 1 for quota race fallback.
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 2

        quota_failures = [
            a for a in failed if a.resource_type == "preallocated_fip_quota"
        ]
        assert len(quota_failures) == 1
        assert "must free" in quota_failures[0].details
        # Fallback quota was 4 (from get_quota), not 3 (max of desired/actual).
        assert "set to 4" in quota_failures[0].details

        # get_quota called after BadRequestException.
        shared_ctx.conn.network.get_quota.assert_called_once_with(
            "proj-123", details=True
        )
        # Final quota set to 4 (fallback usage).
        assert (
            call("proj-123", floating_ips=4)
            in shared_ctx.conn.network.update_quota.call_args_list
        )


# ---------------------------------------------------------------------------
# Edge case: BadRequestException fallback during quota set
# ---------------------------------------------------------------------------


class TestQuotaSetBadRequestFallback:
    """Quota set fails with BadRequestException even at max(desired, actual) → fallback."""

    def test_quota_below_usage_race_condition(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        existing = [
            _make_fip("id-1", "10.0.0.1", port_id="port-1"),
            _make_fip("id-2", "10.0.0.2", port_id="port-2"),
            _make_fip("id-3", "10.0.0.3", port_id="port-3"),
        ]
        shared_ctx.conn.network.ips.return_value = existing

        # Simulate race condition: even max(desired=1, actual=3) = 3 is rejected.
        def quota_side_effect(*args, **kwargs):
            fip_quota = kwargs.get("floating_ips")
            if fip_quota == 3:
                # Race: usage increased between list and quota set.
                raise openstack.exceptions.BadRequestException(
                    message="Quota below usage"
                )
            # Fallback to actual usage (4) succeeds.
            return

        shared_ctx.conn.network.update_quota.side_effect = quota_side_effect

        # Mock get_quota to return usage=4 (race: another FIP allocated).
        mock_quota = MagicMock()
        mock_quota.floating_ips = {"used": 4}
        shared_ctx.conn.network.get_quota.return_value = mock_quota

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        # 2 FAILED actions: 1 for in-use FIPs, 1 for quota race fallback.
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 2

        quota_failures = [
            a for a in failed if a.resource_type == "preallocated_fip_quota"
        ]
        assert len(quota_failures) == 1
        assert "must free" in quota_failures[0].details
        # Fallback quota was 4 (from get_quota), not 3 (max of desired/actual).
        assert "set to 4" in quota_failures[0].details

        # get_quota called after BadRequestException.
        shared_ctx.conn.network.get_quota.assert_called_once_with(
            "proj-123", details=True
        )
        # Final quota set to 4 (fallback usage).
        assert (
            call("proj-123", floating_ips=4)
            in shared_ctx.conn.network.update_quota.call_args_list
        )


class TestPerProjectFipSubnetOverride:
    """Test that FIP allocation uses per-project external network/subnet."""

    def test_fip_uses_per_project_external_subnet(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Test that FIP allocation uses per-project external subnet."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "external_network_name": "project-network",
                "external_network_subnet": "project-subnet",
                "quotas": {"network": {"floating_ips": 1}},
                "_config_path": "/tmp/proj.yaml",
                "_state_key": "test_project",
            }
        )

        # Populate external_network_map with the per-project network
        shared_ctx.external_network_map["project-network"] = "proj-net-id"

        mock_subnet = MagicMock()
        mock_subnet.id = "proj-subnet-id"
        mock_subnet.network_id = "proj-net-id"
        mock_subnet.cidr = "10.0.0.0/24"

        def mock_find_subnet(name):
            if name == "project-subnet":
                return mock_subnet
            return None

        shared_ctx.conn.network.find_subnet = mock_find_subnet
        shared_ctx.conn.network.subnets.return_value = [mock_subnet]

        shared_ctx.conn.network.ips.return_value = []

        mock_fip = _make_fip("fip-123", "10.0.0.5")
        shared_ctx.conn.network.create_ip.return_value = mock_fip

        actions = ensure_preallocated_fips(cfg, "proj-123", shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1

        create_call = shared_ctx.conn.network.create_ip.call_args
        assert create_call[1]["floating_network_id"] == "proj-net-id"
        assert create_call[1]["subnet_id"] == "proj-subnet-id"

    def test_fip_uses_global_defaults_when_no_override(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Test that FIP allocation uses global defaults when no per-project override."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "test_project",
                "resource_prefix": "test",
                "external_network_name": "",
                "external_network_subnet": "",
                "quotas": {"network": {"floating_ips": 1}},
                "_config_path": "/tmp/proj.yaml",
                "_state_key": "test_project",
            }
        )

        shared_ctx.conn.network.ips.return_value = []

        mock_fip = _make_fip("fip-123", "192.0.2.5")
        shared_ctx.conn.network.create_ip.return_value = mock_fip

        actions = ensure_preallocated_fips(cfg, "proj-123", shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1

        create_call = shared_ctx.conn.network.create_ip.call_args
        assert create_call[1]["floating_network_id"] == "ext-net-id-123"
        assert create_call[1]["subnet_id"] == "ext-subnet-123"


class TestForeignFipSafety:
    """Tests for foreign FIP (from different external network) safety checks."""

    def test_foreign_fips_excluded_from_scale_down(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """3 FIPs from 'dmz' + 2 from configured 'public', desired=3 → allocates 1 more."""
        foreign = [
            _make_fip("dmz-1", "10.1.0.1", floating_network_id="dmz-net-id"),
            _make_fip("dmz-2", "10.1.0.2", floating_network_id="dmz-net-id"),
            _make_fip("dmz-3", "10.1.0.3", floating_network_id="dmz-net-id"),
        ]
        matching = [
            _make_fip("pub-1", "10.0.0.1"),
            _make_fip("pub-2", "10.0.0.2"),
        ]
        shared_ctx.conn.network.ips.return_value = foreign + matching

        new_fip = _make_fip("pub-3", "10.0.0.3")
        shared_ctx.conn.network.create_ip.return_value = new_fip

        actions = ensure_preallocated_fips(_cfg(3), "proj-123", shared_ctx)

        # 1 FAILED (foreign warning) + 1 CREATED (scale-up from 2 to 3)
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(failed) == 1
        assert "foreign" in failed[0].details
        assert "3 FIP(s)" in failed[0].details
        assert len(created) == 1

        # dmz FIPs never touched
        shared_ctx.conn.network.delete_ip.assert_not_called()

    def test_foreign_fips_excluded_from_scale_up(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """2 foreign + 1 matching, desired=3 → allocates 2 more from configured network."""
        foreign = [
            _make_fip("dmz-1", "10.1.0.1", floating_network_id="dmz-net-id"),
            _make_fip("dmz-2", "10.1.0.2", floating_network_id="dmz-net-id"),
        ]
        matching = [
            _make_fip("pub-1", "10.0.0.1"),
        ]
        shared_ctx.conn.network.ips.return_value = foreign + matching

        new_a = _make_fip("pub-2", "10.0.0.2")
        new_b = _make_fip("pub-3", "10.0.0.3")
        shared_ctx.conn.network.create_ip.side_effect = [new_a, new_b]

        actions = ensure_preallocated_fips(_cfg(3), "proj-123", shared_ctx)

        # 1 FAILED (foreign warning) + 2 CREATED
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(failed) == 1
        assert "2 FIP(s)" in failed[0].details
        assert len(created) == 2
        assert shared_ctx.conn.network.create_ip.call_count == 2

    def test_all_fips_foreign_allocates_fresh(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """3 foreign FIPs, desired=2 → allocates 2 fresh, warning recorded."""
        foreign = [
            _make_fip("dmz-1", "10.1.0.1", floating_network_id="dmz-net-id"),
            _make_fip("dmz-2", "10.1.0.2", floating_network_id="dmz-net-id"),
            _make_fip("dmz-3", "10.1.0.3", floating_network_id="dmz-net-id"),
        ]
        shared_ctx.conn.network.ips.return_value = foreign

        new_a = _make_fip("pub-1", "10.0.0.1")
        new_b = _make_fip("pub-2", "10.0.0.2")
        shared_ctx.conn.network.create_ip.side_effect = [new_a, new_b]

        actions = ensure_preallocated_fips(_cfg(2), "proj-123", shared_ctx)

        # 1 FAILED (foreign warning) + 2 CREATED
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(failed) == 1
        assert "3 FIP(s)" in failed[0].details
        assert "foreign" in failed[0].details
        assert len(created) == 2

        # Foreign FIPs never deleted
        shared_ctx.conn.network.delete_ip.assert_not_called()

    def test_no_foreign_fips_normal_behavior(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """All FIPs match configured network → no foreign warning."""
        existing = [
            _make_fip("pub-1", "10.0.0.1"),
            _make_fip("pub-2", "10.0.0.2"),
        ]
        shared_ctx.conn.network.ips.return_value = existing

        actions = ensure_preallocated_fips(_cfg(2), "proj-123", shared_ctx)

        # No FAILED actions for foreign FIPs
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        assert len(failed) == 0

    def test_foreign_fips_dry_run_reports(
        self,
        dry_run_ctx: SharedContext,
    ) -> None:
        """Dry-run with foreign FIPs → warning in output."""
        foreign = [
            _make_fip("dmz-1", "10.1.0.1", floating_network_id="dmz-net-id"),
        ]
        matching = [
            _make_fip("pub-1", "10.0.0.1"),
        ]
        dry_run_ctx.conn.network.ips.return_value = foreign + matching

        actions = ensure_preallocated_fips(_cfg(2), "proj-123", dry_run_ctx)

        # Should have FAILED (foreign warning) + CREATED (would allocate 1)
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(failed) == 1
        assert "foreign" in failed[0].details
        assert len(created) == 1
        assert "would allocate 1" in created[0].details

        # No writes in dry-run
        dry_run_ctx.conn.network.create_ip.assert_not_called()
        dry_run_ctx.conn.network.delete_ip.assert_not_called()


def _make_router(*, external_gateway_info: dict | None = None) -> MagicMock:
    """Return a mock router with the given external_gateway_info."""
    router = MagicMock()
    router.external_gateway_info = external_gateway_info
    return router


class TestDetectRouterGateway:
    """Tests for _detect_router_gateway helper."""

    def test_returns_network_and_subnet(self, mock_conn: MagicMock) -> None:
        """Router with full gateway info → returns (network_id, subnet_id)."""
        router = _make_router(
            external_gateway_info={
                "network_id": "router-net-id",
                "external_fixed_ips": [
                    {"subnet_id": "router-subnet-id", "ip_address": "10.0.0.1"}
                ],
            }
        )
        mock_conn.network.routers.return_value = [router]

        net_id, subnet_id = _detect_router_gateway(mock_conn, "proj-123")

        assert net_id == "router-net-id"
        assert subnet_id == "router-subnet-id"
        mock_conn.network.routers.assert_called_once_with(project_id="proj-123")

    def test_returns_network_without_fixed_ips(self, mock_conn: MagicMock) -> None:
        """Router with gateway but no external_fixed_ips → returns (net, '')."""
        router = _make_router(external_gateway_info={"network_id": "router-net-id"})
        mock_conn.network.routers.return_value = [router]

        net_id, subnet_id = _detect_router_gateway(mock_conn, "proj-123")

        assert net_id == "router-net-id"
        assert subnet_id == ""

    def test_returns_empty_when_no_routers(self, mock_conn: MagicMock) -> None:
        """No routers → returns ('', '')."""
        mock_conn.network.routers.return_value = []

        net_id, subnet_id = _detect_router_gateway(mock_conn, "proj-123")

        assert net_id == ""
        assert subnet_id == ""

    def test_returns_empty_when_no_gateway(self, mock_conn: MagicMock) -> None:
        """Router with no gateway info → returns ('', '')."""
        router = _make_router(external_gateway_info=None)
        mock_conn.network.routers.return_value = [router]

        net_id, subnet_id = _detect_router_gateway(mock_conn, "proj-123")

        assert net_id == ""
        assert subnet_id == ""


class TestFipUsesRouterGateway:
    """FIP allocation should use the router's actual external network/subnet
    when it differs from the config-resolved values."""

    def test_fip_uses_router_network_when_config_differs(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Router on network A, config resolves to network B → FIPs allocated on A."""
        router = _make_router(
            external_gateway_info={
                "network_id": "router-ext-net",
                "external_fixed_ips": [
                    {"subnet_id": "router-subnet", "ip_address": "10.1.0.1"}
                ],
            }
        )
        shared_ctx.conn.network.routers.return_value = [router]

        shared_ctx.conn.network.ips.return_value = []

        new_fip = _make_fip("fip-1", "10.1.0.5", floating_network_id="router-ext-net")
        shared_ctx.conn.network.create_ip.return_value = new_fip

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1

        # FIP allocated on the router's network and subnet.
        create_call = shared_ctx.conn.network.create_ip.call_args
        assert create_call[1]["floating_network_id"] == "router-ext-net"
        assert create_call[1]["subnet_id"] == "router-subnet"

    def test_fip_uses_router_subnet_when_same_network_different_subnet(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Same external network, but router on subnet B while config auto-selected
        subnet A → FIPs allocated on subnet B (the router's subnet)."""
        # Router is on ext-net-id-123 (same as config) but different subnet.
        router = _make_router(
            external_gateway_info={
                "network_id": "ext-net-id-123",
                "external_fixed_ips": [
                    {"subnet_id": "external1-subnet-id", "ip_address": "78.104.208.109"}
                ],
            }
        )
        shared_ctx.conn.network.routers.return_value = [router]

        shared_ctx.conn.network.ips.return_value = []

        new_fip = _make_fip("fip-1", "78.104.208.50")
        shared_ctx.conn.network.create_ip.return_value = new_fip

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1

        # Same network, but subnet overridden to router's subnet.
        create_call = shared_ctx.conn.network.create_ip.call_args
        assert create_call[1]["floating_network_id"] == "ext-net-id-123"
        assert create_call[1]["subnet_id"] == "external1-subnet-id"

    def test_fip_uses_config_when_router_matches(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Router on same network+subnet as config → no override."""
        router = _make_router(
            external_gateway_info={
                "network_id": "ext-net-id-123",
                "external_fixed_ips": [
                    {"subnet_id": "ext-subnet-123", "ip_address": "10.0.0.1"}
                ],
            }
        )
        shared_ctx.conn.network.routers.return_value = [router]

        shared_ctx.conn.network.ips.return_value = []

        new_fip = _make_fip("fip-1", "10.0.0.1")
        shared_ctx.conn.network.create_ip.return_value = new_fip

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1

        # Config network and subnet used (matches router).
        create_call = shared_ctx.conn.network.create_ip.call_args
        assert create_call[1]["floating_network_id"] == "ext-net-id-123"
        assert create_call[1]["subnet_id"] == "ext-subnet-123"

    def test_fip_uses_config_when_no_router(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """No routers → config network used as-is."""
        shared_ctx.conn.network.routers.return_value = []

        shared_ctx.conn.network.ips.return_value = []

        new_fip = _make_fip("fip-1", "10.0.0.1")
        shared_ctx.conn.network.create_ip.return_value = new_fip

        actions = ensure_preallocated_fips(_cfg(1), "proj-123", shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1

        # Config network used, subnet preserved.
        create_call = shared_ctx.conn.network.create_ip.call_args
        assert create_call[1]["floating_network_id"] == "ext-net-id-123"
        assert create_call[1]["subnet_id"] == "ext-subnet-123"
