"""Tests for federation mapping provisioning — ensure_federation_mapping."""

from __future__ import annotations

import dataclasses

from src.models import ProjectConfig
from src.resources.federation import (
    _build_generated_rules,
    augment_group_role_assignments,
    ensure_federation_mapping,
)
from src.utils import ActionStatus, SharedContext


def _make_project_cfg(
    name: str,
    role_assignments: list[dict],
    issuer: str = "https://myidp.corp/realms/myrealm",
    mapping_id: str = "my-mapping",
    group_prefix: str = "/services/openstack/",
    domain: str | None = None,
    domain_id: str = "default",
    user_type: str = "",
    mapping_mode: str = "project",
    group_name_separator: str = " ",
    group_role_assignments: list[dict] | None = None,
) -> ProjectConfig:
    """Build a minimal project config with federation settings."""
    fed_dict: dict = {
        "issuer": issuer,
        "mapping_id": mapping_id,
        "group_prefix": group_prefix,
        "role_assignments": role_assignments,
        "mapping_mode": mapping_mode,
        "group_name_separator": group_name_separator,
    }
    if user_type:
        fed_dict["user_type"] = user_type
    project_dict: dict = {
        "name": name,
        "resource_prefix": name,
        "domain_id": domain_id,
        "federation": fed_dict,
    }
    if domain is not None:
        project_dict["domain"] = domain
    if group_role_assignments is not None:
        project_dict["group_role_assignments"] = group_role_assignments
    return ProjectConfig.from_dict(project_dict)


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


