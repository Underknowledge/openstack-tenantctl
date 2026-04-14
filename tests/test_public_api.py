"""Verify the public API surface exported from ``src``."""

from __future__ import annotations

import src

EXPECTED_ALL = {
    "Action",
    "ActionStatus",
    "ConfigSource",
    "ConfigValidationError",
    "DefaultsConfig",
    "InMemoryStateStore",
    "ProjectConfig",
    "ProvisionerError",
    "RawProject",
    "ReconcileScope",
    "RunResult",
    "SharedContext",
    "StateStore",
    "TenantCtl",
    "YamlFileStateStore",
    "__version__",
    "augment_group_role_assignments",
    "auto_populate_subnet_defaults",
    "build_external_network_map",
    "build_projects",
    "ensure_baseline_sg",
    "ensure_federation_mapping",
    "ensure_group_role_assignments",
    "ensure_keystone_groups",
    "ensure_network_stack",
    "ensure_preallocated_fips",
    "ensure_preallocated_network",
    "ensure_project",
    "ensure_quotas",
    "expand_security_group_rules",
    "find_existing_project",
    "find_network",
    "identity_v3",
    "list_project_servers",
    "replace_placeholders",
    "resolve_default_external_network",
    "resolve_external_subnet",
    "resolve_project_external_network",
    "retry",
    "shelve_all_servers",
    "track_router_ips",
    "unshelve_all_servers",
}


def test_all_matches_expected_set() -> None:
    """``src.__all__`` must equal the known public API set."""
    assert set(src.__all__) == EXPECTED_ALL


def test_all_names_importable() -> None:
    """Every name in ``src.__all__`` must be accessible as an attribute."""
    for name in src.__all__:
        assert hasattr(src, name), f"src.{name} listed in __all__ but not importable"
