# TenantCtl Library API Reference

**Public API for programmatic OpenStack project provisioning**

This document catalogs the public exports from the `src` package, organized by category. For complete internal module documentation, see [API-REFERENCE.md](API-REFERENCE.md).

---

## Table of Contents

- [Quick Start](#quick-start)
- [Core Types](#core-types)
- [Client API](#client-api)
- [Config Loading](#config-loading)
- [Resource Handlers](#resource-handlers)
- [Context Building](#context-building)
- [Config Processing](#config-processing)
- [State Management](#state-management)
- [Utilities](#utilities)

---

## Quick Start

```python
import openstack
from src import TenantCtl

# Basic usage: load from YAML config directory
client = TenantCtl.from_config_dir("config/")
result = client.run()

print(f"Success: {result.success}")
print(f"Actions: {len(result.actions)}")

# Advanced: custom workflows with individual handlers
from src import (
    SharedContext,
    ensure_project,
    ensure_network_stack,
    ensure_quotas,
)

conn = openstack.connect(cloud="mycloud")
ctx = SharedContext(conn=conn, dry_run=False)

for cfg in all_projects:
    action, project_id = ensure_project(cfg, ctx)
    if project_id:
        ensure_network_stack(cfg, project_id, ctx)
        ensure_quotas(cfg, project_id, ctx)
```

---

## Core Types

### `__version__`

**Module version string** following semantic versioning.

```python
import src

print(src.__version__)  # e.g., "0.4.0"
```

---

### `ProjectConfig`

**Frozen dataclass** representing a fully-validated project configuration.

```python
from src import ProjectConfig

# Build from dict (with validation)
cfg = ProjectConfig.build(
    name="my-project",
    state="present",
    domain_id="default",
    description="My OpenStack project",
    network={
        "subnet": {
            "cidr": "10.0.0.0/24",
            "dns_nameservers": ["8.8.8.8"],
        }
    },
    quotas={
        "compute": {"instances": 10, "cores": 20, "ram": 51200},
        "network": {"networks": 1, "subnets": 1, "routers": 1},
    },
)

# Access fields
print(cfg.name)              # "my-project"
print(cfg.state)             # "present"
print(cfg.quotas.compute)    # {"instances": 10, "cores": 20, "ram": 51200}
```

**Key fields:**
- `name: str` - Project name
- `state: str` - `"present"`, `"absent"`, or `"locked"`
- `domain_id: str` - Keystone domain
- `network: NetworkConfig | None` - Network stack
- `quotas: QuotaConfig | None` - Quota limits
- `security_group: SecurityGroupConfig | None` - Security group rules

**See:** [SPECIFICATION.md](SPECIFICATION.md) for complete schema

---

### `DefaultsConfig`

**Frozen dataclass** for pipeline-level defaults.

```python
from src import DefaultsConfig

defaults = DefaultsConfig.from_dict({
    "external_network_name": "public",
    "dns_nameservers": ["8.8.8.8", "8.8.4.4"],
})
```

---

### `SharedContext`

**Mutable dataclass** holding state shared across resource handlers.

```python
from src import SharedContext
import openstack

conn = openstack.connect(cloud="mycloud")
ctx = SharedContext(conn=conn, dry_run=False, external_net_id="net-123")
```

**Key fields:**
- `conn: Connection | None` - OpenStack connection (None = offline mode)
- `dry_run: bool` - Read-only preview mode
- `state_store: StateStore | None` - State persistence
- `external_net_id: str` - Resolved external network ID

**Methods:**
- `ctx.record(status, resource_type, name, details) -> Action`

---

### `Action`

**Frozen dataclass** representing a single provisioning action.

```python
from src import Action, ActionStatus

action = Action(
    status=ActionStatus.CREATED,
    resource_type="network",
    name="my-network",
    details="id=net-123, cidr=10.0.0.0/24",
)
```

---

### `ActionStatus`

**String enum** for action statuses.

```python
from src import ActionStatus

ActionStatus.CREATED   # Resource was created
ActionStatus.UPDATED   # Resource was modified
ActionStatus.SKIPPED   # No changes needed
ActionStatus.FAILED    # Action failed
ActionStatus.DELETED   # Resource was removed
```

---

### `ProvisionerError`

**Base exception** for expected provisioner failures.

```python
from src import ProvisionerError

raise ProvisionerError("No external network configured")
```

---

## Client API

### `TenantCtl`

**Main client class** for running the provisioning pipeline.

```python
from src import TenantCtl

# Load from config directory
client = TenantCtl.from_config_dir("config/")
result = client.run()

# Direct injection (no YAML)
client = TenantCtl.from_cloud(
    cloud="mycloud",
    projects=[cfg1, cfg2],
    defaults=defaults,
)

# Dry-run preview
result = client.run(dry_run=True)

# Filter to specific projects
result = client.run(projects=["project-A"])

# Selective reconciliation
from src import ReconcileScope
result = client.run(only={ReconcileScope.QUOTAS})

# Reuse connection
import openstack
conn = openstack.connect(cloud="mycloud")
result = client.run(connection=conn)
```

---

### `RunResult`

**Frozen dataclass** summarizing pipeline execution.

```python
result = client.run()

print(f"Success: {result.success}")
print(f"Total actions: {len(result.actions)}")
for action in result.actions:
    print(f"{action.status}: {action.resource_type} - {action.details}")
```

---

### `ReconcileScope`

**String enum** for selective handler execution.

```python
from src import ReconcileScope

# Available scopes
ReconcileScope.FIPS
ReconcileScope.QUOTAS
ReconcileScope.NETWORK
ReconcileScope.SECURITY_GROUPS
ReconcileScope.GROUP_ROLE_ASSIGNMENTS
ReconcileScope.FEDERATION

# Only update quotas
client.run(only={ReconcileScope.QUOTAS})
```

---

## Config Loading

### `build_projects`

**Build and validate** project configs from raw dictionaries.

```python
from src import build_projects, RawProject

raw_projects = [
    RawProject(
        source="database",
        state_key="project-A",
        data={"name": "project-A", "state": "present", "domain_id": "default"},
    ),
]

projects, errors = build_projects(raw_projects, defaults_dict)
```

**Returns:** `tuple[list[ProjectConfig], list[str]]`

---

### `RawProject`

**Dataclass** for raw project data with metadata.

```python
from src import RawProject

raw = RawProject(
    source="config/projects/project-A.yaml",
    state_key="project-A",
    data={...},
)
```

---

### `ConfigSource`

**Protocol** for custom config backends (database, REST API).

```python
from src import ConfigSource, RawProject

class DatabaseConfigSource:
    def load_raw_projects(self) -> tuple[list[RawProject], dict]:
        # Load from database
        return raw_projects, defaults_dict
```

---

### `ConfigValidationError`

**Exception** raised when validation fails.

```python
from src import ConfigValidationError

raise ConfigValidationError("Invalid CIDR: not-a-cidr")
```

---

## Resource Handlers

All handlers: `(cfg: ProjectConfig, project_id: str, ctx: SharedContext) -> Action | list[Action]`

See [API-REFERENCE.md](API-REFERENCE.md#12-creating-new-resource-types) for handler patterns.

### `ensure_project`

Create or update Keystone project.

```python
from src import ensure_project

action, project_id = ensure_project(cfg, ctx)
```

**Returns:** `tuple[Action, str]`

---

### `find_existing_project`

Look up existing project without creating it.

```python
from src import find_existing_project

project_id, domain_id = find_existing_project(cfg, ctx)
```

**Returns:** `tuple[str | None, str | None]`

---

### `ensure_network_stack`

Create network, subnet, and router.

```python
from src import ensure_network_stack

action = ensure_network_stack(cfg, project_id, ctx)
```

**Features:**
- Router IP reclamation
- Per-project external network override
- Create-once pattern (skips if exists)

---

### `track_router_ips`

Track router external IPs for reclamation.

```python
from src import track_router_ips

actions = track_router_ips(cfg, project_id, ctx)
```

**Returns:** `list[Action]`

---

### `ensure_quotas`

Set compute, network, and block storage quotas. Sole owner of every quota
write except `floating_ips` (which is managed by `ensure_preallocated_fips`
because that quota is coupled to actual FIP allocation). All Neutron keys —
including `networks`, `subnets`, and `routers` — are written here regardless
of value.

```python
from src import ensure_quotas

actions = ensure_quotas(cfg, project_id, ctx)
```

**Services:**
- Compute (Nova)
- Network (Neutron) — owns `networks`/`subnets`/`routers`/`ports`/etc.
- Load Balancer (Octavia - graceful skip if unavailable)
- Block Storage (Cinder)

**Returns:** `list[Action]`

---

### `ensure_baseline_sg`

Create security group with configured rules.

```python
from src import ensure_baseline_sg

action = ensure_baseline_sg(cfg, project_id, ctx)
```

**Behavior:**
- Non-default SGs: create-once
- Default SG: additive reconciliation

---

### `ensure_group_role_assignments`

Grant or revoke Keystone group roles.

```python
from src import ensure_group_role_assignments

actions = ensure_group_role_assignments(cfg, project_id, ctx)
```

**Supports:** `state: present` (grant) and `state: absent` (revoke)

**Returns:** `list[Action]`

---

### `shelve_all_servers`

Shelve all ACTIVE servers.

```python
from src import shelve_all_servers

actions = shelve_all_servers(cfg, project_id, ctx)
```

**Use case:** `present` → `locked` transition

---

### `unshelve_all_servers`

Unshelve all SHELVED servers.

```python
from src import unshelve_all_servers

actions = unshelve_all_servers(cfg, project_id, ctx)
```

**Use case:** `locked` → `present` transition

---

### `ensure_preallocated_fips`

Pre-allocate and enforce floating IP quota.

```python
from src import ensure_preallocated_fips

actions = ensure_preallocated_fips(cfg, project_id, ctx)
```

**Features:**
- **Scale-up:** Allocates missing FIPs to reach desired count
- **Scale-down:** Releases unused FIPs (blocks on in-use)
- **Drift detection:** Adopts untracked FIPs, releases orphaned ones
- **Reclamation:** Re-allocates specific addresses if `reclaim_floating_ips=True`
- **Quota enforcement:** Sets quota to desired count (fallback to usage if needed)

**Requires:** NETWORK scope (auto-expanded by reconciler)

**Returns:** `list[Action]`

---

### `ensure_preallocated_network`

Ensure the project's pre-allocated network/subnet/router resource exists when
`quotas.network.networks <= 1`. **Does not write quotas** — pair with
`ensure_quotas` (or `TenantCtl.run()`) to configure quota values.

```python
from src import ensure_preallocated_network

actions = ensure_preallocated_network(cfg, project_id, ctx)
```

**Handles:**
- `networks=0`: SKIPPED, no network resource created
- `networks=1`: Ensures network/subnet/router resource exists (idempotent;
  falls back to `ensure_network_stack` if the stack is missing)
- `networks>=2`: SKIPPED — multi-network case is handled by the NETWORK
  scope and quotas by `ensure_quotas`

**Returns:** `list[Action]`

---

### `ensure_federation_mapping`

Build and update Keystone federation mapping from project configs.

```python
from src import ensure_federation_mapping

action = ensure_federation_mapping(all_projects, ctx)
```

⚠️ **IMPORTANT:** Requires ALL projects (not just filtered subset).

**Signature:** `(all_projects: list[ProjectConfig], ctx: SharedContext) -> Action`

**Features:**
- Aggregates federation rules from ALL projects
- Merges with static admin rules (loaded from files)
- Deterministic sorting by `(project_name, idp_group)`
- Supports both "project" and "group" modes
- Idempotent: skips if no changes detected

**Ordering:** Run AFTER per-project reconciliation.

---

### `ensure_keystone_groups`

Create Keystone groups required by group-mode federation.

```python
from src import ensure_keystone_groups

actions = ensure_keystone_groups(all_projects, ctx)
```

⚠️ **IMPORTANT:** Requires ALL projects (not just filtered subset).

**Signature:** `(all_projects: list[ProjectConfig], ctx: SharedContext) -> list[Action]`

**Features:**
- Deduplicates groups across projects (same name created once)
- Domain-aware: creates groups in project's domain
- Idempotent: skips existing groups

**Ordering:** Run BEFORE per-project reconciliation (groups must exist for role assignment).

---

### `augment_group_role_assignments`

Convert group-mode federation into GroupRoleAssignment entries.

```python
from src import augment_group_role_assignments

augmented_cfg = augment_group_role_assignments(cfg)
```

**Signature:** `(cfg: ProjectConfig) -> ProjectConfig`

**Returns:** ProjectConfig with combined role assignments (original + federation-derived).

**Use case:** Call before `ensure_group_role_assignments()` to include federation groups.

---

## Context Building

### `build_external_network_map`

Discover external networks and build lookup map.

```python
from src import build_external_network_map
import openstack

conn = openstack.connect(cloud="mycloud")
net_map = build_external_network_map(conn)

net_id = net_map.get("public")  # Lookup by name
```

**Returns:** `dict[str, str]` - Name→ID and ID→ID map

---

### `resolve_default_external_network`

Pick default external network from map.

```python
from src import resolve_default_external_network

ext_net_id = resolve_default_external_network(net_map, defaults)
```

**Strategy:**
1. Use `defaults.external_network_name` if configured
2. Auto-select if exactly one external network
3. Return empty string if multiple (warns to configure)

---

### `resolve_external_subnet`

Resolve external subnet for router gateways.

```python
from src import resolve_external_subnet

ext_subnet_id = resolve_external_subnet(
    conn,
    ext_net_id="net-123",
    configured_subnet="",  # Empty = auto-discover
)
```

**Strategy:**
1. If configured: validate it belongs to external network
2. If single subnet: use it
3. If multiple: prefer first IPv4 (warns)

**Raises:** `ProvisionerError` if configured subnet invalid

---

### `resolve_project_external_network`

Resolve per-project external network overrides.

```python
from src import resolve_project_external_network

project_net_id, project_subnet_id = resolve_project_external_network(cfg, ctx)
```

**Returns:** `tuple[str, str]`

**Features:**
- Returns global defaults if no override
- Caches results per (network_name, subnet_name)

---

## Config Processing

### `expand_security_group_rules`

Expand SG preset names to full rule dicts.

```python
from src import expand_security_group_rules

project = {
    "security_group": {
        "rules": ["SSH", "HTTP", {"direction": "ingress", ...}]
    }
}

errors = []
expand_security_group_rules(project, errors)
```

**Available presets:**
- `SSH` - TCP 22
- `HTTP` - TCP 80
- `HTTPS` - TCP 443
- `ICMP`, `All TCP`, `All UDP`, `DNS`, `RDP`

---

### `replace_placeholders`

Replace `{name}` placeholders in config.

```python
from src import replace_placeholders

config = {"description": "Project for {name} team"}
expanded = replace_placeholders(config, name="my-project")
# {"description": "Project for my-project team"}
```

---

### `auto_populate_subnet_defaults`

Auto-populate gateway IP and allocation pools from CIDR.

```python
from src import auto_populate_subnet_defaults

project = {
    "network": {"subnet": {"cidr": "10.0.1.0/24"}}
}

auto_populate_subnet_defaults(project)
# Adds gateway_ip="10.0.1.1", allocation_pools=[{"start": "10.0.1.2", "end": "10.0.1.254"}]
```

---

## State Management

### `StateStore`

**Protocol** for state persistence backends.

```python
class CustomStateStore:
    def load(self, state_key: str, path: list[str]) -> Any:
        ...
    def save(self, state_key: str, path: list[str], value: Any) -> None:
        ...
```

---

### `YamlFileStateStore`

**Default** state store using YAML files.

```python
from src import YamlFileStateStore

store = YamlFileStateStore("state/")
```

**Storage:** `state/<state_key>.yaml` per project

---

### `InMemoryStateStore`

**Ephemeral** state store for testing.

```python
from src import InMemoryStateStore

store = InMemoryStateStore()  # State not persisted
```

---

## Utilities

### `retry`

**Decorator** for retrying transient API failures.

```python
from src import retry

@retry()
def fetch_resource(conn, resource_id):
    return conn.service.get_resource(resource_id)

@retry(max_attempts=3, backoff_base=1.0)
def custom_retry(conn):
    ...
```

**Retries on:**
- HTTP 5xx, 429
- Connection errors

**Doesn't retry:** 4xx client errors

**Backoff:** Exponential (default: 2s, 4s, 8s, 16s)

---

### `identity_v3`

**Typed helper** for Keystone v3 API.

```python
from src import identity_v3
import openstack

conn = openstack.connect(cloud="mycloud")
identity = identity_v3(conn)

project = identity.find_project("my-project", domain_id="default")
```

**Why:** Casts `conn.identity` to v3 (v2 removed in 2020)

---

### `find_network`

**Retry-wrapped** network lookup.

```python
from src import find_network

network = find_network(conn, "my-network", project_id="proj-123")
```

**Returns:** `Network | None`

---

### `list_project_servers`

List all servers in a project.

```python
from src import list_project_servers
import openstack

conn = openstack.connect(cloud="prod")
servers = list_project_servers(conn, project_id)

active_servers = [s for s in servers if s.status == "ACTIVE"]
```

**Signature:** `(conn: openstack.connection.Connection, project_id: str) -> list[Server]`

**Use case:** Custom server discovery, lifecycle workflows, inventory management.

---

## Complete Example

```python
"""Complete example: custom provisioning workflow."""

import openstack
from src import (
    ProjectConfig,
    SharedContext,
    build_external_network_map,
    resolve_default_external_network,
    resolve_external_subnet,
    DefaultsConfig,
    ensure_project,
    ensure_network_stack,
    ensure_baseline_sg,
    ensure_quotas,
)

# 1. Build context
conn = openstack.connect(cloud="production")

net_map = build_external_network_map(conn)
defaults = DefaultsConfig.from_dict({"external_network_name": "public"})

ext_net_id = resolve_default_external_network(net_map, defaults)
ext_subnet_id = resolve_external_subnet(conn, ext_net_id, "")

ctx = SharedContext(
    conn=conn,
    dry_run=False,
    external_net_id=ext_net_id,
    external_subnet_id=ext_subnet_id,
    external_network_map=net_map,
)

# 2. Build project config
cfg = ProjectConfig.build(
    name="customer-A",
    state="present",
    domain_id="default",
    network={"subnet": {"cidr": "10.100.0.0/24"}},
    quotas={"compute": {"instances": 20, "cores": 40, "ram": 102400}},
    security_group={"name": "default", "rules": ["SSH", "HTTP", "HTTPS"]},
)

# 3. Provision in correct order
action, project_id = ensure_project(cfg, ctx)
print(f"{action.status}: Project - {action.details}")

ensure_network_stack(cfg, project_id, ctx)
ensure_baseline_sg(cfg, project_id, ctx)

for action in ensure_quotas(cfg, project_id, ctx):
    print(f"{action.status}: {action.resource_type} - {action.details}")
```

---

## See Also

- [API-REFERENCE.md](API-REFERENCE.md) - Complete internal module reference (includes handler patterns)
- [SPECIFICATION.md](SPECIFICATION.md) - YAML config schema
- [examples/](examples/) - Working code examples
