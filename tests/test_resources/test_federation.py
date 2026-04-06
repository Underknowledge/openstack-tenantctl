"""Tests for federation mapping provisioning — ensure_federation_mapping."""

from __future__ import annotations

import dataclasses

from src.models import ProjectConfig
from src.resources.federation import _build_generated_rules, ensure_federation_mapping
from src.utils import ActionStatus, SharedContext


def _make_project_cfg(
    name: str,
    role_assignments: list[dict],
    issuer: str = "https://myidp.corp/realms/myrealm",
    mapping_id: str = "my-mapping",
    group_prefix: str = "/services/openstack/",
) -> ProjectConfig:
    """Build a minimal project config with federation settings."""
    return ProjectConfig.from_dict(
        {
            "name": name,
            "resource_prefix": name,
            "federation": {
                "issuer": issuer,
                "mapping_id": mapping_id,
                "group_prefix": group_prefix,
                "role_assignments": role_assignments,
            },
        }
    )


class TestEnsureFederationMapping:
    """Core functionality: update/skip/create decisions and API interactions."""

    def test_update_when_rules_differ(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [
                    {
                        "idp_group": "member",
                        "roles": ["member", "load-balancer_member"],
                    },
                    {"idp_group": "reader", "roles": ["reader"]},
                ],
            ),
        ]

        shared_ctx.current_mapping_rules = [{"old": "rule"}]
        shared_ctx.mapping_exists = True

        action = ensure_federation_mapping(projects, shared_ctx)

        # Verify the code chose to UPDATE (not CREATE) because mapping exists
        assert action.status == ActionStatus.UPDATED
        assert action.resource_type == "federation_mapping"
        assert "rules=2" in action.details
        shared_ctx.conn.identity.update_mapping.assert_called_once()
        shared_ctx.conn.identity.create_mapping.assert_not_called()

        # Verify update_mapping received the correct rules structure
        call_kwargs = shared_ctx.conn.identity.update_mapping.call_args[1]
        rules = call_kwargs["rules"]
        assert len(rules) == 2

        # First rule: member group grants member + load-balancer_member
        member_rule = rules[0]
        assert member_rule["local"][0] == {"user": {"name": "{0}", "email": "{1}"}}
        member_roles = member_rule["local"][1]["projects"][0]["roles"]
        assert member_roles == [{"name": "member"}, {"name": "load-balancer_member"}]
        assert member_rule["local"][1]["projects"][0]["name"] == "test_project"
        assert member_rule["remote"][3]["any_one_of"] == ["/services/openstack/test_project/member"]

        # Second rule: reader group grants reader
        reader_rule = rules[1]
        reader_roles = reader_rule["local"][1]["projects"][0]["roles"]
        assert reader_roles == [{"name": "reader"}]
        assert reader_rule["remote"][3]["any_one_of"] == ["/services/openstack/test_project/reader"]

    def test_skip_when_rules_match(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [{"idp_group": "member", "roles": ["member"]}],
            ),
        ]

        expected_rules = _build_generated_rules(projects)
        shared_ctx.current_mapping_rules = expected_rules
        shared_ctx.static_mapping_rules = []

        action = ensure_federation_mapping(projects, shared_ctx)

        # Verify the code detected matching rules and chose to skip
        assert action.status == ActionStatus.SKIPPED
        assert action.resource_type == "federation_mapping"
        assert "rules=1" in action.details
        assert "already up to date" in action.details
        # Verify NO API calls were made (not just that update wasn't called)
        shared_ctx.conn.identity.update_mapping.assert_not_called()
        shared_ctx.conn.identity.create_mapping.assert_not_called()

    def test_dry_run_reports_update(
        self,
        dry_run_ctx: SharedContext,
    ) -> None:
        """Online dry-run compares rules and reports what would change."""
        projects = [
            _make_project_cfg(
                "test_project",
                [{"idp_group": "member", "roles": ["member"]}],
            ),
        ]

        # Current rules differ from generated → would update
        dry_run_ctx.current_mapping_rules = [{"old": "rule"}]

        action = ensure_federation_mapping(projects, dry_run_ctx)

        assert action.status == ActionStatus.UPDATED
        assert "would update" in action.details
        # Verify NO API calls were made (dry-run contract)
        dry_run_ctx.conn.identity.update_mapping.assert_not_called()
        dry_run_ctx.conn.identity.create_mapping.assert_not_called()

    def test_dry_run_skips_when_up_to_date(
        self,
        dry_run_ctx: SharedContext,
    ) -> None:
        """Online dry-run with matching rules �� SKIPPED."""
        projects = [
            _make_project_cfg(
                "test_project",
                [{"idp_group": "member", "roles": ["member"]}],
            ),
        ]

        expected_rules = _build_generated_rules(projects)
        dry_run_ctx.current_mapping_rules = expected_rules
        dry_run_ctx.static_mapping_rules = []

        action = ensure_federation_mapping(projects, dry_run_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert "already up to date" in action.details
        dry_run_ctx.conn.identity.update_mapping.assert_not_called()
        dry_run_ctx.conn.identity.create_mapping.assert_not_called()

    def test_mapping_creation_when_not_exists(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [{"idp_group": "member", "roles": ["member"]}],
            ),
        ]

        shared_ctx.current_mapping_rules = [{"old": "rule"}]
        shared_ctx.mapping_exists = False  # Mapping doesn't exist yet

        action = ensure_federation_mapping(projects, shared_ctx)

        # Verify the code chose to CREATE (not UPDATE) because mapping doesn't exist
        assert action.status == ActionStatus.CREATED
        assert action.resource_type == "federation_mapping"
        shared_ctx.conn.identity.create_mapping.assert_called_once()
        shared_ctx.conn.identity.update_mapping.assert_not_called()

        # Verify create_mapping received correct mapping ID and rules
        call_kwargs = shared_ctx.conn.identity.create_mapping.call_args[1]
        assert call_kwargs["id"] == "my-mapping"
        assert len(call_kwargs["rules"]) == 1


