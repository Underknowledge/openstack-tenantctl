"""Tests for config loading, merging, validation, and placeholder replacement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path
import yaml

from src.config_loader import (
    ConfigSource,
    ConfigValidationError,
    RawProject,
    YamlConfigSource,
    build_projects,
    load_all_projects,
)
from src.models import FipEntry, RouterIpEntry
from src.state_store import YamlFileStateStore


def _minimum_valid_project(
    *,
    name: str = "validproject",
    resource_prefix: str = "validproject",
    cidr: str = "192.168.1.0/24",
    gateway_ip: str | None = None,
    allocation_pools: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return the minimum valid project config dict.

    Override individual keys by passing keyword arguments.

    By default, gateway_ip and allocation_pools are auto-calculated from CIDR
    during validation (matching production behavior). Pass explicit values to
    override auto-calculation.
    """
    subnet_config: dict[str, Any] = {"cidr": cidr}

    # Only include gateway_ip if explicitly provided
    if gateway_ip is not None:
        subnet_config["gateway_ip"] = gateway_ip

    # Only include allocation_pools if explicitly provided
    if allocation_pools is not None:
        subnet_config["allocation_pools"] = allocation_pools

    return {
        "name": name,
        "resource_prefix": resource_prefix,
        "network": {
            "subnet": subnet_config,
        },
    }