class TestDomainAwareFederationRules:
    """Domain and user_type inclusion in generated federation mapping rules."""

    def test_domain_adds_domain_element(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                domain="MyDomain",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        projects_element = rules[0]["local"][1]
        assert projects_element["domain"] == {"name": "MyDomain"}
        assert projects_element["projects"][0]["name"] == "proj"

    def test_user_type_adds_type_to_user(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                user_type="ephemeral",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        user_element = rules[0]["local"][0]["user"]
        assert user_element["type"] == "ephemeral"
        assert user_element["name"] == "{0}"
        assert user_element["email"] == "{1}"

    def test_domain_and_user_type_together(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                domain="MyDomain",
                user_type="ephemeral",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        assert rules[0]["local"][0]["user"]["type"] == "ephemeral"
        assert rules[0]["local"][1]["domain"] == {"name": "MyDomain"}

    def test_no_domain_no_user_type(self) -> None:
        """Backward compatibility: neither set → rule unchanged."""
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        # No "type" key on user element
        assert "type" not in rules[0]["local"][0]["user"]
        # No "domain" key on projects element
        assert "domain" not in rules[0]["local"][1]

    def test_domain_without_user_type(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                domain="X",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        assert rules[0]["local"][1]["domain"] == {"name": "X"}
        assert "type" not in rules[0]["local"][0]["user"]

    def test_user_type_without_domain(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                user_type="ephemeral",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        assert rules[0]["local"][0]["user"]["type"] == "ephemeral"
        assert "domain" not in rules[0]["local"][1]

    def test_custom_user_type_value(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                user_type="local",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        assert rules[0]["local"][0]["user"]["type"] == "local"

    def test_domain_with_multiple_assignments(self) -> None:
        """All rules for a domain project include the domain element."""
        projects = [
            _make_project_cfg(
                "proj",
                [
                    {"idp_group": "member", "roles": ["member"]},
                    {"idp_group": "reader", "roles": ["reader"]},
                ],
                domain="MyDomain",
                user_type="ephemeral",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 2
        for rule in rules:
            assert rule["local"][1]["domain"] == {"name": "MyDomain"}
            assert rule["local"][0]["user"]["type"] == "ephemeral"

    def test_mixed_projects(self) -> None:
        """One project with domain+user_type, one without → only configured project gets elements."""
        projects = [
            _make_project_cfg(
                "domain_proj",
                [{"idp_group": "member", "roles": ["member"]}],
                domain="MyDomain",
                user_type="ephemeral",
            ),
            _make_project_cfg(
                "plain_proj",
                [{"idp_group": "member", "roles": ["member"]}],
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 2
        # Rules are sorted by project name: domain_proj first, plain_proj second
        domain_rule = rules[0]
        plain_rule = rules[1]

        assert domain_rule["local"][1]["projects"][0]["name"] == "domain_proj"
        assert domain_rule["local"][1]["domain"] == {"name": "MyDomain"}
        assert domain_rule["local"][0]["user"]["type"] == "ephemeral"

        assert plain_rule["local"][1]["projects"][0]["name"] == "plain_proj"
        assert "domain" not in plain_rule["local"][1]
        assert "type" not in plain_rule["local"][0]["user"]


class TestGroupModeRules:
    """Group-mode rule generation produces group elements instead of projects."""

    def test_group_mode_generates_group_element(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="group",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        # Group element instead of projects element
        assert "group" in rules[0]["local"][1]
        assert "projects" not in rules[0]["local"][1]
        assert rules[0]["local"][1]["group"]["name"] == "proj member"
        assert rules[0]["local"][1]["group"]["domain"] == {"name": "Default"}

    def test_group_mode_auto_derived_name(self) -> None:
        projects = [
            _make_project_cfg(
                "my project",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="group",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        assert rules[0]["local"][1]["group"]["name"] == "my project member"

    def test_group_mode_space_separator(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "reader", "roles": ["reader"]}],
                mapping_mode="group",
                group_name_separator=" ",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert rules[0]["local"][1]["group"]["name"] == "proj reader"

    def test_group_mode_custom_separator(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="group",
                group_name_separator="-",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert rules[0]["local"][1]["group"]["name"] == "proj-member"

    def test_group_mode_explicit_keystone_group(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"], "keystone_group": "custom-group"}],
                mapping_mode="group",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert rules[0]["local"][1]["group"]["name"] == "custom-group"

    def test_group_mode_absolute_idp_path_stripped(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "/services/openstack/org/member", "roles": ["member"]}],
                mapping_mode="group",
            ),
        ]
        rules = _build_generated_rules(projects)

        # Absolute path stripped to last segment for group name
        assert rules[0]["local"][1]["group"]["name"] == "proj member"

    def test_group_mode_list_idp_group(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": ["alpha", "beta"], "roles": ["member"]}],
                mapping_mode="group",
            ),
        ]
        rules = _build_generated_rules(projects)

        # Uses first entry for group name derivation
        assert rules[0]["local"][1]["group"]["name"] == "proj alpha"

    def test_group_mode_user_type_propagated(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="group",
                user_type="ephemeral",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert rules[0]["local"][0]["user"]["type"] == "ephemeral"

    def test_group_mode_with_domain(self) -> None:
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="group",
                domain="MyDomain",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert rules[0]["local"][1]["group"]["domain"] == {"name": "MyDomain"}

    def test_group_mode_no_domain_defaults_to_default(self) -> None:
        """When cfg.domain is None, group-mode rules use 'Default' as domain name."""
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="group",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert rules[0]["local"][1]["group"]["domain"] == {"name": "Default"}

    def test_project_mode_unchanged(self) -> None:
        """Backward compat: project mode still produces projects element."""
        projects = [
            _make_project_cfg(
                "proj",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="project",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 1
        assert "projects" in rules[0]["local"][1]
        assert "group" not in rules[0]["local"][1]

    def test_mixed_modes_coexist(self) -> None:
        """One project group mode, one project mode, in same mapping."""
        projects = [
            _make_project_cfg(
                "group_proj",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="group",
            ),
            _make_project_cfg(
                "project_proj",
                [{"idp_group": "member", "roles": ["member"]}],
                mapping_mode="project",
            ),
        ]
        rules = _build_generated_rules(projects)

        assert len(rules) == 2
        # Sorted by project name: group_proj first, project_proj second
        assert "group" in rules[0]["local"][1]
        assert "projects" in rules[1]["local"][1]


class TestAugmentGroupRoleAssignments:
    """augment_group_role_assignments derives group_role_assignments from federation."""

    def test_group_mode_adds_derived_assignments(self) -> None:
        cfg = _make_project_cfg(
            "proj",
            [
                {"idp_group": "member", "roles": ["member", "load-balancer_member"]},
                {"idp_group": "reader", "roles": ["reader"]},
            ],
            mapping_mode="group",
        )
        result = augment_group_role_assignments(cfg)

        # Two derived assignments appended
        assert len(result.group_role_assignments) == 2
        assert result.group_role_assignments[0].group == "proj member"
        assert result.group_role_assignments[0].roles == ["member", "load-balancer_member"]
        assert result.group_role_assignments[1].group == "proj reader"
        assert result.group_role_assignments[1].roles == ["reader"]

    def test_project_mode_no_change(self) -> None:
        cfg = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
            mapping_mode="project",
        )
        result = augment_group_role_assignments(cfg)

        assert result is cfg  # Same object, no change

    def test_preserves_existing_assignments(self) -> None:
        cfg = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
            mapping_mode="group",
            group_role_assignments=[{"group": "manual-group", "roles": ["admin"]}],
        )
        result = augment_group_role_assignments(cfg)

        # Manual assignment preserved + derived appended
        assert len(result.group_role_assignments) == 2
        assert result.group_role_assignments[0].group == "manual-group"
        assert result.group_role_assignments[0].roles == ["admin"]
        assert result.group_role_assignments[1].group == "proj member"
        assert result.group_role_assignments[1].roles == ["member"]

    def test_no_federation_no_change(self) -> None:
        cfg = ProjectConfig.from_dict({"name": "nofed", "resource_prefix": "nofed"})
        result = augment_group_role_assignments(cfg)

        assert result is cfg

    def test_teardown_revokes_derived_assignments(self) -> None:
        """Verify that derived assignments can be flipped to absent for revocation."""
        cfg = _make_project_cfg(
            "proj",
            [{"idp_group": "member", "roles": ["member"]}],
            mapping_mode="group",
        )
        effective = augment_group_role_assignments(cfg)

        # Simulate teardown: flip all assignments to absent
        revoked = dataclasses.replace(
            effective,
            group_role_assignments=[
                dataclasses.replace(entry, state="absent") for entry in effective.group_role_assignments
            ],
        )
        assert len(revoked.group_role_assignments) == 1
        assert revoked.group_role_assignments[0].state == "absent"
        assert revoked.group_role_assignments[0].group == "proj member"
