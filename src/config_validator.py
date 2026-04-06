"""Config validation for OpenStack provisioner.

Delegates all constraint checking to typed model ``validate()`` classmethods
and raises ``ConfigValidationError`` when any fail.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from src.models import ProjectConfig, ProjectState
from src.utils import ProvisionerError

logger = logging.getLogger(__name__)


class ConfigValidationError(ProvisionerError):
    """Raised when configuration validation finds errors."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Configuration validation failed with {len(errors)} error(s)")


def validate_project(project: dict[str, Any], errors: list[str]) -> ProjectConfig | None:
    """Validate a single merged project config, appending errors to *errors*.

    Returns a ``ProjectConfig`` if construction was possible, ``None`` otherwise.
    """
    label = project.get("name", "<unknown>")
    return ProjectConfig.validate(project, errors, label)


def check_cidr_overlaps(projects: list[ProjectConfig], errors: list[str]) -> None:
    """Check for CIDR overlaps between any two projects."""
    networks: list[tuple[str, ipaddress.IPv4Network | ipaddress.IPv6Network]] = []
    for proj in projects:
        if proj.state == ProjectState.ABSENT:
            continue
        if proj.network is None:
            continue
        cidr_str = proj.network.subnet.cidr
        try:
            net = ipaddress.ip_network(cidr_str, strict=True)
        except ValueError:
            continue  # already reported by per-project validation
        networks.append((proj.name, net))

    for i in range(len(networks)):
        for j in range(i + 1, len(networks)):
            name_a, net_a = networks[i]
            name_b, net_b = networks[j]
            if net_a.overlaps(net_b):
                errors.append(f"CIDR overlap: {name_a} ({net_a}) overlaps with {name_b} ({net_b})")
