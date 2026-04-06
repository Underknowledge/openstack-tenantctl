"""Tests for the reconciler module — orchestration of resource provisioning."""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.models import ProjectConfig
from src.reconciler import reconcile
from src.utils import Action, ActionStatus, SharedContext


@pytest.fixture
def _make_project_cfg():
    """Factory for creating project config dicts with unique names."""

    def _factory(name: str = "test_project", state: str = "present") -> ProjectConfig:
        return ProjectConfig.from_dict(
            {
                "name": name,
                "resource_prefix": name,
                "_state_key": name,
                "state": state,
                "description": f"{name} description",
                "enabled": True,
                "domain_id": "default",
                "network": {
                    "mtu": 1500,
                    "subnet": {
                        "cidr": "192.168.1.0/24",
                        "gateway_ip": "192.168.1.254",
                        "allocation_pools": [
                            {"start": "192.168.1.1", "end": "192.168.1.253"},
                        ],
                        "dns_nameservers": ["8.8.8.8"],
                        "dhcp": True,
                    },
                },
                "quotas": {
                    "compute": {"cores": 20, "ram": 51200, "instances": 10},
                    "network": {
                        "floating_ips": 0,
                        "networks": 1,
                        "subnets": 1,
                        "routers": 1,
                        "ports": 50,
                        "security_groups": 10,
                        "security_group_rules": 100,
                    },
                    "block_storage": {
                        "gigabytes": 500,
                        "volumes": 20,
                        "snapshots": 10,
                    },
                },
                "security_group": {
                    "name": "default",
                    "rules": [],
                },
                "federation": {
                    "roles": ["member"],
                    "issuer": "https://idp.example.com/realms/test",
                    "mapping_id": "my-mapping",
                },
                "group_role_assignments": [
                    {"group": "test-admins", "roles": ["admin"]},
                ],
            }
        )

    return _factory


_PATCH_PREFIX = "src.reconciler"


@pytest.fixture
def patched_resources():
    """Patch all resource functions used by reconciler.

    Reduces 129 @patch decorators across 18 tests to a single fixture.
    Each mock is pre-configured with sensible defaults for happy-path testing.
    Tests can override these defaults by setting new side_effects or return_values.
    """
    targets = [
        "src.reconciler.ensure_project",
        "src.reconciler.ensure_group_role_assignments",
        "src.reconciler.ensure_network_stack",
        "src.reconciler.track_router_ips",
        "src.reconciler.ensure_preallocated_fips",
        "src.reconciler.ensure_preallocated_network",
        "src.reconciler.ensure_quotas",
        "src.reconciler.ensure_baseline_sg",
        "src.reconciler.unshelve_all_servers",
        "src.reconciler.shelve_all_servers",
        "src.reconciler.ensure_federation_mapping",
        "src.reconciler.find_existing_project",
        "src.reconciler.safety_check",
        "src.reconciler.teardown_project",
        "src.reconciler.is_project_disabled",
    ]
    with ExitStack() as stack:
        mocks = {t.rsplit(".", 1)[1]: stack.enter_context(patch(t)) for t in targets}

        # Default return values for happy-path scenarios
        mocks["ensure_project"].return_value = (
            Action(
                status=ActionStatus.CREATED,
                resource_type="project",
                name="test_project",
            ),
            "proj-123",
        )
        mocks["find_existing_project"].return_value = (None, None)
        mocks["safety_check"].return_value = []
        # Default: project is not disabled (steady-state present, no unshelve)
        mocks["is_project_disabled"].return_value = False

        yield SimpleNamespace(**mocks)


