# Configuration Schema Reference

<!--
**Last Updated**: 2026-04-05
-->

Complete reference for OpenStack TenantCtl configuration files.

## Table of Contents

1. [Overview](#overview)
2. [File Locations](#file-locations)
3. [Configuration Inheritance](#configuration-inheritance)
4. [Complete Schema](#complete-schema)
5. [Field Reference](#field-reference)
6. [Validation Rules](#validation-rules)
7. [Placeholder Substitution](#placeholder-substitution)
8. [System-Managed Fields](#system-managed-fields)
9. [Complete Examples](#complete-examples)

## Overview

OpenStack TenantCtl uses a YAML-based configuration system with:

- **Defaults file**: Shared configuration for all projects
- **Project files**: Per-project overrides and specific settings
- **Deep-merge inheritance**: Projects inherit from defaults with intelligent merging
- **Validation**: Comprehensive validation catches configuration errors before provisioning

## File Locations

```
<config-dir>/
├── defaults.yaml                # Shared defaults for all projects
├── federation_static.json       # Static federation mapping rules
└── projects/
    ├── dev_2.yaml              # Individual project configurations
    ├── prod_2.yaml
    └── ...
```

**Default config directory**: `./config/`

## Configuration Inheritance

Projects inherit configuration from `defaults.yaml` using **deep-merge** strategy:

- **Dictionaries**: Merged recursively (both defaults and project values are combined)
- **Lists**: Override completely (project value replaces defaults)
- **Scalars**: Override (project value replaces defaults)

**Example**:

```yaml
# defaults.yaml
quotas:
  compute:
    cores: 20
    ram: 51200
  network:
    ports: 50

# projects/myproject.yaml
quotas:
  compute:
    cores: 16  # Override cores
  # network.ports inherited (50)
```

**Result after merge**:
```yaml
quotas:
  compute:
    cores: 16      # From project
    ram: 51200     # From defaults (inherited)
  network:
    ports: 50      # From defaults (inherited)
```

## Complete Schema

Top-level fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Project name (OpenStack identifier format) |
| `resource_prefix` | string | Yes | Prefix for resource names (lowercase alphanumeric) |
| `description` | string | No | Human-readable description |
| `enabled` | boolean | No | Whether to provision this project (default: `true`) |
| `state` | string | No | Project lifecycle state: `"present"` (default), `"locked"`, or `"absent"` |
| `network` | object | Yes | Network configuration |
| `quotas` | object | Yes | Resource quotas for compute, network, and block storage |
| `security_group` | object | No | Default security group configuration |
| `federation` | object | No | Identity federation configuration |
| `group_role_assignments` | list | No | Keystone group-to-role assignments for the project |
| `external_network_name` | string | No | External network for router gateway (default: `"external"`) |
| `domain_id` | string or null | No | OpenStack domain UUID or name. Set `null` to auto-detect from env vars (default: `null`) |
| `domain` | string | No | OpenStack domain friendly name (use `domain_id` if both specified) |

## Field Reference

### `name`

**Type**: String (required)

**Format**: Must match regex `^[a-zA-Z][a-zA-Z0-9_ -]{0,63}$`

**Description**: OpenStack project name. Must start with a letter, followed by letters, digits, spaces, underscores, or hyphens. Maximum 64 characters.

**Examples**:
```yaml
name: dev_2           # Valid
name: production-env  # Valid
name: my project      # Valid (spaces allowed)
name: My Project      # Valid
name: 2ndProject      # Invalid (starts with digit)
```

---

### `resource_prefix`

**Type**: String (required)

**Format**: Must match regex `^[a-z0-9]+$`

**Description**: Prefix for created resources (network, router, subnet names). Lowercase alphanumeric only, no hyphens or underscores.

**Examples**:
```yaml
resource_prefix: dev2   # Valid
resource_prefix: prod1  # Valid
resource_prefix: Dev2   # Invalid (uppercase)
resource_prefix: dev-2  # Invalid (hyphen)
```

**Resource naming convention**:
- Network: `{resource_prefix}-network`
- Router: `{resource_prefix}-router`
- Subnet: `{resource_prefix}-subnet`

---

### `domain_id` and `domain`

**Type**: String or `null` (optional)

**Description**: OpenStack domain for the project. You can specify either `domain_id` (for UUIDs) or `domain` (for friendly names). If both are specified, `domain_id` takes precedence.

Set to `null` (or omit entirely) to auto-discover the domain from environment variables. This is useful when a project needs to opt back into auto-discovery even when `defaults.yaml` sets a concrete domain.

**Federation impact**: When `domain` is set, generated federation mapping rules automatically include `"domain": {"name": "<domain>"}` in the projects element, so Keystone resolves the project in the correct domain. See [`federation.user_type`](#federationuser_type) for related cross-domain configuration.

**Default Precedence** (highest to lowest):
1. Per-project `domain_id` config (string value)
2. Per-project `domain` config (string value)
3. Defaults `domain_id` config in defaults.yaml (string value)
4. Defaults `domain` config in defaults.yaml (string value)
5. `OS_PROJECT_DOMAIN_ID` environment variable
6. `OS_USER_DOMAIN_NAME` environment variable
7. Hardcoded `"default"`

**Note**: `null` at any level triggers auto-discovery (steps 5-7). A project can set `domain_id: null` to override a concrete domain from defaults and fall back to env vars.

**Use Cases**:
- **Single-domain deployments**: Set `domain_id: null` or omit (auto-discovers from env vars, falls back to `"default"`)
- **Multi-domain with UUIDs**: Use `domain_id: "07eeb9c46a204ba797054914ceb5fb56"`
- **Multi-domain with names**: Use `domain: "acme-corp"` or `domain_id: "acme-corp"` (SDK accepts both)
- **Per-project auto-discovery**: Set `domain_id: null` in a project file to override a concrete default

**Examples**:
```yaml
# In defaults.yaml — auto-discover from openrc env vars
domain_id: null

# In defaults.yaml (applies to all projects)
domain: "default"

# In project file (UUID)
domain_id: "07eeb9c46a204ba797054914ceb5fb56"

# In project file (friendly name)
domain: "acme-corp"

# Both specified (domain_id wins)
domain_id: "07eeb9c46a204ba797054914ceb5fb56"
domain: "acme-corp"  # This is ignored

# Per-project opt-in to auto-discovery (overrides defaults)
domain_id: null
```

**Environment Variable Fallback**:
```bash
export OS_PROJECT_DOMAIN_ID=07eeb9c46a204ba797054914ceb5fb56
export OS_USER_DOMAIN_NAME=acme-corp  # Used if OS_PROJECT_DOMAIN_ID not set
```

---

### `description`

**Type**: String (optional)

**Default**: `""` (empty string in models; typically set to `"Managed by openstack-tenantctl"` in defaults.yaml)

**Description**: Human-readable description for the project.

```yaml
description: "Development environment 2"
```

---

### `enabled`

**Type**: Boolean (optional)

**Default**: `true`

**Description**: Controls whether this project should be provisioned. Set to `false` to skip provisioning without deleting the configuration file.

```yaml
enabled: false  # Skip this project
```

---

### `state`

**Type**: String (optional)

**Default**: `"present"`

**Valid Values**: `"present"`, `"locked"`, `"absent"`

**Description**: Controls the project lifecycle state and determines which resources are managed during provisioning.

#### State Behaviors

**`present` (default)** - Full provisioning mode:
- Creates/updates all resources (project, network, router, quotas, security groups, federation)
- Ensures project is enabled
- This is normal operational mode

**`locked`** - Maintenance/frozen mode:
- Disables the project (sets `enabled: false` in OpenStack)
- Shelves all running instances (stops VMs but preserves state)
- Skips network, quota, and security group provisioning
- Use for maintenance windows or cost reduction without data loss

**`absent`** - Teardown mode:
- Validates project is safe to delete (no resources in use)
- Deletes the project from OpenStack
- **Safety check**: Fails if project has active resources (instances, volumes, networks)
- Does NOT delete the configuration file (allows re-provisioning)

#### Use Cases

```yaml
# Normal operations - full provisioning
state: present  # or omit (default)

# Maintenance window - freeze project, stop instances
state: locked

# Decommission - remove project from OpenStack
state: absent
```

#### State Transitions

Common workflows:

- **New project**: `absent` → `present` (initial provisioning)
- **Maintenance**: `present` → `locked` → `present` (temporary freeze)
- **Decommission**: `present` → `absent` (teardown)

**Important**: When state is `absent`, the provisioner will attempt to delete the project but will fail if any resources are still in use. Clean up all resources manually before setting `state: absent`.

---

### `reclaim_floating_ips`

**Type**: Boolean (optional)

**Default**: `false`

**Description**: Controls whether the provisioner attempts to re-allocate the *same* IP address when a tracked floating IP is deleted externally.

**When `false`** (default):
- Missing FIPs are recorded in `released_fips` and removed from `preallocated_fips`
- Normal scale-up allocates new (different) FIPs to reach the desired count
- Action status: UPDATED (system handles it gracefully)

**When `true`**:
- Missing FIPs trigger a `create_ip` call with the specific address
- If the address is still available, the FIP is reclaimed with a new ID
- If the address is taken (409 Conflict), the FIP is moved to `released_fips` with FAILED status

**Use `true` when** the project has external dependencies on specific IP addresses (DNS records, firewall rules, partner allowlists).

```yaml
# Default: don't reclaim specific addresses
reclaim_floating_ips: false

# For IP-stable projects (DNS, firewall rules)
reclaim_floating_ips: true
```

Adoption of untracked FIPs and the `released_fips` audit trail are always active regardless of this setting. See [DD-019](DESIGN-DECISIONS.md#dd-019-optional-fip-reclamation) for rationale.

---

### `reclaim_router_ips`

**Type**: Boolean (optional)

**Default**: `false`

**Description**: Controls whether the provisioner attempts to re-create a router with the *same* external IP address when the network stack is recreated for an empty project.

**When `false`** (default):
- A new router gets whatever external IP Neutron allocates from the pool
- The previous external IP (if any) remains in `released_router_ips` as an audit trail

**When `true`**:
- During network stack creation, the provisioner looks up the previous router IP from `router_ips` state
- If a previous IP is found, the router is created with that specific IP requested
- If the address is still available, the router gets its old IP back
- If the address is taken (409 Conflict), the IP is moved to `released_router_ips` and the router is created with a normal allocation

**Safety**: Reclaim only fires when `ensure_network_stack` is creating a brand-new stack (no network exists, project has no networks/routers). It has zero effect when the network already exists.

**Use `true` when** the project has external dependencies on the router's external IP (DNS records, firewall rules, VPN peer addresses).

```yaml
# Default: don't reclaim router external IPs
reclaim_router_ips: false

# For IP-stable projects (DNS, firewall rules, VPN peers)
reclaim_router_ips: true
```

Router IP tracking and the `released_router_ips` audit trail are always active regardless of this setting.

---

### `track_fip_changes`

**Type**: Boolean (optional)

**Default**: `false`

**Description**: Controls whether the provisioner persists FIP state on every steady-state run, even when the floating IP count hasn't changed. When enabled, the provisioner compares tracked FIP IDs against actual OpenStack FIPs and writes updated state plus a tracking metadata snapshot (timestamp, quota, allocated count) whenever they differ.

**When `false`** (default):
- The steady-state path (`existing_count == desired_count`) only sets the quota
- FIP state is persisted during scale-up, scale-down, and drift reconciliation but not on no-op runs
- Suitable for most deployments where FIP identity doesn't matter

**When `true`**:
- On every run where the count matches but FIP IDs differ (user swapped a FIP), the provisioner persists the updated FIP list and writes a `fip_tracking_snapshot` to the state file
- The snapshot includes `timestamp`, `quota`, and `allocated` count for downstream consumers

**Use `true` when** downstream systems (NFS export services, monitoring, CMDB sync) need to know the current FIP addresses/IDs without polling OpenStack directly.

```yaml
# Default: don't track FIP swaps on steady-state runs (in defaults.yaml)
track_fip_changes: false
```

**Per-project override** — enable only on specific projects that need it:

```yaml
# defaults.yaml — off globally
track_fip_changes: false

# projects/prod.yaml — enabled for this project only
track_fip_changes: true

# projects/dev.yaml — inherits false from defaults, no change needed
```

---

### `enforce_unique_cidrs`

**Type**: Boolean (optional)

**Default**: `false`

**Description**: Controls whether the provisioner rejects configurations where two or more projects have overlapping `network.subnet.cidr` ranges.

**When `false`** (default):
- Overlapping CIDRs across projects are allowed
- This is safe because OpenStack uses overlay networks — each project's network is isolated at L2/L3 by Neutron

**When `true`**:
- The provisioner checks all active (non-absent) projects for CIDR overlaps during config validation
- Overlapping CIDRs produce a validation error and block provisioning

**Use `true` when** your environment requires globally unique CIDRs — e.g., projects share networks, use VPN peering, or have routing between project networks.

```yaml
# Default: allow overlapping CIDRs (overlay isolation)
enforce_unique_cidrs: false

# For environments with cross-project routing
enforce_unique_cidrs: true
```

See [DD-021](DESIGN-DECISIONS.md#dd-021-opt-in-cidr-overlap-enforcement) for rationale.

---

### `network`

**Type**: Object (required)

**Structure**:

```yaml
network:
  mtu: <integer>                # Maximum transmission unit (0 = use default)
  subnet:
    cidr: <string>              # REQUIRED: Subnet CIDR (e.g., "192.168.30.0/24")
    gateway_ip: <string>        # REQUIRED: Gateway IP address
    allocation_pools:           # REQUIRED: IP allocation pools
      - start: <string>
        end: <string>
    dns_nameservers:            # Optional: List of DNS servers
      - <string>
    dhcp: <boolean>             # Optional: Enable DHCP (default: true)
```

#### `network.mtu`

**Type**: Integer (optional)

**Default**: `0` (use OpenStack default; defaults.yaml sets this to `1500`)

**Description**: Maximum transmission unit for the network. Set to `0` to use the OpenStack default MTU.

```yaml
network:
  mtu: 1500  # Set explicit MTU
  mtu: 0     # Use default
```

#### `network.subnet.cidr`

**Type**: String (required)

**Format**: Valid IPv4 or IPv6 CIDR notation

**Description**: IP network range for the subnet.

**Validation**:
- Must be valid CIDR (strict mode)
- Overlap checked only when `enforce_unique_cidrs: true`
- Gateway IP must be inside this CIDR
- All allocation pool IPs must be inside this CIDR

```yaml
network:
  subnet:
    cidr: 192.168.30.0/24  # Valid
    cidr: 10.0.0.0/16      # Valid
    cidr: 192.168.30/24    # Invalid (missing .0)
```

#### `network.subnet.gateway_ip`

**Type**: String (required)

**Format**: Valid IPv4 or IPv6 address

**Description**: Default gateway IP for the subnet.

**Validation**:
- Must be a valid IP address
- Must be inside the subnet CIDR

```yaml
network:
  subnet:
    cidr: 192.168.30.0/24
    gateway_ip: 192.168.30.254  # Valid (inside CIDR)
    gateway_ip: 192.168.31.1    # Invalid (outside CIDR)
```

#### `network.subnet.allocation_pools`

**Type**: List of objects (required)

**Description**: IP address ranges available for automatic assignment to instances.

**Structure**:
```yaml
allocation_pools:
  - start: <IP address>
    end: <IP address>
```

**Validation**:
- Each pool must have both `start` and `end`
- Both must be valid IP addresses
- Both must be inside the subnet CIDR
- `start` must be <= `end`

**Example**:
```yaml
network:
  subnet:
    cidr: 192.168.30.0/24
    allocation_pools:
      - start: 192.168.30.1
        end: 192.168.30.253
      # Reserve 192.168.30.254 for gateway
```

#### `network.subnet.dns_nameservers`

**Type**: List of strings (optional)

**Default**: `["8.8.8.8"]`

**Description**: DNS nameservers for instances in this subnet.

```yaml
network:
  subnet:
    dns_nameservers:
      - 8.8.8.8
      - 8.8.4.4
```

#### `network.subnet.dhcp`

**Type**: Boolean (optional)

**Default**: `true`

**Description**: Enable DHCP for automatic IP assignment.

```yaml
network:
  subnet:
    dhcp: true
```

---

### `quotas`

**Type**: Object (required)

**Description**: Resource quotas for the project across three OpenStack services.

**Structure**:

```yaml
quotas:
  compute:
    cores: <integer>          # vCPU cores
    ram: <integer>            # RAM in MiB (or unit string, e.g. "50GiB")
    ram_gibibytes: <integer>  # Convenience: RAM in GiB (plain integer, converted to MiB)
    instances: <integer>      # Maximum instances
  network:
    floating_ips: <integer>   # Desired number of floating IPs (managed by pre-allocated resources)
    networks: <integer>       # Networks (must be 1 for pre-allocated resources)
    subnets: <integer>        # Subnets
    routers: <integer>        # Routers
    ports: <integer>          # Network ports
    security_groups: <integer>      # Security groups
    security_group_rules: <integer> # Security group rules
    load_balancers: <integer>       # Load balancers (Octavia, optional)
    listeners: <integer>            # LB listeners (Octavia, optional)
    pools: <integer>                # LB pools (Octavia, optional)
    health_monitors: <integer>      # LB health monitors (Octavia, optional)
    members: <integer>              # LB pool members (Octavia, optional)
  block_storage:
    gigabytes: <integer>      # Total storage in GB
    volumes: <integer>        # Maximum volumes
    snapshots: <integer>      # Maximum snapshots
```

**Validation**:
- All quota values must be non-negative integers (≥ 0)
- For pre-allocated resources (see [Pre-Allocated Resources](#pre-allocated-resources)):
  - `network.networks` must be exactly 1

#### Quota Fields

**Compute Quotas** (`quotas.compute`):

| Field | Description | Example |
|-------|-------------|---------|
| `cores` | Maximum vCPU cores | `20` |
| `ram` | Maximum RAM in MiB (supports unit strings) | `51200` or `"50GiB"` |
| `ram_gibibytes` | Convenience alias for `ram` in GiB (plain integer) | `50` (= 51200 MiB) |
| `instances` | Maximum instances (VMs) | `10` |

**Network Quotas** (`quotas.network`):

| Field | Description | Example |
|-------|-------------|---------|
| `floating_ips` | Desired floating IPs | `0` = none, `1`+ = allocate and lock that many |
| `networks` | Maximum networks | `1` (required for pre-allocated resources) |
| `subnets` | Maximum subnets | `1` |
| `routers` | Maximum routers | `1` |
| `ports` | Maximum network ports | `50` |
| `security_groups` | Maximum security groups | `10` |
| `security_group_rules` | Maximum security group rules | `100` |
| `load_balancers` | Maximum load balancers (Octavia) | `5` |
| `listeners` | Maximum LB listeners (Octavia) | `10` |
| `pools` | Maximum LB pools (Octavia) | `10` |
| `health_monitors` | Maximum LB health monitors (Octavia) | `10` |
| `members` | Maximum LB pool members (Octavia) | `50` |

**Note**: Load balancer quotas (`load_balancers`, `listeners`, `pools`, `health_monitors`, `members`) are managed by the Octavia service (Load Balancer as a Service). These quotas are optional and only needed if your deployment uses load balancers.

**Block Storage Quotas** (`quotas.block_storage`):

| Field | Description | Example |
|-------|-------------|---------|
| `gigabytes` | Total volume storage in GB | `500` |
| `volumes` | Maximum volumes | `20` |
| `snapshots` | Maximum snapshots | `10` |

#### `ram_gibibytes` — Convenience Alias for RAM

Instead of dealing with MiB values or unit strings, you can specify RAM in **gibibytes (GiB)** as a plain integer:

```yaml
quotas:
  compute:
    ram_gibibytes: 50   # 50 GiB = 51200 MiB — no unit confusion
    cores: 20
```

This is equivalent to `ram: 51200` or `ram: "50GiB"` but avoids the common GiB-vs-GB confusion.

**Rules**:
- Must be a non-negative integer (or `-1` for unlimited)
- Converted internally: `value × 1024` → MiB
- Can coexist with `ram` when both resolve to the same MiB value (useful for defaults + project overrides)
- Produces a clear error when `ram` and `ram_gibibytes` resolve to different values — pick one

#### Human-Readable Units for RAM and Storage

**Supported fields**: RAM and storage quotas can use human-readable unit strings instead of plain integers:

- **RAM** (`quotas.compute.ram`): Accepts unit strings, converts to MiB for OpenStack API
- **Storage** (`quotas.block_storage.gigabytes`, `quotas.block_storage.backup_gigabytes`): Accepts unit strings, converts to GB for OpenStack API

**Supported units**:

| Unit Type | Units | Base | Example Conversion |
|-----------|-------|------|-------------------|
| **Decimal** (base-10) | KB, MB, GB, TB, PB | Powers of 1000 | `"50GB"` → 50,000 MB |
| **Binary** (base-2) | KiB, MiB, GiB, TiB, PiB | Powers of 1024 | `"50GiB"` → 53,687 MB |
| **Shorthand** | K, M, G, T, P | Maps to binary | `"50G"` → 53,687 MB (same as GiB) |

**Syntax**:
- Format: `"<number><unit>"` (e.g., `"50GB"`, `"2TB"`, `"100GiB"`)
- Whitespace allowed: `"50 GB"` and `"50GB"` are equivalent
- Fractional values supported: `"1.5TB"` → 1500 GB (rounded to nearest integer)
- **Recommend quoting strings in YAML**: `ram: "50GB"` not `ram: 50GB`

**Special values**:
- `-1` (unlimited) must be a literal integer: `ram: -1` ✓
- Negative with units not allowed: `ram: "-10GB"` ✗

**Examples**:

```yaml
# Using human-readable units
quotas:
  compute:
#   ram: 102400
    ram: "100GB"      # same result
    cores: 20
  block_storage:
    gigabytes: "2TB"  # Clearer than 2000
    backup_gigabytes: "500GB"
    volumes: 50

# Binary units (powers of 1024)
quotas:
  compute:
    ram: "100GiB"     # = 107,374 MB (larger than 100GB)

# Shorthand (maps to binary)
quotas:
  compute:
    ram: "100G"       # Same as "100GiB"

# Mixing integers and unit strings
quotas:
  compute:
    ram: "50GB"       # Unit string
    cores: 20         # Plain integer
  block_storage:
    gigabytes: "2TB"  # Unit string
    volumes: 50       # Plain integer

# Traditional plain integers still work (backward compatible)
quotas:
  compute:
    ram: 51200        # 50 GB in MB
    cores: 20
  block_storage:
    gigabytes: 500    # GB
```

**Decimal vs Binary difference**:

| Value | Decimal (base-10) | Binary (base-2) | Difference |
|-------|-------------------|-----------------|------------|
| 100GB | 100,000 MB | - | - |
| 100GiB | - | 107,374 MB | +7.4% larger |
| 1TB | 1,000 GB | - | - |
| 1TiB | - | 1,099 GB | +9.9% larger |

**Example**:
```yaml
quotas:
  compute:
    cores: 16
    ram: "32GB"      # friendly units, will get converted to MB
    instances: 10
  network:
    floating_ips: 2  # Allocate two FIPs, then set quota to 2
    networks: 1
    subnets: 1
    routers: 1
    ports: 50
    security_groups: 10
    security_group_rules: 100
  block_storage:
    gigabytes: "500GB"  # Can also use "0.5TB"
    volumes: 20
    snapshots: 10
```

#### Pre-Allocated Resources

The provisioner supports **pre-allocated resources**: resources that are pre-allocated by the provisioner, with quotas set to enforce the configured limit and drift detection to maintain the desired state.

**Current pre-allocated resources**:
- Floating IPs (`quotas.network.floating_ips`)
- Networks (`quotas.network.networks`)

**How it works - Pre-allocation with quota enforcement**:

The provisioner pre-allocates resources and enforces quotas to limit the total count, with drift detection to maintain the desired state:

1. **User specifies desired count** in configuration:
   ```yaml
   quotas:
     network:
       floating_ips: 2  # "I want exactly 2 floating IPs"
   ```

2. **Provisioner pre-allocates resources**:
   - Raises OpenStack quota to allow allocation
   - Creates the requested number of floating IPs
   - Tracks allocated resources in state file (`preallocated_fips` field)

3. **Provisioner sets quota to enforce limit**:
   - Sets OpenStack floating IP quota to **desired count** (e.g., 2)
   - Users can **replace** resources within this limit (delete one, create another)
   - Users **cannot exceed** the total count (limited by quota)

4. **Drift detection and reconciliation**:
   - Detects manually deleted FIPs → releases them (or reclaims same address if `reclaim_floating_ips: true`)
   - Detects manually created FIPs → adopts them into config
   - Tracks released FIPs in audit trail for compliance

5. **State file tracks allocated resources**:
   ```yaml
   # In your project config file (YOU control this):
   quotas:
     network:
       floating_ips: 2  # Desired count

   # In state file config/state/<project>.state.yaml (provisioner manages this):
   preallocated_fips:
     - id: abc-123-def
       address: 203.0.113.42
     - id: def-456-ghi
       address: 203.0.113.43
   ```

**Key concepts**:

- **Your config value is the quota**: `floating_ips: 2` sets OpenStack quota to 2
- **Pre-allocated, not locked**: Resources are allocated by provisioner, quota enforces limit
- **Users can replace within quota**: Delete one FIP and create another (quota still 2)
- **Cannot exceed count**: Quota prevents creating more than configured count
- **Drift detection**: Provisioner adopts untracked FIPs and releases missing ones (or reclaims same address if `reclaim_floating_ips: true`)
- **State tracking**: Allocated FIPs are tracked in `preallocated_fips` field in the state file

**Scale-up example** (increase from 2 to 3 FIPs):
```yaml
# 1. Edit configuration
quotas:
  network:
    floating_ips: 3  # Change from 2 to 3

# 2. Run provisioner
# 3. Provisioner allocates 1 additional FIP
# 4. Result: 3 FIPs tracked, OpenStack quota set to 3
```

**Scale-down example** (decrease from 2 to 1 FIPs):
```yaml
# 1. Edit configuration
quotas:
  network:
    floating_ips: 1  # Change from 2 to 1

# 2. Run provisioner
# 3. Provisioner releases 1 unused FIP (if not attached to a port)
# 4. Result: 1 FIP tracked, OpenStack quota set to 1
```

**Scale-down constraints**:
- Only **unused** FIPs can be released automatically
- FIPs attached to ports (in-use) cannot be automatically released
- If scale-down requires releasing in-use FIPs, provisioning fails with FAILED action
- Manual cleanup required: detach FIPs from instances, then re-run provisioner

**Zero FIPs** (no floating IPs needed):
```yaml
quotas:
  network:
    floating_ips: 0  # Valid: no FIPs requested
```

This is different from omitting the field (inherits from defaults).

**Important**: The `preallocated_fips` section in state files is auto-managed by the provisioner. Do not manually edit state files — any changes will be overwritten on the next run.

#### Audit Trail Fields

The provisioner maintains audit trail fields to track resource drift and history. These fields are **system-managed** and auto-populated during provisioning. Do not manually edit these fields.

**`preallocated_fips`** (list of objects):
- Current snapshot of allocated floating IPs
- Each entry contains `id` (UUID) and `address` (IP address)
- Updated during every provisioning run

**`released_fips`** (list of objects):
- Audit trail of floating IPs that were permanently lost or released
- Populated when a tracked FIP is deleted from OpenStack (manual deletion)
- Each entry contains `id`, `address`, and timestamp of when drift was detected
- Never automatically cleared — provides compliance audit trail

**`router_ips`** (list of objects):
- Current snapshot of all router external IP addresses in the project
- Each entry contains `id` (router UUID), `name` (router name), and `external_ip` (IP address)
- Updated during every provisioning run by `track_router_ips()`
- Tracks ALL routers in the project, not just provisioner-created ones
- Structure: `[{id: "router-uuid", name: "router-name", external_ip: "203.0.113.42"}, ...]`

**`released_router_ips`** (list of objects):
- Audit trail of router IPs that were released or changed
- Populated when tracked routers are deleted or their external IPs change
- Each entry contains `address`, `router_name`, `released_at` (ISO 8601 timestamp), and `reason`
- Never automatically cleared — provides permanent compliance audit trail
- Structure: `[{address: "203.0.113.42", router_name: "old-router", released_at: "2026-04-01T10:30:00+00:00", reason: "router no longer exists"}, ...]`

**Drift Detection and Reconciliation**:

When resources are manually deleted from OpenStack outside the provisioner:
1. **Detection**: Provisioner compares tracked IDs against current OpenStack state
2. **Adopt**: If extra resources exist (not tracked), they are adopted into tracking
3. **Release**: If tracked resources are missing, they are moved to `released_*` audit trail
4. **Reconcile**: Missing resources may be recreated depending on configuration

**Example of audit trail after drift**:

```yaml
# Initial state (in state file)
preallocated_fips:
  - id: e7b5c8d4-1234-5678-9abc-def012345678
    address: 203.0.113.42

# After manual deletion in OpenStack (state file updated)
preallocated_fips: []  # FIP was deleted

released_fips:
  - id: e7b5c8d4-1234-5678-9abc-def012345678
    address: 203.0.113.42
    released_at: "2024-03-15T10:30:00Z"  # Example timestamp
```

---

### `security_group`

**Type**: Object (optional)

**Description**: Default security group configuration for the project.

**Structure**:

```yaml
security_group:
  name: <string>              # Security group name (default: "default")
  rules:
    # Three rule formats supported:
    - <preset_name>           # Predefined preset (e.g., "SSH", "HTTP")
    - rule: <preset_name>     # Preset with field overrides
      <field>: <value>
    - direction: <string>     # Fully custom inline rule
      protocol: <string>
      port_range_min: <int>
      port_range_max: <int>
      remote_ip_prefix: <string>
      description: <string>
```

#### Predefined Rule Presets

Common rules can be specified as short preset names instead of full rule dicts. Presets are expanded to full rule dicts at config-load time.

| Preset | Protocol | Port(s) | Direction | Remote IP | Description |
|--------|----------|---------|-----------|-----------|-------------|
| `SSH` | tcp | 22 | ingress | `0.0.0.0/0` | Allow SSH |
| `HTTP` | tcp | 80 | ingress | `0.0.0.0/0` | Allow HTTP |
| `HTTPS` | tcp | 443 | ingress | `0.0.0.0/0` | Allow HTTPS |
| `ICMP` | icmp | — | ingress | `0.0.0.0/0` | Allow ICMP |
| `All ICMP` | icmp | — | ingress | `0.0.0.0/0` | Allow ICMP (Horizon alias) |
| `All TCP` | tcp | 1–65535 | ingress | `0.0.0.0/0` | Allow all TCP |
| `All UDP` | udp | 1–65535 | ingress | `0.0.0.0/0` | Allow all UDP |
| `DNS` | udp | 53 | ingress | `0.0.0.0/0` | Allow DNS |
| `RDP` | tcp | 3389 | ingress | `0.0.0.0/0` | Allow RDP |

#### Rule Formats

**1. Preset name (string)**:
```yaml
rules:
  - SSH        # Expands to full SSH rule dict
  - ICMP       # Expands to full ICMP rule dict
```

**2. Preset with overrides (dict with `rule` key)**:
```yaml
rules:
  - rule: SSH                        # Start from SSH preset
    remote_ip_prefix: "10.0.0.0/8"  # Override remote CIDR
```
The `rule` key selects the preset as a base; any additional fields override the preset values. The `rule` key itself is stripped from the final dict.

**3. Fully custom inline rule (dict without `rule` key)**:
```yaml
rules:
  - direction: ingress
    protocol: tcp
    port_range_min: 8080
    port_range_max: 8080
    remote_ip_prefix: "0.0.0.0/0"
    description: "Allow custom port"
```
This is the original format and remains fully backward compatible.

#### Security Group Rule Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `direction` | string | Yes | `"ingress"` or `"egress"` |
| `protocol` | string | Yes | `"tcp"`, `"udp"`, `"icmp"`, or IP protocol number |
| `port_range_min` | integer | For TCP/UDP | Starting port number (1-65535) |
| `port_range_max` | integer | For TCP/UDP | Ending port number (1-65535) |
| `remote_ip_prefix` | string | Yes | Source/destination CIDR (e.g., `"0.0.0.0/0"`) |
| `description` | string | No | Human-readable rule description |

**Examples**:

```yaml
# Simple — just preset names
security_group:
  name: default
  rules:
    - ICMP
    - SSH

# Preset with overrides (e.g., restrict SSH to a specific CIDR)
security_group:
  name: default
  rules:
    - ICMP
    - rule: SSH
      remote_ip_prefix: "10.0.0.0/8"
    - HTTPS

# Mixed — all three styles together
security_group:
  name: default
  rules:
    - ICMP
    - SSH
    - rule: HTTP
      remote_ip_prefix: "10.0.0.0/8"
    - direction: ingress
      protocol: tcp
      port_range_min: 8443
      port_range_max: 8443
      remote_ip_prefix: "0.0.0.0/0"
      description: "Custom HTTPS alt"
```

---

### `federation`

**Type**: Object (optional)

**Description**: Identity federation configuration for external authentication (e.g., SAML, OIDC).

**Structure**:

```yaml
federation:
  issuer: <string>              # REQUIRED: Identity provider issuer URL
  mapping_id: <string>          # REQUIRED: Federation mapping ID
  group_prefix: <string>        # REQUIRED: Group path prefix
  user_type: <string>           # Optional: User type in mapping rules (e.g., "ephemeral")
  mode: <string | list[string]>  # Optional: Default mode — "project" (default), "group", or ["project", "group"]
  group_name_separator: <string> # Optional: Separator for auto-derived group names (default: "-")
  role_assignments:             # REQUIRED: Role mappings for IdP groups
    - idp_group: <string | list[string]>  # IdP group name(s) (short or absolute path)
      roles:                    # List of OpenStack roles
        - <string>
      keystone_group: <string>  # Optional: Explicit Keystone group name override (group mode)
      mode: <string | list[string]>  # Optional: Per-entry override — "project", "group", or ["project", "group"]
```

#### `federation.issuer`

**Type**: String (required for federation)

**Description**: Identity provider issuer URL (e.g., Keycloak realm URL).

```yaml
federation:
  issuer: "https://myidp.corp/realms/myrealm"
```

#### `federation.mapping_id`

**Type**: String (required for federation)

**Description**: ID of the federation mapping in OpenStack. This mapping must exist before provisioning.

```yaml
federation:
  mapping_id: "my-mapping"
```

#### `federation.group_prefix`

**Type**: String (required for federation)

**Description**: Prefix prepended to `idp_group` short names to construct absolute group paths.

**Group path resolution**:
- **Short names**: If `idp_group` doesn't start with `/`, prefix is prepended
  - `idp_group: "member"` → `"/services/openstack/member"`
- **Absolute paths**: If `idp_group` starts with `/`, used as-is
  - `idp_group: "/admin/superuser"` → `"/admin/superuser"`

```yaml
federation:
  group_prefix: "/services/openstack/"
  role_assignments:
    - idp_group: member           # → "/services/openstack/member"
    - idp_group: /admin/superuser # → "/admin/superuser" (no prefix)
```

#### `federation.user_type`

**Type**: String (optional)

**Default**: `""` (empty — omitted from mapping rules)

**Description**: Sets the `"type"` field on the user element in generated federation mapping rules. When empty (default), the user element contains only `name` and `email`. When set, the specified value is included as `"type"` in the user element.

**Common values**:
- `"ephemeral"` — Keystone creates shadow users on first login. Required when projects use a non-default domain, because Keystone needs `"type": "ephemeral"` for cross-domain federated authentication.
- `"local"` — Users must already exist in Keystone before federated login.

**Interaction with `domain`**: The `user_type` and `domain` fields are independent — each is emitted in the generated rule only when its own value is set. However, when using a non-default domain, Keystone typically requires `user_type: "ephemeral"` for federated authentication to work correctly.

```yaml
# Omitted by default (no "type" in user element)
federation:
  user_type: ""

# Enable ephemeral users for cross-domain federation
federation:
  user_type: "ephemeral"

# Per-project override
federation:
  user_type: "local"
```

**Generated rule example** (with `user_type: "ephemeral"` and `domain: "MyDomain"`):
```json
{
  "local": [
    {"user": {"name": "{0}", "email": "{1}", "type": "ephemeral"}},
    {"domain": {"name": "MyDomain"}, "projects": [{"name": "proj", "roles": [{"name": "member"}]}]}
  ],
  "remote": [...]
}
```

#### `federation.domain`

**Type**: String or `null` (optional)

**Default**: `""` (empty — inherit from project-level `domain`)

**Description**: Override the domain used in federation mapping rules, independently of the project-level `domain` field. This decouples the OpenStack project domain from the domain referenced in IDP mapping rules.

**Three-state behavior**:

| YAML value | Python value | Effect |
|---|---|---|
| *(absent)* | `""` | Inherit from `project.domain` (backward compatible) |
| `null` | `None` | Suppress domain (project mode: no domain element; group mode: `"Default"`) |
| `"SomeDomain"` | `"SomeDomain"` | Use this value instead of project domain |

**Use case**: A project operates in domain `"eodc-eu"` for OpenStack purposes, but the IDP mapping should reference `"Default"` (or no domain at all) instead.

**Examples**:
```yaml
# Project uses eodc-eu for OpenStack, but IDP mapping omits domain
domain: "eodc-eu"
federation:
  domain: null  # suppress domain from mapping rules
  role_assignments:
    - idp_group: member
      roles: [member]

# Project uses eodc-eu for OpenStack, but IDP mapping uses a different domain
domain: "eodc-eu"
federation:
  domain: "Default"  # explicit override
  role_assignments:
    - idp_group: member
      roles: [member]

# Default behavior — inherit project domain (no change needed)
domain: "eodc-eu"
federation:
  # domain absent or "" → inherits "eodc-eu" from project
  role_assignments:
    - idp_group: member
      roles: [member]
```

---

#### `federation.mode`

**Type**: String or list of strings (optional)

**Default**: `"project"`

**Valid values**: `"project"`, `"group"`, or a list like `["project", "group"]`

**Description**: Default mode for `role_assignments` entries that don't specify their own `mode`. Each entry can override this with its own `mode` field, allowing mixed strategies in a single project.

- **`"project"`** (default): Rules use `{"projects": [...]}` — direct project assignment. Each rule assigns roles directly to the project.
- **`"group"`**: Rules use `{"group": {...}}` — recommended for multi-project access. Users are placed into Keystone groups (which accumulate across rules), and those groups have role assignments on the project.
- **`["project", "group"]`**: Rules include **both** elements — users get direct project roles **and** Keystone group membership. This is useful when application credentials require direct project-scoped roles alongside group membership.

**Mode resolution order**: entry `mode` > federation `mode` > hard-coded default (`"project"`)

**In group mode, tenantctl automatically**:
1. Creates Keystone groups named `{project_name}{separator}{idp_group}` (before per-project reconciliation)
2. Derives `group_role_assignments` from `role_assignments` so the groups get the correct roles on the project
3. Generates mapping rules that place IDP users into those Keystone groups

**Per-entry override**: Entries within a single project can use different modes. Group-mode, project-mode, and combined-mode entries coexist in the same mapping document.

```yaml
# Default: all entries use project mode
federation:
  mode: "project"

# All entries default to group mode
federation:
  mode: "group"

# Combined mode: both project assignment and group membership
federation:
  mode: ["project", "group"]

# Mixed modes: federation default is project, one entry overrides to group
federation:
  mode: "project"
  role_assignments:
    - idp_group: member
      roles: [member]
      # inherits mode: "project"
    - idp_group: reader
      roles: [reader]
      mode: "group"   # override for this entry only
    - idp_group: operator
      roles: [admin]
      mode: ["project", "group"]  # both elements in one rule
```

**Generated rule example** (combined mode `["project", "group"]`, project "my project", idp_group "member"):
```json
{
  "local": [
    {"user": {"name": "{0}", "email": "{1}", "type": "ephemeral"}},
    {"group": {"name": "my project member", "domain": {"name": "Default"}}},
    {"projects": [{"name": "my project", "roles": [{"name": "member"}]}]}
  ],
  "remote": [
    {"type": "OIDC-preferred_username"},
    {"type": "OIDC-email"},
    {"type": "HTTP_OIDC_ISS", "any_one_of": ["https://myidp.corp/realms/myrealm"]},
    {"type": "OIDC-groups", "any_one_of": ["/services/openstack/my project/member"]}
  ]
}
```

#### `federation.group_name_separator`

**Type**: String (optional)

**Default**: `" "` (space)

**Description**: Separator between project name and IDP group name when auto-deriving Keystone group names in group mode.

```yaml
# Default: space separator
federation:
  group_name_separator: " "   # "my project" + "member" → "my project member"

# Custom separator
federation:
  group_name_separator: "-"   # "my project" + "member" → "my project-member"
```

#### `federation.role_assignments`

**Type**: List of objects (required for federation)

**Description**: Maps IdP groups to OpenStack roles within the project.

**Structure**:
```yaml
role_assignments:
  - idp_group: <string | list[string]>   # IdP group name(s)
    roles:                               # OpenStack roles to assign
      - <string>
    keystone_group: <string>             # Optional: explicit group name (group mode only)
    mode: <string | list[string]>         # Optional: per-entry override — "project", "group", or ["project", "group"]
```

**Fields**:
- `idp_group`: Identity provider group name(s) — a non-empty string or a non-empty list of non-empty strings. When a list is given, all resolved group paths are placed in a single `any_one_of` clause, meaning membership in **any** of the listed groups grants the specified roles.
- `roles`: List of OpenStack role names (list of strings, non-empty)
- `keystone_group`: Optional explicit Keystone group name override (group mode only). When empty (default), the group name is auto-derived as `{project_name}{separator}{idp_group}`. When set, this exact name is used instead.
- `mode`: Optional per-entry mode override. When empty, inherits from `federation.mode` (which defaults to `"project"`). Valid values: `"project"`, `"group"`, or a list like `["project", "group"]`.

**Validation**:
- `idp_group` must be a non-empty string **or** a non-empty list of non-empty strings
- `roles` must be a non-empty list of non-empty strings
- `keystone_group` must be a string (if present)
- `mode` must be `"project"`, `"group"`, a list like `["project", "group"]`, or empty (if present)

**Example** (project mode — default):
```yaml
federation:
  issuer: "https://myidp.corp/realms/myrealm"
  mapping_id: "my-mapping"
  group_prefix: "/services/openstack/"
  role_assignments:
    # Members get read-write access
    - idp_group: member
      roles:
        - member
        - load-balancer_member

    # Readers get read-only access
    - idp_group: reader
      roles:
        - reader

    # Admins (absolute path, no prefix)
    - idp_group: /admin/cloud-admins
      roles:
        - admin
        - heat_stack_owner

    # Multiple groups sharing the same roles (list syntax)
    - idp_group:
        - /acme-it-staff
        - /acme-dev-staff
      roles:
        - member
        - load-balancer_member
```

**Example** (group mode default with per-entry override):
```yaml
federation:
  issuer: "https://myidp.corp/realms/myrealm"
  mapping_id: "my-mapping"
  group_prefix: "/services/openstack/"
  mode: "group"
  user_type: "ephemeral"
  role_assignments:
    # Inherits mode: "group" — auto-derived group: "my-project member"
    - idp_group: member
      roles:
        - member
        - load-balancer_member

    # Explicit group name override (still group mode)
    - idp_group: reader
      roles:
        - reader
      keystone_group: "my-custom-readers"

    # Override to project mode for this entry only
    - idp_group: operator
      roles:
        - admin
      mode: "project"
```

---

### `group_role_assignments`

**Type**: List of objects (optional)

**Description**: Assigns Keystone groups to the project with specific roles. Supports granting (`state: present`) and revoking (`state: absent`) assignments. Useful for ensuring admin groups have access to every project via defaults.

**Structure**:

```yaml
group_role_assignments:
  - group: <string>           # REQUIRED: Keystone group name
    roles:                    # REQUIRED: List of role names
      - <string>
    state: <string>           # Optional: "present" (default) or "absent"
```

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `group` | string | Yes | Keystone group name (supports `{name}` placeholder) |
| `roles` | list of strings | Yes | OpenStack role names to assign/revoke |
| `state` | string | No | `"present"` (default) — grant roles; `"absent"` — revoke roles |

#### `state` Semantics

- **`present`** (default if omitted): Ensure the group has all listed roles on the project. Missing assignments are created.
- **`absent`**: Ensure the group does NOT have the listed roles on the project. Existing assignments are revoked.

#### Inheritance

Since lists override in deep-merge, a project that specifies `group_role_assignments` **replaces** the defaults list entirely. To keep default assignments and add project-specific ones, repeat the defaults entries in the project file.

#### Validation

- Must be a list (or omitted)
- Each entry must be a dict with:
  - `group`: non-empty string
  - `roles`: non-empty list of non-empty strings
  - `state` (if present): must be `"present"` or `"absent"`

**Examples**:

```yaml
# In defaults.yaml — grant admin access to all projects
group_role_assignments:
  - group: acme-domain-admin
    roles:
      - admin
      - load-balancer_member

# Per-project — add project-specific groups (replaces defaults)
group_role_assignments:
  - group: acme-domain-admin
    roles:
      - admin
      - load-balancer_member
  - group: "{name}-operators"
    roles:
      - member

# Revoke a specific assignment
group_role_assignments:
  - group: acme-domain-admin
    roles:
      - admin
      - load-balancer_member
  - group: legacy-group
    state: absent
    roles:
      - member

# Opt-out entirely (no assignments)
group_role_assignments: []
```

---

### `external_network_name`

**Type**: String (optional)

**Default**: `""` (empty string; auto-discovered if omitted)

**Description**: Name or UUID of the external network to use for router gateway attachment and floating IP allocation. If not specified, the provisioner will auto-discover the external network.

```yaml
external_network_name: "public"
```

---

### `external_network_subnet`

**Type**: String (optional)

**Default**: `""` (empty string; auto-selected if omitted)

**Scope**: Global default in `defaults.yaml`, **per-project override** in project YAML

**Description**: Subnet name or UUID within the external network for router gateway and floating IP allocation.

**Why this matters**: When an external network has multiple subnets with different IP ranges, OpenStack may allocate router gateway IPs and floating IPs from different subnets. This causes connectivity failures because FIPs rely on the router's external gateway for routing.

**Discovery behavior**:
- If set: Look up by name or UUID, validate it belongs to the external network
- If unset and external network has 1 subnet: Auto-select that subnet
- If unset and external network has multiple subnets: Auto-select first IPv4 subnet with warning

**Per-project override**: Projects can specify their own `external_network_name` and `external_network_subnet` to use a different external network or subnet than the global default. This is useful for:
- IP allocation from different subnets per project
- Multi-region setups where projects target different external networks
- Quota management or isolation across subnets

**Examples**:
```yaml
# Using subnet name (must be unique in your cloud)
external_network_name: "public"
external_network_subnet: "external0"

# Using subnet UUID (recommended for scripts)
external_network_name: "public"
external_network_subnet: "8f2d3e4f-1234-5678-9abc-def012345678"

# Mixed: network UUID, subnet name
external_network_name: "511e1d0b-9732-418d-88ac-cab5d2d4b03a"
external_network_subnet: "external0"

# Per-project override example:
# defaults.yaml (global default for all projects)
external_network_name: "public"
external_network_subnet: "external0"

# projects/prod.yaml (override for specific project)
name: "prod-env"
external_network_name: "public"
external_network_subnet: "external1"  # This project uses external1 instead
```

**Validation**: The tool validates that the specified subnet belongs to the configured/discovered external network and will error if they don't match.

**To find available subnets**:
```bash
openstack subnet list --network <external-network-name>
```

---

## Validation Rules

The provisioner validates all configurations before connecting to OpenStack. Validation failures prevent provisioning.

### Required Fields

The following fields must be present in every project configuration (after merging with defaults):

- `name`
- `resource_prefix`
- `network.subnet.cidr`
- `network.subnet.gateway_ip`
- `network.subnet.allocation_pools`

### Format Validation

- **`name`**: Must match `^[a-zA-Z][a-zA-Z0-9_ -]{0,63}$`
- **`resource_prefix`**: Must match `^[a-z0-9]+$`
- **`state`**: Must be one of: `"present"`, `"locked"`, or `"absent"`
- **`network.subnet.cidr`**: Must be valid CIDR (strict mode)
- **`network.subnet.gateway_ip`**: Must be valid IP address inside CIDR
- **`network.subnet.allocation_pools[].start/end`**: Must be valid IP addresses inside CIDR
- **All quota values**: Must be non-negative integers (≥ 0)

### Cross-Field Validation

- **Gateway in CIDR**: `gateway_ip` must be inside `cidr`
- **Pools in CIDR**: All allocation pool `start` and `end` IPs must be inside `cidr`
- **Pool order**: For each pool, `start` must be <= `end`
- **No CIDR overlaps** (opt-in): When `enforce_unique_cidrs: true`, no two active projects can have overlapping `network.subnet.cidr` ranges

### Federation Validation

If `federation` is configured:
- `issuer`, `mapping_id`, `group_prefix`, and `role_assignments` are required
- Each `role_assignments` entry must have:
  - `idp_group`: Non-empty string or non-empty list of non-empty strings
  - `roles`: Non-empty list of non-empty strings

### Group Role Assignments Validation

If `group_role_assignments` is configured:
- Must be a list
- Each entry must be a mapping with:
  - `group`: Non-empty string
  - `roles`: Non-empty list of non-empty strings
  - `state` (optional): Must be `"present"` or `"absent"`

### Quota Validation

All quota values must be -1 (unlimited) or a non-negative integer (≥ 0):
```yaml
quotas:
  compute:
    cores: 16      # Valid
    ram: -1        # Valid (-1 means unlimited in OpenStack)
    instances: 30  # Valid
  network:
    ports: 50.5    # Invalid (not an integer)
    floating_ips: -2  # Invalid (only -1 or ≥ 0 allowed)
```

---

## Placeholder Substitution

The provisioner supports placeholder substitution using `{name}` in string values.

**Available placeholders**:
- `{name}`: Replaced with the project's `name` field value

**Substitution scope**: All string values in the merged configuration (after inheritance).

**Example**:

```yaml
# Configuration
name: dev_2
description: "Development environment for {name}"

federation:
  group_prefix: "/services/{name}/"
```

**After substitution**:
```yaml
name: dev_2
description: "Development environment for dev_2"
federation:
  group_prefix: "/services/dev_2/"
```

**Note**: Placeholder substitution happens after deep-merge, so you can use `{name}` in both defaults and project files.

---

## State Directory (`config/state/`)

The provisioner stores observed runtime state in separate YAML files under `config/state/`, keeping project configuration files purely declarative. Each project gets its own state file named after the config file stem (e.g., `config/projects/dev-team.yaml` → `config/state/dev-team.state.yaml`).

The state directory is created automatically on the first provisioning run. State files are **managed entirely by the provisioner** and should not be manually edited.

### State File Schema

**`metadata`** - Reconciliation metadata:
- `project_id`: OpenStack project UUID
- `domain_id`: Resolved domain identifier
- `last_reconciled_at`: ISO 8601 timestamp of last successful run

**`preallocated_fips`** - Current floating IP allocations:
- Written after allocating floating IPs
- Updated during each provisioning run to reflect current state
- Structure: List of `{id: <uuid>, address: <ip>}` objects

**`released_fips`** - Audit trail of lost floating IPs:
- Populated when tracked FIPs are deleted outside the provisioner
- Never automatically cleared — permanent audit trail
- Structure: List of `{id: <uuid>, address: <ip>, released_at: <timestamp>, reason: <text>}` objects

**`router_ips`** - Current router external IP addresses:
- Written after router gateway is attached
- Updated during each provisioning run
- Structure: List of `{id: <uuid>, name: <router_name>, external_ip: <ip>}` objects

**`released_router_ips`** - Audit trail of released router IPs:
- Populated when tracked router IPs are no longer present
- Never automatically cleared — permanent audit trail
- Structure: List of `{address: <ip>, router_name: <name>, released_at: <timestamp>, reason: <text>}` objects

### Usage Guidelines

**Do**:
- Review state files to understand current resource state
- Use them for auditing and compliance reporting
- Track resource drift by comparing current vs. released fields
- Add `config/state/` to `.gitignore` if you prefer not to track state in version control

**Don't**:
- Manually add, edit, or remove entries from state files
- Rely on state files existing before initial provisioning
- Copy state files between environments

**Note**: The provisioner will overwrite any manual changes to state files during the next run.

### Migration from Config Writeback

If your project YAML files contain state keys (`preallocated_fips`, `released_fips`, `router_ips`, `released_router_ips`) from a previous version, the provisioner will automatically migrate them to state files on the first run. State file values always take precedence over YAML state keys.

### Example State File

```yaml
# config/state/dev-team.state.yaml
metadata:
  project_id: abc123-4567-89ab-cdef-0123456789ab
  domain_id: default
  last_reconciled_at: "2026-04-04T14:10:00+00:00"

preallocated_fips:
  - id: abc123-4567-89ab-cdef-0123456789ab
    address: 203.0.113.42
  - id: def456-7890-abcd-ef01-23456789abcd
    address: 203.0.113.43

router_ips:
  - id: rtr123-4567-89ab-cdef-0123456789ab
    name: dev-team-router
    external_ip: 198.51.100.5
```

---

## Complete Examples

### Minimal Project Configuration

```yaml
# projects/minimal.yaml
name: minimal_project
resource_prefix: minimal

network:
  subnet:
    cidr: 10.0.0.0/24
    gateway_ip: 10.0.0.254
    allocation_pools:
      - start: 10.0.0.1
        end: 10.0.0.253
```

All other fields inherited from `defaults.yaml`.

---

### Full Project Configuration

```yaml
# projects/production.yaml
name: production
resource_prefix: prod
description: "Production environment"
enabled: true

network:
  mtu: 1500
  subnet:
    cidr: 10.100.0.0/24
    gateway_ip: 10.100.0.254
    allocation_pools:
      - start: 10.100.0.10
        end: 10.100.0.200
    dns_nameservers:
      - 8.8.8.8
      - 8.8.4.4
    dhcp: true

quotas:
  compute:
    cores: 32
    ram: 102400  # 100 GB
    instances: 20
  network:
    floating_ips: 1
    networks: 1
    subnets: 2
    routers: 1
    ports: 100
    security_groups: 20
    security_group_rules: 200
  block_storage:
    gigabytes: 1000
    volumes: 50
    snapshots: 20

security_group:
  name: default
  rules:
    - ICMP
    - rule: SSH
      remote_ip_prefix: "10.0.0.0/8"
    - HTTPS

domain: "production-domain"

federation:
  issuer: "https://idp.example.com/realms/production"
  mapping_id: "production-mapping"
  group_prefix: "/production/"
  user_type: "ephemeral"  # Required for cross-domain federation
  role_assignments:
    - idp_group: developers
      roles:
        - member
    - idp_group: operators
      roles:
        - member
        - load-balancer_member
    - idp_group: /admin/cloud-admins
      roles:
        - admin

group_role_assignments:
  - group: acme-domain-admin
    roles:
      - admin
      - load-balancer_member
  - group: "{name}-operators"
    roles:
      - member

external_network_name: "public-internet"
```

---

### Defaults File

```yaml
# defaults.yaml - Shared configuration for all projects
description: "Managed by openstack-tenantctl"
enabled: true

network:
  mtu: 0  # Use OpenStack default
  subnet:
    dns_nameservers:
      - 8.8.8.8
    dhcp: true

quotas:
  compute:
    cores: 20
    ram: 51200
    instances: 10
  network:
    floating_ips: 0
    networks: 1
    subnets: 1
    routers: 1
    ports: 50
    security_groups: 10
    security_group_rules: 100
  block_storage:
    gigabytes: 500
    volumes: 20
    snapshots: 10

security_group:
  name: default
  rules:
    - ICMP
    - SSH

federation:
  issuer: "https://myidp.corp/realms/myrealm"
  mapping_id: "my-mapping"
  group_prefix: "/services/openstack/"
  role_assignments:
    - idp_group: member
      roles:
        - member
        - load-balancer_member
    - idp_group: reader
      roles:
        - reader

group_role_assignments:
  - group: acme-domain-admin
    roles:
      - admin
      - load-balancer_member

external_network_name: "external"
```

---

### Inheritance Example

**defaults.yaml**:
```yaml
quotas:
  compute:
    cores: 20
    ram: 51200
    instances: 10
  network:
    ports: 50
    floating_ips: 0

security_group:
  rules:
    - ICMP
```

**projects/dev.yaml**:
```yaml
name: dev
resource_prefix: dev

quotas:
  compute:
    cores: 8      # Override
    ram: 16384    # Override
    # instances: 10 inherited from defaults

  network:
    floating_ips: 1  # Override
    # ports: 50 inherited from defaults

security_group:
  rules:  # Completely replaces defaults (lists override)
    - rule: SSH
      remote_ip_prefix: "10.0.0.0/8"

network:
  subnet:
    cidr: 10.1.0.0/24
    gateway_ip: 10.1.0.254
    allocation_pools:
      - start: 10.1.0.10
        end: 10.1.0.250
```

**Merged result** (after deep-merge):
```yaml
name: dev
resource_prefix: dev

quotas:
  compute:
    cores: 8         # From project
    ram: 16384       # From project
    instances: 10    # From defaults (inherited)
  network:
    ports: 50        # From defaults (inherited)
    floating_ips: 1  # From project

security_group:
  rules:  # From project (list override, preset expanded)
    - direction: ingress
      protocol: tcp
      port_range_min: 22
      port_range_max: 22
      remote_ip_prefix: "10.0.0.0/8"
      description: "Allow SSH"

network:
  subnet:
    cidr: 10.1.0.0/24
    gateway_ip: 10.1.0.254
    allocation_pools:
      - start: 10.1.0.10
        end: 10.1.0.250
```

---

### Project Lifecycle State Examples

#### Locked State (Maintenance Mode)

Use `state: locked` to freeze a project temporarily without deleting resources. This is useful for maintenance windows or cost reduction.

```yaml
# projects/maintenance_project.yaml
name: staging
resource_prefix: staging
state: locked  # Disables project, shelves instances

network:
  subnet:
    cidr: 10.50.0.0/24
    gateway_ip: 10.50.0.254
    allocation_pools:
      - start: 10.50.0.10
        end: 10.50.0.250

quotas:
  compute:
    cores: 16
    ram: 32768
    instances: 10
  network:
    floating_ips: 1
    networks: 1
    subnets: 1
    routers: 1
    ports: 50
    security_groups: 10
    security_group_rules: 100
```

**What happens**:
- Project is disabled in OpenStack
- All running instances are shelved (stopped but preserved)
- Network, quota, and security group provisioning is skipped
- To resume: change `state: locked` to `state: present` and re-run provisioner

---

#### Absent State (Teardown)

Use `state: absent` to decommission a project and remove it from OpenStack.

```yaml
# projects/decommissioned_project.yaml
name: old_project
resource_prefix: oldproj
state: absent  # Request project deletion

# Other fields are ignored when state is absent
# But keep them for audit trail or potential re-provisioning
```

**What happens**:
- Provisioner validates the project is safe to delete (no active resources)
- If validation passes, deletes the project from OpenStack
- If resources exist (instances, volumes, networks), provisioning fails with error
- Configuration file is NOT deleted (allows re-provisioning by changing state back to `present`)

**Important**: Manually clean up all resources before setting `state: absent`.

---

### Load Balancer Configuration Example

Example project with load balancer quotas configured:

```yaml
# projects/webapp.yaml
name: webapp_production
resource_prefix: webapp
description: "Web application with load balancers"

network:
  subnet:
    cidr: 10.200.0.0/24
    gateway_ip: 10.200.0.254
    allocation_pools:
      - start: 10.200.0.10
        end: 10.200.0.250

quotas:
  compute:
    cores: 32
    ram: 65536
    instances: 20
  network:
    floating_ips: 3
    networks: 1
    subnets: 1
    routers: 1
    ports: 100
    security_groups: 15
    security_group_rules: 150
    # Load balancer quotas (Octavia)
    load_balancers: 2      # Allow 2 load balancers
    listeners: 4           # 2 listeners per LB
    pools: 4               # 2 pools per LB
    health_monitors: 4     # 2 monitors per LB
    members: 20            # 10 backend instances per pool
  block_storage:
    gigabytes: 1000
    volumes: 30
    snapshots: 15

security_group:
  name: default
  rules:
    - ICMP
    - SSH
    - HTTP
    - HTTPS
```

---

## See Also

- [USER-GUIDE.md](USER-GUIDE.md) - Practical configuration guide for operators
- [SPECIFICATION.md](SPECIFICATION.md) - Technical architecture and design patterns
- [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md) - Why configuration inheritance works this way (DD-003)