def _write_config(
    tmp_path: Path,
    *,
    defaults: dict[str, Any] | None = None,
    projects: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Write defaults.yaml and project files under *tmp_path*, return the config dir path.

    Parameters
    ----------
    defaults:
        Contents written to ``defaults.yaml``.  Omit to skip.
    projects:
        Mapping of ``{filename: project_dict}``.  Each entry is written to
        ``projects/<filename>.yaml``.
    """
    if defaults is not None:
        defaults_file = tmp_path / "defaults.yaml"
        defaults_file.write_text(yaml.dump(defaults, default_flow_style=False), encoding="utf-8")

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(exist_ok=True)

    if projects is not None:
        for filename, content in projects.items():
            proj_file = projects_dir / f"{filename}.yaml"
            proj_file.write_text(yaml.dump(content, default_flow_style=False), encoding="utf-8")

    return str(tmp_path)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestLoadValidProject:
    """Verify that a valid project loads, merges defaults, and is returned."""

    def test_load_valid_project(self, tmp_path: Path) -> None:
        defaults = {
            "description": "Default description",
            "quotas": {"compute": {"cores": 10}},
        }
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _loaded_defaults = load_all_projects(config_dir)

        assert len(merged_projects) == 1
        proj = merged_projects[0]
        # Project values are present
        assert proj.name == "validproject"
        assert proj.network.subnet.cidr == "192.168.1.0/24"
        # Defaults are merged in
        assert proj.description == "Default description"
        assert proj.quotas.compute["cores"] == 10


class TestDeepMerge:
    """Verify that project-level values override defaults during deep merge."""

    def test_deep_merge_project_overrides_defaults(self, tmp_path: Path) -> None:
        """Verify deep-merge: project values override, but non-overridden defaults survive."""
        defaults = {"quotas": {"compute": {"cores": 10, "ram": 2048}}}
        project = _minimum_valid_project()
        project["quotas"] = {"compute": {"cores": 40}}
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        proj = merged_projects[0]
        # Verify project override won
        assert proj.quotas.compute["cores"] == 40
        # Verify non-overridden default survived the merge (proves deep-merge worked)
        assert proj.quotas.compute["ram"] == 2048
        # Verify both keys are present in the same dict (structure verification)
        assert "cores" in proj.quotas.compute
        assert "ram" in proj.quotas.compute
        assert len(proj.quotas.compute) == 2


class TestPlaceholderReplacement:
    """Verify that ``{name}`` placeholders are substituted with the project name."""

    def test_placeholder_replacement(self, tmp_path: Path) -> None:
        project = _minimum_valid_project(name="myproject")
        project["description"] = "Project {name}"
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].description == "Project myproject"


# ---------------------------------------------------------------------------
# Validation-error tests (all should trigger SystemExit)
# ---------------------------------------------------------------------------


class TestMissingRequiredField:
    """Missing required field causes sys.exit(1)."""

    def test_missing_required_field_exits(self, tmp_path: Path) -> None:
        project: dict[str, Any] = {
            "name": "nocidproject",
            "network": {
                "subnet": {
                    "gateway_ip": "192.168.1.254",
                    "allocation_pools": [
                        {"start": "192.168.1.1", "end": "192.168.1.253"},
                    ],
                },
            },
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


class TestMissingNameShowsFilename:
    """When 'name' is missing, the error should show the source filename."""

    def test_missing_name_error_contains_filename(self, tmp_path: Path) -> None:
        project: dict[str, Any] = {
            "network": {"subnet": {"cidr": "192.168.1.0/24"}},
        }
        config_dir = _write_config(tmp_path, projects={"dev-team": project})

        with pytest.raises(ConfigValidationError) as exc_info:
            load_all_projects(config_dir)

        error_text = " ".join(exc_info.value.errors)
        assert "dev-team.yaml" in error_text
        assert "<unknown>" not in error_text


class TestInvalidProjectName:
    """A project name that does not match the identifier regex causes exit."""

    def test_invalid_project_name_exits(self, tmp_path: Path) -> None:
        project = _minimum_valid_project(name="123invalid")
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


class TestValidProjectNameWithSpaces:
    """A project name with spaces should be accepted."""

    def test_project_name_with_spaces_succeeds(self, tmp_path: Path) -> None:
        project = _minimum_valid_project(name="My Project Name")
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 1
        assert merged_projects[0].name == "My Project Name"


class TestInvalidCidr:
    """A malformed CIDR string causes exit."""

    def test_invalid_cidr_exits(self, tmp_path: Path) -> None:
        project = _minimum_valid_project(cidr="not-a-cidr")
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


class TestGatewayOutsideCidr:
    """A gateway IP outside the CIDR range causes exit."""

    def test_gateway_outside_cidr_exits(self, tmp_path: Path) -> None:
        project = _minimum_valid_project(
            cidr="192.168.1.0/24",
            gateway_ip="10.0.0.1",
        )
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


class TestCidrOverlap:
    """Two projects with overlapping CIDRs cause exit."""

    def test_cidr_overlap_exits(self, tmp_path: Path) -> None:
        """Verify CIDR overlap detection includes both project names in error."""
        project_a = _minimum_valid_project(name="projecta", resource_prefix="projecta", cidr="192.168.1.0/24")
        # Use non-overlapping CIDR for projectb but one that overlaps with projecta
        project_b = _minimum_valid_project(
            name="projectb",
            resource_prefix="projectb",
            cidr="192.168.1.128/25",
        )
        config_dir = _write_config(
            tmp_path,
            defaults={"enforce_unique_cidrs": True},
            projects={"a_proj": project_a, "b_proj": project_b},
        )

        with pytest.raises(ConfigValidationError) as exc_info:
            load_all_projects(config_dir)

        # Verify error message contains both project names
        errors = exc_info.value.errors
        error_text = " ".join(errors)
        assert "projecta" in error_text
        assert "projectb" in error_text
        assert "overlap" in error_text.lower()

    def test_cidr_overlap_allowed_by_default(self, tmp_path: Path) -> None:
        """Overlapping CIDRs load successfully when enforce_unique_cidrs is off."""
        project_a = _minimum_valid_project(name="projecta", resource_prefix="projecta", cidr="192.168.1.0/24")
        project_b = _minimum_valid_project(name="projectb", resource_prefix="projectb", cidr="192.168.1.0/24")
        config_dir = _write_config(
            tmp_path,
            projects={"a_proj": project_a, "b_proj": project_b},
        )

        # Should NOT raise — overlapping CIDRs are allowed by default
        merged_projects, _defaults = load_all_projects(config_dir)

        assert len(merged_projects) == 2


class TestInvalidQuotaValue:
    """A negative quota value causes exit."""

    def test_invalid_quota_value_exits(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["quotas"] = {"compute": {"cores": -2}}
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


class TestAllocationPoolStartGtEnd:
    """An allocation pool where start > end causes exit."""

    def test_allocation_pool_start_gt_end_exits(self, tmp_path: Path) -> None:
        project = _minimum_valid_project(
            allocation_pools=[{"start": "192.168.1.200", "end": "192.168.1.100"}],
        )
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


class TestReclaimFloatingIpsValidation:
    """Validate the reclaim_floating_ips boolean field."""

    def test_valid_boolean_true(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["reclaim_floating_ips"] = True
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].reclaim_floating_ips is True

    def test_valid_boolean_false(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["reclaim_floating_ips"] = False
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].reclaim_floating_ips is False

    @pytest.mark.parametrize("invalid_value", ["yes", 1])
    def test_non_boolean_exits(self, tmp_path: Path, invalid_value: Any) -> None:
        """Non-boolean values (string or int) cause validation error."""
        project = _minimum_valid_project()
        project["reclaim_floating_ips"] = invalid_value
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_omitted_is_valid(self, tmp_path: Path) -> None:
        """When omitted, no validation error (inherits from defaults)."""
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 1


class TestReclaimRouterIpsValidation:
    """Validate the reclaim_router_ips boolean field."""

    def test_valid_boolean_true(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["reclaim_router_ips"] = True
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].reclaim_router_ips is True

    def test_valid_boolean_false(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["reclaim_router_ips"] = False
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].reclaim_router_ips is False

    @pytest.mark.parametrize("invalid_value", ["yes", 1])
    def test_non_boolean_exits(self, tmp_path: Path, invalid_value: Any) -> None:
        """Non-boolean values (string or int) cause validation error."""
        project = _minimum_valid_project()
        project["reclaim_router_ips"] = invalid_value
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


class TestTrackFipChangesValidation:
    """Validate the track_fip_changes boolean field."""

    def test_valid_boolean_true(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["track_fip_changes"] = True
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].track_fip_changes is True

    def test_valid_boolean_false(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["track_fip_changes"] = False
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].track_fip_changes is False

    @pytest.mark.parametrize("invalid_value", ["yes", 1])
    def test_non_boolean_exits(self, tmp_path: Path, invalid_value: Any) -> None:
        """Non-boolean values (string or int) cause validation error."""
        project = _minimum_valid_project()
        project["track_fip_changes"] = invalid_value
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_omitted_is_valid(self, tmp_path: Path) -> None:
        """When omitted, no validation error (inherits from defaults)."""
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 1


class TestDomainConfiguration:
    """Domain configuration and validation tests."""

    def test_domain_id_from_project_config(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["domain_id"] = "my-domain-uuid"
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "my-domain-uuid"

    def test_domain_from_project_config(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["domain"] = "my-friendly-name"
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        # Config loader converts domain -> domain_id
        assert merged_projects[0].domain_id == "my-friendly-name"

    def test_domain_id_from_defaults(self, tmp_path: Path) -> None:
        defaults = {"domain_id": "default-domain"}
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "default-domain"

    def test_domain_from_defaults(self, tmp_path: Path) -> None:
        defaults = {"domain": "default-friendly"}
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "default-friendly"

    def test_domain_id_overrides_domain(self, tmp_path: Path) -> None:
        """domain_id takes precedence over domain."""
        project = _minimum_valid_project()
        project["domain_id"] = "uuid-domain"
        project["domain"] = "friendly-domain"
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        # domain_id should win
        assert merged_projects[0].domain_id == "uuid-domain"

    def test_domain_project_overrides_defaults(self, tmp_path: Path) -> None:
        defaults = {"domain_id": "default-domain"}
        project = _minimum_valid_project()
        project["domain_id"] = "project-domain"
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "project-domain"

    def test_domain_env_var_fallback(self, tmp_path: Path, monkeypatch) -> None:
        """When no config domain, fall back to OS_PROJECT_DOMAIN_ID env var."""
        monkeypatch.setenv("OS_PROJECT_DOMAIN_ID", "env-domain-uuid")
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "env-domain-uuid"

    def test_domain_env_var_user_domain_fallback(self, tmp_path: Path, monkeypatch) -> None:
        """When no config domain or OS_PROJECT_DOMAIN_ID, use OS_USER_DOMAIN_NAME."""
        monkeypatch.delenv("OS_PROJECT_DOMAIN_ID", raising=False)
        monkeypatch.setenv("OS_USER_DOMAIN_NAME", "user-domain")
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "user-domain"

    def test_domain_precedence_project_over_env(self, tmp_path: Path, monkeypatch) -> None:
        """Project config takes precedence over env vars."""
        monkeypatch.setenv("OS_PROJECT_DOMAIN_ID", "env-domain")
        project = _minimum_valid_project()
        project["domain_id"] = "project-domain"
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "project-domain"

    def test_domain_defaults_to_default(self, tmp_path: Path, monkeypatch) -> None:
        """When no config or env vars, default to 'default'."""
        monkeypatch.delenv("OS_PROJECT_DOMAIN_ID", raising=False)
        monkeypatch.delenv("OS_USER_DOMAIN_NAME", raising=False)
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "default"

    def test_domain_id_null_triggers_auto_discovery(self, tmp_path: Path, monkeypatch) -> None:
        """domain_id: null in defaults triggers auto-discovery, resolves to 'default'."""
        monkeypatch.delenv("OS_PROJECT_DOMAIN_ID", raising=False)
        monkeypatch.delenv("OS_USER_DOMAIN_NAME", raising=False)
        defaults = {"domain_id": None}
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "default"

    def test_domain_id_null_with_env_var(self, tmp_path: Path, monkeypatch) -> None:
        """domain_id: null with OS_PROJECT_DOMAIN_ID env var uses the env var."""
        monkeypatch.setenv("OS_PROJECT_DOMAIN_ID", "my-env-domain")
        defaults = {"domain_id": None}
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "my-env-domain"

    def test_domain_id_null_project_overrides_default(self, tmp_path: Path, monkeypatch) -> None:
        """Project domain_id: null overrides a concrete default, triggering auto-discovery."""
        monkeypatch.delenv("OS_PROJECT_DOMAIN_ID", raising=False)
        monkeypatch.delenv("OS_USER_DOMAIN_NAME", raising=False)
        defaults = {"domain_id": "shared-domain"}
        project = _minimum_valid_project()
        project["domain_id"] = None
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "default"

    def test_domain_id_null_raw_yaml_triggers_auto_discovery(self, tmp_path: Path, monkeypatch) -> None:
        """Raw YAML 'domain_id: null' is parsed as None and triggers auto-discovery."""
        monkeypatch.delenv("OS_PROJECT_DOMAIN_ID", raising=False)
        monkeypatch.delenv("OS_USER_DOMAIN_NAME", raising=False)
        defaults_file = tmp_path / "defaults.yaml"
        defaults_file.write_text("domain_id: null\n", encoding="utf-8")
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "test.yaml").write_text(yaml.dump(_minimum_valid_project()), encoding="utf-8")

        merged_projects, _ = load_all_projects(str(tmp_path))

        assert merged_projects[0].domain_id == "default"

    def test_domain_id_null_in_defaults_with_concrete_project_domain(self, tmp_path: Path) -> None:
        """Defaults domain_id: null does not interfere when project sets a concrete domain."""
        defaults = {"domain_id": None}
        project = _minimum_valid_project()
        project["domain_id"] = "project-concrete-domain"
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].domain_id == "project-concrete-domain"

    def test_invalid_domain_id_empty_string_exits(self, tmp_path: Path) -> None:
        """An empty string domain_id causes exit."""
        project = _minimum_valid_project()
        project["domain_id"] = ""
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_invalid_domain_empty_string_exits(self, tmp_path: Path) -> None:
        """An empty string domain causes exit."""
        project = _minimum_valid_project()
        project["domain"] = ""
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_invalid_domain_id_non_string_exits(self, tmp_path: Path) -> None:
        """A non-string domain_id causes exit."""
        project = _minimum_valid_project()
        project["domain_id"] = 123
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


# ---------------------------------------------------------------------------
# Security group rule preset tests
# ---------------------------------------------------------------------------


class TestSecurityGroupPresetExpansion:
    """Verify that preset names in security_group.rules expand to full dicts."""

    def test_string_preset_expands(self, tmp_path: Path) -> None:
        """A string preset name is expanded to the full rule dict."""
        project = _minimum_valid_project()
        project["security_group"] = {"name": "default", "rules": ["SSH"]}
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert len(rules) == 1
        assert rules[0].direction == "ingress"
        assert rules[0].protocol == "tcp"
        assert rules[0].port_range_min == 22
        assert rules[0].port_range_max == 22
        assert rules[0].remote_ip_prefix == "0.0.0.0/0"
        assert rules[0].description == "Allow SSH"

    def test_multiple_presets_expand(self, tmp_path: Path) -> None:
        """Multiple string presets all expand correctly."""
        project = _minimum_valid_project()
        project["security_group"] = {
            "name": "default",
            "rules": ["ICMP", "SSH", "HTTP", "HTTPS"],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert len(rules) == 4
        assert rules[0].protocol == "icmp"
        assert rules[1].port_range_min == 22
        assert rules[2].port_range_min == 80
        assert rules[3].port_range_min == 443

    def test_preset_with_override(self, tmp_path: Path) -> None:
        """A dict with ``rule`` key uses preset as base with overrides."""
        project = _minimum_valid_project()
        project["security_group"] = {
            "name": "default",
            "rules": [
                {"rule": "SSH", "remote_ip_prefix": "10.0.0.0/8"},
            ],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert len(rules) == 1
        assert rules[0].port_range_min == 22
        assert rules[0].remote_ip_prefix == "10.0.0.0/8"

    def test_backward_compatible_dict_rule(self, tmp_path: Path) -> None:
        """A dict without ``rule`` key is left as-is (backward compatible)."""
        custom_rule = {
            "direction": "ingress",
            "protocol": "tcp",
            "port_range_min": 8080,
            "port_range_max": 8080,
            "remote_ip_prefix": "0.0.0.0/0",
            "description": "Custom port",
        }
        project = _minimum_valid_project()
        project["security_group"] = {"name": "default", "rules": [custom_rule]}
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert len(rules) == 1
        assert rules[0].port_range_min == 8080

    def test_mixed_rule_styles(self, tmp_path: Path) -> None:
        """All three rule styles can be mixed in one list."""
        project = _minimum_valid_project()
        project["security_group"] = {
            "name": "default",
            "rules": [
                "ICMP",
                {"rule": "SSH", "remote_ip_prefix": "10.0.0.0/8"},
                {
                    "direction": "ingress",
                    "protocol": "tcp",
                    "port_range_min": 8443,
                    "port_range_max": 8443,
                    "remote_ip_prefix": "0.0.0.0/0",
                    "description": "Custom HTTPS alt",
                },
            ],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert len(rules) == 3
        assert rules[0].protocol == "icmp"
        assert rules[1].port_range_min == 22
        assert rules[1].remote_ip_prefix == "10.0.0.0/8"
        assert rules[2].port_range_min == 8443

    def test_unknown_string_preset_exits(self, tmp_path: Path) -> None:
        """An unknown string preset name causes exit."""
        project = _minimum_valid_project()
        project["security_group"] = {"name": "default", "rules": ["TELNET"]}
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_unknown_rule_key_preset_exits(self, tmp_path: Path) -> None:
        """An unknown preset in a dict with ``rule`` key causes exit."""
        project = _minimum_valid_project()
        project["security_group"] = {
            "name": "default",
            "rules": [{"rule": "TELNET", "remote_ip_prefix": "10.0.0.0/8"}],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_horizon_style_preset_aliases(self, tmp_path: Path) -> None:
        """Horizon-style names like 'All ICMP' expand correctly."""
        project = _minimum_valid_project()
        project["security_group"] = {
            "name": "default",
            "rules": ["All ICMP", "All TCP", "All UDP", "DNS", "RDP"],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert len(rules) == 5
        assert rules[0].protocol == "icmp"
        assert rules[1].protocol == "tcp"
        assert rules[1].port_range_min == 1
        assert rules[1].port_range_max == 65535
        assert rules[2].protocol == "udp"
        assert rules[3].port_range_min == 53
        assert rules[4].port_range_min == 3389

    def test_preset_expansion_is_independent_copy(self, tmp_path: Path) -> None:
        """Each expanded preset is an independent copy (mutations don't leak)."""
        project = _minimum_valid_project()
        project["security_group"] = {"name": "default", "rules": ["SSH", "SSH"]}
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert len(rules) == 2
        # Both rules are equal but distinct objects (frozen dataclasses are independent copies)
        assert rules[0] == rules[1]
        assert rules[0] is not rules[1]
        assert rules[0].description == "Allow SSH"
        assert rules[1].description == "Allow SSH"


# ---------------------------------------------------------------------------
# Group role assignments validation tests
# ---------------------------------------------------------------------------


class TestGroupRoleAssignmentsValidation:
    """Validate group_role_assignments config entries."""

    def test_valid_config_passes(self, tmp_path: Path) -> None:
        """Valid group_role_assignments with explicit state passes."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = [
            {"group": "admins", "roles": ["admin"], "state": "present"},
        ]
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 1
        assert merged_projects[0].group_role_assignments[0].group == "admins"

    def test_valid_config_without_state(self, tmp_path: Path) -> None:
        """Valid group_role_assignments without state field passes."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = [
            {"group": "ops", "roles": ["member", "reader"]},
        ]
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 1

    def test_empty_list_passes(self, tmp_path: Path) -> None:
        """An empty list opt-out passes validation."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = []
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].group_role_assignments == []

    def test_not_a_list_exits(self, tmp_path: Path) -> None:
        """A non-list group_role_assignments causes exit."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = "not-a-list"
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_entry_not_a_dict_exits(self, tmp_path: Path) -> None:
        """A non-dict entry in the list causes exit."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = ["not-a-dict"]
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_missing_group_exits(self, tmp_path: Path) -> None:
        """Missing group field causes exit."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = [{"roles": ["admin"]}]
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_empty_group_exits(self, tmp_path: Path) -> None:
        """Empty group string causes exit."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = [{"group": "", "roles": ["admin"]}]
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_missing_roles_exits(self, tmp_path: Path) -> None:
        """Missing roles field causes exit."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = [{"group": "admins"}]
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_empty_roles_exits(self, tmp_path: Path) -> None:
        """Empty roles list causes exit."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = [{"group": "admins", "roles": []}]
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_invalid_state_exits(self, tmp_path: Path) -> None:
        """Invalid state value causes exit."""
        project = _minimum_valid_project()
        project["group_role_assignments"] = [
            {"group": "admins", "roles": ["admin"], "state": "invalid"},
        ]
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_placeholder_replacement(self, tmp_path: Path) -> None:
        """{name} placeholder in group name is replaced."""
        project = _minimum_valid_project(name="myproject")
        project["group_role_assignments"] = [
            {"group": "{name}-operators", "roles": ["member"]},
        ]
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        gra = merged_projects[0].group_role_assignments
        assert gra[0].group == "myproject-operators"

    def test_defaults_inherited(self, tmp_path: Path) -> None:
        """group_role_assignments from defaults are inherited."""
        defaults = {
            "group_role_assignments": [
                {"group": "default-admins", "roles": ["admin"]},
            ],
        }
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        gra = merged_projects[0].group_role_assignments
        assert len(gra) == 1
        assert gra[0].group == "default-admins"

    def test_per_project_replaces_defaults(self, tmp_path: Path) -> None:
        """Per-project list replaces defaults entirely (list override)."""
        defaults = {
            "group_role_assignments": [
                {"group": "default-admins", "roles": ["admin"]},
            ],
        }
        project = _minimum_valid_project()
        project["group_role_assignments"] = [
            {"group": "project-ops", "roles": ["member"]},
        ]
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        gra = merged_projects[0].group_role_assignments
        assert len(gra) == 1
        assert gra[0].group == "project-ops"


# ---------------------------------------------------------------------------
# Project state validation tests
# ---------------------------------------------------------------------------


class TestProjectStateValidation:
    """Validate the 'state' field on projects."""

    def test_valid_state_present(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["state"] = "present"
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].state == "present"

    def test_valid_state_locked(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["state"] = "locked"
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].state == "locked"

    def test_valid_state_absent(self, tmp_path: Path) -> None:
        """Absent state passes validation even without CIDR."""
        project: dict[str, Any] = {
            "name": "goneproject",
            "resource_prefix": "goneproject",
            "state": "absent",
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].state == "absent"

    def test_invalid_state_exits(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        project["state"] = "invalid"
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)

    def test_default_state_is_present(self, tmp_path: Path) -> None:
        """When state is not specified, defaults from defaults.yaml apply."""
        defaults = {"state": "present"}
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].state == "present"

    def test_absent_skips_cidr_requirement(self, tmp_path: Path) -> None:
        """Absent state does not require network.subnet.cidr."""
        project: dict[str, Any] = {
            "name": "absentproject",
            "resource_prefix": "absentproject",
            "state": "absent",
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 1
        assert merged_projects[0].name == "absentproject"

    def test_absent_skips_cidr_overlap_check(self, tmp_path: Path) -> None:
        """Absent projects are excluded from CIDR overlap checks."""
        project_a = _minimum_valid_project(name="projecta", resource_prefix="projecta", cidr="192.168.1.0/24")
        project_b: dict[str, Any] = {
            "name": "projectb",
            "resource_prefix": "projectb",
            "state": "absent",
            "network": {"subnet": {"cidr": "192.168.1.0/24"}},
        }
        config_dir = _write_config(
            tmp_path,
            defaults={"enforce_unique_cidrs": True},
            projects={"a_proj": project_a, "b_proj": project_b},
        )

        # Should NOT raise even though CIDRs overlap — absent is excluded
        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 2


class TestAbsentStillValidatesGroupRoleAssignments:
    """Absent projects must still validate group_role_assignments (used during teardown)."""

    def test_absent_with_malformed_gra_exits(self, tmp_path: Path) -> None:
        project: dict[str, Any] = {
            "name": "absentbad",
            "resource_prefix": "absentbad",
            "state": "absent",
            "group_role_assignments": [{"group": "", "roles": ["admin"]}],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        with pytest.raises(ConfigValidationError):
            load_all_projects(config_dir)


# ---------------------------------------------------------------------------
# Config path metadata tests
# ---------------------------------------------------------------------------


class TestConfigPathMetadata:
    """Verify that _config_path is populated for writeback functionality."""

    def test_config_path_is_populated(self, tmp_path: Path) -> None:
        """Verify that _config_path is set for each loaded project."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()

        # Create test project
        project_file = projects_dir / "test.yaml"
        project_file.write_text(yaml.dump(_minimum_valid_project()), encoding="utf-8")

        # Create defaults
        (tmp_path / "defaults.yaml").write_text("{}", encoding="utf-8")

        merged_projects, _ = load_all_projects(str(tmp_path))

        assert len(merged_projects) == 1
        assert merged_projects[0].config_path == str(project_file)


# ---------------------------------------------------------------------------
# State key injection tests
# ---------------------------------------------------------------------------


class TestStateKeyInjection:
    """Verify that _state_key is injected from the config file stem."""

    def test_state_key_equals_file_stem(self, tmp_path: Path) -> None:
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"dev-team": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].state_key == "dev-team"

    def test_state_key_without_state_store(self, tmp_path: Path) -> None:
        """_state_key is always injected even without a state_store."""
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"myproj": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].state_key == "myproj"


