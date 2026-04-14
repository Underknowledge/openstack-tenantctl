"""Tests for Keystone group lifecycle in group-based federation mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.models import ProjectConfig
from src.resources.keystone_groups import ensure_keystone_groups
from src.utils import ActionStatus, SharedContext

DOMAIN_UUID = "domain-uuid-123"


def _stub_domain(conn: MagicMock, domain_id: str = DOMAIN_UUID) -> None:
    """Configure find_domain to return a mock domain with the given UUID."""
    domain = MagicMock()
    domain.id = domain_id
    conn.identity.find_domain.return_value = domain


def _make_project_cfg(
    name: str,
    role_assignments: list[dict],
    mode: str = "group",
    group_name_separator: str = " ",
    state: str = "present",
) -> ProjectConfig:
    """Build a minimal project config for keystone group tests."""
    # Simulate config loader's _resolve_federation_entry_modes():
    # each entry inherits the federation-level mode unless it already has one.
    for entry in role_assignments:
        if "mode" not in entry:
            entry["mode"] = mode
    return ProjectConfig.from_dict(
        {
            "name": name,
            "resource_prefix": name.replace(" ", ""),
            "state": state,
            "domain_id": "default",
            "federation": {
                "issuer": "https://idp.example.com",
                "mapping_id": "my-mapping",
                "role_assignments": role_assignments,
                "mode": mode,
                "group_name_separator": group_name_separator,
            },
        }
    )


class TestEnsureKeystoneGroups:
    """Create/skip Keystone groups for group-mode federation."""

    def test_creates_missing_groups(self, shared_ctx: SharedContext) -> None:
        cfg = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        _stub_domain(shared_ctx.conn)
        shared_ctx.conn.identity.find_group.return_value = None

        actions = ensure_keystone_groups([cfg], shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.CREATED
        assert actions[0].name == "proj member"
        shared_ctx.conn.identity.find_domain.assert_called_once_with("default")
        shared_ctx.conn.identity.find_group.assert_called_once_with("proj member", domain_id=DOMAIN_UUID)
        shared_ctx.conn.identity.create_group.assert_called_once_with(name="proj member", domain_id=DOMAIN_UUID)

    def test_skips_existing_groups(self, shared_ctx: SharedContext) -> None:
        cfg = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        _stub_domain(shared_ctx.conn)
        shared_ctx.conn.identity.find_group.return_value = MagicMock(id="grp-123")

        actions = ensure_keystone_groups([cfg], shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "already exists" in actions[0].details
        shared_ctx.conn.identity.find_group.assert_called_once_with("proj member", domain_id=DOMAIN_UUID)
        shared_ctx.conn.identity.create_group.assert_not_called()

    def test_dry_run_reports_without_creating(self, dry_run_ctx: SharedContext) -> None:
        cfg = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        _stub_domain(dry_run_ctx.conn)
        dry_run_ctx.conn.identity.find_group.return_value = None

        actions = ensure_keystone_groups([cfg], dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.CREATED
        assert "would create" in actions[0].details
        dry_run_ctx.conn.identity.find_group.assert_called_once_with("proj member", domain_id=DOMAIN_UUID)
        dry_run_ctx.conn.identity.create_group.assert_not_called()

    def test_offline_skips(self, offline_ctx: SharedContext) -> None:
        cfg = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )

        actions = ensure_keystone_groups([cfg], offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details

    def test_no_group_mode_projects_skips(self, shared_ctx: SharedContext) -> None:
        cfg = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
            mode="project",
        )

        actions = ensure_keystone_groups([cfg], shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "no group-mode projects" in actions[0].details

    def test_deduplication(self, shared_ctx: SharedContext) -> None:
        """Same derived name across projects is created only once."""
        cfg1 = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        cfg2 = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["reader"]}],
        )
        _stub_domain(shared_ctx.conn)
        shared_ctx.conn.identity.find_group.return_value = None

        actions = ensure_keystone_groups([cfg1, cfg2], shared_ctx)

        # Only one group created despite two projects deriving the same name
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1
        assert created[0].name == "proj member"
        shared_ctx.conn.identity.create_group.assert_called_once()

    def test_absent_projects_excluded(self, shared_ctx: SharedContext) -> None:
        """Only PRESENT projects contribute groups."""
        cfg_present = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        cfg_absent = _make_project_cfg(
            "gone",
            [{"idp_group": "member", "roles": ["member"]}],
            state="absent",
        )

        _stub_domain(shared_ctx.conn)
        shared_ctx.conn.identity.find_group.return_value = None

        actions = ensure_keystone_groups([cfg_present, cfg_absent], shared_ctx)

        # Only present project's group created
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1
        assert created[0].name == "proj member"

    def test_multiple_assignments_multiple_groups(self, shared_ctx: SharedContext) -> None:
        """Each role_assignment produces a distinct group."""
        cfg = _make_project_cfg(
            "proj",
            [
                {"idp_group": "member", "roles": ["member"]},
                {"idp_group": "reader", "roles": ["reader"]},
            ],
        )
        _stub_domain(shared_ctx.conn)
        shared_ctx.conn.identity.find_group.return_value = None

        actions = ensure_keystone_groups([cfg], shared_ctx)

        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 2
        names = {a.name for a in created}
        assert names == {"proj member", "proj reader"}

    def test_mixed_entry_modes(self, shared_ctx: SharedContext) -> None:
        """Only entries with mode=group produce Keystone groups."""
        cfg = ProjectConfig.from_dict(
            {
                "name": "proj",
                "resource_prefix": "proj",
                "state": "present",
                "domain_id": "default",
                "federation": {
                    "issuer": "https://idp.example.com",
                    "mapping_id": "my-mapping",
                    "mode": "project",
                    "role_assignments": [
                        {"idp_group": "member", "roles": ["member"], "mode": "project"},
                        {"idp_group": "reader", "roles": ["reader"], "mode": "group"},
                    ],
                },
            }
        )
        _stub_domain(shared_ctx.conn)
        shared_ctx.conn.identity.find_group.return_value = None

        actions = ensure_keystone_groups([cfg], shared_ctx)

        # Only the group-mode entry produces a Keystone group
        created = [a for a in actions if a.status == ActionStatus.CREATED]
        assert len(created) == 1
        assert created[0].name == "proj reader"
