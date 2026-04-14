"""OpenStack TenantCtl - Project-as-Code for OpenStack.

Declarative tenant provisioning tool that enables IaaS by automating OpenStack
project creation, network setup, quota configuration, and access control.
"""

from __future__ import annotations

__version__ = "0.0.0-dev"  # Fallback for development

try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("openstack-tenantctl")
except PackageNotFoundError:
    # Package not installed - use fallback
    pass

# Public API re-exports ------------------------------------------------
# NOTE: __version__ must be set *before* these imports because
# src.client does ``from src import __version__`` at module level.

# Phase 1: Core types and config loading
from src.client import RunResult, TenantCtl
from src.config_loader import ConfigSource, ConfigValidationError, RawProject, build_projects

# Phase 2: Config processing
from src.config_resolver import auto_populate_subnet_defaults, expand_security_group_rules, replace_placeholders

# Phase 2: Context building
from src.context import build_external_network_map, resolve_default_external_network
from src.models import ProjectConfig
from src.models.defaults import DefaultsConfig
from src.reconciler import ReconcileScope

# Phase 2: Resource handlers
from src.resources.compute import list_project_servers, shelve_all_servers, unshelve_all_servers
from src.resources.federation import augment_group_role_assignments, ensure_federation_mapping
from src.resources.group_roles import ensure_group_role_assignments
from src.resources.keystone_groups import ensure_keystone_groups
from src.resources.network import ensure_network_stack, track_router_ips
from src.resources.prealloc.fip import ensure_preallocated_fips
from src.resources.prealloc.network import ensure_preallocated_network
from src.resources.project import ensure_project, find_existing_project
from src.resources.quotas import ensure_quotas
from src.resources.security_group import ensure_baseline_sg
from src.state_store import InMemoryStateStore, StateStore, YamlFileStateStore
from src.utils import (
    Action,
    ActionStatus,
    ProvisionerError,
    SharedContext,
    find_network,
    identity_v3,
    resolve_external_subnet,
    resolve_project_external_network,
    retry,
)

__all__ = [
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
]
