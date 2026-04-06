# API Reference

<!--
**Last Updated**: 2026-04-05
-->

Reference for the OpenStack TenantCtl codebase. This document covers core types, modules, and functions for extending or integrating with the provisioner.

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Core Types (`src.utils`)](#2-core-types-srcutils)
3. [Configuration Loading (`src.config_loader`)](#3-configuration-loading-srcconfig_loader)
4. [Configuration Resolution (`src.config_resolver`)](#4-configuration-resolution-srcconfig_resolver)
5. [Configuration Validation (`src.config_validator`)](#5-configuration-validation-srcconfig_validator)
6. [State Store (`src.state_store`)](#6-state-store-srcstate_store)
7. [Orchestration (`src.reconciler`)](#7-orchestration-srcreconciler)
8. [Resource Modules (`src.resources.*`)](#8-resource-modules-srcresources)
   - [project](#srcresourcesproject)
   - [network](#srcresourcesnetwork)
   - [quotas](#srcresourcesquotas)
   - [security_group](#srcresourcessecurity_group)
   - [group_roles](#srcresourcesgroup_roles)
   - [federation](#srcresourcesfederation)
   - [compute](#srcresourcescompute)
   - [teardown](#srcresourcesteardown)
   - [prealloc.fip](#srcresourcespreallocfip)
   - [prealloc.network](#srcresourcespreallocnetwork)
9. [CLI Entry Point (`src.main`)](#9-cli-entry-point-srcmain)
10. [Creating New Resource Types](#10-creating-new-resource-types)

---

## 1. Project Structure

```
openstack-tenantctl/
├── src/
│   ├── __init__.py                 # Version info
│   ├── main.py                     # CLI entry point & three-phase orchestration
│   ├── config_loader.py            # YAML loading & deep-merge
│   ├── config_resolver.py          # Subnet auto-calculation, placeholder substitution
│   ├── config_validator.py         # Fail-fast validation
│   ├── reconciler.py               # Per-project resource orchestration
│   ├── state_store.py              # Runtime state persistence
│   ├── utils.py                    # Retry decorator, SharedContext, logging
│   └── resources/
│       ├── project.py              # Keystone projects
│       ├── network.py              # Network stacks (network, subnet, router) + router IP tracking
│       ├── quotas.py               # Compute, network, load balancer, block storage quotas
│       ├── security_group.py       # Security groups with presets
│       ├── federation.py           # SAML/OIDC identity federation
│       ├── group_roles.py          # Group-to-project role assignments
│       ├── compute.py              # Server shelve/unshelve
│       ├── teardown.py             # Safety-checked project removal
│       └── prealloc/
│           ├── __init__.py         # Re-exports ensure_preallocated_fips, ensure_preallocated_network
│           ├── fip.py              # Pre-allocated floating IPs + drift detection
│           └── network.py          # Pre-allocated network stacks + quota enforcement
├── tests/                          # pytest suite
├── config/
│   ├── defaults.yaml               # Global defaults (all projects inherit)
│   ├── projects/                   # Per-project overrides
│   ├── federation_static.json      # Static admin federation rules
│   └── state/                      # Runtime state files (auto-managed)
├── docs/                           # Documentation
├── Makefile                        # install, fmt, lint, test, version bumps
├── CHANGELOG.md
├── CONTRIBUTING.md
└── pyproject.toml
```

---

## 2. Core Types (`src.utils`)

### ProvisionerError

```python
class ProvisionerError(Exception):
    """Base exception for expected provisioner failures."""
```

**Description**: Base class for all expected provisioner failures. Subclassed by `ConfigValidationError`.

---

### ActionStatus

```python
class ActionStatus(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    DELETED = "DELETED"
```

**Description**: Enumeration of possible outcomes for resource operations.

**Values**:
- `CREATED`: Resource was created (didn't exist before)
- `UPDATED`: Resource existed but was modified
- `SKIPPED`: Resource already matches configuration (no change needed)
- `FAILED`: Operation failed (error occurred)
- `DELETED`: Resource was removed (teardown)

---

### Action

```python
@dataclass(frozen=True)
class Action:
    status: ActionStatus
    resource_type: str
    name: str
    details: str = ""
    project_id: str = ""
    project_name: str = ""
```

**Description**: Immutable record of a single resource operation.

**Fields**:
- `status`: Outcome of the operation (ActionStatus enum)
- `resource_type`: Type of resource (e.g., `"project"`, `"network_stack"`, `"quotas"`)
- `name`: Resource identifier (project name, network name, etc.)
- `details`: Optional additional information (e.g., `"id=abc123"`, `"cores=16"`)
- `project_id`: OpenStack project ID (auto-filled from `SharedContext.current_project_id`)
- `project_name`: Friendly project name (auto-filled from `SharedContext.current_project_name`)

---

### SharedContext

```python
@dataclass
class SharedContext:
    conn: openstack.connection.Connection | None = None
    dry_run: bool = False
    external_net_id: str = ""
    current_mapping_rules: list[dict[str, Any]] = field(default_factory=list)
    mapping_exists: bool = False
    static_mapping_rules: list[dict[str, Any]] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    failed_projects: list[str] = field(default_factory=list)
    current_project_id: str = ""
    current_project_name: str = ""
    state_store: StateStore | None = None

    def record(
        self,
        status: ActionStatus,
        resource_type: str,
        name: str,
        details: str = "",
        project_id: str | None = None,
        project_name: str | None = None,
    ) -> Action: ...
```

**Description**: Mutable context object passed to all resource functions, containing shared state and the OpenStack connection. Designed for single-threaded, sequential use.

**Fields**:
- `conn`: OpenStack SDK connection object (`None` in dry-run mode)
- `dry_run`: If `True`, resource functions skip actual operations and return SKIPPED actions
- `external_net_id`: ID of the external network (resolved in Phase 2)
- `current_mapping_rules`: Current federation mapping rules (from OpenStack)
- `mapping_exists`: Whether the federation mapping already exists in OpenStack
- `static_mapping_rules`: Static federation rules (from `federation_static.json`)
- `actions`: List of all actions recorded during execution
- `failed_projects`: List of project names that failed to provision
- `current_project_id`: Project ID currently being reconciled (set by reconciler)
- `current_project_name`: Project name currently being reconciled (set by reconciler)
- `state_store`: State persistence backend (for FIP IDs, router IPs, etc.)

**Methods**:

#### `record(status, resource_type, name, details="", project_id=None, project_name=None) -> Action`

Record an action and append it to the `actions` list.

**Parameters**:
- `status`: ActionStatus enum value
- `resource_type`: String identifying resource type
- `name`: Resource identifier
- `details`: Optional details string
- `project_id`: Override for project ID (defaults to `self.current_project_id`)
- `project_name`: Override for project name (defaults to `self.current_project_name`)

**Returns**: The created Action object (also appended to `self.actions`)

---

### retry()

```python
def retry(
    max_attempts: int = 5,
    backoff_base: float = 2.0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Retry decorator with exponential backoff for transient OpenStack errors."""
```

**Description**: Decorator for retrying functions that make OpenStack API calls. Built on `tenacity`.

**Parameters**:
- `max_attempts`: Maximum number of attempts (default: 5)
- `backoff_base`: Base delay in seconds for exponential backoff (default: 2.0)

**Retryable Exceptions** (`RETRYABLE_EXCEPTIONS`):
- `openstack.exceptions.HttpException` with status code >= 500 or == 429
- `openstack.exceptions.SDKException`
- `requests.exceptions.ConnectionError`
- `ConnectionError` (stdlib)

**Non-Retryable Exceptions** (`_NON_RETRYABLE_EXCEPTIONS`):
- `openstack.exceptions.BadRequestException`
- `openstack.exceptions.ConflictException`
- `openstack.exceptions.EndpointNotFound`
- `openstack.exceptions.ForbiddenException`
- `openstack.exceptions.NotFoundException`
- `openstack.exceptions.PreconditionFailedException`
- `openstack.exceptions.ResourceNotFound`

---

### find_network()

```python
@retry()
def find_network(
    conn: openstack.connection.Connection,
    net_name: str,
    project_id: str,
) -> Network | None:
    """Look up a network by name scoped to project_id."""
```

**Description**: Shared utility for looking up a network by name within a project. Used by both `network.py` and `prealloc/network.py`.

---

### setup_logging()

```python
def setup_logging(verbosity: int) -> None:
    """Configure the root logger based on verbosity level."""
```

**Logging Levels**:
- `0` (default): WARNING
- `1` (`-v`): INFO
- `2+` (`-vv`): DEBUG (also enables `openstack.enable_logging(debug=True)`)

---

## 3. Configuration Loading (`src.config_loader`)

### RawProject

```python
@dataclass(frozen=True)
class RawProject:
    state_key: str
    label: str
    source_path: str
    data: dict[str, Any]
```

**Description**: Raw project data from any configuration source, before merging with defaults.

**Fields**:
- `state_key`: Identifier for state store lookup (e.g., `"dev-team"` — the YAML filename stem)
- `label`: Human-readable label for error messages (e.g., `"dev-team.yaml"`)
- `source_path`: Origin identifier stored as `_config_path` in the merged config
- `data`: The raw config dict before merging with defaults

---

### ConfigSource (Protocol)

```python
@runtime_checkable
class ConfigSource(Protocol):
    def load_defaults(self) -> tuple[dict[str, Any], list[str]]: ...
    def load_raw_projects(self) -> tuple[list[RawProject], list[str]]: ...
```

**Description**: Protocol for loading project configuration from any backend. Implement this to add non-YAML config sources (e.g., database, API).

**Methods**:
- `load_defaults()`: Return `(defaults_dict, errors)`
- `load_raw_projects()`: Return `(raw_projects, errors)`

---

### YamlConfigSource

```python
class YamlConfigSource:
    def __init__(self, config_dir: str) -> None: ...
    def load_defaults(self) -> tuple[dict[str, Any], list[str]]: ...
    def load_raw_projects(self) -> tuple[list[RawProject], list[str]]: ...
```

**Description**: YAML-file-backed implementation of `ConfigSource`. Reads `defaults.yaml` and `projects/*.yaml`.

---

### build_projects()

```python
def build_projects(
    defaults: dict[str, Any],
    raw_projects: list[RawProject],
    state_store: StateStore | None = None,
) -> tuple[list[ProjectConfig], list[str]]:
    """Format-agnostic pipeline: deep-merge, resolve, validate."""
```

**Description**: Takes raw project data from any source and produces fully resolved, validated `ProjectConfig` instances.

**Pipeline**:
1. Deep-merge each project with defaults
2. Load observed state from state file (if `state_store` provided)
3. Replace `{name}` placeholders
4. Expand security group rule presets
5. Auto-populate subnet defaults from CIDR
6. Auto-populate domain (with env var fallback)
7. Validate all projects and construct `ProjectConfig` instances
8. Check CIDR overlaps

**Returns**: `(typed_projects, errors)` — list of `ProjectConfig` objects on success, empty list with errors on failure.

---

### load_all_projects()

```python
def load_all_projects(
    config_dir: str,
    state_store: StateStore | None = None,
) -> tuple[list[ProjectConfig], dict[str, Any]]:
    """Load and validate all project configurations from *config_dir*."""
```

**Description**: Main entry point for loading and validating project configurations from YAML files.

**Parameters**:
- `config_dir`: Path to configuration directory (e.g., `"config/"`)
- `state_store`: Optional state store for loading observed state (FIP IDs, router IPs)

**Returns**: Tuple of `(list_of_typed_project_configs, defaults_dict)` where projects are `ProjectConfig` instances.

**Raises**: `ConfigValidationError` if any validation errors are found.

---

## 4. Configuration Resolution (`src.config_resolver`)

### PREDEFINED_RULES

```python
PREDEFINED_RULES: dict[str, dict[str, str | int]] = {
    "SSH": {...},       # TCP port 22
    "HTTP": {...},      # TCP port 80
    "HTTPS": {...},     # TCP port 443
    "ICMP": {...},      # ICMP
    "All ICMP": {...},  # ICMP (alias)
    "All TCP": {...},   # TCP ports 1-65535
    "All UDP": {...},   # UDP ports 1-65535
    "DNS": {...},       # UDP port 53
    "RDP": {...},       # TCP port 3389
}
```

**Description**: Dictionary of built-in security group rule presets defined in `src.config_resolver`. Each maps a short name to a full rule dict with `direction`, `protocol`, port range, `remote_ip_prefix`, and `description`.

---

### replace_placeholders()

```python
def replace_placeholders(obj: Any, name: str) -> Any:
    """Recursively replace {name} placeholders in all string values."""
```

**Description**: Walks dicts, lists, and strings, replacing `{name}` with the project name. Used during config loading to template resource names.

---

### expand_security_group_rules()

```python
def expand_security_group_rules(project: dict[str, Any], errors: list[str]) -> None:
    """Expand preset names in security_group.rules to full rule dicts."""
```

**Description**: Mutates `project["security_group"]["rules"]` in-place, expanding preset references to full rule dicts.

**Handles three rule formats**:
1. **String** (e.g., `"SSH"`) — looked up in `PREDEFINED_RULES`
2. **Dict with `rule` key** — preset used as base, explicit fields override
3. **Dict without `rule` key** — left as-is (full rule specification)

Unknown preset names are appended to `errors`.

---

### auto_populate_subnet_defaults()

```python
def auto_populate_subnet_defaults(project: dict[str, Any]) -> None:
    """Auto-populate gateway_ip and allocation_pools from CIDR if not specified."""
```

**Description**: Calculates subnet defaults when not explicitly configured:
- `gateway_ip`: First usable IP in the subnet (network address + 1)
- `allocation_pools`: All usable IPs except the gateway

---

## 5. Configuration Validation (`src.config_validator`)

### ConfigValidationError

```python
class ConfigValidationError(ProvisionerError):
    def __init__(self, errors: list[str]) -> None: ...
```

**Description**: Raised when configuration validation finds errors. Contains a list of human-readable error strings.

**Attributes**:
- `errors`: List of validation error messages

---

### validate_project()

```python
def validate_project(
    project: dict[str, Any], errors: list[str]
) -> ProjectConfig | None:
    """Validate a single merged project config, appending errors to *errors*.

    Returns a ProjectConfig if construction was possible, None otherwise.
    """
```

**Description**: Validates a single project configuration against all rules and constructs a `ProjectConfig` instance. This is a thin wrapper that delegates to `ProjectConfig.validate()`.

**Validation rules** (enforced by `ProjectConfig.validate()` and nested model validators):
- **State**: Must be one of `{"present", "locked", "absent"}`
- **Required fields**: `name`, `resource_prefix`, `network.subnet.cidr` (CIDR skipped for `absent` state)
- **Name format**: `^[a-zA-Z][a-zA-Z0-9_ -]{0,63}$`
- **Resource prefix format**: `^[a-z0-9]+$`
- **Domain**: String, non-empty (if specified)
- **Group role assignments**: Valid structure (group, roles, optional state)
- **`reclaim_floating_ips`**: Must be boolean (if present)
- **Network**: CIDR validity (strict mode), gateway inside CIDR, allocation pools inside CIDR
- **Quotas**: All values must be non-negative integers
- **Federation**: Valid `role_assignments` structure (`idp_group` + `roles`)
- **Security group rules**: Must be a list of dicts (post-expansion)

**Returns**: `ProjectConfig` instance if validation succeeded (even with non-fatal errors), `None` if project name is missing (cannot proceed).

---

### check_cidr_overlaps()

```python
def check_cidr_overlaps(projects: list[ProjectConfig], errors: list[str]) -> None:
    """Check for CIDR overlaps between any two projects."""
```

**Description**: Cross-project validation. Takes a list of `ProjectConfig` instances and skips projects with `state: absent`.

---

## 6. State Store (`src.state_store`)

### Constants

```python
STATE_KEYS: frozenset[str] = frozenset({
    "preallocated_fips",
    "released_fips",
    "router_ips",
    "released_router_ips",
})
```

Keys that are stored in the state file rather than the project config YAML. During config loading, values for these keys are loaded from the state file and merged into the in-memory config.

---

### StateStore (Protocol)

```python
@runtime_checkable
class StateStore(Protocol):
    def load(self, state_key: str) -> dict[str, Any]: ...
    def save(self, state_key: str, key_path: list[str], value: Any) -> None: ...
```

**Description**: Protocol for reading/writing per-project observed state. Allows swapping the YAML-file backend for a database-backed implementation.

**Methods**:
- `load(state_key)`: Load all state for a project. Returns empty dict if no state exists.
- `save(state_key, key_path, value)`: Write a nested key in the state store. Raises `ValueError` if `key_path` is empty.

---

### YamlFileStateStore

```python
class YamlFileStateStore:
    def __init__(self, state_dir: Path) -> None: ...
```

**Description**: YAML-file-backed implementation of `StateStore`. State files live at `<state_dir>/<state_key>.state.yaml`. The directory is created lazily on first save.

**Parameters**:
- `state_dir`: Directory for state files (e.g., `Path("config/state")`)

**Behavior**:
1. `load()`: Read YAML file, return empty dict if missing or non-dict
2. `save()`: Read-modify-write — loads existing state, sets value at key path, writes back
3. Directory created lazily with `mkdir(parents=True, exist_ok=True)` on first save
4. Intermediate dicts created as needed when traversing key path

---

## 7. Orchestration (`src.reconciler`)

### reconcile()

```python
def reconcile(
    projects: list[ProjectConfig],
    all_projects: list[ProjectConfig],
    ctx: SharedContext,
) -> list[Action]:
    """Phase 3: per-project resources, then shared federation mapping."""
```

**Description**: Main orchestration function for Phase 3. Provisions resources for each project in sequence, then reconciles the shared federation mapping.

**Parameters**:
- `projects`: List of `ProjectConfig` instances to provision (may be filtered by `--project` flag)
- `all_projects`: Full list of all `ProjectConfig` instances (used for federation mapping)
- `ctx`: SharedContext with OpenStack connection and shared state

**Returns**: List of all Action objects (same as `ctx.actions`)

**Behavior**:
1. For each project in `projects`:
   - Set `ctx.current_project_name`
   - Dispatch to state handler via `_STATE_HANDLERS` dict
   - On success: persist `last_reconciled_at` timestamp to state file
   - On failure: log error, add to `ctx.failed_projects`, continue
2. After all projects:
   - Clear `current_project_id` and `current_project_name`
   - Call `ensure_federation_mapping(all_projects, ctx)` (uses ALL projects, not filtered)

**Error Isolation**: One project's failure doesn't block others from provisioning.

---

### Project State Handlers

The reconciler dispatches to different handler functions based on `cfg["state"]`:

```python
_STATE_HANDLERS: dict[str, StateHandler] = {
    "present": _reconcile_present,
    "locked": _reconcile_locked,
    "absent": _reconcile_absent,
}
```

---

#### State: `present` (Default)

**Function**: `_reconcile_present(cfg: ProjectConfig, ctx: SharedContext)` (internal)

**Purpose**: Full provisioning of all resources + unshelve any previously shelved servers.

**Resource Pipeline** (executed sequentially):
1. `ensure_project()` — Create/update project, set `ctx.current_project_id`
2. Persist project metadata (project_id, domain_id) to state file
3. `ensure_group_role_assignments()` — Assign groups to roles
4. `ensure_network_stack()` — Network, subnet, router
5. `track_router_ips()` — Snapshot router external IPs, detect changes
6. `ensure_preallocated_fips()` — Pre-allocate floating IPs + drift detection
7. `ensure_preallocated_network()` — Network quota enforcement
8. `ensure_quotas()` — Set compute/network/load-balancer/block-storage quotas
9. `ensure_baseline_sg()` — Create security group and rules
10. `unshelve_all_servers()` — Unshelve servers shelved during `locked` state

---

#### State: `locked`

**Function**: `_reconcile_locked(cfg: ProjectConfig, ctx: SharedContext)` (internal)

**Purpose**: Disable the project and shelve all active VMs.

**Resource Pipeline** (executed sequentially):
1. `ensure_project()` — Force `enabled=False`, set `ctx.current_project_id`
2. Persist project metadata to state file
3. `shelve_all_servers()` — Shelve all ACTIVE servers

**Skipped Resources**: Network stack, floating IPs, quotas, security groups, group role assignments.

---

#### State: `absent`

**Function**: `_reconcile_absent(cfg: ProjectConfig, ctx: SharedContext)` (internal)

**Purpose**: Safety-checked teardown of a project and all its resources.

**Teardown Pipeline** (executed sequentially):
1. `find_existing_project()` — Look up project (skip if not found)
2. `safety_check()` — Verify no VMs or volumes exist (raises if found)
3. Revoke group role assignments (marks all as `state: absent`, calls `ensure_group_role_assignments()`)
4. `teardown_project()` — Delete resources in reverse dependency order

---

#### State Machine Diagram

```
   ┌──────────┐
   │ present  │ ◄─── Default state (full provisioning)
   └────┬─────┘
        │
        ├─────► locked  (disable + shelve VMs)
        │         │
        │         └────► present (re-enable + unshelve)
        │
        └─────► absent (teardown - DESTRUCTIVE)
```

---

## 8. Resource Modules (`src.resources.*`)

All resource modules follow the **universal resource pattern**: check `dry_run` → find existing → create/update/skip → return Action.

---

### `src.resources.project`

#### find_existing_project()

```python
def find_existing_project(
    cfg: ProjectConfig,
    ctx: SharedContext,
) -> tuple[str | None, str | None]:
    """Look up an existing project by name/domain without creating it."""
```

**Returns**: `(project_id, domain_id)` if found, or `(None, None)` if not found or dry-run.

---

#### ensure_project()

```python
def ensure_project(
    cfg: ProjectConfig,
    ctx: SharedContext,
) -> tuple[Action, str]:
    """Ensure the project exists with correct settings."""
```

**Returns**: Tuple of `(Action, project_id)`.

**Behavior**:
- Resolves `domain_id` (name or UUID) via Keystone
- Find project by name in resolved domain
- If not found: create, return (CREATED, project_id)
- If found but needs update (description/enabled changed): update, return (UPDATED, project_id)
- If up to date: return (SKIPPED, project_id)

**Config fields used**: `cfg.name`, `cfg.description`, `cfg.enabled`, `cfg.domain_id`

---

### `src.resources.network`

#### ensure_network_stack()

```python
def ensure_network_stack(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> Action:
    """Create network, subnet, router for a project. Idempotent -- skips if network exists."""
```

**Returns**: Single Action object.

**Behavior**:
- If network with expected name exists → SKIPPED
- Safety: if project owns any network (even with different name) → SKIPPED (prevent duplicate)
- Otherwise: create network → create subnet → create router with external gateway → attach subnet to router → CREATED

**Config fields used**:
- `cfg.resource_prefix`: Prefix for resource names (`{prefix}-network`, `{prefix}-subnet`, `{prefix}-router`)
- `cfg.network.mtu`: MTU value (0 = use cloud default)
- `cfg.network.subnet.cidr`, `cfg.network.subnet.gateway_ip`, `cfg.network.subnet.allocation_pools`
- `cfg.network.subnet.dns_nameservers`, `cfg.network.subnet.enable_dhcp`

---

#### track_router_ips()

```python
def track_router_ips(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Snapshot all router external IPs and track changes."""
```

**Description**: Observes every router in the project, extracts external (SNAT) IPs, compares against the previous snapshot, and records changes.

**Behavior**:
- Builds current snapshot from OpenStack routers
- Compares against previous snapshot in `cfg.router_ips`
- Detects: new (adopted) routers, removed routers, IP changes on existing routers
- Lost IPs are appended to `released_router_ips` as an audit trail
- Persists snapshots via `ctx.state_store`

**Returns**: List of UPDATED actions for each detected change.

---

### `src.resources.quotas`

#### Constants

```python
LOAD_BALANCER_QUOTA_KEYS = {
    "load_balancers", "listeners", "pools", "health_monitors", "members",
}
```

Keys that are routed to the Octavia (load balancer) API instead of Neutron.

---

#### ensure_quotas()

```python
def ensure_quotas(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Ensure compute, network, and block_storage quotas are set correctly."""
```

**Returns**: List of Action objects (one per service that was changed, or single SKIPPED).

**Behavior**:
- `_ensure_compute_quotas()`: Set compute quotas (cores, ram, instances)
- `_ensure_network_quotas()`: Set network quotas (Neutron) and load balancer quotas (Octavia, with graceful degradation)
- `_ensure_block_storage_quotas()`: Set block storage quotas with overlay strategy (read all → merge → write all to avoid Cinder resetting unspecified keys)

**Special handling**:
- `floating_ips` excluded from network quotas unconditionally (managed by prealloc FIP module)
- `networks` excluded when <= 1 (managed by prealloc network module)
- Load balancer quotas: keys in `LOAD_BALANCER_QUOTA_KEYS` are routed to `conn.load_balancer`; `EndpointNotFound` is caught gracefully
- Block storage: catch any exception (service may be unavailable)

---

### `src.resources.security_group`

#### ensure_baseline_sg()

```python
def ensure_baseline_sg(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> Action:
    """Ensure the baseline security group and its configured rules exist."""
```

**Returns**: Single Action object.

**Behavior**:
- For the `"default"` SG (auto-created by OpenStack):
  - If unconfigured (exactly 4 auto-created rules): add missing configured rules (additive)
  - If already configured (>4 rules): SKIPPED (belongs to project team)
- For non-default SGs:
  - If already exists: SKIPPED (created once, then left to project team)
  - If missing: create SG + add all configured rules → CREATED

**Rule matching**: Rules are compared by fingerprint: `(direction, protocol, port_range_min, port_range_max, remote_ip_prefix)`.

**Config fields used**: `cfg.security_group.name`, `cfg.security_group.rules`

---

### `src.resources.group_roles`

#### ensure_group_role_assignments()

```python
def ensure_group_role_assignments(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Ensure group role assignments match the desired state."""
```

**Returns**: List of Action objects, or single SKIPPED.

**Behavior**: For each entry in `cfg.group_role_assignments`:
- `state: present` (default): grant any missing roles → CREATED action per grant
- `state: absent`: revoke any existing roles → UPDATED action per revocation

**Config fields used**:
- `cfg.group_role_assignments[].group`: Keystone group name
- `cfg.group_role_assignments[].roles`: List of role names
- `cfg.group_role_assignments[].state`: `"present"` or `"absent"` (default: `"present"`)

Group and role lookups are cached to avoid redundant API calls.

---

### `src.resources.federation`

#### ensure_federation_mapping()

```python
def ensure_federation_mapping(
    all_projects: list[ProjectConfig],
    ctx: SharedContext,
) -> Action:
    """Build and push the federation mapping from ALL project configs."""
```

**Returns**: Single Action object.

**Behavior**:
- Collect federation configs from all `present`-state projects
- Build per-project rules: each `role_assignment` maps IdP group(s) to project + roles
- Static rules (from `ctx.static_mapping_rules`) placed first, then sorted generated rules
- Compare with `ctx.current_mapping_rules`
- If changed: create or update mapping → CREATED/UPDATED
- If unchanged: SKIPPED

**Group path resolution**:
- If `idp_group` starts with `/`: use as-is (absolute path)
- Otherwise: prepend `{group_prefix}{project_name}/`
- `idp_group` can be a single string or a list of strings (placed in `any_one_of` clause)

**Config fields used** (per project): `cfg.federation.issuer`, `cfg.federation.mapping_id`, `cfg.federation.group_prefix`, `cfg.federation.role_assignments[].idp_group`, `cfg.federation.role_assignments[].roles`

---

### `src.resources.compute`

#### list_project_servers()

```python
def list_project_servers(conn: Connection, project_id: str) -> list[Server]:
    """List all servers in the given project."""
```

---

#### shelve_all_servers()

```python
def shelve_all_servers(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Shelve all ACTIVE servers in the project."""
```

**Returns**: List of UPDATED actions (one per shelved server), or SKIPPED if no active servers.

**Behavior**: Iterates all servers. ACTIVE servers are shelved; other states are skipped. Individual shelve failures are caught and recorded as FAILED (other servers still attempted).

---

#### unshelve_all_servers()

```python
def unshelve_all_servers(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Unshelve all SHELVED/SHELVED_OFFLOADED servers in the project."""
```

**Returns**: List of UPDATED actions (one per unshelved server), or SKIPPED if no shelved servers.

**Behavior**: Iterates all servers. SHELVED and SHELVED_OFFLOADED servers are unshelved; other states are skipped. Individual unshelve failures are caught and recorded as FAILED.

---

### `src.resources.teardown`

#### safety_check()

```python
def safety_check(
    conn: Connection,
    project_id: str,
    project_name: str,
) -> list[str]:
    """Return a list of reasons the project cannot be safely torn down."""
```

**Returns**: Empty list means all checks passed. Each string describes a blocking condition.

**Checks**:
- **Servers**: Refuses if any servers exist (must be deleted manually)
- **Volumes**: Refuses if any volumes exist (must be deleted manually)
- `EndpointNotFound` for a service → skip that check (no resources possible)
- Other exceptions → added as "inconclusive" error (fail safe)

---

#### teardown_project()

```python
def teardown_project(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Delete all project resources in reverse dependency order."""
```

**Returns**: List of DELETED/FAILED actions.

**Teardown order**: floating IPs → snapshots → routers (detach interfaces + clear gateway first) → subnets → networks → non-default security groups → project.

Each deletion is individually error-handled. `NotFoundException` is treated as success (already gone). After all resources, if any failures occurred a summary `TeardownError` is raised.

---

### `src.resources.prealloc.fip`

#### ensure_preallocated_fips()

```python
def ensure_preallocated_fips(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Pre-allocate and enforce quota for floating IPs."""
```

**Returns**: List of Action objects.

**Behavior**:
1. **Drift detection**: Compare persisted FIP list against actual OpenStack state
   - Adopt untracked FIPs (in OpenStack but not in state)
   - Reclaim missing FIPs (in state but deleted from OpenStack) — if `cfg.reclaim_floating_ips: true`
   - Record permanently lost FIPs in `released_fips`
2. **Scale**: After drift reconciliation:
   - `existing == desired`: set quota, SKIPPED
   - `existing < desired`: raise quota → allocate missing → persist → set quota
   - `existing > desired`: release unused (port_id=None) → persist → set quota
3. **Quota**: Always set to `max(desired, actual)` to avoid Neutron rejecting quota < usage

**Config fields used**: `cfg.quotas.network.floating_ips` (desired count), `cfg.reclaim_floating_ips` (opt-in reclamation)

**State fields** (via `ctx.state_store`): `cfg.preallocated_fips`, `cfg.released_fips`

---

### `src.resources.prealloc.network`

#### ensure_preallocated_network()

```python
def ensure_preallocated_network(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> list[Action]:
    """Enforce network quotas for the pre-allocated case (networks <= 1)."""
```

**Returns**: List of Action objects.

**Behavior**:
- `networks >= 2`: SKIPPED (quotas handled by `ensure_quotas`)
- `networks == 0`: Set network/subnet/router quotas to configured values, SKIPPED
- `networks == 1`:
  - If network already exists: set quotas, SKIPPED
  - Safety: if project owns any network with unexpected name: set quotas without creating
  - Otherwise: create network stack via `ensure_network_stack()`, then set quotas

**Config fields used**: `cfg.quotas.network.networks`, `cfg.quotas.network.subnets`, `cfg.quotas.network.routers`

---

## 9. CLI Entry Point (`src.main`)

### main()

```python
def main(argv: list[str] | None = None) -> int:
    """Main entry point. Returns 0 on success, 1 on failure."""
```

**CLI Arguments**:
- `--config-dir`: Path to config directory (default: `config/`)
- `--os-cloud`: Named cloud from `clouds.yaml`
- `--project`: Filter to a single project name
- `--dry-run`: Preview planned actions with live cloud reads (field-level diffs), no writes
- `--offline`: Skip cloud connection in dry-run (use with `--dry-run` for connectionless preview)
- `-v` / `--verbose`: Increase verbosity (repeat for more: `-v`=INFO, `-vv`=DEBUG)
- `--version`: Show version and exit

**Three-Phase Execution**:
1. **Phase 1 — Validate**: Load config, deep-merge, resolve, validate (raises `ConfigValidationError` on failure)
2. **Phase 2 — Connect**: Create OpenStack connection, resolve external network, load federation mapping (skipped only in `--offline` mode; runs normally in `--dry-run` to enable live reads)
3. **Phase 3 — Reconcile**: Call `reconcile()` for all (or filtered) projects

**Returns**: `0` on success, `1` if any projects failed or a phase error occurred.

---

### cli()

```python
def cli() -> None:
    """Console script entry point."""
```

Calls `main()` and passes the return code to `sys.exit()`. Registered as the `tenantctl` console script in `pyproject.toml`.

---

## 10. Creating New Resource Types

To add a new resource type, follow the **universal resource pattern**:

### Template

```python
"""Module docstring explaining the resource type."""

from __future__ import annotations

import logging
from typing import Any

from src.utils import Action, ActionStatus, SharedContext, retry

logger = logging.getLogger(__name__)


@retry()
def _find_resource(conn, name: str, project_id: str):
    """Find existing resource by name."""
    return conn.service.find_resource(name, project_id=project_id)


@retry()
def _create_resource(conn, name: str, project_id: str, **config):
    """Create new resource with given configuration."""
    return conn.service.create_resource(name=name, project_id=project_id, **config)


def ensure_resource(
    cfg: dict[str, Any],
    project_id: str,
    ctx: SharedContext,
) -> Action:
    """Ensure resource exists with correct configuration."""
    resource_name = f"{cfg['resource_prefix']}-resource"

    if ctx.dry_run:
        return ctx.record(ActionStatus.SKIPPED, "resource_type", resource_name, "dry-run")

    existing = _find_resource(ctx.conn, resource_name, project_id)

    if existing is None:
        resource = _create_resource(ctx.conn, resource_name, project_id)
        return ctx.record(ActionStatus.CREATED, "resource_type", resource_name, f"id={resource.id}")

    return ctx.record(ActionStatus.SKIPPED, "resource_type", resource_name, "already exists")
```

### Integration Steps

1. **Create module**: Add new file in `src/resources/`

2. **Import in reconciler**: Add import to `src/reconciler.py`

3. **Add to pipeline**: Add function call to `_reconcile_present()` in the appropriate position

4. **Add configuration schema**: Document config fields in [CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)

5. **Write tests**: Create test file in `tests/`

6. **Add validation**: If new config fields are needed, add validation to `src/config_validator.py`

---

## See Also

- **[SPECIFICATION.md](SPECIFICATION.md)** — Architecture and design patterns
- **[CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)** — Configuration reference
- **[USER-GUIDE.md](USER-GUIDE.md)** — Operator guide
- **[DESIGN-DECISIONS.md](DESIGN-DECISIONS.md)** — Why things are designed this way