class TestRuleSorting:
    """Generated rules are sorted deterministically; static rules appear first."""

    def test_rules_sorted_by_project_and_group(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "zulu_project",
                [
                    {"idp_group": "reader", "roles": ["reader"]},
                    {"idp_group": "member", "roles": ["member"]},
                ],
            ),
            _make_project_cfg(
                "alpha_project",
                [
                    {"idp_group": "admin", "roles": ["admin"]},
                    {"idp_group": "member", "roles": ["member"]},
                ],
            ),
        ]

        shared_ctx.current_mapping_rules = [{"old": "rule"}]
        shared_ctx.mapping_exists = True

        ensure_federation_mapping(projects, shared_ctx)

        shared_ctx.conn.identity.update_mapping.assert_called_once()
        call_kwargs = shared_ctx.conn.identity.update_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify sorting: alpha_project comes before zulu_project
        assert len(rules) == 4
        assert rules[0]["local"][1]["projects"][0]["name"] == "alpha_project"
        assert rules[0]["remote"][3]["any_one_of"] == ["/services/openstack/alpha_project/admin"]
        assert rules[1]["local"][1]["projects"][0]["name"] == "alpha_project"
        assert rules[1]["remote"][3]["any_one_of"] == ["/services/openstack/alpha_project/member"]
        assert rules[2]["local"][1]["projects"][0]["name"] == "zulu_project"
        assert rules[2]["remote"][3]["any_one_of"] == ["/services/openstack/zulu_project/member"]
        assert rules[3]["local"][1]["projects"][0]["name"] == "zulu_project"
        assert rules[3]["remote"][3]["any_one_of"] == ["/services/openstack/zulu_project/reader"]

    def test_static_rules_placed_first(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [{"idp_group": "member", "roles": ["member"]}],
            ),
        ]

        static_rule = {
            "local": [{"user": {"name": "admin"}}],
            "remote": [{"type": "HARDCODED"}],
        }
        shared_ctx.static_mapping_rules = [static_rule]
        shared_ctx.current_mapping_rules = [{"old": "rule"}]
        shared_ctx.mapping_exists = True

        ensure_federation_mapping(projects, shared_ctx)

        shared_ctx.conn.identity.update_mapping.assert_called_once()
        call_kwargs = shared_ctx.conn.identity.update_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify static rule appears BEFORE generated rules
        assert len(rules) == 2
        assert rules[0] == static_rule
        assert rules[1]["remote"][3]["type"] == "OIDC-groups"
        assert rules[1]["local"][1]["projects"][0]["name"] == "test_project"


