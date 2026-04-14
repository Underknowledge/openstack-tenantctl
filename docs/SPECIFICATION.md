# OpenStack TenantCtl Technical Specification

<!--
**Last Updated**: 2026-04-04
-->

Comprehensive technical specification for OpenStack TenantCtl - a declarative tenant provisioning tool (Project-as-Code) that enables IaaS by automating OpenStack project lifecycle management.

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture](#2-architecture)
3. [Core Design Patterns](#3-core-design-patterns)
4. [Resource Provisioning](#4-resource-provisioning)
5. [Configuration System](#5-configuration-system)
6. [Error Handling & Resilience](#6-error-handling--resilience)
7. [Testing Strategy](#7-testing-strategy)
8. [Operational Characteristics](#8-operational-characteristics)

---

## 1. Executive Summary

### 1.1 Purpose

OpenStack TenantCtl is a Python-based CLI tool and library that provisions and manages OpenStack projects (tenants) declaratively from YAML configuration files. It enables IaaS consumption by automating tenant onboarding — creating projects, setting up base network stacks, configuring quotas, and establishing access control through federation and group roles. The tool ensures idempotent, reliable provisioning of complete project environments.

### 1.2 Key Design Principles

- **Idempotency-First**: All operations are safe to repeat; existing resources are detected and preserved
- **Three-Phase Execution**: Validate → Connect → Reconcile for fail-fast operation
- **Error Isolation**: Per-project failures don't block other projects
- **Declarative Configuration**: Infrastructure as code with YAML
- **Configuration Inheritance**: Deep-merge strategy reduces duplication

### 1.3 Project Metrics

- **Language**: Python 3.11+
- **Primary Dependencies**:
  - `openstacksdk` 4.x - OpenStack API client
  - `pyyaml` - Configuration parsing
  - `deepmerge` - Configuration inheritance
  - `ruff`, `mypy` - Code quality tools
  - `pytest`, `pytest-mock` - Testing framework

### 1.4 Key Features

- **State management** (present, locked, absent) for project lifecycle control
- Complete project lifecycle management (create, update, idempotent reconciliation, teardown)
- Declarative network stack (network, subnet, router with external gateway)
- Comprehensive quota management (compute, network, block storage, load balancers)
- Locked resource pattern (allocate-then-fix-quota for controlled resources)
- Floating IP drift detection and reconciliation
- Router IP tracking and audit trail
- Security group baseline configuration with preset rules
- Group role assignments (Keystone group-to-project role grants)
- Compute server management (shelve/unshelve for state transitions)
- Identity federation mapping (SAML/OIDC integration) with list support
- Safe project teardown with VM/volume safety checks
- Configuration auto-population (gateway IP, allocation pools, domain ID)
- Dry-run mode for safe previewing
- Retry logic with exponential backoff for transient failures
- Extensive configuration validation before provisioning

---

## 2. Architecture

### 2.1 Three-Phase Execution Model

```
┌─────────────────────────────────────────────────────────────────┐
│                       Phase 1: Validate                         │
│                                                                 │
│  • Load defaults.yaml and projects/*.yaml                       │
│  • Deep-merge project configs with defaults                     │
│  • Validate all fields (required, formats, cross-field)         │
│  • Check CIDR overlaps across all projects                      │
│  • Exit immediately on validation errors (fail-fast)            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  Phase 2: Connect & Resolve                     │
│                                                                 │
│  • Establish OpenStack connection (with retry)                  │
│  • Resolve external network ID                                  │
│  • Load existing federation mapping (if exists)                 │
│  • Load static federation rules from JSON                       │
│  • Build SharedContext with resolved resources                  │
│                                                                 │
│  (Skipped in dry-run mode)                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│                      Phase 3: Reconcile                          │
│                                                                  │
│  For each project (state-dependent reconciliation):              │
│                                                                  │
│  IF state == "present":                                          │
│    1. ensure_project(enabled=True)                               │
│    2. ensure_network_stack()    → Network, subnet, router        │
│    3. ensure_preallocated_fips() → Pre-allocate FIPs             │
│    4. ensure_preallocated_network() → Pre-allocate network stack │
│    5. ensure_quotas()           → Set all quotas                 │
│    6. ensure_baseline_sg()      → Security group + rules         │
│    7. ensure_group_role_assignments() → Grant/revoke group roles  │
│    8. unshelve_all_servers()    → Unshelve previously shelved    │
│                                                                  │
│  IF state == "locked":                                           │
│    1. ensure_project(enabled=False)                              │
│    2. shelve_all_servers()     → Shelve ACTIVE servers           │
│                                                                  │
│  IF state == "absent":                                           │
│    1. teardown_project()       → Safe project deletion           │
│                                                                  │
│  After all projects:                                             │
│    → ensure_federation_mapping() → Update shared mapping         │
│                                                                  │
│  Per-project error isolation: failures don't block other         │
│  projects from attempting reconciliation.                        │
└──────────────────────────────────────────────────────────────────┘
```

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-001: Three-Phase Execution Model

### 2.2 Dependency Order Within Projects

Resources are provisioned in state-dependent strict dependency order:

#### For `state: present` (full provisioning):

1. **Project** (enabled) - Must exist before any project-scoped resources
2. **Group Role Assignments** - Grants roles to groups on project
3. **Network Stack** - Required for pre-allocated FIPs
4. **Track Router IPs** - Records router external IPs in state
5. **Pre-allocated FIPs** - Pre-allocated before quota enforcement
6. **Pre-allocated Network** - Quota set to usage after network creation
7. **Quotas** - Set after pre-allocated resources are created (prevents quota conflicts)
8. **Security Group** - Requires project context
9. **Compute Unshelve** - Unshelves previously shelved servers

#### For `state: locked` (minimal provisioning):

1. **Project** (disabled) - Project must be disabled
2. **Compute Shelve** - Shelve all ACTIVE servers

#### For `state: absent` (teardown):

1. **Teardown** - Reverse-order deletion of all resources (see section 4.10)

**Federation** is a shared resource and runs once after all projects are reconciled.

### 2.3 Module Organization

31 Python modules across the codebase:

```
openstack-tenantctl/
├── src/
│   ├── __init__.py          # Public API re-exports with __all__
│   ├── client.py            # Library API: TenantCtl class, RunResult
│   ├── context.py           # Context-building helpers (external networks, federation)
│   ├── main.py              # Thin CLI adapter delegating to TenantCtl
│   ├── config_loader.py     # Configuration loading, merging, validation
│   ├── config_resolver.py   # Configuration resolution and merging
│   ├── config_validator.py  # Configuration validation logic
│   ├── state_store.py       # StateStore protocol + YamlFileStateStore + InMemoryStateStore
│   ├── reconciler.py        # Per-project orchestration, state dispatch
│   ├── unit_parser.py       # Parse quota units (e.g., "10 GiB")
│   ├── utils.py             # SharedContext, Action, retry decorator
│   ├── models/              # Typed configuration models
│   │   ├── __init__.py
│   │   ├── state.py             # State model (present/locked/absent)
│   │   ├── network.py           # NetworkConfig, SubnetConfig
│   │   ├── security.py          # SecurityGroupConfig
│   │   ├── quotas.py            # QuotaConfig
│   │   ├── federation.py        # FederationConfig
│   │   ├── access.py            # FederationRoleAssignment, GroupRoleAssignment
│   │   └── project.py           # ProjectConfig (top-level, with build() factory)
│   └── resources/
│       ├── __init__.py
│       ├── project.py           # Project create/update
│       ├── network.py           # Network, subnet, router
│       ├── quotas.py            # Compute, network, block storage quotas
│       ├── security_group.py    # Security group + rules
│       ├── federation.py        # Federation mapping (shared resource)
│       ├── group_roles.py       # Group role assignments
│       ├── compute.py           # Server shelving/unshelving
│       ├── teardown.py          # Project teardown (state: absent)
│       └── prealloc/
│           ├── __init__.py
│           ├── fip.py           # Floating IP pre-allocation with quota enforcement
│           └── network.py       # Network quota enforcement
├── config/
│   ├── defaults.yaml            # Default configuration
│   ├── federation_static.json   # Static federation rules
│   └── projects/
│       ├── dev_2.yaml           # Project configurations
│       └── prod_2.yaml
└── tests/
    ├── conftest.py              # Shared fixtures (mock conn, context)
    ├── test_main.py
    ├── test_client.py           # TenantCtl library API tests
    ├── test_context.py          # Context-building helper tests
    ├── test_public_api.py       # Public API surface verification
    ├── test_project_build.py    # ProjectConfig.build() tests
    ├── test_in_memory_state_store.py  # InMemoryStateStore tests
    ├── test_config_loader.py
    ├── test_reconciler.py
    └── test_resources/
        ├── test_project.py
        ├── test_network.py
        ├── test_quotas.py
        ├── test_security_group.py
        ├── test_federation.py
        └── test_locked_resources/
            ├── test_fip.py
            └── test_network.py
```

### 2.4 Architecture Diagram

```
┌────────────┐
│   CLI      │  main.py - Thin adapter: parse args, print summary
└─────┬──────┘
      │
      ▼
┌────────────────┐
│   TenantCtl    │  client.py - Library API, pipeline orchestrator
└─────┬──────────┘
      │
      ├─→ [Phase 1] config_loader.py
      │             ├─ Load YAML files
      │             ├─ Deep-merge (deepmerge.Merger)
      │             ├─ Placeholder substitution
      │             └─ Comprehensive validation
      │
      ├─→ [Phase 2] context.py + OpenStack connection (openstacksdk)
      │             ├─ Authenticate with retry
      │             ├─ Resolve external network
      │             ├─ Load federation mapping
      │             └─ Build SharedContext
      │
      └─→ [Phase 3] reconciler.py
                    ├─ For each project (state-dependent):
                    │   │
                    │   ├─ state: present (full provisioning)
                    │   │   ├─ project.py (enabled)
                    │   │   ├─ group_roles.py
                    │   │   ├─ network.py
                    │   │   ├─ prealloc/fip.py
                    │   │   ├─ prealloc/network.py
                    │   │   ├─ quotas.py
                    │   │   ├─ security_group.py
                    │   │   └─ compute.py (unshelve servers)
                    │   │
                    │   ├─ state: locked (minimal provisioning)
                    │   │   ├─ project.py (disabled)
                    │   │   └─ compute.py (shelve servers)
                    │   │
                    │   └─ state: absent (teardown)
                    │       └─ teardown.py (safe deletion)
                    │
                    └─ Shared resources:
                        └─ federation.py
```

---

## 3. Core Design Patterns

### 3.1 Universal Resource Pattern

All resource modules follow the same pattern:

```python
def ensure_<resource>(cfg: ProjectConfig, ctx: SharedContext, ...) -> Action:
    """Ensure <resource> exists with correct configuration.

    Returns an Action recording what happened (CREATED/UPDATED/SKIPPED/FAILED).
    """

    # 1. Extract configuration
    resource_config = cfg.resource_field

    # 2. Dry-run check
    if ctx.dry_run:
        return ctx.record(ActionStatus.SKIPPED, "resource_type", name, "dry-run")

    # 3. Find existing resource (with retry)
    existing = _find_resource(ctx.conn, name)

    # 4. Create if missing
    if existing is None:
        resource = _create_resource(ctx.conn, resource_config)
        logger.info("Created resource %s", name)
        return ctx.record(ActionStatus.CREATED, "resource_type", name, details)

    # 5. Update if configuration changed
    if _needs_update(existing, resource_config):
        resource = _update_resource(ctx.conn, existing.id, resource_config)
        logger.info("Updated resource %s", name)
        return ctx.record(ActionStatus.UPDATED, "resource_type", name, details)

    # 6. Skip if already correct
    logger.debug("Resource %s already up to date", name)
    return ctx.record(ActionStatus.SKIPPED, "resource_type", name, "already up to date")
```

**Key characteristics**:
- Idempotent: safe to run multiple times
- Dry-run aware: early return with SKIPPED status
- Retry-wrapped helpers: `@retry()` decorator on all OpenStack API calls
- Action recording: all outcomes recorded in `SharedContext.actions`

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-004: Universal Resource Pattern

### 3.2 Pre-Allocated Resources Pattern

Some resources (floating IPs, networks) are pre-allocated by the provisioner, with quotas set to enforce the configured limit and drift detection to maintain the desired state.

**Pattern**:
1. Pre-allocate resources up to desired count
2. Persist resource IDs to project state file (e.g., `preallocated_fips`)
3. Set quota to desired count (users can replace resources within quota, but can't exceed count)
4. Track drift: detect and optionally reclaim missing resources

**Important**: This is **pre-allocation with quota enforcement**, not a true lock. Quota is **set to the configured count**, not zero. Users can **replace** resources within this limit (e.g., delete a FIP and allocate a new one) but cannot **exceed** the total count. Drift detection adopts untracked resources and reclaims missing ones when possible.

**Implementation** (`src/resources/prealloc/fip.py`):

```python
def ensure_preallocated_fips(cfg, project_id, ctx):
    desired_count = cfg.quotas.network["floating_ips"]
    existing_fips = list(conn.network.ips(project_id=project_id))

    # Drift detection: adopt untracked, reclaim missing
    if cfg.preallocated_fips:
        _reconcile_fip_drift(cfg, project_id, ctx, cfg.preallocated_fips, existing_fips)
        existing_fips = list(conn.network.ips(project_id=project_id))  # Re-list after drift fix

    if len(existing_fips) == desired_count:
        # Exact match — just set quota to desired count
        _set_fip_quota_and_record(conn, project_id, ctx, desired_count, len(existing_fips))
        return [SKIPPED]

    if len(existing_fips) > desired_count:
        # Scale down — release unused FIPs (port_id is None)
        # In-use FIPs cannot be released → FAILED action
        return _scale_down_fips(cfg, project_id, ctx, existing_fips, ...)

    # Scale up — pre-allocate missing FIPs
    _raise_fip_quota(conn, project_id, desired_count)  # Raise quota
    for _ in range(desired_count - len(existing_fips)):
        fip = conn.network.create_ip(...)

    # Persist all FIPs to state file, then set quota to desired count
    _persist_fips(cfg, ctx, all_fips)
    _set_fip_quota_and_record(conn, project_id, ctx, desired_count, len(all_fips))
```

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-009: Config Writeback for Idempotency, DD-014: Drift Detection & Reconciliation. Note: DD-002 (Allocate-Then-Lock) was rejected; the working pattern is pre-allocation with quota enforcement.

### 3.3 SharedContext Pattern for Cross-Cutting Concerns

`SharedContext` is a dataclass passed to all resource functions, providing:

```python
@dataclass
class SharedContext:
    conn: openstack.connection.Connection | None = None  # OpenStack API client (None in offline dry-run)
    dry_run: bool = False                  # Whether to skip actual changes
    external_net_id: str = ""              # Resolved default external network ID
    external_subnet_id: str = ""           # Resolved default external subnet ID
    external_network_map: dict[str, str] = field(default_factory=dict)  # name→id map of external networks
    current_mapping_rules: list[Any] = field(default_factory=list)  # Federation
    mapping_exists: bool = False           # Whether mapping exists
    static_mapping_rules: list[Any] = field(default_factory=list)   # Static rules
    actions: list[Action] = field(default_factory=list)  # All recorded actions
    failed_projects: list[str] = field(default_factory=list)  # Failed project names
    current_project_id: str = ""           # Project ID being reconciled
    current_project_name: str = ""         # Project name being reconciled
    state_store: StateStore | None = None  # State persistence backend

    def record(
        self, status: ActionStatus, resource_type: str, name: str, details: str = "",
        project_id: str | None = None, project_name: str | None = None,
    ) -> Action:
        """Record an action and return it."""
        action = Action(status=status, resource_type=resource_type, name=name, details=details,
                        project_id=project_id or self.current_project_id,
                        project_name=project_name or self.current_project_name)
        self.actions.append(action)
        return action
```

**Benefits**:
- Avoids passing many individual parameters to every function
- Centralized action recording via `ctx.record()`
- Easy to extend with new cross-cutting concerns

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-006: SharedContext for Cross-Cutting Concerns

### 3.4 Retry with Exponential Backoff

All OpenStack API calls are wrapped with the `@retry()` decorator:

```python
@retry(max_attempts=5, backoff_base=2.0)
def _find_project(conn, name):
    return conn.identity.find_project(name, domain_id="default")
```

**Behavior**:
- **Retryable errors**: HTTP 5xx, HTTP 429, connection errors, `SDKException`
- **Non-retryable errors**: HTTP 4xx client errors (except 429)
- **Backoff schedule**: 2s, 4s, 8s, 16s (exponential)
- **Max attempts**: 5 (configurable)

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-005: Retry with Exponential Backoff

### 3.5 Deep-Merge Configuration Inheritance

Projects inherit configuration from `defaults.yaml` using a deep-merge strategy:

```python
from deepmerge import Merger

_merger = Merger(
    type_strategies=[
        (list, ["override"]),      # Lists: project completely replaces defaults
        (dict, ["merge"]),         # Dicts: merge recursively
        (set, ["override"]),
    ],
    fallback_strategies=["override"],  # Scalars: project wins
    type_conflict_strategies=["override"],
)

# Usage
merged = copy.deepcopy(defaults)
_merger.merge(merged, project_config)
```

**Example**:
```yaml
# defaults.yaml
quotas:
  compute:
    cores: 20
    ram: 51200
  network:
    ports: 50

# project.yaml
quotas:
  compute:
    cores: 16  # Override

# Merged result
quotas:
  compute:
    cores: 16    # From project
    ram: 51200   # From defaults (inherited)
  network:
    ports: 50    # From defaults (inherited)
```

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-003: Deep-Merge Configuration Inheritance

---

## 4. Resource Provisioning

### 4.1 Projects (`src/resources/project.py`)

**Purpose**: Create or update OpenStack project (tenant).

**Configuration**:
```yaml
name: myproject
description: "My project description"
enabled: true
```

**Provisioning logic**:
1. Find project by name in default domain
2. If not found: create with `name`, `description`, `enabled`
3. If found: update `description` and `enabled` if changed
4. Return `(Action, project_id)`

**Idempotency**: Updates only changed fields; skips if already correct.

### 4.2 Network Stack (`src/resources/network.py`)

**Purpose**: Provision network, subnet, and router with external gateway.

**Configuration**:
```yaml
resource_prefix: myproj
network:
  mtu: 1500
  subnet:
    cidr: 10.0.0.0/24
    gateway_ip: 10.0.0.254
    allocation_pools:
      - start: 10.0.0.10
        end: 10.0.0.250
    dns_nameservers: [8.8.8.8]
    dhcp: true
```

**Provisioning logic**:

1. **Network**: `{resource_prefix}-network`
   - Find or create with `mtu`, `project_id`

2. **Subnet**: `{resource_prefix}-subnet`
   - Find or create with `cidr`, `gateway_ip`, `allocation_pools`, `dns_nameservers`, `dhcp`
   - Attached to network

3. **Router**: `{resource_prefix}-router`
   - Find or create
   - Set external gateway to `external_net_id` (if available)
   - Add interface to subnet

**Idempotency**:
- Networks/subnets: immutable after creation (update not supported by OpenStack)
- Router: external gateway and interfaces checked; added if missing

#### 4.2.4 Router IP Tracking (`src/resources/network.py`)

**Purpose**: Track external IP addresses of all project routers for visibility and drift detection.

**Function**: `track_router_ips(cfg, project_id, ctx) -> list[Action]`

**Behavior**:

The `track_router_ips()` function provides visibility into router external IPs by:

1. **Scanning all routers** in the project (not just provisioner-created routers)
2. **Extracting external gateway IPs** from each router's `external_gateway_info`
3. **Comparing with previous snapshot** stored in `cfg.router_ips`
4. **Recording changes** as actions (ADOPTED, IP_CHANGED, REMOVED)
5. **Building audit trail** in `cfg.released_router_ips` for lost/changed IPs

**State file** (`config/state/<project>.state.yaml`, auto-written):
```yaml
# Current router IP snapshot (system-managed)
router_ips:
  - id: "router-uuid"
    name: "myproj-router"
    external_ip: "203.0.113.42"

# Audit trail of released IPs (append-only)
released_router_ips:
  - address: "203.0.113.10"
    router_name: "old-router"
    released_at: "2026-04-01T10:30:00+00:00"
    reason: "router no longer exists"
```

**Provisioning logic**:

1. **List all routers** in the project using `conn.network.routers(project_id=project_id)`
2. **Extract external IPs** using `_get_router_external_ip(router)`:
   - Read `router.external_gateway_info["external_fixed_ips"][0]["ip_address"]`
   - Return `None` if no gateway or no fixed IPs
3. **Build current snapshot**: List of `RouterIpEntry(id, name, external_ip)` for each router with gateway
4. **Compare with previous snapshot** from `cfg.router_ips`:
   - **New routers** (ID not in previous) → ADOPTED action
   - **Removed routers** (ID in previous but not current) → UPDATED action, add to `released_router_ips`
   - **IP changes** (same ID, different IP) → UPDATED action, add old IP to `released_router_ips`
5. **Persist snapshots to state file**:
   - Update `router_ips` with current snapshot (if changed)
   - Append new releases to `released_router_ips` (never cleared)

**Key characteristics**:

- **Project-wide tracking**: Tracks ALL routers in the project, not just those created by provisioner
- **Capture-and-track pattern**: Unlike FIPs (allocate-then-lock), router IPs are allocated by OpenStack when gateway is attached; we just track them
- **Audit trail**: `released_router_ips` provides permanent record of lost or changed IPs
- **No quota locking**: Router IPs are tied to router lifecycle; no separate quota control
- **Idempotent**: Running twice with no changes produces no actions (current == previous)

**Edge cases handled**:

- **No external gateway**: Routers without `external_gateway_info` are not included in snapshot
- **Multiple external IPs**: Only first IP from `external_fixed_ips` array is tracked
  - **Note**: Current implementation doesn't log when multiple IPs are present (improvement opportunity)
- **Deleted routers**: Detected by comparing previous snapshot; moved to audit trail
- **IP drift**: Router gateway IP changes are detected; old IP logged to audit trail with reason

**When called**: Typically invoked in main reconciliation flow after network stack provisioning, or can be called independently to update tracking.

**Comparison with FIP pattern** (see [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-011):

| Aspect | Router IPs | Floating IPs |
|--------|-----------|--------------|
| **Allocation** | Automatic (when gateway attached) | Manual (provisioner allocates) |
| **Pattern** | Capture-and-track | Pre-allocate with quota enforcement |
| **Quota control** | None (tied to router) | Set to desired count |
| **Scope** | All project routers | Only provisioner-allocated |
| **State writeback** | `router_ips` list | `preallocated_fips` list |
| **Audit trail** | `released_router_ips` | `released_fips` |

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-011: Router IP Capture-and-Track Pattern

### 4.3 Quotas (`src/resources/quotas.py`)

**Purpose**: Set resource quotas for compute, network, block storage, and load balancer services.

**Configuration**:
```yaml
quotas:
  compute:
    cores: 20
    ram: 51200
    instances: 10
  network:
    floating_ips: 0
    networks: 1
    ports: 50
    load_balancers: 5       # Octavia quotas (if available)
    listeners: 10
    pools: 10
    health_monitors: 5
    members: 50
  block_storage:
    gigabytes: 500
    volumes: 20
```

**Provisioning logic**:

For each service (compute, network, block_storage):
1. Get current quotas
2. Build update dict with only changed values
3. If any changes: call `update_quota(project_id, **updates)`

**Special handling**:
- **Cinder (block_storage)**: Uses separate connection (`conn.block_storage`)
  - Catches `EndpointNotFound` if block storage service unavailable (graceful degradation)
- **Nova (compute)**: Uses `conn.compute.update_quota_set()`
- **Neutron (network)**: Uses `conn.network.update_quota()`
- **Octavia (load balancers)**: Network quotas include load balancer resources
  - Catches `EndpointNotFound` if Octavia unavailable (some clouds don't have it)

**Quota enforcement**:
- Always sets quota to the **configured desired count**, not to current usage
- For scale-down scenarios (quota < usage): attempts to set desired count, falls back to usage if Neutron refuses, returns FAILED action

**Idempotency**: Only updates changed quota values; skips if all quotas match.

### 4.4 Security Groups (`src/resources/security_group.py`)

**Purpose**: Create or update baseline security group with rules.

**Configuration**:
```yaml
security_group:
  name: default
  rules:
    - direction: ingress
      protocol: tcp
      port_range_min: 22
      port_range_max: 22
      remote_ip_prefix: "0.0.0.0/0"
      description: "Allow SSH"
```

**Security Group Rule Presets**:

Users can specify rules using preset names instead of full dictionaries. Available presets:

```yaml
security_group:
  rules:
    - rule: SSH           # Port 22/tcp
    - rule: HTTP          # Port 80/tcp
    - rule: HTTPS         # Port 443/tcp
    - rule: ICMP          # ICMP protocol
    - rule: "All TCP"     # Ports 1-65535/tcp
    - rule: "All UDP"     # Ports 1-65535/udp
    - rule: DNS           # Port 53/udp
    - rule: RDP           # Port 3389/tcp
```

**Preset overrides**: Preset defaults can be overridden:
```yaml
- rule: SSH
  remote_ip_prefix: "10.0.0.0/8"  # Override default 0.0.0.0/0
  description: "SSH from internal network"
```

**Provisioning logic**:

1. **Security Group**: Find or create by name in project
2. **Rules**:
   - Expand preset rule names to full rule dictionaries
   - Fetch existing rules
   - Match rules by signature: `(direction, protocol, port_range_min, port_range_max, remote_ip_prefix)`
   - Create missing rules
   - Delete extra rules not in configuration

**Idempotency**: Rules reconciled to match configuration exactly.

### 4.5 Pre-Allocated Resources

#### 4.5.1 Floating IPs (`src/resources/prealloc/fip.py`)

**Purpose**: Pre-allocate floating IPs and set quota to desired count to enforce the limit.

**Configuration**:
```yaml
quotas:
  network:
    floating_ips: 2  # Request two FIPs
```

**State file** (`config/state/<project>.state.yaml`, auto-written):
```yaml
preallocated_fips:
  - id: e7b5c8d4-...
    address: 203.0.113.42
    port_id: null
    device_id: null
    device_owner: null
  - id: a1b2c3d4-...
    address: 203.0.113.43
    port_id: null
    device_id: null
    device_owner: null
```

**Provisioning logic**:

1. List existing floating IPs for the project
2. **Drift detection**: Check if FIPs in state match those in OpenStack
   - **Missing FIPs**: In state but deleted from OpenStack → attempt to reclaim (if enabled)
   - **Untracked FIPs**: Exist in OpenStack but not in state → adopt into state
3. If existing count == desired: set quota to desired count, return SKIPPED
4. If existing count > desired (scale-down):
   - Identify unused FIPs (`port_id is None`)
   - Delete up to excess unused FIPs
   - If in-use FIPs prevent reaching desired count → FAILED action
   - Persist remaining FIPs to state file, set quota to max(desired, current usage)
5. If existing count < desired (scale-up):
   - Raise quota to `desired_count`
   - Allocate missing FIPs from external network
   - Persist all FIPs to state file, set quota to desired count

**Drift reconciliation**:
- **ADOPTED**: Untracked FIP added to state file
- **RECLAIMED**: Missing FIP reallocated (if `reclaim_floating_ips: true`) and added back to state file
- **LOST**: Missing FIP could not be reclaimed, moved to `released_fips` audit trail

**Idempotency**: Once pre-allocated, FIPs are persisted to state file; re-runs skip allocation.

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-18: Separate State File for Observed State, DD-014: Drift Detection & Reconciliation. Note: DD-002 (Allocate-Then-Lock) was rejected; the working pattern is pre-allocation with quota enforcement.

#### 4.5.2 Network Quota (`src/resources/prealloc/network.py`)

**Purpose**: Enforce network quota to 1 after network creation (limit additional networks).

**Configuration**:
```yaml
quotas:
  network:
    networks: 1
```

**Provisioning logic**:

1. Network already created by `ensure_network_stack()`
2. Set network quota to 1 (enforce limit on network count)

**Idempotency**: Quota set to 1 on every run (safe, same value).

### 4.6 Federation Mapping (`src/resources/federation.py`)

**Purpose**: Manage shared identity federation mapping for all projects.

**Configuration** (per project):
```yaml
federation:
  issuer: "https://idp.example.com/realms/myrealm"
  mapping_id: "my-mapping"
  group_prefix: "/services/openstack/"
  role_assignments:
    - idp_group: member
      roles: [member, load-balancer_member]
    # List support: all groups map to same roles
    - idp_group: [group1, group2, group3]
      roles: [member]
    # Absolute paths (starting with /) used as-is
    - idp_group: "/global/superadmin"
      roles: [admin]
```

**Group Path Expansion Rules**:

- **Short names** (no leading `/`): Expanded to `{group_prefix}{project_name}/{idp_group}`
  - Example: `idp_group: "admin"` → `"/services/openstack/myproject/admin"`
- **Absolute paths** (starting with `/`): Used as-is, no expansion
  - Example: `idp_group: "/global/superadmin"` → `"/global/superadmin"`
- **List support**: Single `idp_group` can be a list; all groups map to the same roles
  - Useful for mapping multiple IdP groups to the same project roles

**Provisioning logic**:

1. **Collect all project federation configs** (from all projects, not just filtered)
2. **Build dynamic rules** for each project:
   - For each `role_assignment`:
     - If `idp_group` is a list, expand each group separately
     - Resolve group path: `{group_prefix}{project_name}/{idp_group}` (if `idp_group` doesn't start with `/`)
     - Create federation rule: map IdP group → project + roles
3. **Merge with static rules** from `federation_static.json`
4. **Sort rules deterministically** by `(project_name, group_path)` for idempotent ordering
5. **Update mapping** if rules changed (create new mapping or update existing)

**Idempotency**:
- Rules sorted deterministically
- Mapping only updated if rules differ from current state
- Uses `create_mapping()` for new mappings, `update_mapping()` for existing ones

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-008: Federation Mapping as Shared Resource, DD-010: Deterministic Federation Rule Ordering

### 4.7 State Management (`src/reconciler.py`)

**Purpose**: Control project lifecycle state (active, locked, or removed).

**Configuration**:
```yaml
state: present  # Options: present | locked | absent
```

**State definitions**:

#### 4.7.1 `state: present` (Default)

**Full provisioning mode**: Provisions all resources and ensures project is enabled.

**Provisioning order**:
1. Project (enabled)
2. Network stack (network, subnet, router)
3. Pre-allocated FIPs
4. Pre-allocated network quota
5. Quotas (all services)
6. Security groups
7. **Compute servers**: Unshelve any previously shelved servers

**Use case**: Normal operational state for active projects.

#### 4.7.2 `state: locked`

**Reduced provisioning mode**: Disables project and shelves running servers, skips most resource provisioning.

**Provisioning order**:
1. Project (disabled)
2. **Compute servers**: Shelve all ACTIVE servers (persists server IDs to config)
3. **Skip**: Network stack, pre-allocated FIPs, pre-allocated network, quotas, security groups

**Use case**: Temporarily disable a project without deleting resources (e.g., cost savings, security quarantine).

#### 4.7.3 `state: absent`

**Teardown mode**: Safely deletes project and all resources.

**Safety checks** (blocks teardown if failed):
- Detects existing VMs (compute instances) → refuses to delete
- Detects existing volumes (block storage) → refuses to delete

**Teardown order** (reverse dependency order):
1. Security groups
2. Quotas (reset to defaults)
3. Router interfaces
4. Router
5. Subnet
6. Network
7. Floating IPs
8. Project

**Graceful handling**: Skips resources that don't exist (allows partial teardown recovery).

**Use case**: Permanent project removal.

### 4.8 Group Role Assignments (`src/resources/group_roles.py`)

**Purpose**: Grant or revoke Keystone group roles on projects.

**Configuration**:
```yaml
group_role_assignments:
  - group: "my-group"
    roles: [member, admin]
    state: present  # Grant roles
  - group: "old-group"
    roles: [member]
    state: absent   # Revoke roles
```

**Provisioning logic**:

1. **For each assignment**:
   - Find group by name in default domain
   - Find project by ID
   - For each role:
     - If `state: present`: Grant role to group on project (idempotent)
     - If `state: absent`: Revoke role from group on project (idempotent)

**Idempotency**:
- Granting existing role assignments is a no-op
- Revoking non-existent role assignments is a no-op

**Error handling**: Missing groups or roles are logged and recorded as FAILED actions.

### 4.9 Compute Server Management (`src/resources/compute.py`)

**Purpose**: Shelve and unshelve compute servers based on project state.

**Provisioning logic**:

#### Shelving (when `state: locked`):
1. List all servers in the project via `conn.compute.servers(project_id=project_id)`
2. Filter for ACTIVE servers
3. Shelve each ACTIVE server via `conn.compute.shelve_server(server_id)`

**Note**: Only ACTIVE servers are shelved; servers in other states are skipped.

#### Unshelving (when `state: present`):
1. List all servers in the project via `conn.compute.servers(project_id=project_id)`
2. Filter for servers in SHELVED or SHELVED_OFFLOADED state
3. Unshelve each shelved server via `conn.compute.unshelve_server(server_id)`

**Idempotency**:
- Shelving: Only ACTIVE servers are targeted; already-shelved servers are skipped
- Unshelving: Only SHELVED/SHELVED_OFFLOADED servers are targeted; active servers are skipped

**Error handling**: Server operations that fail (e.g., API errors) are logged and recorded as FAILED actions; other servers continue processing.

**Note**: Server states are discovered dynamically from OpenStack on each run; no server IDs are persisted to config or state files.

### 4.10 Project Teardown (`src/resources/teardown.py`)

**Purpose**: Safely delete project and all resources when `state: absent`.

**Safety checks** (blocking):

```python
def _check_vms_exist(conn, project_id) -> bool:
    """Return True if any VMs exist in project."""

def _check_volumes_exist(conn, project_id) -> bool:
    """Return True if any volumes exist in project."""
```

**Teardown refuses to proceed** if VMs or volumes exist, returning FAILED action with explanation.

**Teardown order** (reverse dependency order):

1. **Security groups**: Delete project security groups (except 'default')
2. **Quotas**: Reset all quotas to 0 (or remove quota overrides)
3. **Router interfaces**: Remove subnet interfaces from router
4. **Router**: Delete router
5. **Subnet**: Delete subnet
6. **Network**: Delete network
7. **Floating IPs**: Delete all floating IPs in project
8. **Project**: Delete project

**Graceful degradation**:
- Skips resources that don't exist (e.g., if network already deleted)
- Logs skipped resources at DEBUG level
- Continues teardown even if individual resource deletions fail (logs errors)

**Idempotency**: Running teardown multiple times is safe (resources already deleted are skipped).

**Rationale**: Reverse dependency order ensures child resources are deleted before parents, preventing OpenStack constraint violations.

---

## 5. Configuration System

### 5.1 Directory Structure

```
config/
├── defaults.yaml            # Shared defaults for all projects
├── federation_static.json   # Static federation mapping rules
└── projects/
    ├── dev_2.yaml          # Project-specific configurations
    ├── prod_2.yaml
    └── ...
```

### 5.2 Configuration Inheritance

See section [3.5 Deep-Merge Configuration Inheritance](#35-deep-merge-configuration-inheritance).

**Complete reference**: [CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)

### 5.3 Placeholder Substitution

After deep-merge, all string values are scanned for `{name}` placeholders:

```python
def _replace_placeholders(obj: Any, name: str) -> Any:
    if isinstance(obj, str):
        return obj.format(name=name)
    if isinstance(obj, dict):
        return {k: _replace_placeholders(v, name) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_placeholders(item, name) for item in obj]
    return obj
```

**Example**:
```yaml
name: myproject
description: "Environment for {name}"  # → "Environment for myproject"
```

### 5.4 Auto-Population of Configuration Fields

Several configuration fields are automatically populated if not explicitly provided:

#### 5.4.1 Domain ID (`domain_id`)

**Auto-population sources** (in priority order):
1. Environment variable `OS_PROJECT_DOMAIN_ID`
2. Environment variable `OS_USER_DOMAIN_NAME`
3. Default value: `"default"`

**Usage**: Used for project creation and identity operations.

**Example**:
```bash
export OS_PROJECT_DOMAIN_ID="my-domain"
# Config doesn't need to specify domain_id
```

#### 5.4.2 Gateway IP (`network.subnet.gateway_ip`)

**Auto-calculation**: First usable IP address in the subnet CIDR.

**Example**:
```yaml
network:
  subnet:
    cidr: 10.0.0.0/24
    # gateway_ip auto-calculated as 10.0.0.1
```

**Note**: Can be explicitly overridden in configuration if needed.

#### 5.4.3 Allocation Pools (`network.subnet.allocation_pools`)

**Auto-calculation**: All usable IP addresses in the CIDR, excluding the gateway IP.

**Example**:
```yaml
network:
  subnet:
    cidr: 10.0.0.0/24
    gateway_ip: 10.0.0.1
    # allocation_pools auto-calculated as:
    # - start: 10.0.0.2
    #   end: 10.0.0.254
```

**Note**: Can be explicitly overridden to reserve specific IP ranges.

### 5.5 Schema Validation Rules

See [CONFIG-SCHEMA.md](CONFIG-SCHEMA.md#validation-rules) for complete validation rules.

**Key validations**:
- Required fields present
- Name/prefix format validation
- CIDR validity and overlap detection
- Gateway and allocation pools within CIDR
- Quota values are positive integers
- Federation role assignments structure

**Validation timing**: Phase 1 (before OpenStack connection)

**Failure mode**: Immediate exit with error messages

---

## 6. Error Handling & Resilience

### 6.1 Retry Strategy

**Decorator**: `@retry(max_attempts=5, backoff_base=2.0)`

**Applied to**: All OpenStack API call helpers (`_find_*`, `_create_*`, `_update_*`)

**Retryable exceptions**:
- `openstack.exceptions.HttpException` (5xx, 429)
- `openstack.exceptions.SDKException`
- `requests.exceptions.ConnectionError`
- `ConnectionError`

**Non-retryable exceptions**:
- HTTP 4xx client errors (except 429 rate limit)

**Backoff schedule**: 2s, 4s, 8s, 16s (total max ~30s)

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-005: Retry with Exponential Backoff

### 6.2 Error Isolation

**Per-project isolation** (`src/reconciler.py`):

```python
for cfg in projects:
    project_name = cfg.name
    try:
        _reconcile_project(cfg, ctx)
    except Exception:
        logger.error("Failed to reconcile project %s", project_name, exc_info=True)
        ctx.failed_projects.append(project_name)
        continue  # Don't block other projects
```

**Benefits**:
- One project failure doesn't prevent others from provisioning
- All errors logged with full traceback
- Failed projects listed in final output

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-007: Error Isolation Per Project

### 6.3 Dry-Run Safety

**Three-tier safety**:

1. **No connection created** (Phase 2 skipped in offline mode)
   ```python
   # TenantCtl._setup_context() handles this:
   # offline=True → ctx = SharedContext(conn=None, dry_run=True)
   # offline=False + dry_run=True → connects but only reads
   ```

2. **Early return in resource functions**
   ```python
   if ctx.dry_run:
       return ctx.record(ActionStatus.SKIPPED, "resource", name, "dry-run")
   ```

3. **All actions recorded as SKIPPED**

**Guarantees**: Dry-run mode makes zero OpenStack API calls.

---

## 7. Testing Strategy

### 7.1 Test Coverage

- **Test organization**: Module-aligned test files mirroring `src/` structure

### 7.2 Test Architecture

**Fixtures** (`tests/conftest.py`):

```python
@pytest.fixture
def mock_conn():
    """Mock OpenStack connection with all service namespaces."""
    return Mock(spec=openstack.connection.Connection)

@pytest.fixture
def dry_run_ctx(mock_conn):
    """SharedContext in dry-run mode."""
    return SharedContext(conn=mock_conn, dry_run=True)

@pytest.fixture
def normal_ctx(mock_conn):
    """SharedContext in normal mode."""
    return SharedContext(conn=mock_conn, dry_run=False)
```

### 7.3 Testing Patterns

**Class-based organization**:
```python
class TestEnsureProject:
    def test_creates_project_when_missing(self, normal_ctx, mock_conn):
        # Given: project doesn't exist
        # When: ensure_project()
        # Then: project created, CREATED action recorded

    def test_updates_project_when_changed(self, normal_ctx, mock_conn):
        # Given: project exists with old description
        # When: ensure_project()
        # Then: project updated, UPDATED action recorded

    def test_skips_when_up_to_date(self, normal_ctx, mock_conn):
        # Given: project exists with correct config
        # When: ensure_project()
        # Then: no API calls, SKIPPED action recorded

    def test_dry_run_skips_all_operations(self, dry_run_ctx, mock_conn):
        # Given: dry-run mode
        # When: ensure_project()
        # Then: no API calls, SKIPPED action recorded
```

**Behavior-driven test names**: `test_<action>_<when>_<condition>`

**Mock verification**:
```python
mock_conn.identity.create_project.assert_called_once_with(
    name="myproject",
    domain_id="default",
    description="My description",
    is_enabled=True,
)
```

### 7.4 Running Tests

```bash
# Run all tests
make test

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_resources/test_project.py

# Run specific test
pytest tests/test_resources/test_project.py::TestEnsureProject::test_creates_project_when_missing
```

---

## 8. Operational Characteristics

### 8.1 Idempotency Guarantees

**Definition**: Running the provisioner multiple times with the same configuration produces the same result.

**Implementation**:
- All resource functions check for existing resources before creating
- Updates only applied when configuration differs from current state
- Locked resources persisted to config file (not re-allocated)

**Verification**: Run provisioner twice; second run should show all SKIPPED actions.

### 8.2 Performance Characteristics

**Execution model**: Sequential per-project reconciliation

**Typical timing**:
- Validation (Phase 1): <1s for 10 projects
- Connection (Phase 2): 2-5s (depends on OpenStack API)
- Per-project reconciliation: 3-10s (depends on existing resources)
- Federation mapping: 1-2s

**Retry delays**: Up to 30s per retried call (2s + 4s + 8s + 16s)

**Scalability**: Linear with number of projects; no parallelization currently.

### 8.3 Failure Modes

**Validation failures** (Phase 1):
- Invalid YAML syntax
- Missing required fields
- Invalid CIDR/IP formats
- CIDR overlaps
- **Outcome**: Immediate exit with error messages, no resources provisioned

**Connection failures** (Phase 2):
- OpenStack authentication failure
- External network not found
- Federation mapping errors
- **Outcome**: Exit after retries, no resources provisioned

**Reconciliation failures** (Phase 3):
- Per-project errors (API failures, quota exceeded, etc.)
- **Outcome**: Error logged, project marked failed, other projects continue
- Federation mapping errors
- **Outcome**: Error logged, `__federation__` marked failed

**Partial failure handling**: Failed projects listed in output; exit code 1.

### 8.4 State Persistence

**Purpose**: Persist observed runtime state (FIP IDs, router IPs, audit trails) separately from declarative configuration.

**Implementation** (`src/state_store.py`):

```python
class StateStore(Protocol):
    def load(self, state_key: str) -> dict[str, Any]: ...
    def save(self, state_key: str, key_path: list[str], value: Any) -> None: ...

class YamlFileStateStore:
    """YAML-file-backed implementation. State files live at
    <state_dir>/<state_key>.state.yaml."""
```

**State keys**: `preallocated_fips`, `released_fips`, `router_ips`, `released_router_ips`

**Metadata**: `project_id`, `domain_id`, `last_reconciled_at` (under `metadata` namespace)

**Triggered by**:
- `ensure_preallocated_fips()`: Writes allocated floating IP IDs and addresses
- `track_router_ips()`: Writes router external IPs and release audit trail
- `reconcile()`: Writes metadata (project_id, domain_id, last_reconciled_at)

**State loading**: During config loading (`config_loader.py`), state is loaded from the state file and merged into the in-memory config dict, then converted to typed `ProjectConfig` dataclass instances with state fields accessible via `cfg.preallocated_fips`, `cfg.router_ips`, etc.

**Migration**: State keys found in project YAML but not in the state file are auto-migrated on first run.

**Rationale**: See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md), DD-018: Separate State File for Observed State

### 8.5 Logging Levels

**Levels** (controlled by `-v` flag):

| Verbosity | Flag | Level | Output |
|-----------|------|-------|--------|
| Default | (none) | WARNING | Errors and warnings only |
| Verbose | `-v` | INFO | + Phase transitions, resource creation/updates |
| Debug | `-vv` | DEBUG | + API calls, skip reasons, detailed logic |

**Example**:
```bash
# Minimal output
tenantctl

# Standard operational output
tenantctl -v

# Debugging
tenantctl -vv
```

---

## 9. Related Documentation

- **[USER-GUIDE.md](USER-GUIDE.md)** - Practical guide for operators
- **[CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)** - Complete configuration reference
- **[DESIGN-DECISIONS.md](DESIGN-DECISIONS.md)** - Architecture decision records
- **[API-REFERENCE.md](API-REFERENCE.md)** - Developer API documentation
