# OpenStack TenantCtl — Documentation

<!--
**Last Updated**: 2026-04-05
-->

**Project-as-Code for OpenStack** — a declarative tenant provisioning tool that enables
IaaS by automating project creation, network setup, quota configuration, and federation.

---

## Quick Links

| Document | Purpose | Audience |
|----------|---------|----------|
| **[USER-GUIDE.md](USER-GUIDE.md)** | Practical operational guide | Operators, SREs |
| **[CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)** | Complete configuration reference | All users |
| **[SPECIFICATION.md](SPECIFICATION.md)** | Technical architecture specification | Developers, Architects |
| **[DESIGN-DECISIONS.md](DESIGN-DECISIONS.md)** | Architecture decision records | Architects, Developers |
| **[API-REFERENCE.md](API-REFERENCE.md)** | Developer API documentation | Developers, Contributors |
| **[Library Usage](#library-usage)** | Programmatic usage and ConfigSource protocol | Developers |

---

## What It Does

OpenStack TenantCtl turns YAML configuration files into fully provisioned OpenStack projects. One command reconciles the declared state against reality — creating, updating, or skipping resources as needed.

### Managed Resources

| Resource | Capabilities |
|----------|-------------|
| **Projects** | Create/update tenants with descriptions, domain resolution, enablement |
| **Network stacks** | Complete stack — network, subnet (auto-calculated pools from CIDR), router with external gateway, MTU, DHCP, DNS |
| **Quotas** | Compute (cores, RAM, instances), network (FIPs, ports, security groups), block storage (volumes, gigabytes), load balancer (Octavia) |
| **Floating IPs** | Pre-allocate a fixed pool per project, track by ID, detect drift, optionally reclaim released IPs |
| **Pre-allocated networks** | Provision network stack and lock quota to 1 so tenants can't create extras |
| **Security groups** | Baseline rules from presets (ICMP, SSH, HTTP, HTTPS) or custom rule definitions |
| **Group role assignments** | Keystone group-to-project role grants and revocations with placeholder substitution |
| **Federation mapping** | SAML/OIDC identity mapping with deterministic rule ordering, per-project overrides, static rule support |
| **Compute operations** | Shelve/unshelve servers during lifecycle state transitions |
| **Teardown** | Safety-checked project removal — refuses if VMs or volumes still exist |

### Core Properties

| Property | Description |
|----------|-------------|
| **Idempotent** | Safe to run multiple times — only makes necessary changes |
| **Declarative** | Define what you want in YAML, the tool handles provisioning |
| **Inheritable** | Projects inherit from `defaults.yaml`, override only what's different (deep-merge) |
| **Dry-run** | Preview all changes before making them — no OpenStack connection needed |
| **Resilient** | Automatic retry with exponential backoff for transient failures |
| **Isolated** | One project's failure doesn't prevent others from succeeding |
| **Validated** | Comprehensive config validation catches errors before any API calls |
| **Lifecycle-aware** | `present` / `locked` / `absent` state machine with safe transitions |
| **Drift-aware** | Detects and reconciles floating IPs that drifted from desired state |

---

## Architecture Overview

The provisioner follows a **three-phase execution model**:

```
┌─────────────────────────────────────────────────┐
│ Phase 1: Validate                               │
│ • Load defaults.yaml + projects/*.yaml          │
│ • Deep-merge project configs with defaults      │
│ • Validate all fields and constraints           │
│ • Resolve CIDR → gateway IP, allocation pools   │
│ • Exit immediately on errors (fail-fast)        │
└─────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────┐
│ Phase 2: Connect & Resolve                      │
│ • Establish OpenStack connection (with retry)   │
│ • Resolve external network by name → ID         │
│ • Load existing federation mapping              │
│ • Build SharedContext                           │
│ (Skipped entirely in dry-run mode)              │
└─────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────┐
│ Phase 3: Reconcile                               │
│ Before per-project loop:                         │
│   0. Keystone groups → Create groups (group mode)│
│ For each project (error-isolated):               │
│   1. Project       → Create/update tenant        │
│   2. Network stack → Network, subnet, router     │
│   3. Floating IPs  → Allocate and track pool     │
│   4. Pre-alloc net → Network + quota lock        │
│   5. Quotas        → Compute, network, storage   │
│   6. Security group → Create baseline rules      │
│   7. Group roles   → Keystone role grants        │
│   8. Compute ops   → Shelve/unshelve             │
│ After all projects:                              │
│   9. Federation    → Update shared mapping       │
└──────────────────────────────────────────────────┘
```

The `TenantCtl` class orchestrates these three phases. The CLI (`main.py`) is a thin adapter that delegates to `TenantCtl`; for programmatic use, import `TenantCtl` directly.

**Detailed architecture**: [SPECIFICATION.md § Architecture](SPECIFICATION.md#2-architecture)

**Why this design**: [DESIGN-DECISIONS.md § DD-001](DESIGN-DECISIONS.md#dd-001-three-phase-execution-model)

---

## Getting Started

### Installation

```bash
git clone https://github.com/Underknowlege/openstack-tenantctl.git
cd openstack-tenantctl
make install
```

### Configure OpenStack Credentials

```bash
export OS_AUTH_URL=https://openstack.example.com:5000/v3
export OS_PROJECT_NAME=admin
export OS_USERNAME=admin
export OS_PASSWORD=your-password
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
```

### Preview and Provision

```bash
# Preview what would change (no OpenStack connection needed)
.venv/bin/tenantctl --dry-run -v

# Provision all projects
.venv/bin/tenantctl -v

# Provision a single project
.venv/bin/tenantctl --project "Dev Environment" -v

# Debug mode for troubleshooting
.venv/bin/tenantctl --project "Dev Environment" -vv
```

**Detailed setup**: [USER-GUIDE.md § Quick Start](USER-GUIDE.md#quick-start)

---

## CLI Reference

```bash
tenantctl init [--config-dir PATH]

  Bootstrap a starter config directory from bundled sample files.
  --config-dir PATH    Target directory (default: config/)

tenantctl [OPTIONS]

Options:
  --version            Show version and exit
  --config-dir PATH    Path to configuration directory (default: config/)
  --os-cloud NAME      Named cloud from clouds.yaml
  --project NAME       Provision only specified project
  --dry-run            Preview actions without making changes
  --offline            Skip OpenStack connection (use with --dry-run)
  --only SCOPE [...]   Restrict reconciliation to specific resource scopes
  -v, --verbose        Increase verbosity (-v=INFO, -vv=DEBUG)
  --help               Show help message
```

### Common Commands

```bash
# Reconcile all projects (detect and fix drift)
tenantctl -v

# Preview changes for a single project
tenantctl --project production --dry-run -v

# Use a custom config directory
tenantctl --config-dir /path/to/configs -v

# Debug a specific project
tenantctl --project "Dev Environment" -vv
```

---

## Configuration System

### File Structure

```
config/
├── defaults.yaml              # Shared defaults inherited by all projects
├── federation_static.json     # Static federation rules (optional)
└── projects/
    ├── dev.yaml               # Per-project overrides
    ├── staging.yaml
    └── production.yaml
```

### Configuration Inheritance

Projects inherit all settings from `defaults.yaml` and override only what's different. The merge strategy:

- **Dictionaries**: Merged recursively (project values override defaults)
- **Lists**: Replaced entirely (project list replaces default list)
- **Scalars**: Overridden (project value wins)

### Example: defaults.yaml

```yaml
description: "Managed by tenantctl"
enabled: true
reclaim_floating_ips: false

network:
  mtu: 1500
  subnet:
    dns_nameservers: [9.9.9.9]
    dhcp: true

quotas:
  compute:
    cores: -1
    ram: -1
    instances: 30
  network:
    floating_ips: 0
    networks: 1
    subnets: 1
    ports: 50
  block_storage:
    gigabytes: 500
    volumes: 20

security_group:
  name: default
  rules:
    - ICMP
    - SSH

federation:
  issuer: "https://idp.corp/realms/myrealm"
  mapping_id: "my-mapping"
  group_prefix: "/services/openstack/"
  role_assignments:
    - idp_group: member
      roles: [member, load-balancer_member]
```

### Example: Project Override

```yaml
# config/projects/production.yaml
name: production
resource_prefix: prod

network:
  mtu: 9000
  subnet:
    cidr: 10.1.0.0/24
    gateway_ip: 10.1.0.254
    dns_nameservers: [8.8.8.8, 8.8.4.4]

quotas:
  compute:
    cores: 64
    ram: 131072
  network:
    floating_ips: 2
    ports: 100
    security_groups: 20
  block_storage:
    gigabytes: 2000
    volumes: 50

security_group:
  rules:
    - ICMP
    - rule: SSH
      remote_ip_prefix: "10.0.0.0/8"
    - HTTPS

group_role_assignments:
  - group: cloud-admins
    roles: [admin, load-balancer_member]
  - group: "{name}-operators"
    roles: [member]
```

**Result**: Complete project environment with 9000 MTU network, custom DNS, high quotas, restricted SSH access, and role assignments — all other settings (DHCP, federation, base security, description) inherited from defaults.

**Complete schema reference**: [CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)

---

## Project Lifecycle

Projects support three states with safe transitions:

| State | Behavior |
|-------|----------|
| **`present`** (default) | Full provisioning — create/update all resources |
| **`locked`** | Disable project, shelve all active servers, preserve resources |
| **`absent`** | Safety-checked teardown — refuses if VMs or volumes exist |

```
present ←→ locked → absent
```

- **present → locked**: Disables project, shelves all ACTIVE servers
- **locked → present**: Re-enables project, unshelves previously shelved servers
- **locked → absent**: Tears down project (with safety checks)
- **present → absent**: Not allowed (must lock first)

**Details**: [USER-GUIDE.md § Project States](USER-GUIDE.md#project-states)

---

## Operational Procedures

### Add a New Project

```bash
# 1. Copy a template
cp src/sample_config/projects/dev.yaml config/projects/mynewproject.yaml

# 2. Edit: set name, resource_prefix, network.subnet.cidr (must be unique)
vim config/projects/mynewproject.yaml

# 3. Validate
tenantctl --project mynewproject --dry-run -v

# 4. Provision
tenantctl --project mynewproject -v
```

### Update Quotas

```bash
# 1. Edit the project file
vim config/projects/production.yaml

# 2. Preview the change
tenantctl --project production --dry-run -v

# 3. Apply
tenantctl --project production -v
```

### Ensure Compliance (Reconcile All)

```bash
# Run against all projects — idempotent
tenantctl -v

# Expected output when everything matches:
# 0 created, 0 updated, N skipped, 0 failed
```

### Preview Federation Mapping

```bash
# Dry-run with verbose to see generated mapping rules as JSON
tenantctl --dry-run -v
```

### Decommission a Project

```yaml
# 1. Lock the project first (shelves all servers)
state: locked
```

```bash
tenantctl --project myproject -v
```

```yaml
# 2. Then mark absent (tears down after safety check)
state: absent
```

```bash
tenantctl --project myproject -v
```

**Full procedures**: [USER-GUIDE.md § Operational Procedures](USER-GUIDE.md#operational-procedures)

---

## Floating IP Management

The provisioner pre-allocates floating IPs and tracks them by ID for drift detection:

1. **Allocation**: Pre-allocates the desired count from the external network
2. **Tracking**: Persists allocated FIP IDs in state files
3. **Quota lock**: Sets the FIP quota to the desired count (prevents manual allocation)
4. **Drift detection**: On each run, compares tracked IDs against actual OpenStack state
5. **Reconciliation**: Adopts untracked FIPs, optionally reclaims released ones

Enable reclamation per project:

```yaml
reclaim_floating_ips: true
```

**Design rationale**: [DESIGN-DECISIONS.md § DD-014](DESIGN-DECISIONS.md#dd-014-drift-detection--reconciliation)

---

## Federation Configuration

The provisioner generates deterministic SAML/OIDC federation mapping rules from project configurations:

```yaml
# In defaults.yaml or per-project
federation:
  issuer: "https://idp.corp/realms/myrealm"
  mapping_id: "my-mapping"
  group_prefix: "/services/openstack/"
  user_type: "ephemeral"  # Optional: adds "type" to user element in mapping rules
  mode: "group"           # Default mode for entries — "project" (default) or "group"
  role_assignments:
    - idp_group: member
      roles: [member, load-balancer_member]
      # inherits mode: "group" from federation-level default
    - idp_group: admin
      roles: [admin]
      mode: "project"     # per-entry override
```

- **Per-entry mode**: each `role_assignment` entry can use `"project"` or `"group"` mode independently; set a default at the federation level with `mode`
- `"project"` mode (default) uses `{"projects": [...]}` rules; `"group"` uses `{"group": {...}}` rules — recommended when users need access to multiple projects simultaneously (Keystone accumulates group assignments across rules)
- In group mode, tenantctl automatically creates Keystone groups and wires role assignments
- Rules are ordered deterministically for stable diffs
- Per-project overrides for `group_prefix`, `role_assignments`, `issuer`, `user_type`, and `mode`
- Domain-aware: when `domain` is set on a project, rules include `"domain": {"name": "..."}` in the projects element
- `user_type` support: when set (e.g., `"ephemeral"`), rules include `"type"` on the user element — required for cross-domain federated authentication
- Static rules from `federation_static.json` merged into the mapping
- Federation mapping is a shared resource — updated once after all projects

**Configuration details**: [CONFIG-SCHEMA.md § Federation](CONFIG-SCHEMA.md#federation)

---

## Reading Guide by Role

### Operators / SREs

You want to provision and manage projects reliably.

1. **[USER-GUIDE.md](USER-GUIDE.md)** — Setup, daily operations, troubleshooting
2. **[CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)** — Every configuration option explained
3. **[DESIGN-DECISIONS.md § DD-003](DESIGN-DECISIONS.md#dd-003-deep-merge-configuration-inheritance)** — How config inheritance works

Key sections:
- [Quick Start](USER-GUIDE.md#quick-start)
- [Operational Procedures](USER-GUIDE.md#operational-procedures)
- [Best Practices](USER-GUIDE.md#best-practices)
- [Troubleshooting](USER-GUIDE.md#troubleshooting)

### Developers

You want to understand the codebase or add features.

1. **[SPECIFICATION.md](SPECIFICATION.md)** — Architecture, design patterns, internals
2. **[API-REFERENCE.md](API-REFERENCE.md)** — Module APIs, type signatures, extension guide
3. **[DESIGN-DECISIONS.md](DESIGN-DECISIONS.md)** — The "why" behind every decision

Key sections:
- [Core Design Patterns](SPECIFICATION.md#3-core-design-patterns)
- [Core Types](API-REFERENCE.md#1-core-types-srcutils)
- [Creating New Resource Types](API-REFERENCE.md#12-creating-new-resource-types)

### Architects

You want to evaluate the design or understand trade-offs.

1. **[SPECIFICATION.md § Executive Summary](SPECIFICATION.md#1-executive-summary)** — High-level overview
2. **[DESIGN-DECISIONS.md](DESIGN-DECISIONS.md)** — ADRs with context, rationale, and trade-offs
3. **[SPECIFICATION.md § Architecture](SPECIFICATION.md#2-architecture)** — Detailed architecture

Key sections:
- [DD-001: Three-Phase Execution](DESIGN-DECISIONS.md#dd-001-three-phase-execution-model)
- [DD-018: Separate State File](DESIGN-DECISIONS.md#dd-018-separate-state-file)
- [Error Handling & Resilience](SPECIFICATION.md#6-error-handling--resilience)

---

## Source Code Structure

```
src/
├── __init__.py                # Public API re-exports with __all__
├── client.py                  # Library API: TenantCtl class, RunResult
├── context.py                 # Context-building helpers (external networks, federation)
├── main.py                    # Thin CLI adapter delegating to TenantCtl
├── config_loader.py           # Load & deep-merge YAML configs
├── config_resolver.py         # Resolve CIDR pools, gateway IPs
├── config_validator.py        # Comprehensive validation
├── reconciler.py              # Per-project state-dependent dispatch
├── state_store.py             # Persistent state tracking (YamlFileStateStore, InMemoryStateStore)
├── utils.py                   # Action, SharedContext, retry decorator
└── resources/
    ├── project.py             # Project creation/update
    ├── network.py             # Network stack provisioning
    ├── quotas.py              # Compute, network, storage, LB quotas
    ├── security_group.py      # Security group & rule management
    ├── federation.py          # Federation mapping generation
    ├── keystone_groups.py     # Keystone group lifecycle (group-mode federation)
    ├── group_roles.py         # Group-to-role assignments
    ├── compute.py             # Shelve/unshelve operations
    ├── teardown.py            # Safe project deletion
    └── prealloc/
        ├── fip.py             # Floating IP allocation & drift detection
        └── network.py         # Network pre-allocation & quota lock
```

### Key Abstractions

**TenantCtl** — Library entry point wrapping the three-phase pipeline:
- YAML mode: `TenantCtl.from_config_dir("config/").run(dry_run=True)`
- Programmatic mode: `TenantCtl.from_cloud(...)` with `ProjectConfig.build()` and direct injection

**RunResult** — Structured result from `TenantCtl.run()`:
- `actions`, `failed_projects`, `had_connection`

**Universal Resource Pattern** — Every resource module follows the same flow:
```
check dry_run → find existing → create/update/skip → return Action
```

**Action** — The unit of work:
```python
Action(status=CREATED|UPDATED|SKIPPED|FAILED, resource_type, name, details)
```

**SharedContext** — Cross-cutting state holder (internal):
- OpenStack connection, current project, action history, state store

**@retry()** — Decorator on all OpenStack API helpers:
- Exponential backoff, catches transient failures, skips non-retryable errors

**Details**: [API-REFERENCE.md](API-REFERENCE.md)

---

## Library Usage

TenantCtl can be imported and used programmatically via the `TenantCtl` class, which wraps the full three-phase pipeline.

### YAML mode — use existing config directory

```python
from src import TenantCtl

client = TenantCtl.from_config_dir("config/")
result = client.run(dry_run=True)

print(f"{len(result.actions)} actions, {len(result.failed_projects)} failures")
```

### Programmatic mode — inject projects directly

```python
from src import TenantCtl, ProjectConfig, InMemoryStateStore

# Build projects programmatically (auto-populates subnet defaults, domain_id)
proj = ProjectConfig.build(
    name="dev",
    resource_prefix="dev",
    network={"subnet": {"cidr": "10.0.0.0/24"}},
)

# Use InMemoryStateStore for external state management (database, REST API)
store = InMemoryStateStore()
client = TenantCtl.from_cloud("mycloud", state_store=store)
result = client.run(projects=[proj], all_projects=[proj])

# Read updated state for write-back to external system
updated_state = store.snapshot()
```

The `ConfigSource` protocol still exists for custom configuration backends — implement `load_defaults()` and `load_raw_projects()` to back tenantctl with a REST API, database, or any other source. See [API-REFERENCE.md](API-REFERENCE.md) for full details.

---

## Troubleshooting

| Error | Solution |
|-------|----------|
| "External network 'external' not found" | Update `defaults.yaml` with correct external network name (`openstack network list --external`) |
| "CIDR overlap" | Each project needs a unique, non-overlapping CIDR |
| "gateway not inside CIDR" | Ensure gateway IP falls within the subnet CIDR range |
| "Failed to connect to OpenStack" | Verify credentials with `openstack token issue` |
| "quota must be a non-negative integer" | All quota values must be ≥ 0 |
| "Teardown refused: VMs exist" | Shelve or delete VMs before setting `state: absent` |

**Debug mode**: Add `-vv` for DEBUG-level logging with full API call details.

**Full troubleshooting guide**: [USER-GUIDE.md § Troubleshooting](USER-GUIDE.md#troubleshooting)

---

## Project Statistics

| Metric | Value |
|--------|-------|
| **Language** | Python 3.11+ |
| **Dependencies** | openstacksdk 4.x, pyyaml, deepmerge, tenacity |
| **Dev Dependencies** | ruff, mypy, pytest, pytest-mock |
| **Design Decisions** | ADRs documented |

---

## Development

```bash
# Code quality
make fmt           # Format with ruff
make lint          # Lint (ruff + mypy strict)
make test          # Run pytest suite

# Version management
make version       # Show current version
make bump-patch    # x.y.z → x.y.(z+1)
make bump-minor    # x.y.z → x.(y+1).0
make bump-major    # x.y.z → (x+1).0.0
make bump-dry-run  # Preview version bump
make bump-revert   # Undo last version bump
```

Version bumps run quality checks (fmt, lint, test) before proceeding. After bumping, update [CHANGELOG.md](../CHANGELOG.md) and amend the commit.

### Adding a New Resource Type

1. Create `src/resources/myresource.py` following the universal resource pattern
2. Integrate with `reconciler.py` dependency order
3. Add config schema to `config_validator.py`
4. Write comprehensive tests
5. Document in [CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)

**Template and guide**: [API-REFERENCE.md § Creating New Resource Types](API-REFERENCE.md#12-creating-new-resource-types)

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for development workflow, git conventions, code standards, testing requirements, and documentation guidelines.

---

## Version History

See [CHANGELOG.md](../CHANGELOG.md) for detailed release notes.