class TestGroupPathResolution:
    """IDP group path resolution: absolute paths, relative paths, multiple groups."""

    def test_full_path_group_used_as_is(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [
                    {
                        "idp_group": "/custom/path/heat",
                        "roles": ["heat_stack_user"],
                    },
                ],
            ),
        ]

        shared_ctx.current_mapping_rules = []
        shared_ctx.mapping_exists = False

        action = ensure_federation_mapping(projects, shared_ctx)

        assert action.status == ActionStatus.CREATED
        call_kwargs = shared_ctx.conn.identity.create_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify absolute path is used as-is (not expanded with prefix)
        assert len(rules) == 1
        assert rules[0]["remote"][3]["any_one_of"] == ["/custom/path/heat"]
        assert rules[0]["local"][1]["projects"][0]["name"] == "test_project"
        assert rules[0]["local"][1]["projects"][0]["roles"] == [{"name": "heat_stack_user"}]

    def test_multiple_roles_per_group(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [
                    {
                        "idp_group": "member",
                        "roles": ["member", "load-balancer_member", "heat_stack_user"],
                    },
                ],
            ),
        ]

        shared_ctx.current_mapping_rules = []
        shared_ctx.mapping_exists = False

        action = ensure_federation_mapping(projects, shared_ctx)

        assert action.status == ActionStatus.CREATED
        call_kwargs = shared_ctx.conn.identity.create_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify single group can grant multiple roles
        assert len(rules) == 1
        roles = rules[0]["local"][1]["projects"][0]["roles"]
        assert roles == [
            {"name": "member"},
            {"name": "load-balancer_member"},
            {"name": "heat_stack_user"},
        ]
        assert rules[0]["remote"][3]["any_one_of"] == ["/services/openstack/test_project/member"]

    def test_multiple_groups_per_assignment(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [
                    {
                        "idp_group": ["/acme-it-staff", "/acme-dev-staff"],
                        "roles": ["member", "load-balancer_member"],
                    },
                ],
            ),
        ]
        shared_ctx.current_mapping_rules = []
        shared_ctx.mapping_exists = False

        action = ensure_federation_mapping(projects, shared_ctx)

        assert action.status == ActionStatus.CREATED
        call_kwargs = shared_ctx.conn.identity.create_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify multiple groups produce ONE rule with sorted any_one_of list
        assert len(rules) == 1
        assert rules[0]["remote"][3]["any_one_of"] == [
            "/acme-dev-staff",
            "/acme-it-staff",
        ]
        assert rules[0]["local"][1]["projects"][0]["roles"] == [
            {"name": "member"},
            {"name": "load-balancer_member"},
        ]

    def test_mixed_absolute_relative_group_list(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [
                    {
                        "idp_group": ["/org-wide-admins", "project-admins"],
                        "roles": ["admin"],
                    },
                ],
            ),
        ]
        shared_ctx.current_mapping_rules = []
        shared_ctx.mapping_exists = False

        action = ensure_federation_mapping(projects, shared_ctx)

        assert action.status == ActionStatus.CREATED
        call_kwargs = shared_ctx.conn.identity.create_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify mixed absolute/relative paths: absolute stays, relative expands
        assert len(rules) == 1
        assert rules[0]["remote"][3]["any_one_of"] == [
            "/org-wide-admins",
            "/services/openstack/test_project/project-admins",
        ]
        assert rules[0]["local"][1]["projects"][0]["roles"] == [{"name": "admin"}]

    def test_single_item_list_for_idp_group(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [
                    {
                        "idp_group": ["member"],
                        "roles": ["member"],
                    },
                ],
            ),
        ]

        shared_ctx.current_mapping_rules = []
        shared_ctx.mapping_exists = False

        action = ensure_federation_mapping(projects, shared_ctx)

        assert action.status == ActionStatus.CREATED
        call_kwargs = shared_ctx.conn.identity.create_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify single-item list resolves correctly (same as string)
        assert len(rules) == 1
        assert rules[0]["remote"][3]["any_one_of"] == ["/services/openstack/test_project/member"]

    def test_group_prefix_without_trailing_slash(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects = [
            _make_project_cfg(
                "test_project",
                [{"idp_group": "member", "roles": ["member"]}],
                group_prefix="/services/openstack",  # No trailing slash
            ),
        ]

        shared_ctx.current_mapping_rules = []
        shared_ctx.mapping_exists = False

        action = ensure_federation_mapping(projects, shared_ctx)

        assert action.status == ActionStatus.CREATED
        call_kwargs = shared_ctx.conn.identity.create_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify concatenation without trailing slash (no double slash)
        assert rules[0]["remote"][3]["any_one_of"] == ["/services/openstacktest_project/member"]


class TestProjectStateFiltering:
    """Only projects with state=present generate federation rules."""

    def test_locked_project_excluded(self) -> None:
        cfg = _make_project_cfg(
            "locked_proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        projects = [dataclasses.replace(cfg, state="locked")]

        rules = _build_generated_rules(projects)

        # Verify locked project produces no rules
        assert rules == []

    def test_absent_project_excluded(self) -> None:
        cfg = _make_project_cfg(
            "gone_proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        projects = [dataclasses.replace(cfg, state="absent")]

        rules = _build_generated_rules(projects)

        # Verify absent project produces no rules
        assert rules == []

    def test_mixed_states_only_present_generates_rules(self) -> None:
        active = _make_project_cfg(
            "active_proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        locked = _make_project_cfg(
            "locked_proj",
            [{"idp_group": "admin", "roles": ["admin"]}],
        )
        absent = _make_project_cfg(
            "absent_proj",
            [{"idp_group": "reader", "roles": ["reader"]}],
        )
        projects = [
            dataclasses.replace(active, state="present"),
            dataclasses.replace(locked, state="locked"),
            dataclasses.replace(absent, state="absent"),
        ]

        rules = _build_generated_rules(projects)

        # Verify ONLY the present project generated a rule
        assert len(rules) == 1
        assert rules[0]["local"][1]["projects"][0]["name"] == "active_proj"
        assert rules[0]["remote"][3]["any_one_of"] == ["/services/openstack/active_proj/member"]
        # Verify locked and absent projects did NOT generate rules
        project_names = [r["local"][1]["projects"][0]["name"] for r in rules]
        assert "locked_proj" not in project_names
        assert "absent_proj" not in project_names

    def test_all_projects_locked_or_absent(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        locked = _make_project_cfg(
            "locked_proj",
            [{"idp_group": "admin", "roles": ["admin"]}],
        )
        absent = _make_project_cfg(
            "absent_proj",
            [{"idp_group": "member", "roles": ["member"]}],
        )
        projects = [
            dataclasses.replace(locked, state="locked"),
            dataclasses.replace(absent, state="absent"),
        ]

        static_rule = {
            "local": [{"user": {"name": "admin"}}],
            "remote": [{"type": "STATIC"}],
        }
        shared_ctx.static_mapping_rules = [static_rule]
        shared_ctx.current_mapping_rules = [{"old": "rule"}]
        shared_ctx.mapping_exists = True

        action = ensure_federation_mapping(projects, shared_ctx)

        assert action.status == ActionStatus.UPDATED
        call_kwargs = shared_ctx.conn.identity.update_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify only static rule present, no generated rules from locked/absent projects
        assert len(rules) == 1
        assert rules[0] == static_rule


class TestEdgeCases:
    """Edge cases: empty configs, no federation, empty mapping."""

    def test_empty_role_assignments(self) -> None:
        cfg = _make_project_cfg("test_project", role_assignments=[])
        projects = [cfg]

        rules = _build_generated_rules(projects)

        # Verify empty role_assignments produces no rules
        assert rules == []

    def test_project_without_federation(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        # Build project without federation
        cfg = ProjectConfig.from_dict(
            {
                "name": "no_federation",
                "resource_prefix": "no_federation",
            }
        )
        projects = [cfg]

        rules = _build_generated_rules(projects)

        # Verify project without federation config produces no rules
        assert rules == []

    def test_no_projects_and_no_static_rules(
        self,
        shared_ctx: SharedContext,
    ) -> None:
        projects: list[ProjectConfig] = []

        shared_ctx.static_mapping_rules = []
        shared_ctx.current_mapping_rules = [{"old": "rule"}]
        shared_ctx.mapping_exists = True

        action = ensure_federation_mapping(projects, shared_ctx)

        assert action.status == ActionStatus.UPDATED
        assert "rules=0" in action.details
        call_kwargs = shared_ctx.conn.identity.update_mapping.call_args[1]
        rules = call_kwargs["rules"]

        # Verify empty mapping is pushed (clearing old rules)
        assert rules == []
