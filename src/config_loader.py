"""Config loading and orchestration for OpenStack provisioner.

Loads project configurations from any backend (via the ``ConfigSource`` protocol),
deep-merges with defaults, calls resolution and validation, and returns fully
resolved project configurations.
"""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import yaml
from deepmerge import Merger

from src.config_resolver import (
    auto_populate_subnet_defaults,
    expand_security_group_rules,
    replace_placeholders,
)
from src.config_validator import (
    ConfigValidationError,
    check_cidr_overlaps,
    validate_project,
)
from src.models.defaults import DefaultsConfig
from src.state_store import STATE_KEYS

if TYPE_CHECKING:
    from src.models import ProjectConfig
    from src.state_store import StateStore

__all__ = [
    "ConfigSource",
    "ConfigValidationError",
    "RawProject",
    "build_projects",
    "load_all_projects",
]

logger = logging.getLogger(__name__)

# Deep-merge strategy: dicts merge recursively, lists are replaced by
# the override (project wins), scalars are replaced by the override.
_merger = Merger(
    type_strategies=[
        (list, ["override"]),
        (dict, ["merge"]),
        (set, ["override"]),
    ],
    fallback_strategies=["override"],
    type_conflict_strategies=["override"],
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawProject:
    """Raw project data from any config source."""

    state_key: str
    """Identifier for state store lookup (e.g. ``"dev-team"``)."""

    label: str
    """Human-readable label for error messages (e.g. ``"dev-team.yaml"``)."""

    source_path: str
    """Origin identifier stored as ``_config_path``."""

    data: dict[str, Any]
    """The raw config dict before merging with defaults."""


@runtime_checkable
class ConfigSource(Protocol):
    """Protocol for loading project configuration from any backend."""

    def load_defaults(self) -> tuple[dict[str, Any], list[str]]:
        """Return ``(defaults_dict, errors)``."""
        ...

    def load_raw_projects(self) -> tuple[list[RawProject], list[str]]:
        """Return ``(raw_projects, errors)``."""
        ...


# ---------------------------------------------------------------------------
# YAML file backend
# ---------------------------------------------------------------------------


class YamlConfigSource:
    """YAML-file-backed implementation of ``ConfigSource``."""

    def __init__(self, config_dir: str) -> None:
        self._config_path = Path(config_dir)

    def load_defaults(self) -> tuple[dict[str, Any], list[str]]:
        """Load ``defaults.yaml`` from the config directory."""
        errors: list[str] = []
        defaults_file = self._config_path / "defaults.yaml"
        defaults: dict[str, Any] = {}

        if defaults_file.exists():
            logger.info("Loading defaults from %s", defaults_file)
            try:
                with defaults_file.open(encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh)
                    if isinstance(loaded, dict):
                        defaults = loaded
                    elif loaded is not None:
                        errors.append(f"defaults.yaml: expected a mapping, got {type(loaded).__name__}")
            except yaml.YAMLError as exc:
                errors.append(f"defaults.yaml: YAML parse error: {exc}")
        else:
            logger.warning("No defaults.yaml found in %s", self._config_path)

        return defaults, errors

    def load_raw_projects(self) -> tuple[list[RawProject], list[str]]:
        """Discover and parse ``projects/*.yaml`` files."""
        errors: list[str] = []
        raw_projects: list[RawProject] = []

        projects_dir = self._config_path / "projects"
        project_files = sorted(projects_dir.glob("*.yaml")) if projects_dir.is_dir() else []
        if not project_files:
            logger.warning("No project YAML files found in %s", projects_dir)

        for pfile in project_files:
            logger.info("Loading project file %s", pfile.name)
            try:
                with pfile.open(encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
            except yaml.YAMLError as exc:
                errors.append(f"{pfile.name}: YAML parse error: {exc}")
                continue

            if not isinstance(raw, dict):
                errors.append(f"{pfile.name}: expected a mapping at top level, got {type(raw).__name__}")
                continue

            raw_projects.append(
                RawProject(
                    state_key=pfile.stem,
                    label=pfile.name,
                    source_path=str(pfile),
                    data=raw,
                )
            )

        return raw_projects, errors


# ---------------------------------------------------------------------------
# State helper
# ---------------------------------------------------------------------------


def _load_state_into_config(
    merged: dict[str, Any],
    state_store: StateStore,
    raw: dict[str, Any],
) -> None:
    """Load observed state from the state file and merge into *merged*.

    State keys found in the state file take precedence.  If a state key
    exists in the raw project YAML but not in the state file, it is
    auto-migrated to the state file (one-time migration).
    """
    state_key = merged["_state_key"]
    state_data = state_store.load(state_key)

    for key in STATE_KEYS:
        if key in state_data:
            merged[key] = state_data[key]
        elif key in raw:
            # One-time migration: copy from project YAML to state file.
            logger.info(
                "Migrating state key '%s' from project YAML to state file for %s",
                key,
                state_key,
            )
            state_store.save(state_key, [key], raw[key])
            merged[key] = raw[key]


# ---------------------------------------------------------------------------
# Format-agnostic pipeline
# ---------------------------------------------------------------------------


def build_projects(
    defaults: dict[str, Any],
    raw_projects: list[RawProject],
    state_store: StateStore | None = None,
) -> tuple[list[ProjectConfig], list[str]]:
    """Format-agnostic pipeline: deep-merge, resolve, validate.

    Returns ``(merged_projects, errors)``.
    """
    errors: list[str] = []
    merged_projects: list[dict[str, Any]] = []

    for rp in raw_projects:
        # Deep-merge: start with a copy of defaults, then merge project on top.
        merged: dict[str, Any] = copy.deepcopy(defaults)
        _merger.merge(merged, rp.data)
        merged["_config_path"] = rp.source_path
        merged["_state_key"] = rp.state_key

        # Load observed state from state file and merge into config.
        if state_store is not None:
            _load_state_into_config(merged, state_store, rp.data)

        # Replace {name} placeholders throughout the merged config.
        project_name = merged.get("name")
        if isinstance(project_name, str):
            merged = replace_placeholders(merged, project_name)

        # Expand security group rule presets before validation
        expand_security_group_rules(merged, errors)

        # Auto-populate gateway_ip and allocation_pools from CIDR if not specified
        if merged.get("state", "present") != "absent":
            auto_populate_subnet_defaults(merged)

        # Auto-populate domain if not specified (with env var fallback)
        # Supports both domain_id (for UUIDs) and domain (for friendly names)
        # Precedence: domain_id > domain > env vars > "default"
        if merged.get("domain_id") is None and merged.get("domain") is None:
            domain_value = os.environ.get("OS_PROJECT_DOMAIN_ID") or os.environ.get("OS_USER_DOMAIN_NAME") or "default"
            merged["domain_id"] = domain_value
            logger.debug("Auto-populated domain_id=%s for project %s", domain_value, project_name)
        elif merged.get("domain_id") is None and merged.get("domain") is not None:
            # If only domain is specified, copy it to domain_id for internal use
            merged["domain_id"] = merged["domain"]
            logger.debug(
                "Using domain=%s as domain_id for project %s",
                merged["domain"],
                project_name,
            )
        # If domain_id is specified, use it directly (highest priority)

        merged_projects.append(merged)

    # --- Validate AND construct all projects ---
    typed_projects: list[ProjectConfig] = []
    for proj in merged_projects:
        result = validate_project(proj, errors)
        if result is not None:
            typed_projects.append(result)

    if defaults.get("enforce_unique_cidrs", False):
        check_cidr_overlaps(typed_projects, errors)

    if errors:
        return [], errors

    return typed_projects, errors


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_all_projects(
    config_dir: str,
    state_store: StateStore | None = None,
) -> tuple[list[ProjectConfig], DefaultsConfig]:
    """Load and validate all project configurations from *config_dir*.

    When *state_store* is provided, each project's ``_state_key`` is injected
    (the config file stem), and observed state (FIP IDs, router IPs, etc.) is
    loaded from the state file and merged into the in-memory config dict.

    Returns a tuple of ``(list_of_merged_project_configs, defaults)``.

    Raises:
        ConfigValidationError: If any validation errors are found.
    """
    source = YamlConfigSource(config_dir)

    defaults, errors = source.load_defaults()
    raw_projects, project_errors = source.load_raw_projects()
    errors.extend(project_errors)

    merged, pipeline_errors = build_projects(defaults, raw_projects, state_store)
    errors.extend(pipeline_errors)

    if errors:
        raise ConfigValidationError(errors)

    logger.info("Loaded %d project(s) successfully", len(merged))
    return merged, DefaultsConfig.from_dict(defaults)
