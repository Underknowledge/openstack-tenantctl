# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Domain-aware federation mapping rules: when `domain` is set on a project, generated rules include `"domain": {"name": "<domain>"}` in the projects element
- `federation.user_type` field: when set (e.g., `"ephemeral"`), the user element in generated mapping rules includes `"type": "<user_type>"`

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
