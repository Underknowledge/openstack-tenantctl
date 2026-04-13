"""Tests for project group role assignments — ensure_group_role_assignments."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from openstack.exceptions import NotFoundException

from src.resources.group_roles import ensure_group_role_assignments
from src.utils import ActionStatus, SharedContext

if TYPE_CHECKING:
    from src.models import ProjectConfig


def _make_group(group_id: str) -> MagicMock:
    """Return a mock Keystone group resource."""
    g = MagicMock()
    g.id = group_id
    return g


def _make_role(role_id: str) -> MagicMock:
    """Return a mock Keystone role resource."""
    r = MagicMock()
    r.id = role_id
    return r


class TestCreateAndGrantAssignments:
    """state=present — grant missing roles (CREATED)."""

    def test_create_new_assignments(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify new group-role assignments are created with correct IDs."""
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-001")
        identity.find_role.side_effect = lambda name: _make_role(f"role-{name}")
        identity.validate_group_has_project_role.side_effect = NotFoundException("not found")

        actions = ensure_group_role_assignments(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 2
        assert all(a.status == ActionStatus.CREATED for a in actions)
        # Verify the actual group_id, role_id, and project_id were passed to the API
        call_args_list = identity.assign_project_role_to_group.call_args_list
        assert len(call_args_list) == 2
        # Both calls should use project_id="proj-123" and group_id="grp-001"
        for call in call_args_list:
            assert call[0][0] == "proj-123"  # project_id
            assert call[0][1] == "grp-001"  # group_id
        # Verify role IDs match the mock (sample_project_cfg has ["admin", "member"])
        role_ids = {call[0][2] for call in call_args_list}
        assert role_ids == {"role-admin", "role-member"}
        # Verify action details contain meaningful info
        assert any("granted admin to" in a.details for a in actions)
        assert any("granted member to" in a.details for a in actions)

    def test_skip_existing_assignments(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify no API calls when all assignments already exist."""
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-001")
        identity.find_role.side_effect = lambda name: _make_role(f"role-{name}")
        # validate returns True — role already present
        identity.validate_group_has_project_role.return_value = True

        actions = ensure_group_role_assignments(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].details == "all assignments already in desired state"
        identity.assign_project_role_to_group.assert_not_called()

    def test_default_state_is_present(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify omitted state defaults to present (grant)."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "ops", "roles": ["member"]},  # no state key
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-ops")
        identity.find_role.return_value = _make_role("role-member")
        identity.validate_group_has_project_role.side_effect = NotFoundException("not found")

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.CREATED
        assert "granted member to ops" in actions[0].details
        identity.assign_project_role_to_group.assert_called_once_with("proj-123", "grp-ops", "role-member")
        identity.unassign_project_role_from_group.assert_not_called()

    def test_assigns_when_validate_returns_false(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify SDK returning False (not raising) triggers assignment."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "ops", "roles": ["member"]},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-ops")
        identity.find_role.return_value = _make_role("role-member")
        # SDK returns False instead of raising NotFoundException
        identity.validate_group_has_project_role.return_value = False

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.CREATED
        assert "granted member to ops" in actions[0].details
        identity.assign_project_role_to_group.assert_called_once_with("proj-123", "grp-ops", "role-member")


class TestRevokeAssignments:
    """state=absent — revoke existing roles (UPDATED)."""

    def test_remove_absent_assignments(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify existing group-role assignments are revoked with correct IDs."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "legacy-group", "roles": ["member"], "state": "absent"},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-legacy")
        identity.find_role.return_value = _make_role("role-member")
        # validate returns True — role is present, so it should be revoked
        identity.validate_group_has_project_role.return_value = True

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "revoked member from legacy-group" in actions[0].details
        identity.unassign_project_role_from_group.assert_called_once_with("proj-123", "grp-legacy", "role-member")

    def test_skip_already_absent(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify state=absent with no existing assignment is skipped."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "gone-group", "roles": ["member"], "state": "absent"},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-gone")
        identity.find_role.return_value = _make_role("role-member")
        identity.validate_group_has_project_role.side_effect = NotFoundException("not found")

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].details == "all assignments already in desired state"
        identity.unassign_project_role_from_group.assert_not_called()

    def test_revoke_never_assigned_returns_false(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify state=absent with validate returning False (not raising) is skipped."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "ghost-group", "roles": ["phantom"], "state": "absent"},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-ghost")
        identity.find_role.return_value = _make_role("role-phantom")
        # SDK returns False instead of raising NotFoundException
        identity.validate_group_has_project_role.return_value = False

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].details == "all assignments already in desired state"
        identity.unassign_project_role_from_group.assert_not_called()


class TestSkipConditions:
    """Skip scenarios: dry-run, no assignments, already in desired state."""

    def test_dry_run_reads_and_reports(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Online dry-run reads groups/roles, reports what would be granted."""
        identity = dry_run_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-001")
        identity.find_role.side_effect = lambda name: _make_role(f"role-{name}")
        identity.validate_group_has_project_role.side_effect = NotFoundException("not found")

        actions = ensure_group_role_assignments(sample_project_cfg, "proj-123", dry_run_ctx)

        assert len(actions) == 2
        assert all(a.status == ActionStatus.CREATED for a in actions)
        assert all("would grant" in a.details for a in actions)
        # Reads happened
        identity.find_group.assert_called()
        identity.find_role.assert_called()
        # No writes
        identity.assign_project_role_to_group.assert_not_called()

    def test_offline_dry_run_skips(
        self,
        offline_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Offline mode → SKIPPED with no API calls."""
        actions = ensure_group_role_assignments(sample_project_cfg, "proj-123", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details

    def test_missing_key(self, shared_ctx: SharedContext) -> None:
        """Verify missing group_role_assignments key is skipped."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict({"name": "test", "resource_prefix": "test"})
        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].details == "no group_role_assignments configured"

    def test_empty_list(self, shared_ctx: SharedContext) -> None:
        """Verify empty group_role_assignments list is skipped."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [],
            }
        )

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].details == "no group_role_assignments configured"

    def test_empty_roles_list(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify group entry with empty roles list produces no actions."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "nobody", "roles": []},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-nobody")

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        # No roles to assign → SKIPPED (all assignments already in desired state)
        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].details == "all assignments already in desired state"
        identity.assign_project_role_to_group.assert_not_called()
        identity.find_role.assert_not_called()


class TestErrorHandling:
    """Error scenarios: group not found, role not found, partial failures."""

    def test_group_not_found_raises(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify ValueError raised when group cannot be found."""
        shared_ctx.conn.identity.find_group.return_value = None

        with pytest.raises(ValueError, match="Group not found: 'test-admin-group'"):
            ensure_group_role_assignments(sample_project_cfg, "proj-123", shared_ctx)

    def test_role_not_found_raises(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Verify ValueError raised when role cannot be found."""
        shared_ctx.conn.identity.find_group.return_value = _make_group("grp-001")
        shared_ctx.conn.identity.find_role.return_value = None

        with pytest.raises(ValueError, match="Role not found: 'admin'"):
            ensure_group_role_assignments(sample_project_cfg, "proj-123", shared_ctx)

    def test_partial_assignment_failure(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify first role succeeds before second role assignment fails."""
        from openstack.exceptions import HttpException

        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "ops", "roles": ["member", "admin"]},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-ops")
        identity.find_role.side_effect = lambda name: _make_role(f"role-{name}")
        identity.validate_group_has_project_role.return_value = False

        # @retry attempts 5 times total (1 initial + 4 retries) for admin role
        # First assign succeeds, second raises HttpException 5 times
        identity.assign_project_role_to_group.side_effect = [
            None,  # member succeeds (first role)
            HttpException(message="Internal server error"),  # admin attempt 1
            HttpException(message="Internal server error"),  # admin attempt 2
            HttpException(message="Internal server error"),  # admin attempt 3
            HttpException(message="Internal server error"),  # admin attempt 4
            HttpException(message="Internal server error"),  # admin attempt 5
        ]

        # Should raise on second assignment failure (retry exhausted)
        with pytest.raises(HttpException, match="Internal server error"):
            ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        # First role was assigned successfully before exception (1 + 5 retries for admin)
        assert identity.assign_project_role_to_group.call_count == 6


class TestEdgeCases:
    """Edge cases: multiple groups/roles, mixed states, caching behavior."""

    def test_multiple_groups_multiple_roles(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify multiple entries with multiple roles produce one action per assignment."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "admins", "roles": ["admin", "member"]},
                    {"group": "readers", "roles": ["reader"]},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.side_effect = lambda name: _make_group(f"grp-{name}")
        identity.find_role.side_effect = lambda name: _make_role(f"role-{name}")
        identity.validate_group_has_project_role.side_effect = NotFoundException("not found")

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        # 2 roles for admins + 1 role for readers = 3 CREATED actions
        assert len(actions) == 3
        assert all(a.status == ActionStatus.CREATED for a in actions)
        # Verify the correct group/role combinations were assigned
        call_args_list = identity.assign_project_role_to_group.call_args_list
        assert len(call_args_list) == 3
        assigned = {(call[0][1], call[0][2]) for call in call_args_list}
        assert assigned == {
            ("grp-admins", "role-admin"),
            ("grp-admins", "role-member"),
            ("grp-readers", "role-reader"),
        }

    def test_multiple_groups_same_role(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify same role assigned to multiple groups independently."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "team-a", "roles": ["member"]},
                    {"group": "team-b", "roles": ["member"]},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.side_effect = lambda name: _make_group(f"grp-{name}")
        identity.find_role.return_value = _make_role("role-member")
        identity.validate_group_has_project_role.side_effect = NotFoundException("not found")

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        # Two separate assignments of "member" role to two different groups
        assert len(actions) == 2
        assert all(a.status == ActionStatus.CREATED for a in actions)
        # Verify both groups got the same role
        call_args_list = identity.assign_project_role_to_group.call_args_list
        assert len(call_args_list) == 2
        assert call_args_list[0][0] == ("proj-123", "grp-team-a", "role-member")
        assert call_args_list[1][0] == ("proj-123", "grp-team-b", "role-member")

    def test_mixed_present_and_absent(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify config with both present and absent assignments processes correctly."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "new-group", "roles": ["member"], "state": "present"},
                    {"group": "old-group", "roles": ["admin"], "state": "absent"},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.side_effect = lambda name: _make_group(f"grp-{name}")
        identity.find_role.side_effect = lambda name: _make_role(f"role-{name}")

        # new-group/member doesn't exist (will be created), old-group/admin exists (will be revoked)
        def validate_side_effect(project_id: str, group_id: str, role_id: str) -> bool:
            return "new-group" not in group_id  # False for new-group, True for old-group

        identity.validate_group_has_project_role.side_effect = validate_side_effect

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        # One CREATED (new-group+member), one UPDATED (old-group+admin revoked)
        assert len(actions) == 2
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        updated = [a for a in actions if a.status == ActionStatus.UPDATED]
        assert len(created) == 1
        assert len(updated) == 1
        assert "granted member to new-group" in created[0].details
        assert "revoked admin from old-group" in updated[0].details
        # Verify correct API calls with correct IDs
        identity.assign_project_role_to_group.assert_called_once_with("proj-123", "grp-new-group", "role-member")
        identity.unassign_project_role_from_group.assert_called_once_with("proj-123", "grp-old-group", "role-admin")

    def test_group_caching_across_roles(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify group lookup is cached — find_group called once for multiple roles."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "ops", "roles": ["member", "admin", "reader"]},
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.return_value = _make_group("grp-ops")
        identity.find_role.side_effect = lambda name: _make_role(f"role-{name}")
        identity.validate_group_has_project_role.return_value = False

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        # Three roles assigned
        assert len(actions) == 3
        assert all(a.status == ActionStatus.CREATED for a in actions)
        # Group lookup called only once (cached for subsequent roles)
        assert identity.find_group.call_count == 1
        identity.find_group.assert_called_once_with("ops")
        # Role lookup called three times (once per unique role)
        assert identity.find_role.call_count == 3

    def test_role_caching_across_groups(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        """Verify role lookup is cached — find_role called once per unique role."""
        from src.models import ProjectConfig

        cfg = ProjectConfig.from_dict(
            {
                "name": "test",
                "resource_prefix": "test",
                "group_role_assignments": [
                    {"group": "team-a", "roles": ["member"]},
                    {"group": "team-b", "roles": ["member"]},  # same role
                ],
            }
        )
        identity = shared_ctx.conn.identity
        identity.find_group.side_effect = lambda name: _make_group(f"grp-{name}")
        identity.find_role.return_value = _make_role("role-member")
        identity.validate_group_has_project_role.return_value = False

        actions = ensure_group_role_assignments(cfg, "proj-123", shared_ctx)

        # Two groups, same role
        assert len(actions) == 2
        assert all(a.status == ActionStatus.CREATED for a in actions)
        # Group lookup called twice (one per unique group)
        assert identity.find_group.call_count == 2
        # Role lookup called only once (cached for second group)
        assert identity.find_role.call_count == 1
        identity.find_role.assert_called_once_with("member")