# ---------------------------------------------------------------------------
# State loading tests
# ---------------------------------------------------------------------------


class TestStateLoading:
    """Verify that state is loaded from state files and merged into config."""

    def test_state_loaded_from_state_file(self, tmp_path: Path) -> None:
        """State keys in state file are merged into project config."""
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"proj": project})

        state_store = YamlFileStateStore(tmp_path / "state")
        state_store.save("proj", ["preallocated_fips"], [{"id": "fip-1", "address": "10.0.0.1"}])

        merged_projects, _ = load_all_projects(config_dir, state_store=state_store)

        assert merged_projects[0].preallocated_fips == [FipEntry(id="fip-1", address="10.0.0.1")]

    def test_state_file_wins_over_yaml(self, tmp_path: Path) -> None:
        """State file takes precedence over state keys in project YAML."""
        project = _minimum_valid_project()
        project["router_ips"] = [{"id": "old", "name": "old-router", "external_ip": "1.1.1.1"}]
        config_dir = _write_config(tmp_path, projects={"proj": project})

        state_store = YamlFileStateStore(tmp_path / "state")
        state_store.save(
            "proj",
            ["router_ips"],
            [{"id": "new", "name": "new-router", "external_ip": "2.2.2.2"}],
        )

        merged_projects, _ = load_all_projects(config_dir, state_store=state_store)

        assert merged_projects[0].router_ips == [RouterIpEntry(id="new", name="new-router", external_ip="2.2.2.2")]

    def test_no_state_store_yaml_keys_still_work(self, tmp_path: Path) -> None:
        """Without state_store, state keys in YAML are accessible via deep-merge."""
        project = _minimum_valid_project()
        project["router_ips"] = [{"id": "r1", "name": "rtr", "external_ip": "3.3.3.3"}]
        config_dir = _write_config(tmp_path, projects={"proj": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].router_ips == [RouterIpEntry(id="r1", name="rtr", external_ip="3.3.3.3")]

    def test_migration_from_yaml_to_state_file(self, tmp_path: Path) -> None:
        """State keys in YAML but not in state file are auto-migrated."""
        project = _minimum_valid_project()
        project["preallocated_fips"] = [{"id": "fip-1", "address": "10.0.0.1"}]
        config_dir = _write_config(tmp_path, projects={"proj": project})

        state_store = YamlFileStateStore(tmp_path / "state")
        # State file is empty — migration should happen.

        merged_projects, _ = load_all_projects(config_dir, state_store=state_store)

        # Value is available in merged config.
        assert merged_projects[0].preallocated_fips == [FipEntry(id="fip-1", address="10.0.0.1")]

        # Value was migrated to state file.
        state_data = state_store.load("proj")
        assert state_data["preallocated_fips"] == [{"id": "fip-1", "address": "10.0.0.1"}]

    def test_empty_state_file_no_keys_injected(self, tmp_path: Path) -> None:
        """When state file is empty and YAML has no state keys, nothing is injected."""
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"proj": project})

        state_store = YamlFileStateStore(tmp_path / "state")

        merged_projects, _ = load_all_projects(config_dir, state_store=state_store)

        assert merged_projects[0].preallocated_fips == []
        assert merged_projects[0].router_ips == []


