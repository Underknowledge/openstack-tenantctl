"""Tests for ProjectConfig.build() programmatic construction helper."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.config_validator import ConfigValidationError
from src.models import ProjectConfig


def _full_config() -> dict[str, Any]:
    """Return a complete, valid project config dict."""
    return {
        "name": "myproject",
        "resource_prefix": "myproj",
        "network": {
            "subnet": {
                "cidr": "10.0.1.0/24",
                "gateway_ip": "10.0.1.1",
                "allocation_pools": [{"start": "10.0.1.2", "end": "10.0.1.254"}],
            },
        },
    }


class TestBuildHappyPath:
    """Happy-path tests for ProjectConfig.build()."""

    def test_full_config_dict(self) -> None:
        cfg = ProjectConfig.build(_full_config())
        assert cfg.name == "myproject"
        assert cfg.resource_prefix == "myproj"
        assert cfg.network is not None
        assert cfg.network.subnet.cidr == "10.0.1.0/24"

    def test_minimal_config_auto_populates(self) -> None:
        """name + resource_prefix + network.subnet.cidr → auto-populates rest."""
        cfg = ProjectConfig.build(
            {
                "name": "minimal",
                "resource_prefix": "min",
                "network": {"subnet": {"cidr": "10.0.2.0/24"}},
            }
        )
        assert cfg.name == "minimal"
        assert cfg.domain_id == "default"
        assert cfg.network is not None
        assert cfg.network.subnet.gateway_ip == "10.0.2.1"
        assert cfg.network.subnet.allocation_pools is not None
        assert len(cfg.network.subnet.allocation_pools) == 1
        pool = cfg.network.subnet.allocation_pools[0]
        assert pool.start == "10.0.2.2"
        assert pool.end == "10.0.2.254"

    def test_kwargs_mode(self) -> None:
        cfg = ProjectConfig.build(
            name="kwproj",
            resource_prefix="kwp",
            network={"subnet": {"cidr": "10.0.3.0/24"}},
        )
        assert cfg.name == "kwproj"
        assert cfg.resource_prefix == "kwp"

    def test_dict_plus_kwargs_override(self) -> None:
        base = _full_config()
        cfg = ProjectConfig.build(base, name="overridden")
        assert cfg.name == "overridden"
        assert cfg.resource_prefix == "myproj"


class TestBuildAutoPopulate:
    """Auto-population logic in build()."""

    def test_subnet_defaults_from_cidr(self) -> None:
        cfg = ProjectConfig.build(
            {
                "name": "sub",
                "resource_prefix": "sub",
                "network": {"subnet": {"cidr": "172.16.0.0/28"}},
            }
        )
        assert cfg.network is not None
        assert cfg.network.subnet.gateway_ip == "172.16.0.1"
        pools = cfg.network.subnet.allocation_pools
        assert pools is not None
        assert pools[0].start == "172.16.0.2"
        assert pools[0].end == "172.16.0.14"

    def test_domain_id_defaults_to_default(self) -> None:
        cfg = ProjectConfig.build(_full_config())
        assert cfg.domain_id == "default"

    def test_domain_without_domain_id_copies_domain(self) -> None:
        data = _full_config()
        data["domain"] = "mydomain"
        cfg = ProjectConfig.build(data)
        assert cfg.domain_id == "mydomain"
        assert cfg.domain == "mydomain"

    def test_explicit_domain_id_preserved(self) -> None:
        data = _full_config()
        data["domain_id"] = "explicit"
        cfg = ProjectConfig.build(data)
        assert cfg.domain_id == "explicit"

    def test_federation_mode_propagation(self) -> None:
        data = _full_config()
        data["federation"] = {
            "issuer": "https://idp.example.com/realms/test",
            "mapping_id": "my-mapping",
            "group_prefix": "/services/openstack/",
            "mode": "group",
            "role_assignments": [
                {"idp_group": "admin", "roles": ["admin"], "mode": "project"},
                {"idp_group": "member", "roles": ["member"]},
            ],
        }
        cfg = ProjectConfig.build(data)
        assert cfg.federation is not None
        entries = cfg.federation.role_assignments
        # First entry has explicit mode → preserved
        assert entries[0].mode == "project"
        # Second entry has no mode → inherits federation-level "group"
        assert entries[1].mode == "group"

    def test_absent_state_skips_subnet_auto_populate(self) -> None:
        cfg = ProjectConfig.build(
            {
                "name": "gone",
                "resource_prefix": "gone",
                "state": "absent",
            }
        )
        assert cfg.state.value == "absent"
        assert cfg.network is None


class TestBuildValidationErrors:
    """build() raises ConfigValidationError for invalid input."""

    def test_missing_name(self) -> None:
        with pytest.raises(ConfigValidationError) as exc_info:
            ProjectConfig.build(
                {
                    "resource_prefix": "pfx",
                    "network": {"subnet": {"cidr": "10.0.0.0/24"}},
                }
            )
        assert any("name" in e for e in exc_info.value.errors)

    def test_missing_resource_prefix(self) -> None:
        with pytest.raises(ConfigValidationError) as exc_info:
            ProjectConfig.build(
                {
                    "name": "noprefix",
                    "network": {"subnet": {"cidr": "10.0.0.0/24"}},
                }
            )
        assert any("resource_prefix" in e for e in exc_info.value.errors)

    def test_bad_cidr(self) -> None:
        with pytest.raises(ConfigValidationError) as exc_info:
            ProjectConfig.build(
                {
                    "name": "badcidr",
                    "resource_prefix": "bad",
                    "network": {"subnet": {"cidr": "not-a-cidr"}},
                }
            )
        assert len(exc_info.value.errors) > 0

    def test_missing_network_for_present_state(self) -> None:
        with pytest.raises(ConfigValidationError) as exc_info:
            ProjectConfig.build(
                {
                    "name": "nonet",
                    "resource_prefix": "nonet",
                }
            )
        assert any("network" in e or "subnet" in e for e in exc_info.value.errors)


class TestBuildInputSafety:
    """build() does not mutate caller data."""

    def test_does_not_mutate_input_dict(self) -> None:
        original = _full_config()
        frozen = copy.deepcopy(original)
        ProjectConfig.build(original)
        assert original == frozen

    def test_does_not_mutate_input_with_subnet_auto_populate(self) -> None:
        original: dict[str, Any] = {
            "name": "immut",
            "resource_prefix": "immut",
            "network": {"subnet": {"cidr": "10.0.5.0/24"}},
        }
        frozen = copy.deepcopy(original)
        ProjectConfig.build(original)
        # Original must NOT have gateway_ip injected
        assert "gateway_ip" not in original["network"]["subnet"]
        assert original == frozen
