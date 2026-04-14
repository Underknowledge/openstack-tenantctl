# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-04-14

### Features
- Library layer: new `TenantCtl` class (`src/client.py`) wraps the three-phase pipeline for programmatic use
- `RunResult` frozen dataclass for structured pipeline results
- `ProjectConfig.build()` classmethod for constructing validated configs without YAML
- Public API re-exports via `src/__init__` with `__all__` (`TenantCtl`, `RunResult`, `ProjectConfig`, `DefaultsConfig`, `StateStore`, etc.)
- Support direct Python object injection alongside YAML-based config
- Selective resource reconciliation via `ReconcileScope`: `run(only={ReconcileScope.FIPS})` skips network/quotas/SG/roles/federation and only reconciles the specified handlers; `only=None` (default) preserves full reconciliation for backward compatibility
- CLI `--only` flag: `tenantctl --only fips quotas` restricts which resource handlers run for present-state projects
- Connection reuse: `TenantCtl.run()` accepts optional `connection` parameter for reusing pre-existing OpenStack connections; caller retains ownership (TenantCtl will not close provided connections)
- Library API Phase 1: expose `ConfigSource` protocol, `RawProject`, `build_projects()`, `SharedContext`, `retry` decorator, and utility functions (`identity_v3`, `find_network`) for custom config backends and robust OpenStack integrations
- Library API Phase 2: expose resource handlers (`ensure_project`, `ensure_quotas`, `ensure_network_stack`, `ensure_baseline_sg`, `ensure_group_role_assignments`, `shelve_all_servers`, `unshelve_all_servers`, `track_router_ips`, `find_existing_project`), context-building utilities (`build_external_network_map`, `resolve_default_external_network`, `resolve_external_subnet`, `resolve_project_external_network`), and config processing helpers (`expand_security_group_rules`, `replace_placeholders`, `auto_populate_subnet_defaults`) for custom workflows and extensions
- Library API Phase 4: expose FIP pre-allocation handlers (`ensure_preallocated_fips`, `ensure_preallocated_network`), federation handlers (`ensure_federation_mapping`, `ensure_keystone_groups`, `augment_group_role_assignments`), and server discovery utility (`list_project_servers`) for advanced operator workflows including IP management, SAML/OIDC identity mapping, and custom server lifecycle automation

### Code Refactoring
- Extract context-building helpers from `main.py` into dedicated `src/context.py` module
- Slim `main.py` to thin CLI adapter delegating to `TenantCtl`

### Documentation
- `docs/LIBRARY-API.md`: Complete reference for all 42 public exports, organized by category with examples
- `docs/examples/custom_workflow.py`: Phased provisioning workflow example
- `docs/examples/quota_update.py`: Quota-only incremental updates example
- `docs/examples/context_building.py`: Manual context setup for custom integrations
- `docs/examples/fip_management.py`: FIP pre-allocation, drift detection, and reclamation workflow
- `docs/examples/federation_workflow.py`: Complete federation setup with proper ordering (groups → projects → mapping)

### Tests
- Comprehensive test suites for client, context, project build, and public API exports
- Simplify `test_main.py` to verify CLI-to-client delegation

## [0.4.0] - 2026-04-14

### Added
- Configurable static federation mapping files: `federation.static_mapping_files` in `defaults.yaml` accepts glob patterns (e.g., `federation_static.d/*.json`) resolved relative to the config directory, replacing the previously hardcoded `federation_static.json` path

### Documentation
- Readme pip installation instructions

## [0.3.0] - 2026-04-14

### Added
- Domain-aware federation mapping rules: when `domain` is set on a project, generated rules include `"domain": {"name": "<domain>"}` in the projects element
- `federation.user_type` field: when set (e.g., `"ephemeral"`), the user element in generated mapping rules includes `"type": "<user_type>"`
- Per-entry federation mode (`mode: "project"` or `"group"` on each role assignment): entries can independently generate `{"projects": [...]}` or `{"group": {...}}` rules, enabling mixed strategies within a single project; federation-level `mode` sets the default, each entry can override
- New federation fields: `federation.mode` (default for entries), `role_assignments[].mode` (per-entry override), `group_name_separator`, and per-assignment `keystone_group` override
- Automatic Keystone group lifecycle: tenantctl creates groups referenced by group-mode federation before per-project reconciliation
- Auto-derived `group_role_assignments` in group mode: role assignments from federation config are wired to Keystone groups automatically

### Fixed
- Resolve mypy `union-attr` errors: add `identity_v3()` typed helper to cast `conn.identity` to the v3 proxy (v2 was removed from OpenStack in 2020)

### Changed
- Type pipeline-level defaults dict with `DefaultsConfig` frozen dataclass
- FIP allocation: prefer router gateway network/subnet over auto-discovery
- Switch formatter from ruff to black (line-length 120, not a teleprompter)
- Bump CI actions versions
- Reorganize README structure
- Validation errors now show source filename instead of `<unknown>` when project name is missing

## [0.2.8] - 2026-04-05

### Changed
- Added helper commands like `make bump` to make release tasks a bit easier
- Carried out a major refactor across the codebase, improving structure, clarity, and maintainability.
- Dry-run mode with offline option (no cloud connection required)
- File-based state store with locking for concurrent access
- Teardown with safety checks
- Reinitialized Git to leave behind outdated clutter and a few less glorious moments.

### CI
- Single-job CI workflow (lint, type-check, test)
- Cloud enforcement workflow / provisions on push and detects drift on schedule

### Documentation
- User guide, API reference, config schema, design decisions, and specification


## [0.2.7] - 2026-04-03

### Features
- Production tested drift detection for floating IPs
- Security group preset expansion (SSH, HTTP, HTTPS, ICMP)

### Documentation
- Updated API reference with new resource types

## [0.2.5] - 2026-04-03

### Features
- Project lifecycle states (present/locked/absent)
- Teardown functionality with safety checks

## [0.2.0] - 2026-03-29

### Features
- Router IP tracking with writeback pattern
- Graceful service degradation for missing services

## [0.1.2] - 2026-03-28

### Features
- Complete quota management
- First working end-to-end POC

## [0.1.1] - 2026-03-28

### Features
- Locked floating IPs with config writeback

## [0.1.0] - 2026-03-28

### Features
- Federation mapping with deterministic rule ordering
- Security group management

## [0.0.5] - 2026-03-28

### Improvements
- Retry logic for transient failures
- Error isolation between projects
- SharedContext for action tracking

## [0.0.3] - 2026-03-28

### Features
- Deep-merge config inheritance
- Universal resource pattern

## [0.0.1] - 2026-03-28

### Features
- Initial POC: three-phase execution model
- Basic project and network provisioning
