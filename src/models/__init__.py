"""Typed configuration models for OpenStack provisioner.

Replaces ``dict[str, Any]`` project config dicts with frozen dataclasses,
providing attribute access, type safety, and IDE autocompletion.

All models are frozen (immutable) — use ``dataclasses.replace()`` for
mutation patterns (e.g. reconciler state transitions).
"""

from src.models.access import GroupRoleAssignment
from src.models.defaults import DefaultsConfig
from src.models.federation import FederationConfig, FederationRoleAssignment
from src.models.lifetime import LifetimeConfig
from src.models.network import AllocationPool, NetworkConfig, SubnetConfig
from src.models.project import ProjectConfig, ProjectState
from src.models.quotas import QuotaConfig
from src.models.reservations import ReservationConfig
from src.models.security import SecurityGroupConfig, SecurityGroupRule
from src.models.state import (
    FipEntry,
    GrantedFlavorAccessEntry,
    ReleasedFipEntry,
    ReleasedRouterIpEntry,
    RevokedFlavorAccessEntry,
    RouterIpEntry,
)

__all__ = [
    "AllocationPool",
    "DefaultsConfig",
    "FederationConfig",
    "FederationRoleAssignment",
    "FipEntry",
    "GrantedFlavorAccessEntry",
    "GroupRoleAssignment",
    "LifetimeConfig",
    "NetworkConfig",
    "ProjectConfig",
    "ProjectState",
    "QuotaConfig",
    "ReleasedFipEntry",
    "ReleasedRouterIpEntry",
    "ReservationConfig",
    "RevokedFlavorAccessEntry",
    "RouterIpEntry",
    "SecurityGroupConfig",
    "SecurityGroupRule",
    "SubnetConfig",
]