# ---------------------------------------------------------------------------
# ConfigSource Protocol conformance tests
# ---------------------------------------------------------------------------


class TestConfigSourceProtocol:
    """Verify that YamlConfigSource satisfies the ConfigSource protocol."""

    def test_yaml_config_source_is_config_source(self, tmp_path: Path) -> None:
        source = YamlConfigSource(str(tmp_path))
        assert isinstance(source, ConfigSource)


# ---------------------------------------------------------------------------
# YamlConfigSource isolation tests
# ---------------------------------------------------------------------------


class TestYamlConfigSource:
    """Test YamlConfigSource methods in isolation."""

    def test_load_defaults_returns_dict(self, tmp_path: Path) -> None:
        (tmp_path / "defaults.yaml").write_text(yaml.dump({"domain_id": "test-domain"}), encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        defaults, errors = source.load_defaults()

        assert errors == []
        assert defaults == {"domain_id": "test-domain"}

    def test_load_defaults_missing_file(self, tmp_path: Path) -> None:
        source = YamlConfigSource(str(tmp_path))

        defaults, errors = source.load_defaults()

        assert errors == []
        assert defaults == {}

    def test_load_defaults_bad_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "defaults.yaml").write_text(":\ninvalid: [", encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        _defaults, errors = source.load_defaults()

        assert len(errors) == 1
        assert "YAML parse error" in errors[0]

    def test_load_defaults_non_mapping(self, tmp_path: Path) -> None:
        (tmp_path / "defaults.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        _defaults, errors = source.load_defaults()

        assert len(errors) == 1
        assert "expected a mapping" in errors[0]

    def test_load_raw_projects_returns_raw_projects(self, tmp_path: Path) -> None:
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "alpha.yaml").write_text(yaml.dump({"name": "alpha"}), encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        raw_projects, errors = source.load_raw_projects()

        assert errors == []
        assert len(raw_projects) == 1
        assert raw_projects[0].state_key == "alpha"
        assert raw_projects[0].label == "alpha.yaml"
        assert raw_projects[0].data == {"name": "alpha"}

    def test_load_raw_projects_bad_yaml(self, tmp_path: Path) -> None:
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "bad.yaml").write_text(":\n[invalid", encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        raw_projects, errors = source.load_raw_projects()

        assert len(errors) == 1
        assert "bad.yaml" in errors[0]
        assert raw_projects == []

    def test_load_raw_projects_non_mapping(self, tmp_path: Path) -> None:
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "list.yaml").write_text("- a\n- b\n", encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        raw_projects, errors = source.load_raw_projects()

        assert len(errors) == 1
        assert "list.yaml" in errors[0]
        assert "expected a mapping" in errors[0]
        assert raw_projects == []


# ---------------------------------------------------------------------------
# build_projects() pipeline tests (in-memory, no file I/O)
# ---------------------------------------------------------------------------


class TestBuildProjects:
    """Test the format-agnostic pipeline with in-memory RawProject objects."""

    def test_basic_merge_and_validate(self) -> None:
        """Pipeline merges defaults with raw project data."""
        defaults: dict[str, Any] = {"description": "from defaults"}
        raw = [
            RawProject(
                state_key="proj",
                label="proj.yaml",
                source_path="/fake/proj.yaml",
                data=_minimum_valid_project(),
            )
        ]

        merged, errors = build_projects(defaults, raw)

        assert errors == []
        assert len(merged) == 1
        assert merged[0].description == "from defaults"
        assert merged[0].name == "validproject"
        assert merged[0].state_key == "proj"
        assert merged[0].config_path == "/fake/proj.yaml"

    def test_placeholder_replacement(self) -> None:
        """Pipeline performs {name} placeholder replacement."""
        raw = [
            RawProject(
                state_key="proj",
                label="proj.yaml",
                source_path="/fake/proj.yaml",
                data={
                    **_minimum_valid_project(name="myproj"),
                    "description": "Project {name}",
                },
            )
        ]

        merged, errors = build_projects({}, raw)

        assert errors == []
        assert merged[0].description == "Project myproj"

    def test_validation_errors_returned(self) -> None:
        """Pipeline returns validation errors without raising."""
        raw = [
            RawProject(
                state_key="bad",
                label="bad.yaml",
                source_path="/fake/bad.yaml",
                data={"name": "123invalid"},
            )
        ]

        merged, errors = build_projects({}, raw)

        # Verify validation errors were collected
        assert len(errors) > 0
        # Verify the specific error: project name starting with digit is invalid
        assert any("name" in e.lower() for e in errors)
        # Verify no projects were returned (validation failed)
        assert merged == []

    def test_missing_name_shows_filename_in_error(self, tmp_path: Path) -> None:
        """When name is missing, error message includes the source filename."""
        project = _minimum_valid_project()
        del project["name"]  # Remove required field
        config_dir = _write_config(tmp_path, projects={"dev-team": project})

        with pytest.raises(ConfigValidationError) as exc:
            load_all_projects(config_dir)

        # Error should contain filename, not <unknown>
        errors = exc.value.errors
        assert len(errors) > 0
        error_msg = errors[0]
        assert "dev-team.yaml" in error_msg
        assert "<unknown>" not in error_msg
        assert "missing required field 'name'" in error_msg

    def test_cidr_overlap_detected(self) -> None:
        """Pipeline detects CIDR overlaps and names both projects in error."""
        raw = [
            RawProject(
                state_key="a",
                label="a.yaml",
                source_path="/fake/a.yaml",
                data=_minimum_valid_project(name="projecta", resource_prefix="projecta", cidr="192.168.1.0/24"),
            ),
            RawProject(
                state_key="b",
                label="b.yaml",
                source_path="/fake/b.yaml",
                data=_minimum_valid_project(
                    name="projectb",
                    resource_prefix="projectb",
                    cidr="192.168.1.0/25",
                ),
            ),
        ]

        merged, errors = build_projects({"enforce_unique_cidrs": True}, raw)

        # Verify overlap was detected
        assert any("overlap" in e.lower() for e in errors)
        # Verify error message contains both project names
        error_str = " ".join(errors)
        assert "projecta" in error_str
        assert "projectb" in error_str
        # Verify no projects were returned due to validation failure
        assert merged == []

    def test_deep_merge_project_overrides_defaults(self) -> None:
        """Project values override defaults during deep merge."""
        defaults: dict[str, Any] = {"quotas": {"compute": {"cores": 10, "ram": 2048}}}
        project_data = _minimum_valid_project()
        project_data["quotas"] = {"compute": {"cores": 40}}
        raw = [
            RawProject(
                state_key="proj",
                label="proj.yaml",
                source_path="/fake/proj.yaml",
                data=project_data,
            )
        ]

        merged, errors = build_projects(defaults, raw)

        assert errors == []
        assert merged[0].quotas.compute["cores"] == 40
        assert merged[0].quotas.compute["ram"] == 2048

    def test_domain_auto_populated(self, monkeypatch) -> None:
        """Pipeline auto-populates domain_id to 'default' when not specified."""
        monkeypatch.delenv("OS_PROJECT_DOMAIN_ID", raising=False)
        monkeypatch.delenv("OS_USER_DOMAIN_NAME", raising=False)
        raw = [
            RawProject(
                state_key="proj",
                label="proj.yaml",
                source_path="/fake/proj.yaml",
                data=_minimum_valid_project(),
            )
        ]

        merged, errors = build_projects({}, raw)

        assert errors == []
        # Verify domain_id was auto-populated with the default fallback value
        assert merged[0].domain_id == "default"

    def test_domain_null_in_defaults_triggers_auto_discovery(self, monkeypatch) -> None:
        """Pipeline treats domain_id=None (null) in defaults as auto-discover."""
        monkeypatch.delenv("OS_PROJECT_DOMAIN_ID", raising=False)
        monkeypatch.delenv("OS_USER_DOMAIN_NAME", raising=False)
        defaults: dict[str, Any] = {"domain_id": None}
        raw = [
            RawProject(
                state_key="proj",
                label="proj.yaml",
                source_path="/fake/proj.yaml",
                data=_minimum_valid_project(),
            )
        ]

        merged, errors = build_projects(defaults, raw)

        assert errors == []
        assert merged[0].domain_id == "default"

    def test_domain_null_in_project_overrides_concrete_default(self, monkeypatch) -> None:
        """Pipeline: project domain_id=None overrides concrete default."""
        monkeypatch.delenv("OS_PROJECT_DOMAIN_ID", raising=False)
        monkeypatch.delenv("OS_USER_DOMAIN_NAME", raising=False)
        defaults: dict[str, Any] = {"domain_id": "shared"}
        project_data = _minimum_valid_project()
        project_data["domain_id"] = None
        raw = [
            RawProject(
                state_key="proj",
                label="proj.yaml",
                source_path="/fake/proj.yaml",
                data=project_data,
            )
        ]

        merged, errors = build_projects(defaults, raw)

        assert errors == []
        assert merged[0].domain_id == "default"

    def test_empty_raw_projects(self) -> None:
        """Pipeline handles empty project list gracefully."""
        merged, errors = build_projects({}, [])

        assert errors == []
        assert merged == []

    def test_state_store_integration(self, tmp_path: Path) -> None:
        """Pipeline loads state from state_store when provided."""
        state_store = YamlFileStateStore(tmp_path / "state")
        state_store.save("proj", ["preallocated_fips"], [{"id": "fip-1", "address": "10.0.0.1"}])
        raw = [
            RawProject(
                state_key="proj",
                label="proj.yaml",
                source_path="/fake/proj.yaml",
                data=_minimum_valid_project(),
            )
        ]

        merged, errors = build_projects({}, raw, state_store=state_store)

        assert errors == []
        assert merged[0].preallocated_fips == [FipEntry(id="fip-1", address="10.0.0.1")]


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestMinimalProjectConfig:
    """Test projects with minimal configuration (only required fields)."""

    def test_project_with_only_name(self, tmp_path: Path) -> None:
        """Project with only name field gets all defaults applied."""
        defaults = {
            "resource_prefix": "defaultprefix",
            "description": "default description",
            "enabled": True,
            "network": {
                "subnet": {
                    "cidr": "10.0.0.0/24",
                    "gateway_ip": "10.0.0.254",
                    "allocation_pools": [{"start": "10.0.0.1", "end": "10.0.0.253"}],
                }
            },
        }
        project = {"name": "minimalproject"}
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 1
        proj = merged_projects[0]
        assert proj.name == "minimalproject"
        assert proj.resource_prefix == "defaultprefix"
        assert proj.description == "default description"
        assert proj.enabled is True
        assert proj.network.subnet.cidr == "10.0.0.0/24"

    def test_minimal_project_without_defaults(self, tmp_path: Path) -> None:
        """Minimal project without defaults still validates (uses model defaults)."""
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 1
        assert merged_projects[0].name == "validproject"


class TestEmptyProjectsDirectory:
    """Test behavior when projects directory is empty or missing."""

    def test_empty_projects_directory_succeeds(self, tmp_path: Path) -> None:
        """Empty projects directory returns empty list without errors."""
        defaults = {"domain_id": "test-domain"}
        config_dir = _write_config(tmp_path, defaults=defaults)

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects == []

    def test_missing_projects_directory_succeeds(self, tmp_path: Path) -> None:
        """Missing projects directory returns empty list without errors."""
        (tmp_path / "defaults.yaml").write_text(yaml.dump({}), encoding="utf-8")

        merged_projects, _ = load_all_projects(str(tmp_path))

        assert merged_projects == []


class TestDuplicateProjectNames:
    """Test detection of duplicate project names across files."""

    def test_duplicate_project_names_allowed(self, tmp_path: Path) -> None:
        """Duplicate project names are currently allowed (no validation error)."""
        project_a = _minimum_valid_project(name="dupname", resource_prefix="prefixa")
        project_b = _minimum_valid_project(
            name="dupname",
            resource_prefix="prefixb",
            cidr="10.1.0.0/24",
        )
        config_dir = _write_config(
            tmp_path,
            projects={"file_a": project_a, "file_b": project_b},
        )

        # Currently no validation error for duplicate names
        merged_projects, _ = load_all_projects(config_dir)

        assert len(merged_projects) == 2
        assert merged_projects[0].name == "dupname"
        assert merged_projects[1].name == "dupname"


class TestPlaceholderNestedStrings:
    """Test {name} placeholder replacement in deeply nested config."""

    def test_placeholder_in_security_group_description(self, tmp_path: Path) -> None:
        """{name} placeholder expanded in nested security_group.rules descriptions."""
        project = _minimum_valid_project(name="testproject")
        project["security_group"] = {
            "name": "default",
            "rules": [
                {
                    "direction": "ingress",
                    "protocol": "tcp",
                    "port_range_min": 22,
                    "port_range_max": 22,
                    "remote_ip_prefix": "0.0.0.0/0",
                    "description": "SSH for {name}",
                }
            ],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert rules[0].description == "SSH for testproject"

    def test_placeholder_in_quota_nested_config(self, tmp_path: Path) -> None:
        """{name} placeholder replaced in nested config (description and resource_prefix)."""
        project = _minimum_valid_project(name="myproject", resource_prefix="{name}prefix")
        project["description"] = "Desc for {name}"
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].description == "Desc for myproject"
        assert merged_projects[0].resource_prefix == "myprojectprefix"


class TestListOverrideBehavior:
    """Test that lists in project config replace defaults entirely (no merge)."""

    def test_security_group_rules_list_override(self, tmp_path: Path) -> None:
        """Project security_group.rules replaces default rules entirely."""
        defaults = {
            "security_group": {
                "name": "default",
                "rules": ["SSH", "HTTP"],
            }
        }
        project = _minimum_valid_project()
        project["security_group"] = {
            "name": "default",
            "rules": ["ICMP"],
        }
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        rules = merged_projects[0].security_group.rules
        assert len(rules) == 1
        assert rules[0].protocol == "icmp"

    def test_allocation_pools_list_override(self, tmp_path: Path) -> None:
        """Project allocation_pools replaces default entirely (no merge)."""
        defaults = {
            "network": {
                "subnet": {
                    "cidr": "192.168.1.0/24",
                    "gateway_ip": "192.168.1.254",
                    "allocation_pools": [
                        {"start": "192.168.1.1", "end": "192.168.1.100"},
                    ],
                }
            }
        }
        project = _minimum_valid_project()
        project["network"]["subnet"]["allocation_pools"] = [
            {"start": "192.168.1.50", "end": "192.168.1.200"},
        ]
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        pools = merged_projects[0].network.subnet.allocation_pools
        assert len(pools) == 1
        assert pools[0].start == "192.168.1.50"
        assert pools[0].end == "192.168.1.200"


class TestDeepNestedDictMerge:
    """Test deep-merge behavior for nested dicts (defaults + project override)."""

    def test_partial_quota_override_merges_deeply(self, tmp_path: Path) -> None:
        """Project quotas.compute partial override merges with defaults."""
        defaults = {
            "quotas": {
                "compute": {"cores": 10, "ram": 2048, "instances": 5},
                "network": {"networks": 3, "subnets": 5},
            }
        }
        project = _minimum_valid_project()
        # Only override compute.cores, rest should merge from defaults
        project["quotas"] = {"compute": {"cores": 50}}
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        quotas = merged_projects[0].quotas
        assert quotas.compute["cores"] == 50
        assert quotas.compute["ram"] == 2048
        assert quotas.compute["instances"] == 5
        assert quotas.network["networks"] == 3

    def test_network_config_partial_override(self, tmp_path: Path) -> None:
        """Project network.subnet.cidr override merges with default gateway_ip."""
        defaults = {
            "network": {
                "mtu": 1500,
                "subnet": {
                    "cidr": "10.0.0.0/24",
                    "gateway_ip": "10.0.0.254",
                    "enable_dhcp": True,
                    "dns_nameservers": ["8.8.8.8"],
                    "allocation_pools": [{"start": "10.0.0.1", "end": "10.0.0.253"}],
                },
            }
        }
        project = _minimum_valid_project()
        project["network"] = {
            "subnet": {
                "cidr": "192.168.1.0/24",
                "gateway_ip": "192.168.1.254",
                "allocation_pools": [{"start": "192.168.1.1", "end": "192.168.1.253"}],
            }
        }
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        network = merged_projects[0].network
        # Project overrides
        assert network.subnet.cidr == "192.168.1.0/24"
        # Defaults merged in
        assert network.mtu == 1500
        assert network.subnet.enable_dhcp is True
        assert network.subnet.dns_nameservers == ["8.8.8.8"]


class TestYamlConfigSourceEdgeCases:
    """Edge cases for YamlConfigSource file loading."""

    def test_load_raw_projects_empty_directory(self, tmp_path: Path) -> None:
        """Empty projects directory returns empty list without errors."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        source = YamlConfigSource(str(tmp_path))

        raw_projects, errors = source.load_raw_projects()

        assert errors == []
        assert raw_projects == []

    def test_load_raw_projects_no_yaml_files(self, tmp_path: Path) -> None:
        """Projects directory with non-YAML files returns empty list."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "readme.txt").write_text("Not a yaml file", encoding="utf-8")
        (projects_dir / "config.json").write_text("{}", encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        raw_projects, errors = source.load_raw_projects()

        assert errors == []
        assert raw_projects == []

    def test_load_defaults_empty_file(self, tmp_path: Path) -> None:
        """Empty defaults.yaml returns empty dict without errors."""
        (tmp_path / "defaults.yaml").write_text("", encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        defaults, errors = source.load_defaults()

        assert errors == []
        assert defaults == {}

    def test_multiple_projects_sorted_by_filename(self, tmp_path: Path) -> None:
        """Multiple project files are loaded in sorted order by filename."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "c-project.yaml").write_text(yaml.dump({"name": "c"}), encoding="utf-8")
        (projects_dir / "a-project.yaml").write_text(yaml.dump({"name": "a"}), encoding="utf-8")
        (projects_dir / "b-project.yaml").write_text(yaml.dump({"name": "b"}), encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        raw_projects, errors = source.load_raw_projects()

        assert errors == []
        assert len(raw_projects) == 3
        assert raw_projects[0].state_key == "a-project"
        assert raw_projects[1].state_key == "b-project"
        assert raw_projects[2].state_key == "c-project"

    def test_mixed_valid_and_invalid_yaml_files(self, tmp_path: Path) -> None:
        """Mix of valid and invalid YAML files: valid loaded, errors reported."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "valid.yaml").write_text(yaml.dump({"name": "valid"}), encoding="utf-8")
        (projects_dir / "bad.yaml").write_text(":\ninvalid: [", encoding="utf-8")
        (projects_dir / "also-valid.yaml").write_text(yaml.dump({"name": "also-valid"}), encoding="utf-8")
        source = YamlConfigSource(str(tmp_path))

        raw_projects, errors = source.load_raw_projects()

        assert len(errors) == 1
        assert "bad.yaml" in errors[0]
        assert len(raw_projects) == 2
        assert raw_projects[0].data["name"] == "also-valid"
        assert raw_projects[1].data["name"] == "valid"


class TestMinimumValidProjectHelper:
    """Verify _minimum_valid_project helper auto-calculation behavior."""

    def test_default_auto_calculates_gateway_and_pools(self, tmp_path: Path) -> None:
        """Default project should auto-calculate gateway/pools from CIDR."""
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        subnet = merged_projects[0].network.subnet
        assert subnet.cidr == "192.168.1.0/24"
        assert subnet.gateway_ip == "192.168.1.1"  # Auto-calculated
        assert subnet.allocation_pools[0].start == "192.168.1.2"
        assert subnet.allocation_pools[0].end == "192.168.1.254"

    def test_custom_cidr_auto_calculates(self, tmp_path: Path) -> None:
        """Custom CIDR should auto-calculate compatible gateway/pools."""
        project = _minimum_valid_project(cidr="10.5.0.0/16")
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        subnet = merged_projects[0].network.subnet
        assert subnet.cidr == "10.5.0.0/16"
        assert subnet.gateway_ip == "10.5.0.1"
        assert subnet.allocation_pools[0].start == "10.5.0.2"
        assert subnet.allocation_pools[0].end == "10.5.255.254"

    def test_explicit_gateway_overrides_auto_calculation(self, tmp_path: Path) -> None:
        """Explicit gateway_ip should be preserved."""
        project = _minimum_valid_project(cidr="192.168.1.0/24", gateway_ip="192.168.1.254")
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        assert merged_projects[0].network.subnet.gateway_ip == "192.168.1.254"

    def test_explicit_pools_overrides_auto_calculation(self, tmp_path: Path) -> None:
        """Explicit allocation_pools should be preserved."""
        project = _minimum_valid_project(
            cidr="192.168.1.0/24",
            allocation_pools=[{"start": "192.168.1.100", "end": "192.168.1.200"}],
        )
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        pools = merged_projects[0].network.subnet.allocation_pools
        assert pools[0].start == "192.168.1.100"
        assert pools[0].end == "192.168.1.200"


# ---------------------------------------------------------------------------
# Federation entry mode resolution tests
# ---------------------------------------------------------------------------


class TestFederationEntryModeResolution:
    """Verify _resolve_federation_entry_modes propagates mode to entries."""

    def test_entries_inherit_federation_default(self, tmp_path: Path) -> None:
        """Entries without mode inherit the federation-level mode."""
        project = _minimum_valid_project()
        project["federation"] = {
            "issuer": "https://idp.example.com",
            "mapping_id": "m",
            "mode": "group",
            "role_assignments": [
                {"idp_group": "member", "roles": ["member"]},
            ],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        entry = merged_projects[0].federation.role_assignments[0]
        assert entry.mode == "group"

    def test_entry_override_wins(self, tmp_path: Path) -> None:
        """Entry-level mode overrides federation-level mode."""
        project = _minimum_valid_project()
        project["federation"] = {
            "issuer": "https://idp.example.com",
            "mapping_id": "m",
            "mode": "group",
            "role_assignments": [
                {"idp_group": "member", "roles": ["member"], "mode": "project"},
            ],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        entry = merged_projects[0].federation.role_assignments[0]
        assert entry.mode == "project"

    def test_missing_federation_mode_defaults_to_project(self, tmp_path: Path) -> None:
        """When federation has no mode, entries inherit 'project'."""
        project = _minimum_valid_project()
        project["federation"] = {
            "issuer": "https://idp.example.com",
            "mapping_id": "m",
            "role_assignments": [
                {"idp_group": "member", "roles": ["member"]},
            ],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        entry = merged_projects[0].federation.role_assignments[0]
        assert entry.mode == "project"

    def test_defaults_yaml_mode_inherited(self, tmp_path: Path) -> None:
        """Federation mode from defaults.yaml propagates to entries."""
        defaults: dict[str, Any] = {
            "federation": {
                "issuer": "https://idp.example.com",
                "mapping_id": "m",
                "mode": "group",
                "role_assignments": [
                    {"idp_group": "reader", "roles": ["reader"]},
                ],
            },
        }
        project = _minimum_valid_project()
        config_dir = _write_config(tmp_path, defaults=defaults, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        entry = merged_projects[0].federation.role_assignments[0]
        assert entry.mode == "group"

    def test_mixed_modes_in_one_project(self, tmp_path: Path) -> None:
        """Entries can have different modes within one project."""
        project = _minimum_valid_project()
        project["federation"] = {
            "issuer": "https://idp.example.com",
            "mapping_id": "m",
            "mode": "project",
            "role_assignments": [
                {"idp_group": "member", "roles": ["member"]},
                {"idp_group": "reader", "roles": ["reader"], "mode": "group"},
            ],
        }
        config_dir = _write_config(tmp_path, projects={"test": project})

        merged_projects, _ = load_all_projects(config_dir)

        entries = merged_projects[0].federation.role_assignments
        assert entries[0].mode == "project"
        assert entries[1].mode == "group"
