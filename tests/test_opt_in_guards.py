"""Regression tests: every resource function must be opt-in.

Each function should return a SKIPPED action (not crash) when its
config section is absent.  Add a row to the parametrize table for
every new resource function.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import pytest

from src.resources.group_roles import ensure_group_role_assignments
from src.resources.network import ensure_network_stack
from src.resources.prealloc.fip import ensure_preallocated_fips
from src.resources.prealloc.network import ensure_preallocated_network
from src.resources.quotas import ensure_quotas
from src.resources.security_group import ensure_baseline_sg
from src.utils import ActionStatus

if TYPE_CHECKING:
    from src.models import ProjectConfig


@pytest.mark.parametrize(
    ("func", "key_to_delete", "expected_resource_type", "returns_list"),
    [
        pytest.param(
            ensure_baseline_sg,
            "security_group",
            "security_group",
            False,
            id="baseline_sg",
        ),
        pytest.param(
            ensure_group_role_assignments,
            "group_role_assignments",
            "group_role_assignment",
            True,
            id="group_role_assignments",
        ),
        pytest.param(
            ensure_network_stack,
            "network",
            "network_stack",
            False,
            id="network_stack",
        ),
        pytest.param(
            ensure_quotas,
            "quotas",
            "quotas",
            True,
            id="quotas",
        ),
        pytest.param(
            ensure_preallocated_fips,
            "quotas",
            "preallocated_fip",
            True,
            id="preallocated_fips",
        ),
        pytest.param(
            ensure_preallocated_network,
            "quotas",
            "preallocated_network",
            True,
            id="preallocated_network",
        ),
    ],
)
def test_skips_when_config_section_absent(
    func: Any,
    key_to_delete: str,
    expected_resource_type: str,
    returns_list: bool,  # noqa: FBT001
    sample_project_cfg: ProjectConfig,
    shared_ctx: Any,
) -> None:
    """Resource functions must return SKIPPED when their config key is missing."""
    # Use dataclasses.replace to set the field to None or empty list
    replacement_values = {
        "security_group": None,
        "group_role_assignments": [],
        "network": None,
        "quotas": None,
    }
    cfg = dataclasses.replace(sample_project_cfg, **{key_to_delete: replacement_values[key_to_delete]})

    result = func(cfg, "fake-project-id", shared_ctx)

    if returns_list:
        assert isinstance(result, list)
        assert len(result) >= 1
        action = result[0]
    else:
        action = result

    assert action.status == ActionStatus.SKIPPED
    assert action.resource_type == expected_resource_type