class TestPresentStatePipeline:
    """Verify present state execution order and behavior."""

    def test_present_calls_resources_in_dependency_order(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify present-state pipeline calls resource functions in correct dependency order.

        This is an implementation test because call order IS the contract:
        - ensure_project must run first (provides project_id)
        - network stack must exist before tracking router IPs
        - quotas/SG apply after network resources are created
        - unshelve runs last (after all provisioning complete, only on locked->present)
        """
        # Trigger unshelve by simulating locked->present transition.
        shared_ctx.state_store.load.return_value = {
            "metadata": {"last_reconciled_state": "locked"},
        }

        call_order: list[str] = []

        def track(name: str, return_value=None):
            def side_effect(*args, **kwargs):
                call_order.append(name)
                return return_value

            return side_effect

        project_action = Action(
            status=ActionStatus.CREATED,
            resource_type="project",
            name="test",
        )
        patched_resources.ensure_project.side_effect = track(
            "ensure_project",
            (project_action, "proj-123"),
        )
        patched_resources.ensure_group_role_assignments.side_effect = track(
            "ensure_group_role_assignments",
        )
        patched_resources.ensure_network_stack.side_effect = track("ensure_network_stack")
        patched_resources.track_router_ips.side_effect = track("track_router_ips")
        patched_resources.ensure_preallocated_fips.side_effect = track("ensure_preallocated_fips")
        patched_resources.ensure_preallocated_network.side_effect = track(
            "ensure_preallocated_network"
        )
        patched_resources.ensure_quotas.side_effect = track("ensure_quotas")
        patched_resources.ensure_baseline_sg.side_effect = track("ensure_baseline_sg")
        patched_resources.unshelve_all_servers.side_effect = track("unshelve_all_servers")
        patched_resources.ensure_federation_mapping.side_effect = track(
            "ensure_federation_mapping",
        )

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        assert call_order == [
            "ensure_project",
            "ensure_group_role_assignments",
            "ensure_network_stack",
            "track_router_ips",
            "ensure_preallocated_fips",
            "ensure_preallocated_network",
            "ensure_quotas",
            "ensure_baseline_sg",
            "unshelve_all_servers",
            "ensure_federation_mapping",
        ]

    def test_error_isolation_failed_project_does_not_block_others(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify that a failure in one project does not prevent others from running.

        Critical behavior: error isolation ensures one bad project doesn't block
        the entire reconciliation run. Federation mapping must still execute even
        when some projects fail.
        """
        cfg_fail = _make_project_cfg("project_fail")
        cfg_ok = _make_project_cfg("project_ok")

        project_action = Action(
            status=ActionStatus.CREATED,
            resource_type="project",
            name="project_ok",
        )

        def ensure_project_side_effect(cfg, ctx):
            if cfg.name == "project_fail":
                msg = "Simulated failure"
                raise Exception(msg)
            return project_action, "proj-ok-id"

        patched_resources.ensure_project.side_effect = ensure_project_side_effect

        reconcile([cfg_fail, cfg_ok], [cfg_fail, cfg_ok], shared_ctx)

        # Verify the failed project is recorded
        assert "project_fail" in shared_ctx.failed_projects
        assert len(shared_ctx.failed_projects) == 1

        # Verify the OK project is NOT in failed list
        assert "project_ok" not in shared_ctx.failed_projects

        # Verify both projects were attempted (error isolation, not short-circuit)
        assert patched_resources.ensure_project.call_count == 2

        # Verify federation mapping still runs despite failure
        patched_resources.ensure_federation_mapping.assert_called_once()


class TestErrorIsolationContinuesOnFailure:
    """Error in one project must not prevent reconciliation of subsequent projects."""

    def test_federation_barrier_runs_after_all_projects(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify federation mapping runs AFTER all projects, receives all_projects arg.

        Federation mapping is a shared resource that depends on ALL project configs,
        not just the filtered subset. This test verifies:
        1. Only filtered projects are reconciled (projects=[cfg1])
        2. But federation receives the complete list (all_projects=[cfg1, cfg2])
        3. Federation runs after per-project reconciliation completes
        """
        cfg1 = _make_project_cfg("project_one")
        cfg2 = _make_project_cfg("project_two")

        call_order: list[str] = []

        def track_ensure_project(cfg, ctx):
            call_order.append(f"ensure_project:{cfg.name}")
            return (
                Action(
                    status=ActionStatus.CREATED,
                    resource_type="project",
                    name=cfg.name,
                ),
                f"proj-{cfg.name}-id",
            )

        def track_federation(all_projects, ctx):
            call_order.append("ensure_federation_mapping")

        patched_resources.ensure_project.side_effect = track_ensure_project
        patched_resources.ensure_federation_mapping.side_effect = track_federation

        all_projects = [cfg1, cfg2]
        reconcile(projects=[cfg1], all_projects=all_projects, ctx=shared_ctx)

        # Only cfg1 was reconciled (filtered projects list)
        assert "ensure_project:project_one" in call_order
        assert "ensure_project:project_two" not in call_order

        # Federation mapping called with ALL projects, not just [cfg1]
        patched_resources.ensure_federation_mapping.assert_called_once_with(
            all_projects,
            shared_ctx,
        )

        # Federation ran AFTER project reconciliation
        assert call_order[-1] == "ensure_federation_mapping"


class TestFederationUsesAllProjects:
    """Federation mapping must receive all_projects, not the filtered projects list."""


class TestDryRunPropagated:
    """In dry_run mode, all ensure functions are still called (behavior is internal)."""

    def test_dry_run_propagates_to_all_resource_functions(
        self,
        patched_resources: SimpleNamespace,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify dry-run context is passed to all resource functions.

        Reconciler doesn't short-circuit in dry-run mode — it calls all resource
        functions and lets them handle dry-run internally. This test verifies the
        orchestration layer doesn't skip any steps.

        Note: unshelve is conditional (only on locked->present transition), so
        it is NOT called in steady-state dry-run.
        """
        project_action = Action(
            status=ActionStatus.SKIPPED,
            resource_type="project",
            name="test_project",
        )
        patched_resources.ensure_project.return_value = (project_action, "proj-123")

        reconcile(
            [sample_project_cfg],
            [sample_project_cfg],
            dry_run_ctx,
        )

        # Verify all present-state functions were called with dry_run_ctx
        patched_resources.ensure_project.assert_called_once()
        call_args = patched_resources.ensure_project.call_args
        assert call_args[0][1].dry_run is True

        patched_resources.ensure_group_role_assignments.assert_called_once()
        patched_resources.ensure_network_stack.assert_called_once()
        patched_resources.track_router_ips.assert_called_once()
        patched_resources.ensure_preallocated_fips.assert_called_once()
        patched_resources.ensure_preallocated_network.assert_called_once()
        patched_resources.ensure_quotas.assert_called_once()
        patched_resources.ensure_baseline_sg.assert_called_once()
        # Unshelve is conditional — not called in steady-state present
        patched_resources.unshelve_all_servers.assert_not_called()
        patched_resources.ensure_federation_mapping.assert_called_once()


class TestLockedStateDispatch:
    """Locked state calls ensure_project(enabled=False) + shelve, skips network/quotas/SG."""

    def test_locked_disables_project_and_shelves_vms_only(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify locked state: disable project + shelve VMs, skip network/quotas/SG.

        Locked state is a minimal provisioning mode:
        - Forces enabled=False on the project (even if config says enabled=True)
        - Shelves all ACTIVE servers
        - Skips network, quota, and security group provisioning
        - Group role assignments are skipped (unlike present state)
        """
        cfg = _make_project_cfg("locked_project", state="locked")

        project_action = Action(
            status=ActionStatus.UPDATED,
            resource_type="project",
            name="locked_project",
        )
        patched_resources.ensure_project.return_value = (
            project_action,
            "proj-locked-id",
        )

        reconcile([cfg], [cfg], shared_ctx)

        # Verify ensure_project called with enabled=False (overridden from config)
        call_args = patched_resources.ensure_project.call_args[0]
        assert call_args[0].enabled is False

        # Verify shelve_all_servers called
        patched_resources.shelve_all_servers.assert_called_once()
        shelve_call_args = patched_resources.shelve_all_servers.call_args[0]
        assert shelve_call_args[1] == "proj-locked-id"  # project_id

        # Verify resource provisioning skipped
        patched_resources.ensure_group_role_assignments.assert_not_called()
        patched_resources.ensure_network_stack.assert_not_called()
        patched_resources.ensure_quotas.assert_not_called()
        patched_resources.ensure_baseline_sg.assert_not_called()
        patched_resources.unshelve_all_servers.assert_not_called()


class TestAbsentStateDispatch:
    """Absent state runs safety_check -> revoke roles -> teardown."""

    def test_absent_runs_safety_check_then_revoke_then_teardown(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify absent state execution order: find → safety check → revoke → teardown.

        Absent state is destructive and must follow strict ordering:
        1. Find existing project (skip if not found)
        2. Safety check (fail if VMs/volumes exist)
        3. Revoke all group role assignments (convert state="absent")
        4. Tear down resources in reverse dependency order
        """
        cfg = _make_project_cfg("doomed_project", state="absent")

        patched_resources.find_existing_project.return_value = (
            "proj-doomed-id",
            "default",
        )
        patched_resources.safety_check.return_value = []  # Safe to delete

        reconcile([cfg], [cfg], shared_ctx)

        # Verify find_existing_project called with correct config
        patched_resources.find_existing_project.assert_called_once()
        find_call_args = patched_resources.find_existing_project.call_args[0]
        assert find_call_args[0].name == "doomed_project"

        # Verify safety_check called with project_id and name
        patched_resources.safety_check.assert_called_once_with(
            shared_ctx.conn, "proj-doomed-id", "doomed_project"
        )

        # Verify group roles revoked: all assignments converted to state="absent"
        patched_resources.ensure_group_role_assignments.assert_called_once()
        revoke_cfg = patched_resources.ensure_group_role_assignments.call_args[0][0]
        assert all(entry.state == "absent" for entry in revoke_cfg.group_role_assignments)

        # Verify teardown_project called with correct IDs
        patched_resources.teardown_project.assert_called_once()
        teardown_call_args = patched_resources.teardown_project.call_args[0]
        assert teardown_call_args[1] == "proj-doomed-id"  # project_id

    def test_absent_with_existing_vms_blocks_teardown_and_fails(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify absent state fails when safety check detects VMs.

        Safety check prevents accidental data loss: if VMs or volumes exist,
        teardown is blocked and the project goes to failed_projects.
        """
        cfg = _make_project_cfg("vm_project", state="absent")

        patched_resources.find_existing_project.return_value = ("proj-vm-id", "default")
        patched_resources.safety_check.return_value = [
            "project 'vm_project' has 2 server(s): web1, web2"
        ]

        reconcile([cfg], [cfg], shared_ctx)

        # Verify project recorded as failed
        assert "vm_project" in shared_ctx.failed_projects
        assert len(shared_ctx.failed_projects) == 1

        # Verify safety_check was called
        patched_resources.safety_check.assert_called_once_with(
            shared_ctx.conn, "proj-vm-id", "vm_project"
        )

        # Verify teardown_project NOT called (safety gate blocked it)
        patched_resources.teardown_project.assert_not_called()


class TestAbsentWithVMsFails:
    """Absent state with existing VMs should fail and go to failed_projects."""

    def test_absent_project_not_found_skips_gracefully(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify absent state skips when project doesn't exist (idempotent).

        If a project is already gone, teardown should skip gracefully with a
        SKIPPED action explaining why. This makes teardown idempotent.
        """
        cfg = _make_project_cfg("gone_project", state="absent")

        patched_resources.find_existing_project.return_value = (None, None)

        reconcile([cfg], [cfg], shared_ctx)

        # Verify project NOT in failed list (skip is success, not failure)
        assert "gone_project" not in shared_ctx.failed_projects

        # Verify teardown NOT called (nothing to tear down)
        patched_resources.teardown_project.assert_not_called()

        # Verify SKIPPED action recorded — it's a project skip, not a teardown
        skip_actions = [
            a
            for a in shared_ctx.actions
            if a.resource_type == "project" and a.status == ActionStatus.SKIPPED
        ]
        assert len(skip_actions) == 1
        assert skip_actions[0].name == "gone_project"
        assert "already absent" in skip_actions[0].details


class TestAbsentProjectNotFound:
    """Absent state with non-existent project should skip gracefully."""


class TestUnknownStateDispatch:
    """Unknown state should land the project in failed_projects."""

    def test_unknown_state_records_failure_and_continues(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify unknown state values fail gracefully without blocking other projects.

        If a project has an unrecognized state value, it should be added to
        failed_projects, but reconciliation should continue for other projects
        and federation mapping should still run.
        """
        cfg = _make_project_cfg("bogus_project", state="present")
        # Bypass frozen dataclass validation to inject invalid state for error testing
        object.__setattr__(cfg, "state", "bogus")

        reconcile([cfg], [cfg], shared_ctx)

        # Verify unknown state recorded as failure
        assert "bogus_project" in shared_ctx.failed_projects

        # Verify no resource functions were called for the invalid state
        patched_resources.ensure_project.assert_not_called()
        patched_resources.shelve_all_servers.assert_not_called()
        patched_resources.find_existing_project.assert_not_called()

        # Verify federation mapping still runs despite failed project
        patched_resources.ensure_federation_mapping.assert_called_once()


class TestAbsentDryRunSkips:
    """Absent state in dry-run mode should record SKIPPED without calling teardown."""

    def test_absent_dry_run_finds_project_and_reports(
        self,
        patched_resources: SimpleNamespace,
        dry_run_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Online dry-run for absent state reads project + safety check, reports DELETED.

        Online dry-run performs find_existing_project and safety_check (reads)
        but skips the actual teardown (writes).
        """
        cfg = _make_project_cfg("absent_project", state="absent")
        patched_resources.find_existing_project.return_value = (
            "proj-absent-123",
            "domain-123",
        )
        patched_resources.safety_check.return_value = []

        reconcile([cfg], [cfg], dry_run_ctx)

        # Reads happened
        patched_resources.find_existing_project.assert_called_once()
        patched_resources.safety_check.assert_called_once()

        # Teardown NOT called (dry-run)
        patched_resources.teardown_project.assert_not_called()

        # Verify NOT in failed list
        assert "absent_project" not in dry_run_ctx.failed_projects

        # Verify DELETED action recorded with dry-run explanation
        deleted_actions = [
            a
            for a in dry_run_ctx.actions
            if a.resource_type == "teardown" and a.status == ActionStatus.DELETED
        ]
        assert len(deleted_actions) == 1
        assert deleted_actions[0].name == "absent_project"
        assert "would tear down" in deleted_actions[0].details


class TestAbsentInconclusiveDoesNotBlockOtherProjects:
    """Inconclusive safety check on one absent project must not block other projects."""

    def test_safety_check_failure_isolates_one_project_not_all(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify error isolation: absent project's safety check failure doesn't block others.

        When one project's teardown is blocked by safety check errors (VMs exist,
        API errors, etc.), other projects must still be reconciled. This test
        verifies the error isolation mechanism works across different state types.
        """
        cfg_absent = _make_project_cfg("absent_project", state="absent")
        cfg_ok = _make_project_cfg("ok_project", state="present")

        # absent_project: found but safety check inconclusive (API error)
        patched_resources.find_existing_project.return_value = (
            "proj-absent-id",
            "default",
        )
        patched_resources.safety_check.return_value = [
            "project 'absent_project': server check inconclusive (API error)"
        ]

        # ok_project: present-state pipeline succeeds
        def ensure_project_side_effect(cfg, ctx):
            return (
                Action(
                    status=ActionStatus.CREATED,
                    resource_type="project",
                    name=cfg.name,
                ),
                f"proj-{cfg.name}-id",
            )

        patched_resources.ensure_project.side_effect = ensure_project_side_effect

        reconcile([cfg_absent, cfg_ok], [cfg_absent, cfg_ok], shared_ctx)

        # Verify absent_project failed (safety check blocked teardown)
        assert "absent_project" in shared_ctx.failed_projects

        # Verify teardown NOT called (safety gate blocked it)
        patched_resources.teardown_project.assert_not_called()

        # Verify ok_project's present-state pipeline still ran
        patched_resources.ensure_project.assert_called_once()
        ensure_call_args = patched_resources.ensure_project.call_args[0]
        assert ensure_call_args[0].name == "ok_project"

        # Verify ok_project NOT in failed list
        assert "ok_project" not in shared_ctx.failed_projects
        assert len(shared_ctx.failed_projects) == 1


# ---------------------------------------------------------------------------
# Metadata persistence tests
# ---------------------------------------------------------------------------


class TestPresentStatePartialFailure:
    """Test _reconcile_present when one resource module fails but others succeed."""

    def test_midstream_failure_short_circuits_and_records_failed(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify present-state pipeline short-circuits on error and records failure.

        When a resource function raises an exception partway through the pipeline:
        1. The project is added to failed_projects
        2. Functions BEFORE the failure point were already called
        3. Functions AFTER the failure point are NOT called (short-circuit)
        4. Other projects still run (error isolation)
        """
        project_action = Action(
            status=ActionStatus.CREATED,
            resource_type="project",
            name="test_project",
        )
        patched_resources.ensure_project.return_value = (project_action, "proj-123")
        patched_resources.ensure_network_stack.side_effect = RuntimeError("Network API failure")

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        # Verify project recorded as failed
        assert "test_project" in shared_ctx.failed_projects

        # Verify functions BEFORE failure point were called
        patched_resources.ensure_project.assert_called_once()
        patched_resources.ensure_group_role_assignments.assert_called_once()

        # Verify function AT failure point was called and raised
        patched_resources.ensure_network_stack.assert_called_once()

        # Verify functions AFTER failure point NOT called (short-circuit)
        patched_resources.track_router_ips.assert_not_called()
        patched_resources.ensure_preallocated_fips.assert_not_called()
        patched_resources.ensure_quotas.assert_not_called()
        patched_resources.ensure_baseline_sg.assert_not_called()
        patched_resources.unshelve_all_servers.assert_not_called()

    def test_locked_order_disable_before_shelve(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify locked state disables project BEFORE shelving servers.

        This is an implementation test because call order IS the contract:
        - ensure_project(enabled=False) must run first to disable the project
        - shelve_all_servers runs second to shelve VMs
        This order prevents users from launching new VMs while shelving is in progress.
        """
        cfg = _make_project_cfg("locked_project", state="locked")

        call_order: list[str] = []

        def track_ensure_project(cfg, ctx):
            call_order.append("ensure_project")
            return (
                Action(
                    status=ActionStatus.UPDATED,
                    resource_type="project",
                    name="locked_project",
                ),
                "proj-locked-id",
            )

        def track_shelve(cfg, project_id, ctx):
            call_order.append("shelve_all_servers")

        patched_resources.ensure_project.side_effect = track_ensure_project
        patched_resources.shelve_all_servers.side_effect = track_shelve

        reconcile([cfg], [cfg], shared_ctx)

        # Verify order: disable project, then shelve
        assert call_order == ["ensure_project", "shelve_all_servers"]


class TestLockedOrderDisableThenShelve:
    """Test that locked state disables project before shelving servers."""

    def test_safety_check_with_volumes_blocks_teardown(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify safety check blocks teardown when volumes exist.

        Safety check prevents accidental data loss by blocking teardown when:
        - VMs exist (servers)
        - Volumes exist (block storage)
        - API errors make the check inconclusive
        """
        cfg = _make_project_cfg("volume_project", state="absent")

        patched_resources.find_existing_project.return_value = (
            "proj-volume-id",
            "default",
        )
        patched_resources.safety_check.return_value = [
            "project 'volume_project' has 3 volume(s): vol1, vol2, vol3"
        ]

        reconcile([cfg], [cfg], shared_ctx)

        # Verify project recorded as failed (safety gate blocked teardown)
        assert "volume_project" in shared_ctx.failed_projects

        # Verify safety_check was called with correct args
        patched_resources.safety_check.assert_called_once_with(
            shared_ctx.conn, "proj-volume-id", "volume_project"
        )

        # Verify teardown NOT called (safety gate blocked it)
        patched_resources.teardown_project.assert_not_called()


class TestAbsentSafetyCheckBlocks:
    """Test that absent state raises SafetyCheckError when safety_check returns errors."""


class TestMixedStatesInSameRun:
    """Test reconciliation with multiple projects in different states."""

    def test_mixed_present_locked_absent_in_single_run(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Verify reconciler handles present/locked/absent states correctly in one run.

        This integration test verifies:
        - Present project: full pipeline (no unshelve in steady state)
        - Locked project: disable + shelve only (no network/quotas/SG)
        - Absent project: safety check + teardown
        - All three succeed without interfering with each other
        - Federation mapping runs once at the end
        """
        cfg_present = _make_project_cfg("present_project", state="present")
        cfg_locked = _make_project_cfg("locked_project", state="locked")
        cfg_absent = _make_project_cfg("absent_project", state="absent")

        def ensure_project_side_effect(cfg, ctx):
            return (
                Action(
                    status=ActionStatus.CREATED,
                    resource_type="project",
                    name=cfg.name,
                ),
                f"proj-{cfg.name}-id",
            )

        patched_resources.ensure_project.side_effect = ensure_project_side_effect
        patched_resources.find_existing_project.return_value = (
            "proj-absent-id",
            "default",
        )
        patched_resources.safety_check.return_value = []  # Safe to delete

        all_projects = [cfg_present, cfg_locked, cfg_absent]
        reconcile(all_projects, all_projects, shared_ctx)

        # Verify present project: full pipeline (unshelve skipped in steady state)
        assert patched_resources.ensure_project.call_count == 2  # present + locked
        assert patched_resources.ensure_network_stack.call_count == 1  # present only
        assert patched_resources.unshelve_all_servers.call_count == 0  # steady state

        # Verify locked project: disable + shelve only
        assert patched_resources.shelve_all_servers.call_count == 1  # locked only

        # Verify absent project: teardown
        patched_resources.teardown_project.assert_called_once()
        teardown_call_args = patched_resources.teardown_project.call_args[0]
        assert teardown_call_args[0].name == "absent_project"

        # Verify no failures
        assert len(shared_ctx.failed_projects) == 0

        # Verify federation mapping runs once at end
        patched_resources.ensure_federation_mapping.assert_called_once()


class TestPresentStateWithEnabledFalse:
    """Test that a project with state='present' and enabled=False follows normal provisioning."""

    def test_present_with_enabled_false_provisions_resources(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify present state with enabled=False still provisions all resources.

        Important distinction between state and enabled flag:
        - state='present' with enabled=False: disabled project WITH resources
        - state='locked': disabled project WITHOUT resources (minimal)
        This test verifies present state runs the full pipeline regardless of enabled.
        Unshelve is skipped because enabled=False (never unshelve a disabled project).
        """
        import dataclasses

        cfg_disabled = dataclasses.replace(sample_project_cfg, enabled=False)

        project_action = Action(
            status=ActionStatus.CREATED,
            resource_type="project",
            name="test_project",
        )
        patched_resources.ensure_project.return_value = (project_action, "proj-123")

        reconcile([cfg_disabled], [cfg_disabled], shared_ctx)

        # Verify ensure_project called with enabled=False (respects config)
        call_args = patched_resources.ensure_project.call_args[0]
        assert call_args[0].enabled is False

        # Verify all present-state pipeline functions still called
        patched_resources.ensure_group_role_assignments.assert_called_once()
        patched_resources.ensure_network_stack.assert_called_once()
        patched_resources.track_router_ips.assert_called_once()
        patched_resources.ensure_preallocated_fips.assert_called_once()
        patched_resources.ensure_preallocated_network.assert_called_once()
        patched_resources.ensure_quotas.assert_called_once()
        patched_resources.ensure_baseline_sg.assert_called_once()
        # Unshelve skipped: enabled=False means we never unshelve
        patched_resources.unshelve_all_servers.assert_not_called()

        # Verify no failure
        assert "test_project" not in shared_ctx.failed_projects


class TestMetadataPersistence:
    """Verify that project_id, domain_id, and last_reconciled_at are persisted."""

    def test_metadata_persisted_on_successful_reconciliation(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify reconciler persists project_id, domain_id, and last_reconciled_at on success.

        Metadata persistence allows:
        - Tracking which OpenStack project_id corresponds to each config
        - Recording when each project was last successfully reconciled
        - Detecting config drift over time
        """
        project_action = Action(
            status=ActionStatus.CREATED,
            resource_type="project",
            name="test_project",
        )
        patched_resources.ensure_project.return_value = (project_action, "proj-123")

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        # Verify metadata saves: project_id, domain_id, last_reconciled_at
        save_calls = shared_ctx.state_store.save.call_args_list

        # Verify project_id saved
        project_id_calls = [c for c in save_calls if c[0][1] == ["metadata", "project_id"]]
        assert len(project_id_calls) == 1
        assert project_id_calls[0][0][2] == "proj-123"

        # Verify domain_id saved
        domain_id_calls = [c for c in save_calls if c[0][1] == ["metadata", "domain_id"]]
        assert len(domain_id_calls) == 1
        assert domain_id_calls[0][0][2] == "default"

        # Verify last_reconciled_at saved as ISO 8601 timestamp
        last_reconciled_calls = [
            c for c in save_calls if c[0][1] == ["metadata", "last_reconciled_at"]
        ]
        assert len(last_reconciled_calls) == 1
        timestamp = last_reconciled_calls[0][0][2]
        assert "T" in timestamp  # ISO 8601 format: YYYY-MM-DDTHH:MM:SS
        assert timestamp.endswith(("Z", "+00:00"))  # UTC timezone

    def test_metadata_not_saved_when_reconciliation_fails(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify last_reconciled_at NOT saved when reconciliation fails.

        When reconciliation fails midstream (any resource function raises),
        project_id and domain_id ARE saved (from ensure_project before failure),
        but last_reconciled_at is NOT saved (only on full success).
        """
        patched_resources.ensure_project.side_effect = RuntimeError("boom")

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        # Verify no last_reconciled_at saved (reconciliation failed)
        save_calls = shared_ctx.state_store.save.call_args_list
        last_reconciled_calls = [
            c for c in save_calls if c[0][1] == ["metadata", "last_reconciled_at"]
        ]
        assert len(last_reconciled_calls) == 0

        # Verify project recorded as failed
        assert "test_project" in shared_ctx.failed_projects


# ---------------------------------------------------------------------------
# Conditional unshelve tests
# ---------------------------------------------------------------------------


class TestConditionalUnshelve:
    """Unshelve only runs on locked->present transition, not steady-state present."""

    def test_state_store_says_locked_triggers_unshelve(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """State store records previous state as 'locked' → unshelve called."""
        shared_ctx.state_store.load.return_value = {
            "metadata": {"last_reconciled_state": "locked"},
        }

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        patched_resources.unshelve_all_servers.assert_called_once()

    def test_state_store_says_present_skips_unshelve(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """State store records previous state as 'present' → unshelve skipped."""
        shared_ctx.state_store.load.return_value = {
            "metadata": {"last_reconciled_state": "present"},
        }

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        patched_resources.unshelve_all_servers.assert_not_called()

    def test_state_store_says_absent_skips_unshelve(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """State store records previous state as 'absent' → unshelve skipped."""
        shared_ctx.state_store.load.return_value = {
            "metadata": {"last_reconciled_state": "absent"},
        }

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        patched_resources.unshelve_all_servers.assert_not_called()

    def test_no_state_store_api_disabled_triggers_unshelve(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """No state store metadata + API says disabled → unshelve called (fallback)."""
        # Empty state store (no metadata) → falls through to API check
        shared_ctx.state_store.load.return_value = {}
        patched_resources.is_project_disabled.return_value = True

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        patched_resources.unshelve_all_servers.assert_called_once()

    def test_no_state_store_api_enabled_skips_unshelve(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """No state store metadata + API says enabled → unshelve skipped."""
        shared_ctx.state_store.load.return_value = {}
        patched_resources.is_project_disabled.return_value = False

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        patched_resources.unshelve_all_servers.assert_not_called()

    def test_locked_previous_but_enabled_false_skips_unshelve(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """State store says 'locked' but cfg.enabled=False → unshelve skipped."""
        import dataclasses

        cfg_disabled = dataclasses.replace(sample_project_cfg, enabled=False)
        shared_ctx.state_store.load.return_value = {
            "metadata": {"last_reconciled_state": "locked"},
        }

        reconcile([cfg_disabled], [cfg_disabled], shared_ctx)

        patched_resources.unshelve_all_servers.assert_not_called()


class TestLastReconciledStatePersistence:
    """Verify last_reconciled_state is persisted after successful reconciliation."""

    def test_present_state_persisted(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Successful present reconciliation persists last_reconciled_state='present'."""
        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        save_calls = shared_ctx.state_store.save.call_args_list
        state_calls = [c for c in save_calls if c[0][1] == ["metadata", "last_reconciled_state"]]
        assert len(state_calls) == 1
        assert state_calls[0][0][2] == "present"

    def test_locked_state_persisted(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        _make_project_cfg,
    ) -> None:
        """Successful locked reconciliation persists last_reconciled_state='locked'."""
        cfg = _make_project_cfg("locked_project", state="locked")

        project_action = Action(
            status=ActionStatus.UPDATED,
            resource_type="project",
            name="locked_project",
        )
        patched_resources.ensure_project.return_value = (
            project_action,
            "proj-locked-id",
        )

        reconcile([cfg], [cfg], shared_ctx)

        save_calls = shared_ctx.state_store.save.call_args_list
        state_calls = [c for c in save_calls if c[0][1] == ["metadata", "last_reconciled_state"]]
        assert len(state_calls) == 1
        assert state_calls[0][0][2] == "locked"

    def test_state_not_persisted_on_failure(
        self,
        patched_resources: SimpleNamespace,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """last_reconciled_state NOT saved when reconciliation fails."""
        patched_resources.ensure_project.side_effect = RuntimeError("boom")

        reconcile([sample_project_cfg], [sample_project_cfg], shared_ctx)

        save_calls = shared_ctx.state_store.save.call_args_list
        state_calls = [c for c in save_calls if c[0][1] == ["metadata", "last_reconciled_state"]]
        assert len(state_calls) == 0
