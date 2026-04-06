# OpenStack TenantCtl User Guide

<!--
**Last Updated**: 2026-04-05
-->

Practical guide for operators using OpenStack TenantCtl to manage cloud projects.

## Table of Contents

1. [Quick Start](#quick-start)
2. [Configuration Guide](#configuration-guide)
3. [Project States and Lifecycle](#project-states-and-lifecycle)
4. [Command-Line Reference](#command-line-reference)
5. [Understanding Output](#understanding-output)
6. [Operational Procedures](#operational-procedures)
7. [Troubleshooting](#troubleshooting)
8. [Best Practices](#best-practices)

---

## Quick Start

Get started with OpenStack TenantCtl in three steps:

### Step 1: Installation

```bash
# Clone the repository (or download the release)
cd /path/to/openstack-tenantctl

# Create virtual environment and install
make install

# Verify installation
.venv/bin/tenantctl --help
```

**Prerequisites**:
- Python 3.11 or later
- OpenStack credentials configured (see [OpenStack Configuration](#openstack-configuration))

### Step 2: Configure OpenStack Credentials

The provisioner uses standard OpenStack environment variables or `clouds.yaml` configuration.

**Option A: Environment variables**

```bash
export OS_AUTH_URL=https://openstack.example.com:5000/v3
export OS_PROJECT_NAME=admin
export OS_USERNAME=admin
export OS_PASSWORD=your-password
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
```

**Option B: clouds.yaml**

Create `~/.config/openstack/clouds.yaml`:

```yaml
clouds:
  mycloud:
    auth:
      auth_url: https://openstack.example.com:5000/v3
      username: admin
      password: your-password
      project_name: admin
      user_domain_name: Default
      project_domain_name: Default
```

Then set:
```bash
export OS_CLOUD=mycloud
```

### Step 3: Run Your First Dry-Run

```bash
# Preview what would change (connects to cloud, reads current state, shows diffs)
.venv/bin/tenantctl --dry-run -v

# Or preview offline (no cloud connection, generic planned actions)
.venv/bin/tenantctl --dry-run --offline -v
```

**Expected output (online dry-run)**:
```
INFO src.main: Phase 1: Validating configuration
INFO src.config_loader: Loading defaults from config/defaults.yaml
INFO src.config_loader: Loading project file dev_2.yaml
INFO src.config_loader: Loaded 2 project(s) successfully
INFO src.main: Phase 2: Connecting to OpenStack
INFO src.main: Phase 3: Reconciling resources

--- Dry-run: planned changes (live cloud reads) ---
  [CREATED] project: dev_2 (would create, description='Dev project', enabled=True)
  [CREATED] network_stack: dev2-network (would create dev2-network, dev2-subnet, dev2-router)
  [UPDATED] compute_quota: (cores: 10 → 20, ram: 25600 → 51200)
  [SKIPPED] network_quota: (up to date)
  ...

1 created, 1 updated, 13 skipped, 0 failed
```

### Step 4: Provision for Real

Once you've reviewed the dry-run output:

```bash
# Provision all projects
.venv/bin/tenantctl -v

# Or provision a single project
.venv/bin/tenantctl --project dev_2 -v
```

---

## Configuration Guide

### Directory Structure

Your configuration lives in the `config/` directory:

```
config/
├── defaults.yaml              # Shared defaults for all projects
├── federation_static.json     # Static federation rules (if using federation)
└── projects/
    ├── dev_2.yaml            # Individual project configs
    ├── prod_2.yaml
    └── ...
```

### Creating a New Project

**Step 1: Copy a template**

```bash
cp config/projects/dev_2.yaml config/projects/mynewproject.yaml
```

**Step 2: Edit the configuration**

```yaml
# config/projects/mynewproject.yaml
name: mynewproject           # REQUIRED: OpenStack project name
resource_prefix: mynew       # REQUIRED: Prefix for resources (lowercase, no dashes)

network:
  subnet:
    cidr: 192.168.50.0/24          # REQUIRED: Choose unique CIDR
    # gateway_ip and allocation_pools auto-calculated from CIDR

quotas:
  compute:
    cores: 16                # Override defaults as needed
    ram: 32768
```

**Step 3: Validate**

```bash
# Dry-run to validate configuration
.venv/bin/tenantctl --project mynewproject --dry-run -v

# If validation passes, provision it
.venv/bin/tenantctl --project mynewproject -v
```

### Configuration Inheritance

Projects automatically inherit settings from `defaults.yaml`. You only need to override what's different.

**Example**:

```yaml
# defaults.yaml
quotas:
  compute:
    cores: 20
    ram: 51200
    instances: 10
  network:
    ports: 50

security_group:
  rules:
    - direction: ingress
      protocol: icmp
      remote_ip_prefix: "0.0.0.0/0"
```

```yaml
# projects/myproject.yaml
name: myproject
resource_prefix: myproj

quotas:
  compute:
    cores: 8       # Override just cores
    # ram and instances inherited from defaults

network:
  subnet:
    cidr: 10.1.0.0/24
    # gateway_ip and allocation_pools auto-calculated from CIDR
```

**Result**: Your project gets `cores: 8` but inherits `ram: 51200`, `instances: 10`, `network.ports: 50`, and all security group rules from defaults.

**Important**: Lists (like `security_group.rules`) are **replaced entirely**, not merged.

- **Default security group**: Rules are seeded only when the SG is unconfigured (≤4 auto-created rules). Once configured (>4 rules), the SG is left to the project team.
- **Custom security groups**: Created once, then left to project teams

If you specify security group rules in your project configuration, you override all default rules.

### Placeholder Variables

Use `{name}` in configuration values to reference the project name:

```yaml
name: myproject
description: "Development environment for {name}"  # → "Development environment for myproject"

federation:
  group_prefix: "/services/{name}/"  # → "/services/myproject/"
```

### Common Configuration Patterns

#### Minimal Project

```yaml
name: minimal
resource_prefix: min

network:
  subnet:
    cidr: 10.0.0.0/24
    # gateway_ip and allocation_pools auto-calculated from CIDR
```

Everything else inherited from defaults.

#### Development Environment

```yaml
name: dev_environment
resource_prefix: dev

network:
  subnet:
    cidr: 192.168.10.0/24
    # gateway_ip and allocation_pools auto-calculated from CIDR

quotas:
  compute:
    cores: 8
    ram: 16384
    instances: 5
  network:
    floating_ips: 1

security_group:
  rules:
    - direction: ingress
      protocol: tcp
      port_range_min: 22
      port_range_max: 22
      remote_ip_prefix: "10.0.0.0/8"
      description: "SSH from internal only"
```

#### Production Environment

```yaml
name: production
resource_prefix: prod

network:
  subnet:
    cidr: 10.100.0.0/24
    # gateway_ip and allocation_pools auto-calculated from CIDR

quotas:
  compute:
    cores: 32
    ram: 102400
    instances: 20
  network:
    floating_ips: 1
    ports: 100
  block_storage:
    gigabytes: 1000
    volumes: 50

security_group:
  rules:
    - direction: ingress
      protocol: tcp
      port_range_min: 443
      port_range_max: 443
      remote_ip_prefix: "0.0.0.0/0"
      description: "HTTPS from anywhere"
    - direction: ingress
      protocol: tcp
      port_range_min: 22
      port_range_max: 22
      remote_ip_prefix: "10.0.0.0/8"
      description: "SSH from internal"
```

### Network Auto-Calculation

The provisioner automatically calculates `gateway_ip` and `allocation_pools` from the subnet CIDR, simplifying configuration.

**Default Behavior**:

```yaml
network:
  subnet:
    cidr: 10.0.0.0/24
    # gateway_ip and allocation_pools auto-calculated from CIDR
```

**Auto-calculated values**:
- `gateway_ip`: First usable IP in the range (network_address + 1) → `10.0.0.1`
- `allocation_pools`: All usable IPs except the gateway → `10.0.0.2` to `10.0.0.254`

**When to Override**:

You may want custom values when:
- Using a non-standard gateway placement (e.g., last IP instead of first)
- Reserving IP ranges for specific purposes
- Working with existing network constraints

**Override Example**:

```yaml
network:
  subnet:
    cidr: 10.0.0.0/24
    gateway_ip: 10.0.0.254         # Custom: use last IP instead of first
    allocation_pools:
      - start: 10.0.0.10
        end: 10.0.0.200            # Reserve .1-.9 and .201-.254 for other uses
```

**Recommendation**: Use auto-calculation by default. Only override when you have specific networking requirements.

### Group Role Assignments

Grant OpenStack Keystone groups specific roles on projects. This provides direct group-based access control.

**Purpose**: Assign roles to OpenStack groups (created in Keystone) on projects, enabling centralized access management.

**vs Federation**:
- **Group roles**: Static OpenStack groups (created in Keystone) granted directly
- **Federation**: Dynamic mapping from external IdP groups to OpenStack roles

**State Management**:
- `state: present` (default): Grant role to group on project
- `state: absent`: Revoke role from group on project

**Common Patterns**:

1. **Admin groups in defaults.yaml** (applied to all projects):
   ```yaml
   # config/defaults.yaml
   group_role_assignments:
     - group: cloud-admins
       roles: [admin]
   ```

2. **Project-specific operators** (in project YAML):
   ```yaml
   # config/projects/dev_2.yaml
   group_role_assignments:
     - group: "{name}-operators"  # Expands to "dev_2-operators"
       roles: [member, load-balancer_member]
     - group: "{name}-viewers"
       roles: [reader]
   ```

3. **Revoking access**:
   ```yaml
   group_role_assignments:
     - group: legacy-ops
       roles: [member]
       state: absent  # Revoke this group's access
   ```

**Placeholder Support**: Use `{name}` to reference the project name, enabling template-based group names like `{name}-operators`.

**Example Configuration**:

```yaml
# In defaults.yaml (all projects)
group_role_assignments:
  - group: cloud-admins
    roles: [admin]
  - group: network-operators
    roles: [member]

# In projects/production.yaml (project-specific)
group_role_assignments:
  - group: "production-operators"
    roles: [member, load-balancer_member, heat_stack_owner]
  - group: "production-viewers"
    roles: [reader]
```

### State Files

The provisioner stores observed runtime state in separate files under `config/state/`, keeping your project YAML files purely declarative. Each project gets a state file named after its config file:

```
config/
├── projects/
│   └── dev-team.yaml          # Declarative config (you edit this)
└── state/
    └── dev-team.state.yaml    # Observed state (provisioner manages this)
```

**Do NOT manually edit state files.** They contain:

1. **`preallocated_fips`**: Pre-allocated floating IPs
2. **`router_ips`**: Current external IPs assigned to project routers
3. **`released_fips`** / **`released_router_ips`**: Audit trail of released IPs with timestamps
4. **`metadata`**: Project ID, domain ID, and last reconciliation timestamp

**Purpose**:
- **Drift detection**: Compare expected vs actual state
- **Audit trail**: Track IP lifecycle and changes over time
- **Idempotent operations**: Skip allocation if resources already exist at desired count

**Version control**: You can add `config/state/` to `.gitignore` if you prefer not to track state in version control. The provisioner will recreate state files from OpenStack on the next run.

**Migration**: If your project YAML files contain state keys (`preallocated_fips`, `router_ips`, etc.) from a previous version, the provisioner will automatically migrate them to state files on the first run.

---

## Project States and Lifecycle

The provisioner supports three distinct project states, each with different behaviors for managing project lifecycles.

### State Overview

| State | Purpose | Resource Behavior | Server Behavior |
|-------|---------|-------------------|-----------------|
| `present` | Normal operation (default) | Full provisioning | Unshelves previously shelved servers |
| `locked` | Temporary disable | Skips provisioning | Shelves ACTIVE servers, disables project |
| `absent` | Safe decommissioning | Safety-checked deletion | Prevents deletion if servers exist |

### State: present (Default)

**Normal operation mode**. The provisioner fully reconciles all resources.

**Behavior**:
- Creates/updates project, network stack, quotas, security groups
- Grants federation and group role assignments
- Unshelves servers that were previously shelved (handles `locked` → `present` transition)
- Standard idempotent provisioning

**Configuration**:
```yaml
# config/projects/myproject.yaml
name: myproject
state: present  # Optional - this is the default
resource_prefix: myproj

network:
  subnet:
    cidr: 10.0.0.0/24

quotas:
  compute:
    cores: 16
```

**Use Cases**:
- Active projects in regular use
- Initial provisioning of new projects
- Re-enabling a previously locked project

### State: locked

**Temporary disable mode**. Reduces resource consumption while preserving project infrastructure.

**Behavior**:
- Disables the project in Keystone (`enabled: false`)
- **Shelves all ACTIVE servers** (preserves VM state while freeing compute resources)
- Skips network, quota, and security group provisioning
- Revokes federation mappings and group role assignments (if configured)

**Configuration**:
```yaml
# config/projects/myproject.yaml
name: myproject
state: locked  # Temporarily disable this project
resource_prefix: myproj

# All other configuration remains (network, quotas, etc.)
# but won't be provisioned while in locked state
```

**Use Cases**:
- Seasonal projects (disable during off-season)
- Cost reduction (shelved VMs consume less resources)
- Maintenance windows (temporarily disable access)
- Projects pending budget approval

**Server Shelving**:
- ACTIVE servers are automatically shelved (preserves disk, releases compute)
- Already-shelved servers remain shelved (idempotent)
- Other server states (STOPPED, ERROR, etc.) are left unchanged

**State Transitions**:

**locked → present**: Automatically unshelves servers and re-enables project
```yaml
# Step 1: Lock the project
state: locked

# Run provisioner - servers are shelved

# Step 2: Re-enable the project
state: present  # or remove the line (defaults to present)

# Run provisioner - servers are unshelved, project re-enabled
```

### State: absent

**Safe decommissioning mode**. Deletes resources with safety checks and reverse dependency ordering.

**Behavior**:
- **Safety check**: Prevents deletion if servers or volumes exist
- Revokes group role assignments and federation mappings
- Deletes resources in reverse dependency order:
  1. Security group rules
  2. Router interfaces
  3. Router
  4. Subnet
  5. Network
  6. Project
- Returns `DELETED` action status for each removed resource

**Configuration**:
```yaml
# config/projects/oldproject.yaml
name: oldproject
state: absent  # Mark for safe deletion
resource_prefix: old

# All other configuration remains for reference
```

**Safety Checks**:

The provisioner will **refuse to delete** if:
- Any servers exist in the project (any state: ACTIVE, STOPPED, SHELVED, ERROR, etc.)
- Any volumes exist in the project

**Error Example**:
```
ERROR: Cannot delete project 'oldproject': 3 servers still exist
ERROR: Cannot delete project 'oldproject': 5 volumes still exist
```

**Decommissioning Workflow**:

1. **Clean up resources first**:
   ```bash
   # Delete all servers
   openstack server list --project oldproject
   openstack server delete <server-id> ...

   # Delete all volumes
   openstack volume list --project oldproject
   openstack volume delete <volume-id> ...
   ```

2. **Set state to absent**:
   ```yaml
   # config/projects/oldproject.yaml
   state: absent
   ```

3. **Run provisioner**:
   ```bash
   tenantctl --project oldproject -v
   ```

4. **Verify deletion**:
   ```
   [DELETED] security_group_rule: oldproject (SSH rule)
   [DELETED] security_group_rule: oldproject (ICMP rule)
   [DELETED] router_interface: old-router (subnet detached)
   [DELETED] router: old-router
   [DELETED] subnet: old-subnet
   [DELETED] network: old-network
   [DELETED] project: oldproject

   0 created, 0 updated, 0 skipped, 7 deleted
   ```

5. **Remove config file** (optional):
   ```bash
   # Keep for audit trail or remove
   rm config/projects/oldproject.yaml
   ```

**Use Cases**:
- Permanent project removal
- Cleanup of test/development environments
- Decommissioning after project completion

**Important Notes**:
- Deletion is permanent and cannot be undone
- Safety checks protect against accidental data loss
- Audit trail is preserved in provisioner logs
- Config file can be kept for historical reference

### State Transition Diagram

```
┌─────────┐
│ present │ ←──┐
│(default)│    │
└────┬────┘    │
     │         │
     │ set     │ set
     │ state:  │ state:
     │ locked  │ present
     │         │
     ▼         │
┌─────────┐   │
│ locked  │───┘
│(temp)   │
└─────────┘

     │
     │ manually clean up
     │ servers/volumes
     │ then set state: absent
     ▼
┌─────────┐
│ absent  │ (one-way - project deleted)
│(final)  │
└─────────┘
```

**Key Points**:
- `present` ↔ `locked`: Reversible, preserves all infrastructure
- `present`/`locked` → `absent`: One-way, requires manual cleanup first
- Servers are automatically shelved/unshelved during `present` ↔ `locked` transitions

---

## Command-Line Reference

### Synopsis

```bash
tenantctl [OPTIONS]
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--config-dir PATH` | Path to configuration directory | `config/` |
| `--os-cloud NAME` | Named cloud from clouds.yaml | (use env vars) |
| `--project NAME` | Provision only specified project | (all projects) |
| `--dry-run` | Preview actions without making changes | `false` |
| `--offline` | Skip OpenStack connection (only with --dry-run) | `false` |
| `-v, --verbose` | Increase verbosity (repeat for more detail) | WARNING level |

### Verbosity Levels

| Flag | Level | Output |
|------|-------|--------|
| (none) | WARNING | Errors and warnings only |
| `-v` | INFO | + Resource creation/updates, phase transitions |
| `-vv` | DEBUG | + API calls, detailed logic, skip reasons |

### Usage Examples

**Preview all changes (live cloud reads, field-level diffs)**:
```bash
tenantctl --dry-run -v
```

**Preview changes offline (no cloud connection)**:
```bash
tenantctl --dry-run --offline -v
```

**Provision all projects**:
```bash
tenantctl -v
```

**Provision single project**:
```bash
tenantctl --project dev_2 -v
```

**Use custom config directory**:
```bash
tenantctl --config-dir /path/to/configs -v
```

**Debug mode** (maximum verbosity):
```bash
tenantctl --project dev_2 -vv
```

**Production run** (minimal output):
```bash
tenantctl
```

---

## Understanding Output

### Action Types

The provisioner outputs actions for each resource:

| Status | Meaning |
|--------|---------|
| `CREATED` | Resource was created (didn't exist before) |
| `UPDATED` | Resource existed but was updated with new configuration |
| `DELETED` | Resource was deleted (appears during teardown with state: absent) |
| `SKIPPED` | Resource already matches configuration (no change needed) |
| `FAILED` | Operation failed (error logged) |

### Example Output

```
INFO src.main: Phase 1: Validating configuration
INFO src.config_loader: Loading defaults from config/defaults.yaml
INFO src.config_loader: Loading project file dev_2.yaml
INFO src.config_loader: Loaded 1 project(s) successfully

INFO src.main: Phase 2: Connecting to OpenStack and resolving shared resources
INFO src.main: Resolved external network 'external' -> abc123...

INFO src.main: Phase 3: Reconciling resources
INFO src.reconciler: Reconciling project: dev_2
INFO src.resources.project: Created project dev_2 (def456...)
INFO src.resources.network: Created network dev2-network
INFO src.resources.network: Created subnet dev2-subnet
INFO src.resources.network: Created router dev2-router

  [CREATED] project: dev_2 (id=def456...)
  [CREATED] network: dev2-network
  [CREATED] subnet: dev2-subnet
  [CREATED] router: dev2-router
  [SKIPPED] preallocated_fip: dev_2 (no floating IPs requested)
  [SKIPPED] preallocated_network: dev_2 (quota already set)
  [CREATED] quotas: dev_2
  [CREATED] security_group: default
  [CREATED] security_group_rule: default (Allow ICMP)
  [CREATED] security_group_rule: default (Allow SSH)
  [SKIPPED] federation_mapping: my-mapping (no changes)

11 created, 0 updated, 4 skipped, 0 failed
```

### Summary Line

The final line shows counts:
```
11 created, 0 updated, 4 skipped, 0 failed
```

- **0 failed**: Success! All resources provisioned correctly.
- **>0 failed**: Check error messages above for details.

### Idempotent Runs

When you run the provisioner again with the same configuration:

```
  [SKIPPED] project: dev_2 (already up to date)
  [SKIPPED] network: dev2-network (immutable)
  [SKIPPED] subnet: dev2-subnet (immutable)
  [SKIPPED] router: dev2-router (already configured)
  ...

0 created, 0 updated, 15 skipped, 0 failed
```

This confirms idempotency: running multiple times is safe and makes no unnecessary changes.

---

## Operational Procedures

### Initial Deployment

**Step 1: Prepare configuration**

1. Review and customize `config/defaults.yaml`
2. Create project configurations in `config/projects/`
3. Ensure unique CIDRs for each project (no overlaps)

**Step 2: Validate**

```bash
tenantctl --dry-run -v
```

Review output carefully. Ensure all projects show expected resources.

**Step 3: Provision**

```bash
tenantctl -v > provisioning.log 2>&1
```

Monitor the log for errors. Address any failures before proceeding.

**Step 4: Verify**

```bash
# List projects
openstack project list

# Check specific project
openstack network list --project dev_2
openstack quota show dev_2
```

### Adding New Projects

**Workflow**:

1. Create new YAML file in `config/projects/`
2. Dry-run to validate:
   ```bash
   tenantctl --project newproject --dry-run -v
   ```
3. Provision:
   ```bash
   tenantctl --project newproject -v
   ```
4. Verify in OpenStack

**Tip**: Use `--project` flag to provision only the new project, avoiding unnecessary reconciliation of existing projects.

### Updating Existing Projects

**Scenario**: Increase quota for an existing project.

**Workflow**:

1. Edit project YAML file:
   ```yaml
   quotas:
     compute:
       cores: 32  # Was 16
   ```

2. Dry-run to preview:
   ```bash
   tenantctl --project myproject --dry-run -v
   ```

3. Apply update:
   ```bash
   tenantctl --project myproject -v
   ```

**Expected output**:
```
  [SKIPPED] project: myproject (already up to date)
  [SKIPPED] network: myproject-network (immutable)
  [UPDATED] quotas: myproject
  [SKIPPED] security_group: default (already up to date)
```

### Pre-Allocated Floating IPs

**Provisioning** with floating IPs:

```yaml
# config/projects/myproject.yaml
quotas:
  network:
    floating_ips: 2  # Desired number of FIPs
```

**First run**:
```bash
tenantctl --project myproject -v
```

**What happens**:
1. Provisioner allocates two floating IPs from the external network
2. Writes IP details to the state file `config/state/myproject.state.yaml`:
   ```yaml
   preallocated_fips:
     - id: e7b5c8d4-...
       address: 203.0.113.42
       project_id: xyz789...
     - id: a1b2c3d4-...
       address: 203.0.113.43
       project_id: xyz789...
   ```
3. Sets the OpenStack quota to the desired count (2 in this case, preventing users from allocating additional FIPs)

**Important**: The config value `floating_ips: 2` represents the desired count and never changes. The quota lock happens internally in OpenStack to prevent users from allocating additional FIPs beyond this count.

**Subsequent runs**:
- Provisioner sees FIPs already allocated at desired count
- Skips allocation, ensures quota remains set

**Scaling down**: If you reduce `floating_ips` (e.g., from 3 to 1), the provisioner releases unused FIPs automatically. FIPs that are attached to a port (in use) cannot be released — the provisioner reports a FAILED action for those.

**Scaling up**: If you increase `floating_ips`, the provisioner allocates the additional FIPs on the next run.

**Drift handling**: If a tracked FIP is deleted externally (outside the provisioner), the provisioner detects the drift and by default releases it gracefully — recording it in `released_fips` in the state file and allocating a new (different) FIP to maintain the desired count. If you need to preserve the *exact same* IP address (for DNS records, firewall rules, or partner allowlists), enable reclamation:

```yaml
# config/projects/critical-prod.yaml
reclaim_floating_ips: true  # Attempt to re-allocate the same IP address
quotas:
  network:
    floating_ips: 2
```

With `reclaim_floating_ips: true`, the provisioner attempts to re-allocate the exact same address. If the address is already taken by another project, the FIP is moved to `released_fips` with a FAILED action.

**Router IP reclamation**: The same pattern applies to router external IPs. If a project's network stack is deleted and recreated (e.g., after teardown and re-provisioning), the router normally gets a new external IP. Enable `reclaim_router_ips: true` to request the same IP:

```yaml
# config/projects/critical-prod.yaml
reclaim_floating_ips: true   # Reclaim FIP addresses
reclaim_router_ips: true     # Reclaim router external IP
```

Reclaim only fires when creating a brand-new network stack (no existing network in the project). If the previous IP is already taken, the router falls back to normal allocation and the old IP is recorded in `released_router_ips`.

**FIP change tracking**: If downstream systems (NFS export services, monitoring, CMDB sync) need to know the current FIP addresses/IDs, enable `track_fip_changes` on specific projects. When a user swaps a FIP (releases one, allocates a new one) without changing the total count, the provisioner detects the ID mismatch and writes the updated FIP list plus a `fip_tracking_snapshot` (timestamp, quota, allocated count) to the state file.

```yaml
# config/projects/prod.yaml — enable only where needed
track_fip_changes: true
reclaim_floating_ips: true
quotas:
  network:
    floating_ips: 2
```

This is opt-in per project (`false` by default in `defaults.yaml`). Projects without the flag are unaffected.

**Important**: Don't manually edit the state files under `config/state/`. They're managed by the provisioner.

### Project Lifecycle Management

The provisioner provides safe, managed approaches for temporarily disabling or permanently removing projects.

#### Temporary Disable: state: locked

**Use when**: You need to temporarily disable a project while preserving all infrastructure (maintenance, cost reduction, seasonal projects).

**Configuration**:
```yaml
# config/projects/myproject.yaml
state: locked
```

**What happens**:
1. Project is disabled in Keystone (`enabled: false`)
2. All ACTIVE servers are automatically shelved (reduces resource consumption)
3. Network, quota, and security group provisioning is skipped
4. Federation and group role assignments are revoked (if configured)

**Re-enabling**:
```yaml
# Change state back to present (or remove the line - defaults to present)
state: present
```

On next run:
- Project is re-enabled
- Servers are automatically unshelved
- Full provisioning resumes

#### Permanent Removal: state: absent

**Use when**: You need to permanently decommission a project with safety checks and proper cleanup ordering.

**Important**: The provisioner **will refuse to delete** if servers or volumes exist. You must clean up these resources first.

**Decommissioning Workflow**:

**Step 1: Clean up servers and volumes**
```bash
# List and delete all servers
openstack server list --project oldproject
openstack server delete <server-id> <server-id> ...

# List and delete all volumes
openstack volume list --project oldproject
openstack volume delete <volume-id> <volume-id> ...
```

**Step 2: Set state to absent**
```yaml
# config/projects/oldproject.yaml
state: absent  # Mark for deletion
```

**Step 3: Run provisioner**
```bash
tenantctl --project oldproject -v
```

**What happens**:
1. **Safety check**: Verifies no servers/volumes exist (fails if found)
2. **Revoke access**: Removes group role assignments and federation mappings
3. **Delete resources** in reverse dependency order:
   - Security group rules
   - Router interfaces (detach subnet)
   - Router
   - Subnet
   - Network
   - Project

**Output example**:
```
[DELETED] security_group_rule: oldproject (SSH)
[DELETED] security_group_rule: oldproject (ICMP)
[DELETED] router_interface: old-router (subnet detached)
[DELETED] router: old-router
[DELETED] subnet: old-subnet
[DELETED] network: old-network
[DELETED] project: oldproject

7 deleted, 0 created, 0 updated, 0 skipped, 0 failed
```

**Step 4: Remove config file** (optional)
```bash
# Keep for audit trail or remove
rm config/projects/oldproject.yaml
```

#### Safety Checks

**Attempting deletion with servers/volumes present**:
```bash
$ tenantctl --project oldproject -v
ERROR: Cannot delete project 'oldproject': 3 servers still exist
ERROR: Cannot delete project 'oldproject': 5 volumes still exist
```

The provisioner **will not proceed** until all servers and volumes are manually removed.

**Why**: Prevents accidental data loss. You must explicitly delete servers/volumes before the provisioner will remove the project infrastructure.

#### Summary

| Goal | Configuration | Behavior | Reversible |
|------|---------------|----------|------------|
| **Temporary disable** | `state: locked` | Shelves VMs, disables project, skips provisioning | ✅ Yes |
| **Permanent removal** | `state: absent` | Safety-checked deletion of all resources | ❌ No |
| **Stop managing** | Delete YAML file | Provisioner ignores, resources remain in OpenStack | N/A |

---

## Troubleshooting

### Common Errors

#### Error: "External network 'external' not found"

**Cause**: The external network specified in `defaults.yaml` doesn't exist.

**Solution**:

1. List available external networks:
   ```bash
   openstack network list --external
   ```

2. Update `defaults.yaml`:
   ```yaml
   external_network_name: "public"  # Use actual external network name
   ```

3. Re-run provisioner

**Impact**: Routers will be created without external gateway if external network isn't found (warning, not fatal).

---

#### Warning: "Multiple external subnets found, auto-selected first IPv4"

**Cause**: Your external network has multiple subnets (e.g., different IP ranges for different regions) and no `external_network_subnet` is configured.

**Why this matters**: Routers and floating IPs must use the same subnet for correct routing. Without this setting, OpenStack may allocate them from different subnets, causing instances to be unreachable from the internet.

**Solution**:

1. List available subnets in the external network:
   ```bash
   openstack subnet list --network <external-network-name>
   # Example output:
   # +--------------------------------------+-----------+--------------------------------------+-----------------+
   # | ID                                   | Name      | Network                              | Subnet          |
   # +--------------------------------------+-----------+--------------------------------------+-----------------+
   # | 069c4312-639d-442b-8608-88931b2b043d | external0 | 511e1d0b-9732-418d-88ac-cab5d2d4b03a | 193.x.x.x/24    |
   # | 8f2d3e4f-1234-5678-9abc-def012345678 | external1 | 511e1d0b-9732-418d-88ac-cab5d2d4b03a | 78.x.x.x/24     |
   # +--------------------------------------+-----------+--------------------------------------+-----------------+
   ```

2. Update `defaults.yaml` with the subnet you want to use:
   ```yaml
   external_network_name: "public"
   external_network_subnet: "external0"  # Use name or UUID
   ```

3. Re-run provisioner

**Validation**: tenantctl will verify that the subnet belongs to the external network and fail fast with a clear error message if they don't match. This prevents configuration mistakes.

**Note**: If tenantctl detects multiple subnets, it will auto-select the first IPv4 subnet and log a warning. Check the logs for: `"Multiple external subnets found"`.

---

#### Error: "CIDR overlap: dev_2 (192.168.30.0/24) overlaps with prod_2 (192.168.0.0/16)"

**Cause**: Two projects have overlapping network CIDRs and `enforce_unique_cidrs: true` is set in `defaults.yaml`.

**Solution**:

1. If your environment requires unique CIDRs (cross-project routing, VPN peering): choose non-overlapping CIDRs for each project
2. If overlapping CIDRs are acceptable (isolated overlay networks): remove or set `enforce_unique_cidrs: false` in `defaults.yaml`
3. Re-run provisioner

> **Note**: This check is opt-in. OpenStack uses overlay networks, so overlapping CIDRs are safe by default. Enable `enforce_unique_cidrs: true` only when your environment needs globally unique CIDRs.

**Prevention** (when enforcement is enabled): Use `/24` subnets from different `/16` ranges:
- Dev projects: `10.1.0.0/24`, `10.2.0.0/24`, ...
- Prod projects: `10.100.0.0/24`, `10.101.0.0/24`, ...

---

#### Error: "gateway 192.168.30.1 is not inside CIDR 192.168.31.0/24"

**Cause**: Gateway IP is outside the subnet CIDR.

**Solution**:

Ensure gateway is within the CIDR range:

```yaml
network:
  subnet:
    cidr: 192.168.30.0/24
    gateway_ip: 192.168.30.254  # Must be in 192.168.30.0/24
```

---

#### Error: "Failed to connect to OpenStack after retries"

**Cause**: OpenStack credentials invalid or API unreachable.

**Solution**:

1. Verify credentials:
   ```bash
   openstack token issue
   ```

2. Check environment variables:
   ```bash
   env | grep OS_
   ```

3. Test connectivity:
   ```bash
   curl -k $OS_AUTH_URL
   ```

4. Verify `clouds.yaml` (if using):
   ```bash
   cat ~/.config/openstack/clouds.yaml
   ```

---

#### Error: "quota 'compute.cores' must be a non-negative integer, got -1"

**Cause**: Invalid quota value in configuration.

**Solution**:

All quota values must be -1 (unlimited) or a non-negative integer (≥ 0).

```yaml
quotas:
  compute:
    cores: 16      # Valid (any non-negative integer)
    ram: 32768     # Valid
    instances: 10  # Valid
    cores: -1      # Also valid (-1 means unlimited / no limit in OpenStack)
    # cores: -2    # Invalid (only -1 or ≥ 0 allowed)
  network:
    floating_ips: 0  # Valid (means "no FIPs requested, don't allocate")
    floating_ips: 2  # Also valid
    ports: 50        # Valid
  block_storage:
    gigabytes: 500   # Valid
    volumes: 0       # Also valid
```

**Quota Rules**:
- **All quotas**: Must be non-negative integers (≥ 0)
- **Zero values**: Valid for all quotas
  - In project configs: 0 means "set quota to 0" (effectively disables that resource)
  - In defaults.yaml: 0 means "no default enforcement" (projects must specify their own values)
- **floating_ips = 0**: Special meaning: "don't pre-allocate FIPs" (quota is not set)

---

### Debug Mode

For detailed troubleshooting, use debug mode (`-vv`):

```bash
tenantctl --project myproject -vv 2>&1 | tee debug.log
```

**Debug output includes**:
- All API calls with parameters
- Resource lookup results (found/not found)
- Skip reasons ("already up to date", "immutable", etc.)
- Configuration values being used

### Verifying OpenStack Credentials

Before running the provisioner, verify your credentials work:

```bash
# Test authentication
openstack token issue

# List projects (verify admin access)
openstack project list

# Check network access
openstack network list
```

If these commands fail, fix credentials before running the provisioner.

---

## Best Practices

### Configuration Management

**Use version control**:

```bash
cd /path/to/openstack-tenantctl
git init
git add config/
git commit -m "Initial configuration"
```

**Benefits**:
- Track configuration changes over time
- Rollback to previous configurations
- Code review for infrastructure changes
- Audit trail

**Branching strategy**:

```bash
# Create feature branch for new project
git checkout -b add-staging-project

# Make changes
vim config/projects/staging.yaml

# Dry-run to test
tenantctl --project staging --dry-run -v

# Commit and merge
git add config/projects/staging.yaml
git commit -m "Add staging project"
git checkout main
git merge add-staging-project
```

### Always Dry-Run First

**Before any provisioning run**:

```bash
tenantctl --dry-run -v | tee preview.log
```

**Review the preview**:
- Are the CREATED resources expected?
- Do UPDATED resources make sense?
- Are there unexpected changes?

**Then provision**:
```bash
tenantctl -v | tee provisioning.log
```

### CIDR Planning

**Allocate CIDR space systematically**:

```
10.0.0.0/8         - Reserved for OpenStack projects
  10.0.0.0/16      - Development environments
    10.0.0.0/24    - dev_1
    10.0.1.0/24    - dev_2
    10.0.2.0/24    - dev_3
  10.100.0.0/16    - Production environments
    10.100.0.0/24  - prod_1
    10.100.1.0/24  - prod_2
  10.200.0.0/16    - Staging environments
    10.200.0.0/24  - staging_1
```

**Document your CIDR allocation** in `config/README.md`:

```markdown
# CIDR Allocation

- 10.0.x.0/24: Dev projects (x = 0-255)
- 10.100.x.0/24: Prod projects (x = 0-255)
- 10.200.x.0/24: Staging projects (x = 0-255)

## Allocated

- 10.0.0.0/24: dev_1
- 10.0.1.0/24: dev_2
- 10.100.0.0/24: prod_1
```

### Quota Management

**Set realistic defaults** in `defaults.yaml`:

```yaml
quotas:
  compute:
    cores: 20        # Reasonable default for most projects
    ram: "50GB"      # Human-readable units (recommended)
    instances: 10
```

**Override only when needed** in project files:

```yaml
# High-performance project
quotas:
  compute:
    cores: 64           # Override for specific needs
    ram: "200GB"        # Much clearer than 204800
  block_storage:
    gigabytes: "2TB"    # Can use TB for large values
```

**Note**: RAM and storage quotas support human-readable units (`"50GB"`, `"2TB"`, `"100GiB"`) for better readability. Plain integers still work for backward compatibility.

**Monitor quota usage**:

```bash
openstack quota show myproject
openstack server list --project myproject
```

### Security Group Discipline

**Define baseline rules in defaults**:

```yaml
# defaults.yaml
security_group:
  rules:
    - direction: ingress
      protocol: icmp
      remote_ip_prefix: "0.0.0.0/0"
      description: "Allow ICMP"
    - direction: ingress
      protocol: tcp
      port_range_min: 22
      port_range_max: 22
      remote_ip_prefix: "10.0.0.0/8"
      description: "SSH from internal network only"
```

**Override for special cases**:

```yaml
# Public-facing web project
security_group:
  rules:
    - direction: ingress
      protocol: tcp
      port_range_min: 443
      port_range_max: 443
      remote_ip_prefix: "0.0.0.0/0"
      description: "HTTPS from internet"
```

**Remember**: Rules are **replaced**, not merged. If you override, specify all rules you want.

### Regular Reconciliation

**Schedule periodic runs** to ensure configuration compliance:

```bash
# Cron job (daily at 2 AM)
0 2 * * * /path/to/openstack-tenantctl/.venv/bin/tenantctl -v >> /var/log/openstack-tenantctl.log 2>&1
```

**Benefits**:
- Detect and fix configuration drift
- Ensure quotas remain correct
- Automatically fix manual changes that violated policy

---

## Getting Help

### Documentation

- **[CONFIG-SCHEMA.md](CONFIG-SCHEMA.md)** - Complete configuration reference
- **[SPECIFICATION.md](SPECIFICATION.md)** - Technical architecture details
- **[DESIGN-DECISIONS.md](DESIGN-DECISIONS.md)** - Why things work the way they do
- **[API-REFERENCE.md](API-REFERENCE.md)** - For extending the tool

### Command-Line Help

```bash
tenantctl --help
```
